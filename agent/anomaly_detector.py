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

← AJOUT (nom de métrique canonique) : chaque anomalie porte désormais un
champ "metric" (ex: "cpu_pct", "disk_temp_max_c") en plus du texte libre
"message" -- avant, classifier_anomalies() (agent/incident_prompt.py)
devinait la catégorie d'un incident en cherchant des mots-clés DANS le
texte du message, avec un repli silencieux vers "ram" par défaut si rien
ne correspondait. Confirmé par le code lui-même : "steal"/"blocked"/
"corosync" n'avaient initialement aucun mot-clé reconnu et tombaient dans
ce repli, envoyant une recherche documentaire et un garde-fou RAM pour un
problème qui n'avait rien à voir. Le nom de métrique correspond exactement
aux champs produits par metriques_proxmox.py, pour que
rules_engine._categorie_depuis_metrique() et classifier_anomalies()
utilisent la même logique de classement -- une seule source de vérité,
plus deux systèmes de deviner séparés.
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


# ══════════════════════════════════════════════════════════════════════════════
# Les seuils appliqués viennent des règles générées par l'IA
# ══════════════════════════════════════════════════════════════════════════════
# ← AJOUT (écart majeur constaté à l'analyse) : jusqu'ici, les seuils
# affichés sur la page "Monitoring Rules" et les seuils qui DÉCLENCHENT
# réellement les alertes étaient DEUX SYSTÈMES SÉPARÉS. rules_engine.py
# faisait la recherche documentaire puis demandait au LLM de raisonner un
# seuil pour chaque métrique -- et ce résultat n'était qu'AFFICHÉ. La
# détection, elle, utilisait 15 valeurs codées en dur dans ce fichier,
# maintenues à la main en parallèle (les commentaires "mêmes seuils que
# rules_engine.SEUILS_PLANCHER" en témoignent : une synchronisation
# humaine, jamais programmatique). Concrètement : le LLM pouvait conclure
# "CPU critique à 82% pour ce cluster" et l'alerte se déclenchait quand
# même à 80%, la valeur écrite ici.
#
# C'est l'écart entre ce que le projet annonce (l'agent recherche et
# décide des règles) et ce qu'il faisait (l'agent recherche, affiche, et
# des constantes décident). Cette fonction ferme cet écart : les seuils
# réellement appliqués sont désormais LUS depuis les règles générées.
#
# Repli délibéré : si aucune règle IA n'existe encore pour cette métrique
# (premier démarrage, génération échouée, quota Groq épuisé), les valeurs
# codées ici restent utilisées -- la surveillance ne doit JAMAIS cesser de
# détecter parce que le LLM est indisponible. Elles passent ainsi du
# statut de "seule vérité" à celui de "filet de sécurité".
def _seuils(metric: str, defaut_warn: float, defaut_crit: float) -> tuple:
    """Seuils (IMPORTANT, CRITIQUE) pour cette métrique, lus dans les
    règles générées par l'IA -- repli sur les valeurs passées en argument
    si aucune règle exploitable n'existe."""
    try:
        from agent.rules_engine import get_regles_ia
        regles = get_regles_ia() or []
    except Exception:
        return defaut_warn, defaut_crit

    warn, crit = None, None
    for r in regles:
        if r.get("metric") != metric:
            continue
        s = r.get("seuil")
        if not isinstance(s, (int, float)) or isinstance(s, bool):
            continue
        sev = str(r.get("severite", "")).upper()
        if "CRIT" in sev:
            crit = float(s)
        elif "IMPORT" in sev or "HIGH" in sev or "WARN" in sev:
            warn = float(s)

    warn = defaut_warn if warn is None else warn
    crit = defaut_crit if crit is None else crit

    # Garde-fou de cohérence : un seuil CRITIQUE doit rester strictement
    # au-dessus du seuil IMPORTANT. Si le LLM produit l'inverse (ou deux
    # valeurs identiques), on retombe entièrement sur les valeurs de repli
    # plutôt que d'appliquer une échelle incohérente qui classerait mal
    # chaque alerte -- même esprit que _valider_seuil() côté
    # rules_engine.py, appliqué ici au COUPLE de seuils et non à chacun
    # pris isolément.
    if crit <= warn:
        return defaut_warn, defaut_crit
    return warn, crit


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
    # Pas de champ "metric" ici : une alerte Proxmox API brute ne
    # correspond pas a une metrique numerique unique de la meme facon --
    # classifier_anomalies() retombe sur son ancien repli mots-cles pour
    # celles-ci specifiquement, ce qui reste correct pour ce cas.
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
                # Pas de "metric" ici non plus : un arret de VM n'est pas
                # un depassement de seuil numerique, c'est une transition
                # d'etat -- deja gere explicitement par "type": "vm_down"
                # cote classifier_anomalies(), qui le reconnait avant tout
                # le reste.
            })
            continue  # une VM stoppee n'a pas de CPU/RAM a verifier ce cycle

        if vm.get("statut") != "running":
            continue

        prec_vm = vms_avant.get(vid, {})

        vcpu, vcpu_p = vm.get("cpu_pct", 0), prec_vm.get("cpu_pct", 0)
        _w, _c = _seuils("vm.cpu_pct", 75, 90)
        palier, palier_p = _palier(vcpu, _w, _c), _palier(vcpu_p, _w, _c)
        cle = _cle(nom_vm, "vm_cpu")
        if (palier and palier != palier_p) or (palier == "CRITIQUE" and _doit_reescalader(cle)):
            mot = "critical" if palier == "CRITIQUE" else "high"
            anomalies.append({"niveau": palier, "cible": nom_vm, "metric": "cpu_pct", "message": f"VM {nom_vm} (ID:{vid}) on {vm.get('noeud','?')} -- CPU {mot}: {vcpu:.1f}%"})
            _marquer_alerte(cle)

        vram, vram_p = vm.get("ram_pct", 0), prec_vm.get("ram_pct", 0)
        _w, _c = _seuils("vm.ram_pct", 80, 90)
        palier, palier_p = _palier(vram, _w, _c), _palier(vram_p, _w, _c)
        cle = _cle(nom_vm, "vm_ram")
        if (palier and palier != palier_p) or (palier == "CRITIQUE" and _doit_reescalader(cle)):
            mot = "critical" if palier == "CRITIQUE" else "high"
            anomalies.append({"niveau": palier, "cible": nom_vm, "metric": "ram_pct", "message": f"VM {nom_vm} (ID:{vid}) on {vm.get('noeud','?')} -- RAM {mot}: {vram:.1f}%"})
            _marquer_alerte(cle)

        # ← AJOUT : disque par VM -- absent jusqu'ici (seuls les noeuds
        # etaient verifies pour le disque), alors que Prometheus fournit
        # deja disk_pct par VM (voir surveillance.py, fusion prom_vms).
        # Memes seuils que le plancher vm.disk_pct deja defini dans
        # rules_engine.SEUILS_PLANCHER (jamais utilise jusqu'ici cote
        # detection reelle) -- coherence entre ce qui est affiche comme
        # regle et ce qui declenche vraiment une alerte.
        vdisk, vdisk_p = vm.get("disk_pct", 0), prec_vm.get("disk_pct", 0)
        _w, _c = _seuils("vm.disk_pct", 80, 90)
        palier, palier_p = _palier(vdisk, _w, _c), _palier(vdisk_p, _w, _c)
        cle = _cle(nom_vm, "vm_disk")
        if (palier and palier != palier_p) or (palier == "CRITIQUE" and _doit_reescalader(cle)):
            mot = "critical" if palier == "CRITIQUE" else "high"
            anomalies.append({"niveau": palier, "cible": nom_vm, "metric": "disk_pct", "message": f"VM {nom_vm} (ID:{vid}) on {vm.get('noeud','?')} -- Disk {mot}: {vdisk:.1f}%"})
            _marquer_alerte(cle)

        # ← NON couvert délibérément : débit disque VM (Read/Write
        # Throughput) et réseau VM (Net In/Out). Même raisonnement déjà
        # appliqué à la bande passante réseau des NŒUDS (voir fin de
        # fichier) : un seuil fixe en MB/s ou Mbps nécessiterait de
        # connaître la capacité réelle de la couche sous-jacente (débit
        # disque du stockage physique, capacité du lien réseau virtuel) --
        # une valeur devinée casserait sur tout changement de matériel ou
        # de charge de travail légitime, contre le principe déjà établi sur
        # ce projet. Contrairement au réseau nœud, ces métriques VM ne sont
        # pas dans le vecteur du LSTM (qui n'agrège qu'au niveau cluster,
        # pas par VM) -- ce trou reste donc réel, pas compensé ailleurs. La
        # bonne solution serait une comparaison à une base glissante récente
        # (même principe que detecter_derive_num_procs dans
        # vm_app_monitor.py), pas un seuil absolu -- un vrai chantier à
        # part, pas ajouté ici par souci de cohérence plutôt que de
        # remplir la case à tout prix.

    # ── Verifications par noeud — changement de palier OU ré-escalade
    # d'un problème CRITIQUE toujours en cours ─────────────────────────
    for noeud in etat.get("noeuds", []):
        nom  = noeud.get("nom", noeud.get("node", "?"))
        prec = noeuds_prec.get(nom, {})

        # NIVEAU 1 - CPU
        cpu, cpu_p = noeud.get("cpu_pct", 0), prec.get("cpu_pct", 0)
        _w, _c = _seuils("server.cpu_pct", 65, 80)
        palier, palier_p = _palier(cpu, _w, _c), _palier(cpu_p, _w, _c)
        cle = _cle(nom, "cpu")
        if (palier and palier != palier_p) or (palier == "CRITIQUE" and _doit_reescalader(cle)):
            mot = "critical" if palier == "CRITIQUE" else "high"
            anomalies.append({"niveau": palier, "cible": nom, "metric": "cpu_pct", "message": f"Node {nom} -- CPU {mot}: {cpu:.1f}%"})
            _marquer_alerte(cle)

        # NIVEAU 1 - RAM
        ram, ram_p = noeud.get("ram_pct", 0), prec.get("ram_pct", 0)
        _w, _c = _seuils("server.ram_pct", 75, 85)
        palier, palier_p = _palier(ram, _w, _c), _palier(ram_p, _w, _c)
        cle = _cle(nom, "ram")
        if (palier and palier != palier_p) or (palier == "CRITIQUE" and _doit_reescalader(cle)):
            mot = "critical" if palier == "CRITIQUE" else "high"
            anomalies.append({"niveau": palier, "cible": nom, "metric": "ram_pct", "message": f"Node {nom} -- RAM {mot}: {ram:.1f}% ({noeud.get('ram_used_gb',0)}/{noeud.get('ram_total_gb',0)}GB)"})
            _marquer_alerte(cle)

        # NIVEAU 1 - Disk
        disk, disk_p = noeud.get("disk_pct", 0), prec.get("disk_pct", 0)
        _w, _c = _seuils("server.disk_pct", 80, 90)
        palier, palier_p = _palier(disk, _w, _c), _palier(disk_p, _w, _c)
        cle = _cle(nom, "disk")
        if (palier and palier != palier_p) or (palier == "CRITIQUE" and _doit_reescalader(cle)):
            mot = "critical" if palier == "CRITIQUE" else "high"
            anomalies.append({"niveau": palier, "cible": nom, "metric": "disk_pct", "message": f"Node {nom} -- Disk {mot}: {disk:.1f}% ({noeud.get('disk_used_gb',0)}/{noeud.get('disk_total_gb',0)}GB)"})
            _marquer_alerte(cle)

        # ← AJOUT : Load Average -- collecte (load_avg_1m, deja utilisee
        # comme feature du LSTM) mais jamais verifiee par seuil individuel.
        # Reutilise le plancher deja etabli dans rules_engine.SEUILS_PLANCHER
        # ("server.load_avg": CRITIQUE=8, IMPORTANT=4), jamais applique cote
        # detection reelle jusqu'ici -- valeur absolue (pas normalisee par
        # nombre de coeurs), coherente avec ce plancher deja choisi ailleurs
        # dans le projet plutot qu'un nouveau calcul invente ici.
        load, load_p = noeud.get("load_avg_1m", 0), prec.get("load_avg_1m", 0)
        _w, _c = _seuils("server.load_avg_1m", 4, 8)
        palier, palier_p = _palier(load, _w, _c), _palier(load_p, _w, _c)
        cle = _cle(nom, "load")
        if (palier and palier != palier_p) or (palier == "CRITIQUE" and _doit_reescalader(cle)):
            mot = "critical" if palier == "CRITIQUE" else "high"
            anomalies.append({"niveau": palier, "cible": nom, "metric": "load_avg_1m", "message": f"Node {nom} -- Load average {mot}: {load:.2f}"})
            _marquer_alerte(cle)

        # NIVEAU 2 - Swap
        swap, swap_p = noeud.get("swap_pct", 0), prec.get("swap_pct", 0)
        _w, _c = _seuils("server.swap_pct", 50, 80)
        palier, palier_p = _palier(swap, _w, _c), _palier(swap_p, _w, _c)
        cle = _cle(nom, "swap")
        if (palier and palier != palier_p) or (palier == "CRITIQUE" and _doit_reescalader(cle)):
            suffixe = "(RAM already saturated)" if palier == "CRITIQUE" else "(memory pressure)"
            mot = "critical" if palier == "CRITIQUE" else "high"
            anomalies.append({"niveau": palier, "cible": nom, "metric": "swap_pct", "message": f"Node {nom} -- Swap {mot}: {swap:.1f}% {suffixe}"})
            _marquer_alerte(cle)

        # NIVEAU 2 - I/O wait
        iowait, iowait_p = noeud.get("cpu_iowait_pct", 0), prec.get("cpu_iowait_pct", 0)
        _w, _c = _seuils("server.cpu_iowait_pct", 15, 30)
        palier, palier_p = _palier(iowait, _w, _c), _palier(iowait_p, _w, _c)
        cle = _cle(nom, "iowait")
        if (palier and palier != palier_p) or (palier == "CRITIQUE" and _doit_reescalader(cle)):
            mot = "critical" if palier == "CRITIQUE" else "high"
            suffixe = " (storage bottleneck)" if palier == "CRITIQUE" else ""
            anomalies.append({"niveau": palier, "cible": nom, "metric": "cpu_iowait_pct", "message": f"Node {nom} -- CPU I/O wait {mot}: {iowait:.1f}%{suffixe}"})
            _marquer_alerte(cle)

        # ← AJOUT : CPU steal -- le temps CPU que l'hyperviseur DEVAIT
        # donner a ce noeud mais ne lui a pas donne (contention avec
        # d'autres charges sur le meme hote physique). Particulierement
        # pertinent sur cette infrastructure precise : virtualisation
        # imbriquee (Windows -> VMware Workstation -> Proxmox -> VMs),
        # chaque couche pouvant introduire du steal. Collecte deja via
        # Prometheus (cpu_steal_pct, voir surveillance.py) mais jamais
        # verifiee jusqu'ici -- meme motif iowait/latence : le noeud
        # "n'obtient pas le CPU dont il a besoin", juste une cause
        # differente (contention hyperviseur plutot que disque).
        steal, steal_p = noeud.get("cpu_steal_pct", 0), prec.get("cpu_steal_pct", 0)
        _w, _c = _seuils("server.cpu_steal_pct", 10, 25)
        palier, palier_p = _palier(steal, _w, _c), _palier(steal_p, _w, _c)
        cle = _cle(nom, "steal")
        if (palier and palier != palier_p) or (palier == "CRITIQUE" and _doit_reescalader(cle)):
            mot = "critical" if palier == "CRITIQUE" else "high"
            suffixe = " (hypervisor contention -- check host-level load)" if palier == "CRITIQUE" else ""
            anomalies.append({"niveau": palier, "cible": nom, "metric": "cpu_steal_pct", "message": f"Node {nom} -- CPU steal {mot}: {steal:.1f}%{suffixe}"})
            _marquer_alerte(cle)

        # ← AJOUT : file descriptors -- aucune verification jusqu'ici alors
        # qu'un plancher existe deja (server.fd_used_pct dans
        # rules_engine.SEUILS_PLANCHER, jamais applique cote detection
        # reelle). Un epuisement de FD peut faire echouer silencieusement
        # des services (impossible d'ouvrir un nouveau fichier/socket) sans
        # jamais apparaitre dans CPU/RAM/Disk. Memes valeurs que le
        # plancher deja etabli, pour rester coherent avec ce qui est deja
        # documente comme regle.
        fd, fd_p = noeud.get("fd_used_pct", 0), prec.get("fd_used_pct", 0)
        _w, _c = _seuils("server.fd_used_pct", 70, 90)
        palier, palier_p = _palier(fd, _w, _c), _palier(fd_p, _w, _c)
        cle = _cle(nom, "fd")
        if (palier and palier != palier_p) or (palier == "CRITIQUE" and _doit_reescalader(cle)):
            mot = "critical" if palier == "CRITIQUE" else "high"
            suffixe = " (services may start failing to open files/sockets)" if palier == "CRITIQUE" else ""
            anomalies.append({"niveau": palier, "cible": nom, "metric": "fd_used_pct", "message": f"Node {nom} -- File descriptors {mot}: {fd:.1f}%{suffixe}"})
            _marquer_alerte(cle)

        # NIVEAU 2 - Latence disque (pire des deux sens, lecture/ecriture)
        rl, wl       = noeud.get("disk_read_latency_ms", 0), noeud.get("disk_write_latency_ms", 0)
        rl_p, wl_p   = prec.get("disk_read_latency_ms", 0),  prec.get("disk_write_latency_ms", 0)
        lat, lat_p   = max(rl, wl), max(rl_p, wl_p)
        _w, _c = _seuils("server.disk_read_latency_ms", 10, 50)
        palier, palier_p = _palier(lat, _w, _c), _palier(lat_p, _w, _c)
        cle = _cle(nom, "latency")
        if (palier and palier != palier_p) or (palier == "CRITIQUE" and _doit_reescalader(cle)):
            mot = "critical" if palier == "CRITIQUE" else "high"
            anomalies.append({"niveau": palier, "cible": nom, "metric": "disk_read_latency_ms", "message": f"Node {nom} -- Disk latency {mot}: read={rl}ms write={wl}ms"})
            _marquer_alerte(cle)

        # ← AJOUT : processus bloques (etat D, uninterruptible sleep) --
        # symptome direct d'un goulot d'I/O, complementaire a iowait/latence
        # (ceux-la mesurent le TEMPS perdu a attendre, celui-ci compte
        # combien de processus sont concretement bloques en train d'attendre
        # le disque). Exactement le type de signal qui aurait pu alerter
        # plus tot sur la saturation du pool LVM de pve2 (VM103, io-error)
        # diagnostiquee cette meme session -- avant que ca degenere en VM
        # completement bloquee. Seuils bas deliberement : meme 2-3 processus
        # bloques de facon soutenue merite d'etre remonte tot.
        blocked, blocked_p = noeud.get("procs_blocked", 0), prec.get("procs_blocked", 0)
        _w, _c = _seuils("server.procs_blocked", 2, 5)
        palier, palier_p = _palier(blocked, _w, _c), _palier(blocked_p, _w, _c)
        cle = _cle(nom, "procs_blocked")
        if (palier and palier != palier_p) or (palier == "CRITIQUE" and _doit_reescalader(cle)):
            mot = "critical" if palier == "CRITIQUE" else "high"
            suffixe = " (processes stuck waiting on I/O -- check storage)" if palier == "CRITIQUE" else ""
            anomalies.append({"niveau": palier, "cible": nom, "metric": "procs_blocked", "message": f"Node {nom} -- Blocked processes {mot}: {blocked:.0f}{suffixe}"})
            _marquer_alerte(cle)

        # NIVEAU 2 - Erreurs et pertes reseau (compteurs d'evenements --
        # ne re-emet que si le seuil n'etait pas DEJA franchi au cycle
        # precedent -- pas de ré-escalade ici, ce sont des compteurs
        # d'événements, pas un état soutenu au même sens que les %)
        net_err,  net_err_p  = noeud.get("net_errors_in", 0) + noeud.get("net_errors_out", 0), \
                                prec.get("net_errors_in", 0)  + prec.get("net_errors_out", 0)
        if net_err > 10 and not (net_err_p > 10):
            anomalies.append({"niveau": "IMPORTANT", "cible": nom, "metric": "net_errors_in", "message": f"Node {nom} -- Network errors: {net_err:.0f}/s (check NIC or cable)"})

        net_drop, net_drop_p = noeud.get("net_drop_in", 0) + noeud.get("net_drop_out", 0), \
                                prec.get("net_drop_in", 0)  + prec.get("net_drop_out", 0)
        if net_drop > 10 and not (net_drop_p > 10):
            anomalies.append({"niveau": "IMPORTANT", "cible": nom, "metric": "net_drop_in", "message": f"Node {nom} -- Packet drops: {net_drop:.0f}/s (network saturation)"})

        # NIVEAU 3 - Temperature CPU
        temp, temp_p = noeud.get("cpu_temp_max_c", 0), prec.get("cpu_temp_max_c", 0)
        _w, _c = _seuils("server.cpu_temp_max_c", 75, 85)
        palier, palier_p = _palier(temp, _w, _c), _palier(temp_p, _w, _c)
        cle = _cle(nom, "temp")
        if (palier and palier != palier_p) or (palier == "CRITIQUE" and _doit_reescalader(cle)):
            mot = "critical" if palier == "CRITIQUE" else "high"
            suffixe = " (throttling risk)" if palier == "CRITIQUE" else ""
            anomalies.append({"niveau": palier, "cible": nom, "metric": "cpu_temp_max_c", "message": f"Node {nom} -- CPU temperature {mot}: {temp}C{suffixe}"})
            _marquer_alerte(cle)

        # ← AJOUT : temperature disque -- absente jusqu'ici, seule la
        # temperature CPU etait verifiee. Deja collectee via Prometheus
        # (disk_temp_max_c, voir surveillance.py) mais jamais exploitee.
        # Seuils generaux (HDD/SSD grand public, la fiabilite se degrade
        # nettement au-dela de ~50C selon les etudes constructeur type
        # Backblaze) -- a ajuster si les disques reels de ce cluster ont
        # des specifications differentes.
        dtemp, dtemp_p = noeud.get("disk_temp_max_c", 0), prec.get("disk_temp_max_c", 0)
        _w, _c = _seuils("server.disk_temp_max_c", 50, 60)
        palier, palier_p = _palier(dtemp, _w, _c), _palier(dtemp_p, _w, _c)
        cle = _cle(nom, "disk_temp")
        if (palier and palier != palier_p) or (palier == "CRITIQUE" and _doit_reescalader(cle)):
            mot = "critical" if palier == "CRITIQUE" else "high"
            suffixe = " (accelerated wear risk)" if palier == "CRITIQUE" else ""
            anomalies.append({"niveau": palier, "cible": nom, "metric": "disk_temp_max_c", "message": f"Node {nom} -- Disk temperature {mot}: {dtemp}C{suffixe}"})
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
                anomalies.append({"niveau": "CRITIQUE", "cible": nom, "metric": "smart_uncorrectable", "message": f"Node {nom} -- DISK FAILURE IMMINENT: {uncorr} uncorrectable errors (backup now!)"})
                _marquer_alerte(cle)
            elif realloc > 0:
                anomalies.append({"niveau": "CRITIQUE", "cible": nom, "metric": "smart_reallocated_sectors", "message": f"Node {nom} -- Disk degraded: {realloc} reallocated sectors (plan replacement)"})
                _marquer_alerte(cle)
            elif pending > 0 and transition_vers_fail:
                anomalies.append({"niveau": "IMPORTANT", "cible": nom, "metric": "smart_pending_sectors", "message": f"Node {nom} -- Disk: {pending} pending sectors"})

        # NIVEAU 3 - ZFS ARC (inverse : plus BAS = pire) -- IMPORTANT
        # uniquement, pas de ré-escalade (cohérent avec le choix de limiter
        # la ré-escalade au palier CRITIQUE)
        if noeud.get("zfs_available"):
            zfs_hit   = noeud.get("zfs_arc_hit_rate", 0)
            zfs_hit_p = prec.get("zfs_arc_hit_rate", 100) if prec.get("zfs_available") else 100
            palier   = "IMPORTANT" if 0 < zfs_hit   < 70 else None
            palier_p = "IMPORTANT" if 0 < zfs_hit_p < 70 else None
            if palier and palier != palier_p:
                anomalies.append({"niveau": "IMPORTANT", "cible": nom, "metric": "zfs_arc_hit_rate", "message": f"Node {nom} -- ZFS ARC hit rate low: {zfs_hit}% (add RAM for better I/O)"})

    # ← AJOUT : demon corosync lui-meme -- distinct de la perte de quorum
    # ci-dessous. corosync_ok=False sur UN noeud peut precede ou accompagner
    # une perte de quorum sans forcement la declencher au meme instant
    # (selon la config exacte du cluster) -- signal independant, verifie
    # comme une transition d'etat par noeud, pas de re-escalade (le quorum
    # ci-dessous couvre deja le cas soutenu le plus grave).
    for noeud in etat.get("noeuds", []):
        nom = noeud.get("nom", noeud.get("node", "?"))
        prec = noeuds_prec.get(nom, {})
        cor_ok, cor_ok_prec = noeud.get("corosync_ok", True), prec.get("corosync_ok", True)
        if not cor_ok and cor_ok_prec:
            anomalies.append({"niveau": "CRITIQUE", "cible": nom, "metric": "corosync_ok", "message": f"Node {nom} -- Corosync daemon down (cluster communication at risk)"})

    # NIVEAU 3 - Corosync quorum -- transition OU ré-escalade si le quorum
    # reste perdu (aussi critique que possible, mérite un rappel périodique)
    quorum_avant = etat_prec.get("cluster", {}).get("corosync_quorum_ok", True)
    quorum_maintenant = etat.get("cluster", {}).get("corosync_quorum_ok", True)
    cle_quorum = "cluster:quorum"
    if (not quorum_maintenant and quorum_avant) or (not quorum_maintenant and _doit_reescalader(cle_quorum)):
        anomalies.append({
            "niveau":  "CRITIQUE",
            "cible":   "cluster",
            "metric":  "corosync_quorum_ok",
            "message": "CLUSTER QUORUM LOST -- All VMs at risk of automatic shutdown",
        })
        _marquer_alerte(cle_quorum)

    # ── NON couvert délibérément : bande passante réseau nœud (net_in_mbps
    # / net_out_mbps). Un seuil fixe demanderait de connaître la capacité
    # réelle du lien (1Gbps ici, vu dans les logs -- mais coder cette
    # hypothèse en dur casserait sur un futur changement de matériel, contre
    # le principe déjà établi sur ce projet). Ces deux métriques sont déjà
    # dans le vecteur du LSTM (voir ml_analyser.py) -- une saturation
    # RELATIVE au comportement normal du cluster est donc déjà couverte par
    # ce chemin-là, sans avoir besoin de deviner une capacité absolue ici.
    #
    # ── NON couvert délibérément : IOPS lecture/écriture (Read/Write IOPS).
    # Raisonnement différent de la bande passante : un débit d'IOPS élevé
    # n'est PAS en soi un signe de dégradation -- un système peut légitimement
    # soutenir des milliers d'IOPS sans aucun problème si le stockage le
    # permet. Le vrai signal de dégradation, quand le stockage sature
    # vraiment, c'est la LATENCE et l'I/O WAIT -- tous les deux déjà
    # vérifiés ci-dessus. Ajouter un seuil sur le nombre brut d'IOPS
    # ferait doublon avec un signal moins fiable (dépend du type de
    # disque/stockage) sans rien capter que latence+iowait ne captent déjà
    # mieux.
    #
    # ── VM Read/Write Throughput et VM Net In/Out : même raisonnement que
    # la bande passante nœud ci-dessus (voir aussi la note dans la boucle
    # VM plus haut) -- pas de référence de capacité fiable, et ces deux-là
    # ne sont même pas dans le vecteur LSTM (qui n'agrège qu'au niveau
    # cluster). Trou réel, pas comblé ici par souci de cohérence -- la
    # bonne solution serait une comparaison à une base glissante récente
    # par VM, pas un seuil absolu deviné.

    return anomalies