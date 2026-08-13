"""
metriques_pc_hote.py — Collecte des ressources du PC hôte Windows via psutil.

But : permettre à l'agent (boucle de surveillance ET assistant conversationnel)
de connaître les ressources réelles du PC hôte, pour que les recommandations
de dimensionnement (nouvelle VM, nouveau node, RAM à ajouter...) soient
basées sur la marge de manœuvre réelle -- pas seulement sur les métriques
virtuelles de pve1/pve2, qui ne disent rien de ce qu'il reste côté physique.

Règles de sécurité appliquées :
  - Windows minimum : 4GB RAM + 2 cores + 60GB disk
  - Jamais plus de 80% des ressources allouées à VMware
  - Répartition 60/40 entre pve1 (principal) et pve2

← RÉÉCRIT ENTIÈREMENT (windows_exporter -> psutil) : ce fichier passait
auparavant par windows_exporter (http://localhost:9182/metrics), parsé
manuellement comme du texte Prometheus. Deux corrections successives y ont
déjà été apportées (calcul CPU via compteur cumulatif mal lu, puis RAM
totale absente de cette installation de windows_exporter) -- la vraie
cause commune : ce fichier dépendait d'un SERVICE EXTERNE à configurer
correctement pour des données que le processus Python peut obtenir
directement, puisqu'il tourne LUI-MÊME sur le PC concerné (confirmé :
`python -m agent.main` s'exécute depuis C:\\Users\\...\\agentic-ai, pas
dans une VM). psutil interroge le système d'exploitation en local -- RAM,
CPU, disque -- sans service intermédiaire, sans nom de métrique qui varie
selon la version d'un exportateur, sans configuration à maintenir. Détection
réellement automatique, contrairement à une valeur figée dans .env.

Contrat inchangé : collecter_ressources_pc_hote() retourne exactement les
mêmes clés qu'avant (disponible, ram_total_gb, ram_used_gb,
ram_available_gb, ram_pct_used, cpu_cores, cpu_pct_used, cpu_pct_free,
cpu_cores_free, disk_total_gb, disk_free_gb, disk_used_gb, disk_pct_used,
vmware_allocation) -- surveillance.py, incident_prompt.py,
websocket_handler.py et PageInfrastructure.jsx n'ont besoin d'AUCUNE
modification, ce fichier est le seul à changer.

windows_exporter n'est plus utilisé par ce fichier. D'après les fichiers
revus jusqu'ici dans ce projet, rien d'autre ne le référence -- si c'est
confirmé de ton côté, le service peut être arrêté sans impact.
"""

import os
import time
import psutil
from dotenv import load_dotenv

load_dotenv()

# Réserve minimale pour Windows + VMware overhead
WINDOWS_RESERVE_RAM_GB  = float(os.getenv("WINDOWS_RESERVE_RAM_GB",  "4.0"))
WINDOWS_RESERVE_CORES   = int(os.getenv("WINDOWS_RESERVE_CORES",     "2"))
WINDOWS_RESERVE_DISK_GB = float(os.getenv("WINDOWS_RESERVE_DISK_GB", "60.0"))

# Cache TTL -- évite de rappeler psutil.cpu_percent(interval=1.0) (qui
# bloque 1s pour mesurer un vrai delta CPU) à chaque cycle de surveillance.
CACHE_TTL_S      = float(os.getenv("HOTE_PC_CACHE_TTL_S", "30"))
_dernier_resultat = None
_dernier_ts       = 0.0


def collecter_ressources_pc_hote(forcer: bool = False) -> dict:
    """
    Point d'entrée public, avec cache (voir CACHE_TTL_S). Retourne le
    dernier résultat connu si celui-ci a moins de CACHE_TTL_S secondes,
    sinon relance une collecte fraîche (~1s, psutil.cpu_percent bloque le
    temps de mesurer un vrai delta). forcer=True ignore le cache -- utilisé
    par le bloc de test en bas de ce fichier.
    """
    global _dernier_resultat, _dernier_ts
    if not forcer and _dernier_resultat is not None and (time.time() - _dernier_ts) < CACHE_TTL_S:
        return _dernier_resultat
    resultat = _collecter_ressources_pc_hote_frais()
    _dernier_resultat = resultat
    _dernier_ts       = time.time()
    return resultat


def _collecter_ressources_pc_hote_frais() -> dict:
    """
    Collecte réelle (sans cache) des ressources du PC hôte via psutil.

    Retourne un dict avec :
      - disponible      : bool — False seulement si psutil lève une erreur
      - ram_total_gb    : RAM physique totale du PC
      - ram_used_gb     : RAM actuellement utilisée
      - ram_available_gb: RAM disponible pour de nouveaux processus
      - ram_pct_used    : % RAM utilisée
      - cpu_cores       : Nombre de cores logiques
      - cpu_pct_used    : % CPU utilisé (système entier, mesuré sur ~1s)
      - disk_total_gb   : Espace disque total (tous volumes fixes)
      - disk_free_gb    : Espace disque libre
      - disk_used_gb    : Espace disque utilisé
      - disk_pct_used   : % disque utilisé
      - vmware_allocation : dict avec allocation optimale pve1/pve2
    """
    pc = {"disponible": False}

    try:
        # ── CPU ──────────────────────────────────────────────────────────────
        # interval=1.0 : psutil mesure lui-même un vrai delta sur 1s (même
        # principe que le double relevé qu'on faisait manuellement avant
        # avec windows_exporter, mais géré nativement, en local, sans
        # requête réseau).
        cpu_pct = psutil.cpu_percent(interval=1.0)
        cores   = psutil.cpu_count(logical=True) or 1

        pc["cpu_cores"]      = int(cores)
        pc["cpu_pct_used"]   = round(cpu_pct, 1)
        pc["cpu_pct_free"]   = round(100 - cpu_pct, 1)
        pc["cpu_cores_free"] = round(cores * (1 - cpu_pct / 100), 1)

        # ── RAM ──────────────────────────────────────────────────────────────
        # psutil.virtual_memory() renvoie total/available directement en
        # octets, déjà cohérent avec la manière dont Windows lui-même
        # calcule la RAM "disponible" (inclut le cache récupérable) --
        # aucune reconstruction manuelle nécessaire.
        mem = psutil.virtual_memory()
        ram_total     = mem.total
        ram_available = mem.available
        ram_used      = max(0, ram_total - ram_available)

        pc["ram_total_gb"]     = round(ram_total     / (1024**3), 1)
        pc["ram_used_gb"]      = round(ram_used      / (1024**3), 1)
        pc["ram_available_gb"] = round(ram_available / (1024**3), 1)
        pc["ram_pct_used"]     = round(ram_used / ram_total * 100, 1) if ram_total > 0 else 0.0

        # ── DISQUE ───────────────────────────────────────────────────────────
        # psutil.disk_partitions(all=False) exclut déjà par défaut les
        # entrées pseudo/dupliquées (équivalent des HarddiskVolume* que le
        # parsing manuel de windows_exporter devait exclure explicitement
        # avant) -- on exclut ici seulement les lecteurs CD-ROM et les
        # entrées sans système de fichiers (lecteur vide).
        disk_total = 0
        disk_free  = 0
        for part in psutil.disk_partitions(all=False):
            if 'cdrom' in part.opts.lower() or not part.fstype:
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except (PermissionError, OSError):
                continue
            disk_total += usage.total
            disk_free  += usage.free
        disk_used = max(0, disk_total - disk_free)

        pc["disk_total_gb"] = round(disk_total / (1024**3), 1)
        pc["disk_free_gb"]  = round(disk_free  / (1024**3), 1)
        pc["disk_used_gb"]  = round(disk_used  / (1024**3), 1)
        pc["disk_pct_used"] = round(disk_used / disk_total * 100, 1) if disk_total > 0 else 0.0

        # ── CALCUL ALLOCATION OPTIMALE VMware ─────────────────────────────────
        # Inchangé depuis l'origine du fichier -- ressources allouables =
        # total - réserve Windows, répartition 60/40 pve1/pve2.
        ram_allouable  = max(0.0, pc["ram_total_gb"]  - WINDOWS_RESERVE_RAM_GB)
        cores_allouble = max(0,   pc["cpu_cores"]      - WINDOWS_RESERVE_CORES)
        disk_allouble  = max(0.0, pc["disk_free_gb"]   - WINDOWS_RESERVE_DISK_GB)

        pc["vmware_allocation"] = {
            "pve1": {
                "ram_recommended_gb":  round(ram_allouable  * 0.60, 1),
                "cores_recommended":   max(1, int(cores_allouble * 0.60)),
                "disk_recommended_gb": round(disk_allouble  * 0.60, 1),
                "ram_mb":              int(ram_allouable * 0.60 * 1024),
            },
            "pve2": {
                "ram_recommended_gb":  round(ram_allouable  * 0.40, 1),
                "cores_recommended":   max(1, int(cores_allouble * 0.40)),
                "disk_recommended_gb": round(disk_allouble  * 0.40, 1),
                "ram_mb":              int(ram_allouable * 0.40 * 1024),
            },
            "windows_reserve": {
                "ram_gb":   WINDOWS_RESERVE_RAM_GB,
                "cores":    WINDOWS_RESERVE_CORES,
                "disk_gb":  WINDOWS_RESERVE_DISK_GB,
            },
            "total_allouable_ram_gb":  round(ram_allouable,  1),
            "total_allouable_cores":   cores_allouble,
            "total_allouable_disk_gb": round(disk_allouble,  1),
        }

        pc["disponible"] = True
        print(
            f"[PC Host] OK — RAM:{pc['ram_total_gb']}GB "
            f"CPU:{pc['cpu_cores']} cores ({pc['cpu_pct_used']}% used) "
            f"Disk:{pc['disk_total_gb']}GB"
        )

    except Exception as e:
        print(f"[PC Host] Erreur collecte psutil: {e}")

    return pc


if __name__ == "__main__":
    import json
    print("Test collecte ressources PC hôte...")
    result = collecter_ressources_pc_hote(forcer=True)
    print(json.dumps(result, indent=2, ensure_ascii=False))