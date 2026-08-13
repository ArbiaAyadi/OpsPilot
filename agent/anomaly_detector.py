"""
anomaly_detector.py — Détection des anomalies infrastructure, cycle après cycle.

← CORRECTION MAJEURE (incident qui se répète) : les seuils par nœud (CPU,
RAM, disque, swap, iowait, latence, température, ZFS) n'avaient AUCUNE
déduplication d'un cycle à l'autre -- tant qu'une condition restait vraie
(ex: RAM critique pendant 10 minutes), CHAQUE cycle générait une NOUVELLE
anomalie, redéclenchant rapport + notification + nouvelle carte dans
Incidents/Recommendations dès la fin du cooldown, en boucle, pour le MÊME
problème en cours.

Nouveau principe, appliqué à chaque métrique par nœud : un PALIER
(CRITIQUE / IMPORTANT / None) est calculé pour la valeur actuelle ET pour
la valeur du cycle précédent (etat_prec) -- une anomalie est émise si le
palier a CHANGÉ (nouvelle entrée dans un palier, ou escalade
IMPORTANT->CRITIQUE).

← AJOUT (ré-escalade) : ce principe seul avait un vrai défaut, signalé à
raison -- un problème qui reste dans le MÊME palier (ex: RAM à 95% pendant
des heures, sans jamais changer de palier) ne redéclenchait plus JAMAIS
rien après son premier signalement. Le système ne distinguait pas "résolu"
de "toujours actif mais déjà vu une fois" -- l'information disparaissait
alors que le problème, lui, persistait. Une anomalie CRITIQUE est
maintenant AUSSI réémise si ça fait plus de INTERVALLE_REESCALADE_S depuis
son dernier signalement, même sans changement de palier -- même principe
que la ré-escalade dans les outils professionnels (PagerDuty, Datadog) :
un problème critique non résolu continue de se rappeler à l'attention
périodiquement, il ne devient jamais silencieux indéfiniment. Limité au
palier CRITIQUE (pas IMPORTANT) pour ne pas réintroduire le bruit qu'on
vient de corriger sur les paliers d'avertissement, moins urgents.
"""

import time

INTERVALLE_REESCALADE_S = 1800  # 30 min -- ajustable si besoin d'un rythme différent

_derniere_alerte_par_cle: dict = {}


def _cle(nom_cible: str, metrique: str) -> str:
    return f"{nom_cible}:{metrique}"


def _marquer_alerte(cle: str):
    _derniere_alerte_par_cle[cle] = time.time()


def _doit_reescalader(cle: str) -> bool:
    """Vrai si ça fait plus de INTERVALLE_REESCALADE_S depuis le dernier
    signalement de CETTE cle précise -- indépendant du fait que le palier
    ait changé ou non ce cycle-ci."""
    return (time.time() - _derniere_alerte_par_cle.get(cle, 0)) >= INTERVALLE_REESCALADE_S


def _palier(valeur: float, seuil_warning: float, seuil_critical: float) -> str | None:
    """Retourne 'CRITIQUE', 'IMPORTANT', ou None selon le palier atteint par cette valeur."""
    if valeur > seuil_critical:
        return "CRITIQUE"
    if valeur > seuil_warning:
        return "IMPORTANT"
    return None


def detecter_anomalies(etat: dict, etat_prec: dict) -> list:
    """
    Compare l'etat actuel avec le precedent et retourne la liste des
    NOUVELLES anomalies detectees ce cycle -- pas la liste de tout ce qui
    est actuellement anormal (ça, c'est etat["noeuds"] lui-même, déjà
    disponible en continu ailleurs). Inclut désormais aussi les
    ré-escalades de problèmes CRITIQUES sustenus (voir en-tête du fichier).
    """
    anomalies   = []
    noeuds_prec = {n.get("nom"): n for n in etat_prec.get("noeuds", [])}

    # ── Alertes Proxmox API (deja deduplique par message exact -- reste
    # correct pour ce type d'alerte, texte stable d'un cycle a l'autre) ──
    alertes_prec = {a["message"] for a in etat_prec.get("alertes", [])}
    for a in etat.get("alertes", []):
        if a.get("message") and a["message"] not in alertes_prec:
            anomalies.append(a)

    # ── VMs stoppees inopinement (deja base sur une transition d'etat,
    # jamais repete tant que la VM reste stoppee -- inchange) ──
    # ← AJOUT : CPU/RAM par VM -- absent jusqu'ici, seule la VM stoppee
    # etait detectee. Memes seuils que les planchers definis dans
    # rules_engine.SEUILS_PLANCHER (vm.cpu_pct, vm.ram_pct), meme principe
    # palier + re-escalade que les noeuds.
    vms_avant = {v["vmid"]: v for v in etat_prec.get("vms", [])}
    for vm in etat.get("vms", []):
        vid = vm["vmid"]
        nom_vm = vm.get("nom", f"VM{vid}")
        if (vid in vms_avant
                and vms_avant[vid]["statut"] == "running"
                and vm["statut"] == "stopped"):
            anomalies.append({
                "niveau":  "CRITIQUE",
                "cible":   nom_vm,
                "message": f"VM {nom_vm} (ID:{vid}) stoppee inopinement sur {vm['noeud']}",
                "type":    "vm_down",
            })
            continue  # une VM stoppee n'a pas de CPU/RAM a verifier ce cycle

        if vm.get("statut") != "running":
            continue

        prec_vm = vms_avant.get(vid, {})

        vcpu, vcpu_p = vm.get("cpu_pct", 0), prec_vm.get("cpu_pct", 0)
        palier, palier_p = _palier(vcpu, 75, 90), _palier(vcpu_p, 75, 90)
        cle = _cle(nom_vm, "vm_cpu")
        if (palier and palier != palier_p) or (palier == "CRITIQUE" and _doit_reescalader(cle)):
            mot = "critical" if palier == "CRITIQUE" else "high"
            anomalies.append({"niveau": palier, "cible": nom_vm, "message": f"VM {nom_vm} (ID:{vid}) on {vm.get('noeud','?')} -- CPU {mot}: {vcpu:.1f}%"})
            _marquer_alerte(cle)

        vram, vram_p = vm.get("ram_pct", 0), prec_vm.get("ram_pct", 0)
        palier, palier_p = _palier(vram, 80, 90), _palier(vram_p, 80, 90)
        cle = _cle(nom_vm, "vm_ram")
        if (palier and palier != palier_p) or (palier == "CRITIQUE" and _doit_reescalader(cle)):
            mot = "critical" if palier == "CRITIQUE" else "high"
            anomalies.append({"niveau": palier, "cible": nom_vm, "message": f"VM {nom_vm} (ID:{vid}) on {vm.get('noeud','?')} -- RAM {mot}: {vram:.1f}%"})
            _marquer_alerte(cle)

    # ── Verifications par noeud — changement de palier OU ré-escalade
    # d'un problème CRITIQUE toujours en cours ─────────────────────────
    for noeud in etat.get("noeuds", []):
        nom  = noeud.get("nom", noeud.get("node", "?"))
        prec = noeuds_prec.get(nom, {})

        # NIVEAU 1 - CPU
        cpu, cpu_p = noeud.get("cpu_pct", 0), prec.get("cpu_pct", 0)
        palier, palier_p = _palier(cpu, 65, 80), _palier(cpu_p, 65, 80)
        cle = _cle(nom, "cpu")
        if (palier and palier != palier_p) or (palier == "CRITIQUE" and _doit_reescalader(cle)):
            mot = "critical" if palier == "CRITIQUE" else "high"
            anomalies.append({"niveau": palier, "cible": nom, "message": f"Node {nom} -- CPU {mot}: {cpu:.1f}%"})
            _marquer_alerte(cle)

        # NIVEAU 1 - RAM
        ram, ram_p = noeud.get("ram_pct", 0), prec.get("ram_pct", 0)
        palier, palier_p = _palier(ram, 75, 85), _palier(ram_p, 75, 85)
        cle = _cle(nom, "ram")
        if (palier and palier != palier_p) or (palier == "CRITIQUE" and _doit_reescalader(cle)):
            mot = "critical" if palier == "CRITIQUE" else "high"
            anomalies.append({"niveau": palier, "cible": nom, "message": f"Node {nom} -- RAM {mot}: {ram:.1f}% ({noeud.get('ram_used_gb',0)}/{noeud.get('ram_total_gb',0)}GB)"})
            _marquer_alerte(cle)

        # NIVEAU 1 - Disk
        disk, disk_p = noeud.get("disk_pct", 0), prec.get("disk_pct", 0)
        palier, palier_p = _palier(disk, 80, 90), _palier(disk_p, 80, 90)
        cle = _cle(nom, "disk")
        if (palier and palier != palier_p) or (palier == "CRITIQUE" and _doit_reescalader(cle)):
            mot = "critical" if palier == "CRITIQUE" else "high"
            anomalies.append({"niveau": palier, "cible": nom, "message": f"Node {nom} -- Disk {mot}: {disk:.1f}% ({noeud.get('disk_used_gb',0)}/{noeud.get('disk_total_gb',0)}GB)"})
            _marquer_alerte(cle)

        # NIVEAU 2 - Swap
        swap, swap_p = noeud.get("swap_pct", 0), prec.get("swap_pct", 0)
        palier, palier_p = _palier(swap, 50, 80), _palier(swap_p, 50, 80)
        cle = _cle(nom, "swap")
        if (palier and palier != palier_p) or (palier == "CRITIQUE" and _doit_reescalader(cle)):
            suffixe = "(RAM already saturated)" if palier == "CRITIQUE" else "(memory pressure)"
            mot = "critical" if palier == "CRITIQUE" else "high"
            anomalies.append({"niveau": palier, "cible": nom, "message": f"Node {nom} -- Swap {mot}: {swap:.1f}% {suffixe}"})
            _marquer_alerte(cle)

        # NIVEAU 2 - I/O wait
        iowait, iowait_p = noeud.get("cpu_iowait_pct", 0), prec.get("cpu_iowait_pct", 0)
        palier, palier_p = _palier(iowait, 15, 30), _palier(iowait_p, 15, 30)
        cle = _cle(nom, "iowait")
        if (palier and palier != palier_p) or (palier == "CRITIQUE" and _doit_reescalader(cle)):
            mot = "critical" if palier == "CRITIQUE" else "high"
            suffixe = " (storage bottleneck)" if palier == "CRITIQUE" else ""
            anomalies.append({"niveau": palier, "cible": nom, "message": f"Node {nom} -- CPU I/O wait {mot}: {iowait:.1f}%{suffixe}"})
            _marquer_alerte(cle)

        # NIVEAU 2 - Latence disque (pire des deux sens, lecture/ecriture)
        rl, wl       = noeud.get("disk_read_latency_ms", 0), noeud.get("disk_write_latency_ms", 0)
        rl_p, wl_p   = prec.get("disk_read_latency_ms", 0),  prec.get("disk_write_latency_ms", 0)
        lat, lat_p   = max(rl, wl), max(rl_p, wl_p)
        palier, palier_p = _palier(lat, 10, 50), _palier(lat_p, 10, 50)
        cle = _cle(nom, "latency")
        if (palier and palier != palier_p) or (palier == "CRITIQUE" and _doit_reescalader(cle)):
            mot = "critical" if palier == "CRITIQUE" else "high"
            anomalies.append({"niveau": palier, "cible": nom, "message": f"Node {nom} -- Disk latency {mot}: read={rl}ms write={wl}ms"})
            _marquer_alerte(cle)

        # NIVEAU 2 - Erreurs et pertes reseau (compteurs d'evenements --
        # ne re-emet que si le seuil n'etait pas DEJA franchi au cycle
        # precedent -- pas de ré-escalade ici, ce sont des compteurs
        # d'événements, pas un état soutenu au même sens que les %)
        net_err,  net_err_p  = noeud.get("net_errors_in", 0) + noeud.get("net_errors_out", 0), \
                                prec.get("net_errors_in", 0)  + prec.get("net_errors_out", 0)
        if net_err > 10 and not (net_err_p > 10):
            anomalies.append({"niveau": "IMPORTANT", "cible": nom, "message": f"Node {nom} -- Network errors: {net_err:.0f}/s (check NIC or cable)"})

        net_drop, net_drop_p = noeud.get("net_drop_in", 0) + noeud.get("net_drop_out", 0), \
                                prec.get("net_drop_in", 0)  + prec.get("net_drop_out", 0)
        if net_drop > 10 and not (net_drop_p > 10):
            anomalies.append({"niveau": "IMPORTANT", "cible": nom, "message": f"Node {nom} -- Packet drops: {net_drop:.0f}/s (network saturation)"})

        # NIVEAU 3 - Temperature CPU
        temp, temp_p = noeud.get("cpu_temp_max_c", 0), prec.get("cpu_temp_max_c", 0)
        palier, palier_p = _palier(temp, 75, 85), _palier(temp_p, 75, 85)
        cle = _cle(nom, "temp")
        if (palier and palier != palier_p) or (palier == "CRITIQUE" and _doit_reescalader(cle)):
            mot = "critical" if palier == "CRITIQUE" else "high"
            suffixe = " (throttling risk)" if palier == "CRITIQUE" else ""
            anomalies.append({"niveau": palier, "cible": nom, "message": f"Node {nom} -- CPU temperature {mot}: {temp}C{suffixe}"})
            _marquer_alerte(cle)

        # NIVEAU 3 - SMART disques -- transition vers FAIL, PLUS ré-escalade
        # si le disque reste en échec CRITIQUE (uncorrectable/reallocated) --
        # un disque qui continue de dégrader mérite un rappel périodique,
        # contrairement à "pending sectors" (IMPORTANT), qui reste
        # transition-only comme avant.
        smart_ok      = noeud.get("smart_ok", True)
        smart_ok_prec = prec.get("smart_ok", True)
        uncorr  = noeud.get("smart_uncorrectable", 0)
        realloc = noeud.get("smart_reallocated_sectors", 0)
        pending = noeud.get("smart_pending_sectors", 0)
        cle = _cle(nom, "smart")
        transition_vers_fail = (not smart_ok) and smart_ok_prec
        reescalade_smart = (not smart_ok) and (uncorr > 0 or realloc > 0) and _doit_reescalader(cle)
        if transition_vers_fail or reescalade_smart:
            if uncorr > 0:
                anomalies.append({"niveau": "CRITIQUE", "cible": nom, "message": f"Node {nom} -- DISK FAILURE IMMINENT: {uncorr} uncorrectable errors (backup now!)"})
                _marquer_alerte(cle)
            elif realloc > 0:
                anomalies.append({"niveau": "CRITIQUE", "cible": nom, "message": f"Node {nom} -- Disk degraded: {realloc} reallocated sectors (plan replacement)"})
                _marquer_alerte(cle)
            elif pending > 0 and transition_vers_fail:
                anomalies.append({"niveau": "IMPORTANT", "cible": nom, "message": f"Node {nom} -- Disk: {pending} pending sectors"})

        # NIVEAU 3 - ZFS ARC (inverse : plus BAS = pire) -- IMPORTANT
        # uniquement, pas de ré-escalade (cohérent avec le choix de limiter
        # la ré-escalade au palier CRITIQUE)
        if noeud.get("zfs_available"):
            zfs_hit   = noeud.get("zfs_arc_hit_rate", 0)
            zfs_hit_p = prec.get("zfs_arc_hit_rate", 100) if prec.get("zfs_available") else 100
            palier   = "IMPORTANT" if 0 < zfs_hit   < 70 else None
            palier_p = "IMPORTANT" if 0 < zfs_hit_p < 70 else None
            if palier and palier != palier_p:
                anomalies.append({"niveau": "IMPORTANT", "cible": nom, "message": f"Node {nom} -- ZFS ARC hit rate low: {zfs_hit}% (add RAM for better I/O)"})

    # NIVEAU 3 - Corosync quorum -- transition OU ré-escalade si le quorum
    # reste perdu (aussi critique que possible, mérite un rappel périodique)
    quorum_avant = etat_prec.get("cluster", {}).get("corosync_quorum_ok", True)
    quorum_maintenant = etat.get("cluster", {}).get("corosync_quorum_ok", True)
    cle_quorum = "cluster:quorum"
    if (not quorum_maintenant and quorum_avant) or (not quorum_maintenant and _doit_reescalader(cle_quorum)):
        anomalies.append({
            "niveau":  "CRITIQUE",
            "cible":   "cluster",
            "message": "CLUSTER QUORUM LOST -- All VMs at risk of automatic shutdown",
        })
        _marquer_alerte(cle_quorum)

    return anomalies