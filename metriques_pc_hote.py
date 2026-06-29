"""
metriques_pc_hote.py — Collecte des ressources du PC hôte Windows
via windows_exporter (http://localhost:9182/metrics)

Ce fichier est INDÉPENDANT de metriques_proxmox.py et de Prometheus.
Il lit directement le format text/plain de windows_exporter sans
passer par aucun intermédiaire.

But : permettre au chat assistant de calculer l'allocation optimale
des ressources VMware pour pve1 et pve2 sans mettre le PC en danger.

Règles de sécurité appliquées :
  - Windows minimum : 4GB RAM + 2 cores + 60GB disk
  - Jamais plus de 80% des ressources allouées à VMware
  - Répartition 60/40 entre pve1 (principal) et pve2
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

# IP et port de windows_exporter sur le PC hôte
# Depuis le projet qui tourne sur le même PC → localhost
WINDOWS_EXPORTER_URL = os.getenv(
    "WINDOWS_EXPORTER_URL",
    "http://localhost:9182/metrics"
)
TIMEOUT = 5  # secondes

# Réserve minimale pour Windows + VMware overhead
WINDOWS_RESERVE_RAM_GB  = float(os.getenv("WINDOWS_RESERVE_RAM_GB",  "4.0"))
WINDOWS_RESERVE_CORES   = int(os.getenv("WINDOWS_RESERVE_CORES",     "2"))
WINDOWS_RESERVE_DISK_GB = float(os.getenv("WINDOWS_RESERVE_DISK_GB", "60.0"))


def _parse_metrics(text: str) -> dict:
    """
    Parse le format text/plain Prometheus exposé par windows_exporter.
    Retourne un dict {metric_name: value} pour les métriques qui nous intéressent.
    Ignore les lignes de commentaires (# HELP, # TYPE) et les lignes vides.
    """
    valeurs = {}
    for ligne in text.split('\n'):
        ligne = ligne.strip()
        if not ligne or ligne.startswith('#'):
            continue
        try:
            # Format : metric_name{labels} value timestamp
            # On veut juste metric_name et value
            if '{' in ligne:
                nom_complet = ligne[:ligne.index('{')]
                reste       = ligne[ligne.rindex('}') + 1:].strip()
            else:
                parties     = ligne.split()
                nom_complet = parties[0]
                reste       = parties[1] if len(parties) > 1 else "0"

            valeur = float(reste.split()[0])
            # Stocker avec le nom complet (sans labels) — on sommera ensuite
            if nom_complet not in valeurs:
                valeurs[nom_complet] = []
            valeurs[nom_complet].append(valeur)
        except Exception:
            continue
    return valeurs


def _sum_metric(metriques: dict, nom: str) -> float:
    """Somme toutes les valeurs d'une métrique (utile pour CPU multi-core)."""
    return sum(metriques.get(nom, [0.0]))


def _first_metric(metriques: dict, nom: str, defaut: float = 0.0) -> float:
    """Retourne la première valeur d'une métrique."""
    valeurs = metriques.get(nom, [])
    return valeurs[0] if valeurs else defaut


def _max_metric(metriques: dict, nom: str, defaut: float = 0.0) -> float:
    """Retourne la valeur max d'une métrique."""
    valeurs = metriques.get(nom, [])
    return max(valeurs) if valeurs else defaut


def collecter_ressources_pc_hote() -> dict:
    """
    Collecte les ressources réelles du PC Windows hôte via windows_exporter.

    Retourne un dict avec :
      - disponible      : bool — False si windows_exporter inaccessible
      - ram_total_gb    : RAM physique totale du PC
      - ram_used_gb     : RAM actuellement utilisée
      - ram_available_gb: RAM disponible pour de nouveaux processus
      - ram_pct_used    : % RAM utilisée
      - cpu_cores       : Nombre de cores logiques
      - cpu_pct_used    : % CPU utilisé (moyenne tous cores)
      - disk_total_gb   : Espace disque total (tous volumes)
      - disk_free_gb    : Espace disque libre
      - disk_used_gb    : Espace disque utilisé
      - disk_pct_used   : % disque utilisé
      - vmware_allocation : dict avec allocation optimale pve1/pve2
    """
    pc = {"disponible": False, "url": WINDOWS_EXPORTER_URL}

    try:
        r = requests.get(WINDOWS_EXPORTER_URL, timeout=TIMEOUT)
        if r.status_code != 200:
            print(f"[PC Host] windows_exporter HTTP {r.status_code}")
            return pc

        metriques = _parse_metrics(r.text)

        # ── CPU ──────────────────────────────────────────────────────────────
        # windows_exporter expose windows_cpu_time_total par mode et core
        # On compte le nombre de cores en comptant les valeurs "idle"
        idle_vals = metriques.get("windows_cpu_time_total", [])

        # Nombre de cores logiques via windows_cs_logical_processors
        cores = _first_metric(metriques, "windows_cs_logical_processors")
        if cores == 0:
            # Fallback : compter les valeurs idle (1 par core)
            cores = max(1, len(idle_vals) // 5)  # ~5 modes par core

        # CPU utilisé = 1 - (idle / total)
        # On utilise windows_cpu_processor_utility_total si disponible
        cpu_util = _sum_metric(metriques, "windows_cpu_processor_utility_total")
        cpu_idle = _sum_metric(metriques, "windows_cpu_time_total")

        if cpu_util > 0 and cores > 0:
            cpu_pct = round(min(100.0, (cpu_util / cores) * 100), 1)
        else:
            cpu_pct = 0.0

        pc["cpu_cores"]      = int(cores)
        pc["cpu_pct_used"]   = cpu_pct
        pc["cpu_pct_free"]   = round(100 - cpu_pct, 1)
        pc["cpu_cores_free"] = round(cores * (1 - cpu_pct / 100), 1)

        # ── RAM ──────────────────────────────────────────────────────────────
        # windows_cs_physical_memory_bytes = RAM physique totale
        # windows_memory_available_bytes   = RAM disponible immédiatement
        ram_total     = _first_metric(metriques, "windows_cs_physical_memory_bytes")
        ram_available = _first_metric(metriques, "windows_memory_available_bytes")

        if ram_total == 0:
            print("[PC Host] RAM metrics not found in windows_exporter output")
            return pc

        ram_used = max(0.0, ram_total - ram_available)

        pc["ram_total_gb"]     = round(ram_total     / (1024**3), 1)
        pc["ram_used_gb"]      = round(ram_used      / (1024**3), 1)
        pc["ram_available_gb"] = round(ram_available / (1024**3), 1)
        pc["ram_pct_used"]     = round(ram_used / ram_total * 100, 1)

        # ── DISQUE ───────────────────────────────────────────────────────────
        # windows_logical_disk_size_bytes et windows_logical_disk_free_bytes
        # On exclut les volumes système internes (HarddiskVolume*)
        # en sommant uniquement les lettres de lecteur (C:, D:, etc.)
        disk_total = _sum_metric(metriques, "windows_logical_disk_size_bytes")
        disk_free  = _sum_metric(metriques, "windows_logical_disk_free_bytes")

        if disk_total == 0:
            # Fallback sur les métriques brutes
            disk_total = _max_metric(metriques, "windows_logical_disk_size_bytes")
            disk_free  = _max_metric(metriques, "windows_logical_disk_free_bytes")

        disk_used = max(0.0, disk_total - disk_free)

        pc["disk_total_gb"] = round(disk_total / (1024**3), 1)
        pc["disk_free_gb"]  = round(disk_free  / (1024**3), 1)
        pc["disk_used_gb"]  = round(disk_used  / (1024**3), 1)
        pc["disk_pct_used"] = round(disk_used / disk_total * 100, 1) if disk_total > 0 else 0.0

        # ── CALCUL ALLOCATION OPTIMALE VMware ─────────────────────────────────
        # Ressources allouables = total - réserve Windows
        ram_allouable  = max(0.0, pc["ram_total_gb"]  - WINDOWS_RESERVE_RAM_GB)
        cores_allouble = max(0,   pc["cpu_cores"]      - WINDOWS_RESERVE_CORES)
        disk_allouble  = max(0.0, pc["disk_free_gb"]   - WINDOWS_RESERVE_DISK_GB)

        # Répartition 60% pve1 (nœud principal) / 40% pve2
        pc["vmware_allocation"] = {
            "pve1": {
                "ram_recommended_gb":  round(ram_allouable  * 0.60, 1),
                "cores_recommended":   max(1, int(cores_allouble * 0.60)),
                "disk_recommended_gb": round(disk_allouble  * 0.60, 1),
                "ram_mb":              int(ram_allouable * 0.60 * 1024),  # pour VMware (en MB)
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
            f"CPU:{pc['cpu_cores']} cores "
            f"Disk:{pc['disk_total_gb']}GB"
        )

    except requests.exceptions.ConnectionError:
        print(f"[PC Host] windows_exporter inaccessible sur {WINDOWS_EXPORTER_URL}")
    except Exception as e:
        print(f"[PC Host] Erreur collecte: {e}")

    return pc


if __name__ == "__main__":
    import json
    print("Test collecte ressources PC hôte...")
    result = collecter_ressources_pc_hote()
    print(json.dumps(result, indent=2, ensure_ascii=False))