"""
hypervisor_metrics.py — Métriques de la couche hyperviseur ELLE-MÊME,
distinctes des métriques du PC hôte (metriques_pc_hote.py, qui couvre TOUT
le PC) et des métriques des noeuds Proxmox (metriques_proxmox.py, qui
couvre ce que CHAQUE noeud pense avoir alloué de l'intérieur).

But : combler un angle mort précis. On sait combien de RAM le PC hôte a au
total (metriques_pc_hote.py), et combien chaque noeud Proxmox pense avoir
(metriques_proxmox.py) -- mais rien ne dit combien la couche de
virtualisation ELLE-MÊME (le logiciel VMware/KVM/Hyper-V, pas le PC entier,
pas les VMs vues de l'intérieur) coûte réellement sur l'hôte physique.

── Architecture générique, symétrique à hypervisor_detect.py ───────────────
Un registre COLLECTEURS_HYPERVISEUR, indexé par le même "type" que
hypervisor_detect.get_hypervisor_context() retourne ("vmware", "kvm",
"hyper-v", "physical", "unknown"). Ajouter le support d'un type non encore
couvert = une fonction + une ligne dans ce registre -- même schéma exact
que ENRICHISSEURS_SERVICE dans vm_app_monitor.py. collecter_metriques_hyperviseur()
retourne {"disponible": False} pour tout type sans collecteur enregistré,
jamais d'exception -- comportement cohérent avec le reste du projet.

Seul "vmware" est implémenté pour l'instant. "kvm" et "hyper-v" ne sont
PAS devinés à l'aveugle : le mécanisme de collecte diffère structurellement
selon le type -- VMware Workstation tourne sur la MÊME machine que l'agent
(psutil local suffit, confirmé), alors que si Proxmox tournait sous
KVM/QEMU, l'hôte pourrait très bien être une machine SÉPARÉE, demandant un
accès SSH distant (comme pour Docker) plutôt que psutil local. Implémentés
le jour où l'un de ces environnements est réellement disponible pour
vérifier le nom exact du processus / la méthode d'accès correcte.

── Limite connue (attribution par VM) ───────────────────────────────────────
Deux processus vmware-vmx.exe confirmés sur cette machine (correspondant
aux 2 VMs pve1/pve2), mais leur CommandLine est vide via WMI/psutil sur
cette installation -- impossible de savoir de façon fiable lequel des deux
PID correspond à pve1 et lequel à pve2 sans un mécanisme supplémentaire non
encore vérifié (vmrun.exe, ou accès élevé). Ce fichier fournit donc un
TOTAL AGRÉGÉ (toutes les VM Workstation confondues), pas une attribution
par VM -- déjà une vraie valeur ajoutée (distingue "la virtualisation pèse
lourd sur le PC" d'"autre chose sur Windows consomme"), l'attribution fine
reste une amélioration future possible, pas bloquante pour ce qui suit.
"""
import os
import glob
import time
import psutil
import subprocess

# ── VMware Workstation ───────────────────────────────────────────────────────
# Noms de processus confirmés par requête directe sur la machine (voir
# conversation), PAS une supposition générique tirée de la documentation.
_VMWARE_VM_PROCESS = "vmware-vmx.exe"
_VMWARE_OVERHEAD_PROCESSES = {
    "vmware.exe", "vmware-tray.exe", "vmware-hostd.exe",
    "vmware-authd.exe", "vmware-usbarbitrator64.exe", "vmware-unity-helper.exe",
}


# ── Localisation des VMs via vmrun.exe (confirmé fonctionnel sur cette
# machine, sans élévation -- contrairement à psutil.open_files() sur les
# processus vmware-vmx.exe, qui échoue avec un refus d'accès). Chemin
# déduit de celui de vmware.exe, déjà confirmé dans la conversation.
_VMRUN_CHEMINS_POSSIBLES = [
    r"C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe",
    r"C:\Program Files\VMware\VMware Workstation\vmrun.exe",
]


def _trouver_vmrun() -> str | None:
    for chemin in _VMRUN_CHEMINS_POSSIBLES:
        if os.path.exists(chemin):
            return chemin
    return None


def _lister_vms_vmrun() -> list:
    """
    Retourne la liste des chemins .vmx des VMs actuellement en cours, via
    `vmrun list`. [] si vmrun.exe introuvable ou la commande échoue --
    jamais d'exception vers l'appelant.
    """
    chemin_vmrun = _trouver_vmrun()
    if not chemin_vmrun:
        return []
    try:
        resultat = subprocess.run([chemin_vmrun, "list"], capture_output=True, text=True, timeout=10)
        lignes = resultat.stdout.strip().split("\n")[1:]  # 1ère ligne : "Total running VMs: N"
        return [l.strip() for l in lignes if l.strip()]
    except Exception:
        return []


def _collecter_disque_reel_vmware() -> dict:
    """
    Taille RÉELLE des .vmdk sur le disque physique, via les chemins donnés
    par `vmrun list` -- pas une estimation, une lecture directe des fichiers
    (l'agent tourne sur cette même machine, os.path.getsize suffit, pas
    besoin de SSH).

    Attribution par VM PARTIELLE et honnête, volontairement : le nom du
    DOSSIER contenant le .vmx sert d'identifiant. Fiable quand il
    correspond à un nom donné manuellement (confirmé sur cette machine :
    un dossier s'appelle littéralement "pve1"). PAS fiable pour une VM dont
    le dossier a gardé le nom par défaut de VMware (basé sur l'OS invité
    choisi à la création, ex: "Debian 10.x 64-bit (4)") -- ça ne dit rien
    du nom du noeud Proxmox correspondant. Dans ce cas, le nom de dossier
    brut est gardé tel quel plutôt que de deviner "pve2" par élimination --
    éviter d'afficher une fausse certitude.

    ← CONFIRMÉ avec de vraies données (liste des fichiers individuels par
    dossier, en plus du total) : le total "réel" peut dépasser l'espace
    "alloué" rapporté par Proxmox -- observé : ~51.5GB réel vs 36.8GB
    alloué. Hypothèse initiale fausse (snapshots) -- les noms de fichiers
    observés ("disque-s001.vmdk" à "-s012.vmdk") sont le format SPLIT DISK
    de VMware (un seul disque virtuel découpé en plusieurs morceaux), pas
    des snapshots. La vraie explication, confirmée par la structure des
    fichiers : un disque en provisionnement fin GRANDIT quand on écrit des
    données, mais ne RÉTRÉCIT jamais automatiquement quand elles sont
    supprimées depuis l'intérieur de la VM -- l'espace occupé sur le disque
    physique peut donc rester bien plus grand que ce que Proxmox pense
    utiliser. Récupérable sans perte de données : VMware Workstation → VM
    éteinte → Settings → Hard Disk → Utilities → Compact, ou `fstrim`
    depuis l'intérieur de chaque VM si le disque virtuel supporte le
    TRIM/discard.
    """
    chemins_vmx = _lister_vms_vmrun()
    if not chemins_vmx:
        return {"disponible": False}

    detail = []
    total_gb = 0.0
    for chemin_vmx in chemins_vmx:
        dossier = os.path.dirname(chemin_vmx)
        nom     = os.path.basename(dossier)
        fichiers_trouves = []
        taille_octets = 0
        for chemin_vmdk in glob.glob(os.path.join(dossier, "*.vmdk")):
            try:
                taille = os.path.getsize(chemin_vmdk)
                taille_octets += taille
                fichiers_trouves.append({
                    "nom": os.path.basename(chemin_vmdk),
                    "taille_gb": round(taille / (1024**3), 2),
                })
            except OSError:
                continue
        taille_gb = round(taille_octets / (1024**3), 2)
        detail.append({
            "nom_dossier": nom,
            "vmdk_reel_gb": taille_gb,
            "fichiers": fichiers_trouves,
        })
        total_gb += taille_gb

    return {
        "disponible":         True,
        "vmdk_reel_total_gb": round(total_gb, 2),
        "detail_par_dossier": detail,
    }


def _collecter_vmware() -> dict:
    """
    Mesure le CPU des processus vmware-vmx.exe (les VMs elles-mêmes) et la
    RAM des autres processus vmware-*.exe (l'overhead de VMware Workstation
    lui-même : interface, service hostd, gestion USB...).

    ← CORRECTION IMPORTANTE : la RAM des VMs elles-mêmes (vms_ram_mb) a été
    RETIRÉE après vérification directe (diagnostic_vmware_ram.py, données
    réelles de cette machine). Aucun champ mémoire standard exposé par
    Windows à un processus utilisateur ne reflète fidèlement les ~1.9GB
    alloués à chaque VM :
      - rss (working set)    : 9-19MB -- très en dessous de la réalité
      - private/vms/pagefile : 47-56MB, TOUS identiques entre eux -- confirme
        qu'ils mesurent la même chose (mémoire "privée" classique), qui
        n'a jamais représenté la RAM du système invité
      - peak_wset            : 1962MB pour une VM (proche des 1.9GB
        attendus), mais seulement 887MB pour l'autre VM de taille
        comparable -- incohérent, ET c'est un MAXIMUM HISTORIQUE depuis le
        démarrage du processus, pas une mesure "en ce moment"
    Conclusion : VMware Workstation gère la RAM du système invité via son
    pilote noyau (vmx86.sys), d'une façon qui n'apparaît fidèlement dans
    aucun champ mémoire standard accessible à un processus utilisateur.
    Plutôt qu'afficher un chiffre dont on sait qu'il est faux (0.03GB,
    0.13GB observés -- des ordres de grandeur en dessous de la réalité),
    mieux vaut ne rien afficher : un mauvais chiffre est pire qu'aucun
    chiffre pour une recommandation censée être correcte. "ram_non_mesurable"
    signale explicitement cette absence aux consommateurs (prompt LLM,
    frontend), pour qu'ils ne l'interprètent jamais comme "0 = négligeable".

    Le CPU, lui, reste fiable -- Windows suit le temps CPU par processus de
    façon standard, sans l'ambiguïté "committed vs resident vs working set"
    propre à la RAM. Même technique qu'ailleurs dans ce projet
    (metriques_pc_hote.py) : amorcer cpu_percent(), attendre 1s, remesurer
    -- un premier appel isolé ne renvoie rien de significatif.

    psutil.NoSuchProcess/AccessDenied ignorées par processus individuel --
    un processus qui se termine entre l'énumération et la lecture (VM
    arrêtée pile à ce moment) ne doit pas faire échouer toute la collecte.
    """
    processus_vm, processus_overhead = [], []

    for proc in psutil.process_iter(["name"]):
        try:
            nom = (proc.info.get("name") or "").lower()
            if nom == _VMWARE_VM_PROCESS:
                proc.cpu_percent(interval=None)  # amorce -- 1er relevé sans signification, jeté
                processus_vm.append(proc)
            elif nom in _VMWARE_OVERHEAD_PROCESSES:
                processus_overhead.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if not processus_vm:
        return {"disponible": False}

    time.sleep(1.0)

    vms_cpu_pct = 0.0
    for proc in processus_vm:
        try:
            vms_cpu_pct += proc.cpu_percent(interval=None)  # vrai delta depuis l'amorce ci-dessus
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    overhead_ram_mb = 0.0
    for proc in processus_overhead:
        try:
            overhead_ram_mb += proc.memory_info().rss / (1024**2)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # ← AJOUT : psutil.cpu_percent() par processus n'est PAS normalisé sur
    # le nombre de coeurs -- 100% = un seul coeur entièrement occupé, pas
    # "tout le PC". Deux VM qui travaillent dur dépassent donc facilement
    # 100% en cumulé (observé : 151.2%), ce qui semble incohérent affiché
    # à côté de "Host PC CPU 21%" (celui-là normalisé sur 100% = le PC
    # entier). vms_cpu_pct_hote corrige ça -- même principe que
    # cpu_pct_vm déjà utilisé dans vm_app_monitor.py pour les services
    # (normaliser par la capacité du contenant, ici le PC plutôt qu'une VM).
    # vms_cpu_pct brut reste disponible aussi, pour qui voudrait le detail
    # par coeur plutôt que relatif au PC.
    cpu_cores_hote = psutil.cpu_count(logical=True) or 1

    # ← AJOUT : taille réelle des .vmdk (voir _collecter_disque_reel_vmware
    # ci-dessus). Séparé du reste de la collecte (vmrun, pas psutil) --
    # une erreur ici (vmrun introuvable, par ex.) ne doit jamais faire
    # échouer le CPU/RAM déjà mesurés au-dessus.
    try:
        disque_reel = _collecter_disque_reel_vmware()
        # ← AJOUT diagnostic : détail par fichier en console -- pour
        # comprendre un total "réel" incohérent avec l'alloué (voir
        # docstring de _collecter_disque_reel_vmware ci-dessus).
        for d in disque_reel.get("detail_par_dossier", []):
            noms = ", ".join(f"{f['nom']} ({f['taille_gb']}GB)" for f in d.get("fichiers", []))
            print(f"[Hypervisor Metrics] {d['nom_dossier']}: {d['vmdk_reel_gb']}GB total -- fichiers: {noms}")
    except Exception:
        disque_reel = {"disponible": False}

    return {
        "disponible":        True,
        "type":              "vmware",
        "vm_count":          len(processus_vm),
        "vms_cpu_pct":       round(vms_cpu_pct, 1),
        "vms_cpu_pct_hote":  round(vms_cpu_pct / cpu_cores_hote, 1),
        "overhead_ram_mb":   round(overhead_ram_mb, 1),  # coût de VMware Workstation lui-même, PAS la RAM des VMs
        "ram_non_mesurable": True,
        "vmdk_reel_gb":      disque_reel.get("vmdk_reel_total_gb"),  # None si vmrun indisponible
        "vmdk_detail":       disque_reel.get("detail_par_dossier"),
    }


COLLECTEURS_HYPERVISEUR = {
    "vmware": _collecter_vmware,
    # "kvm":     à implémenter une fois un environnement KVM/QEMU réel
    #            disponible pour vérifier le nom exact du processus
    #            (qemu-system-x86_64 ou proche) ET si l'hôte est bien la
    #            même machine que l'agent (sinon : SSH distant nécessaire,
    #            pas psutil local).
    # "hyper-v": à implémenter une fois un environnement Hyper-V réel
    #            disponible pour vérifier vmwp.exe / le namespace WMI
    #            root\\virtualization\\v2.
    # "physical"/"unknown" : volontairement absents -- pas de couche
    # hyperviseur séparée à mesurer dans ces cas (bare metal : le noeud
    # Proxmox EST l'hôte physique, déjà couvert par metriques_proxmox.py).
}

# Cache -- même raisonnement que metriques_pc_hote.py : cette collecte
# bloque ~1s (mesure CPU par processus), inutile de la refaire à chaque
# cycle de surveillance (60s) pour une donnée qui ne varie pas seconde par
# seconde.
CACHE_TTL_S       = float(os.getenv("HYPERVISOR_METRICS_CACHE_TTL_S", "30"))
_dernier_resultat = None
_dernier_ts       = 0.0


def collecter_metriques_hyperviseur(type_hyperviseur: str, forcer: bool = False) -> dict:
    """
    Point d'entrée générique -- appelée avec le "type" déjà retourné par
    hypervisor_detect.get_hypervisor_context(). Retourne {"disponible":
    False} pour tout type sans collecteur enregistré (kvm/hyper-v pas
    encore implémentés, "physical"/"unknown" qui n'ont structurellement
    rien à collecter ici) -- jamais d'exception.
    """
    global _dernier_resultat, _dernier_ts
    if not forcer and _dernier_resultat is not None and (time.time() - _dernier_ts) < CACHE_TTL_S:
        return _dernier_resultat

    collecteur = COLLECTEURS_HYPERVISEUR.get(type_hyperviseur)
    if not collecteur:
        resultat = {"disponible": False}
    else:
        try:
            resultat = collecteur()
        except Exception as e:
            print(f"[Hypervisor Metrics] Erreur collecte {type_hyperviseur}: {e}")
            resultat = {"disponible": False}

    _dernier_resultat = resultat
    _dernier_ts        = time.time()
    return resultat


if __name__ == "__main__":
    import json
    print("Test collecte métriques hyperviseur (vmware)...")
    print(json.dumps(collecter_metriques_hyperviseur("vmware", forcer=True), indent=2, ensure_ascii=False))