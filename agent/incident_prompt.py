"""
incident_prompt.py — Classification des anomalies et construction du prompt
envoye au LLM pour generer l'analyse d'incident.

← REFONTE MAJEURE (format de sortie) : le LLM ne produit plus un texte libre
(---INCIDENT---/---RECOMMENDATION---, parsé ensuite par regex côté
frontend) mais un OBJET JSON structuré -- même technique que
rules_engine.py, déjà prouvée fiable avec ce modèle (llama-3.1-8b-instant
via Groq). Deux problèmes concrets que ça corrige :

1. La "Standard Playbook" affichée côté frontend (PageRecommendations.jsx)
   était un texte figé écrit une fois pour chaque type de problème --
   jamais générée par le LLM, jamais liée aux vraies métriques ni à la
   vraie recherche web. Le nouveau format demande explicitement une liste
   d'étapes (immediate/short_term/long_term), remplie par le LLM lui-même
   à partir du contexte réel -- plus besoin de ce filet de secours statique.

2. Le nœud/VMID cible d'une action était extrait par regex depuis le texte
   libre généré par le LLM (fragile : dépend de la formulation exacte).
   Le nouveau format donne ces champs directement, structurés, remplis par
   le LLM à partir de ce qu'il sait déjà avec certitude (CLUSTER STATE),
   pas à deviner après coup depuis une phrase.

← RETRAIT de l'ancienne règle stricte #7 ("Use ONLY the command from
PROBLEM TYPE GUIDANCE — no other command") -- elle empêchait
structurellement le LLM d'utiliser la vraie doc Proxmox récupérée par
Tavily (doc_context, déjà injectée, déjà fonctionnelle), même si "IMMEDIATE
COMMAND: ps aux..." (jamais reconsidéré) contredisait ce que la doc
recommandait pour CE cas précis. Remplacée par un MENU d'actions sûres
(_construire_menu_actions(), générée depuis action_executor.ACTION_META --
jamais recopiée à la main, toujours synchronisée avec les actions
réellement exécutables) que le LLM peut choisir, combiner et ordonner
librement selon les métriques et la recherche disponibles.

← AJOUT (validation post-génération) : parser_reponse_llm() extrait le
JSON ; _valider_step() vérifie ensuite, PAR ÉTAPE, que action_id existe
vraiment dans ACTION_META et que tous ses paramètres requis sont remplis
-- sinon action_id est mis à None (l'étape reste affichée comme texte
informatif, mais aucun bouton "Accepter & Exécuter" qui échouerait
silencieusement à l'exécution). Le LLM propose, le code valide -- jamais
une confiance aveugle dans ce qu'il a rempli.

Extrait de surveillance.py (devenu trop long, ~700 lignes, plusieurs
responsabilites melangees) -- avec UNE correction reelle au passage :
classifier_anomalies() priorise desormais une panne de service complete
(ex: "PostgreSQL is DOWN") au-dessus du nombre d'anomalies des autres
categories. Avant ce correctif, 2 alertes RAM pouvaient faire passer une
base de donnees injoignable au second plan dans l'analyse du LLM, meme si
le titre de la notification parlait de la base de donnees -- d'ou des
emails ou le titre et le contenu ne correspondaient pas.
"""
import json
import re

try:
    from web_search import rechercher_doc_proxmox, get_doc_url
    WEB_SEARCH_OK = True
except ImportError:
    WEB_SEARCH_OK = False
    def get_doc_url(type_probleme: str) -> str:
        return "https://pve.proxmox.com/wiki/Main_Page"

try:
    from action_executor import ACTION_META
    ACTIONS_OK = True
except ImportError:
    ACTIONS_OK = False
    ACTION_META = {}


# ── Commandes de diagnostic par service ─────────────────────────────────────
SERVICE_COMMANDS = {
    "postgresql": (
        "systemctl status postgresql ; sudo -u postgres psql -c 'SELECT 1;' "
        "-- target: service responding, database reachable"
    ),
    "default": (
        "systemctl status <service_name> ; journalctl -u <service_name> --no-pager -n 30 "
        "-- target: service active (running), no repeated errors in recent logs"
    ),
}


from collections import defaultdict

# ← AJOUT : associe une métrique canonique (champ "metric" posé par
# anomaly_detector.py) au bucket de classement le plus pertinent, quand
# plusieurs métriques partagent la même famille de cause (iowait +
# latence + processus bloqués = tous des symptômes du même goulot
# d'I/O storage, ils partagent donc le même garde-fou et la même
# recherche documentaire). Une métrique absente de ce mapping crée
# simplement son propre bucket à la volée (voir classifier_anomalies) --
# jamais un repli silencieux vers une mauvaise catégorie.
_BUCKET_PAR_METRIQUE = {
    "cpu_pct": "cpu", "ram_pct": "ram", "disk_pct": "disk", "swap_pct": "swap",
    "cpu_iowait_pct": "iowait", "disk_read_latency_ms": "iowait",
    "disk_write_latency_ms": "iowait", "procs_blocked": "iowait",
    "corosync_ok": "quorum", "corosync_quorum_ok": "quorum",
    "net_errors_in": "network", "net_errors_out": "network",
    "net_drop_in": "network", "net_drop_out": "network",
    "fd_used_pct": "fd", "cpu_steal_pct": "cpu", "load_avg_1m": "cpu",
    "zfs_arc_hit_rate": "disk",
    "smart_uncorrectable": "disk", "smart_reallocated_sectors": "disk", "smart_pending_sectors": "disk",
}


def _bucket_depuis_metric(metric: str) -> str:
    """Déduit un bucket de classement à partir du champ "metric" canonique
    d'une anomalie -- voir _BUCKET_PAR_METRIQUE pour les regroupements
    déjà établis. Toute métrique absente de ce mapping (une métrique
    totalement nouvelle, jamais vue par ce code) utilise son propre
    premier segment de nom comme bucket -- crée dynamiquement une
    nouvelle catégorie plutôt que de retomber sur "ram" par défaut."""
    if metric in _BUCKET_PAR_METRIQUE:
        return _BUCKET_PAR_METRIQUE[metric]
    if "temp" in metric:
        return "temp"
    return metric.split("_")[0]


def classifier_anomalies(anomalies: list) -> tuple:
    """
    ← REFONTE (classement par métrique canonique, plus par mots-clés
    devinés) : avant, chaque anomalie était classée en cherchant des
    mots-clés DANS SON TEXTE LIBRE ("ram" in message.lower()...), avec un
    repli silencieux vers "ram" si rien ne correspondait -- confirmé par
    le code lui-même avoir déjà touché "steal"/"blocked"/"corosync" avant
    qu'un bucket dédié ne soit ajouté à la main pour chacun. Utilise
    maintenant en priorité le champ "metric" canonique posé par
    anomaly_detector.py (le nom réel du champ mesuré, ex: "cpu_steal_pct")
    -- une métrique totalement nouvelle, jamais vue par ce code, obtient
    son propre bucket automatiquement (voir _bucket_depuis_metric), sans
    intervention manuelle. Le repli mots-clés original est CONSERVÉ mais
    seulement pour les anomalies SANS champ "metric" (alertes Proxmox API
    brutes, qui n'ont pas de métrique numérique unique associée -- voir
    anomaly_detector.py).
    """
    types = defaultdict(list)
    for a in anomalies:
        t = a.get("type", "")
        if t == "ai_score":
            types["ai"].append(a)
            continue
        if t == "service_down":
            types["service"].append(a)
            continue
        if t == "vm_down":
            types["vm"].append(a)
            continue

        metric = a.get("metric")
        if metric:
            types[_bucket_depuis_metric(metric)].append(a)
            continue

        # ← CONSERVÉ : repli mots-clés, uniquement pour les anomalies sans
        # champ "metric" (alertes Proxmox API brutes -- voir
        # anomaly_detector.py, qui n'en pose pas pour celles-ci).
        msg   = a.get("message", "")
        msg_l = msg.lower()
        cible = a.get("cible", "").lower()
        if msg.startswith("VM "):            types["vm"].append(a)
        elif "quorum" in msg_l or "corosync" in msg_l: types["quorum"].append(a)
        elif "temp" in msg_l:                  types["temp"].append(a)
        elif "iowait" in msg_l or "i/o wait" in msg_l or "latency" in msg_l or "blocked processes" in msg_l: types["iowait"].append(a)
        elif "swap" in msg_l:                  types["swap"].append(a)
        elif "disk" in msg_l:                  types["disk"].append(a)
        elif "ram" in msg_l or "memory" in msg_l: types["ram"].append(a)
        elif "cpu" in msg_l or "load average" in msg_l: types["cpu"].append(a)
        elif "vm" in msg_l or "vm" in cible:   types["vm"].append(a)
        elif "net" in msg_l:                   types["network"].append(a)
        elif "file descriptor" in msg_l:       types["fd"].append(a)
        else:
            if "ram" in cible:    types["ram"].append(a)
            elif "disk" in cible: types["disk"].append(a)
            elif "cpu" in cible:  types["cpu"].append(a)
            else:                 types["ram"].append(a)

    if types["service"]:
        return types, "service"

    dominant = max((k for k in types if k != "ai"), key=lambda k: len(types[k]), default="ram")
    if not types[dominant]:
        dominant = "ram"
    return types, dominant


def calculer_seuils_franchis(etat: dict) -> str:
    lignes = []
    for n in etat.get("noeuds", []):
        nom  = n.get("nom", "?")
        ram  = n.get("ram_pct", 0)
        cpu  = n.get("cpu_pct", 0)
        disk = n.get("disk_pct", 0)
        swap = n.get("swap_pct", 0)

        if ram >= 85:
            lignes.append(f"  {nom} RAM: {ram:.1f}% — CRITICAL threshold breached (>=85%) — target <70%")
        elif ram >= 75:
            lignes.append(f"  {nom} RAM: {ram:.1f}% — WARNING threshold breached (>=75%) — NOT yet critical (<85%) — target <70%")

        if cpu >= 90:
            lignes.append(f"  {nom} CPU: {cpu:.1f}% — CRITICAL threshold breached (>=90%) — target <75%")
        elif cpu >= 80:
            lignes.append(f"  {nom} CPU: {cpu:.1f}% — WARNING threshold breached (>=80%) — NOT yet critical (<90%) — target <75%")

        if disk >= 90:
            lignes.append(f"  {nom} DISK: {disk:.1f}% — CRITICAL threshold breached (>=90%) — target <75%")
        elif disk >= 80:
            lignes.append(f"  {nom} DISK: {disk:.1f}% — WARNING threshold breached (>=80%) — NOT yet critical (<90%) — target <75%")

        if swap >= 80:
            lignes.append(f"  {nom} SWAP: {swap:.1f}% — CRITICAL threshold breached (>=80%) — target 0%")
        elif swap >= 50:
            lignes.append(f"  {nom} SWAP: {swap:.1f}% — WARNING threshold breached (>=50%) — target 0%")

        iowait = n.get("cpu_iowait_pct", 0)
        if iowait >= 30:
            lignes.append(f"  {nom} IOWAIT: {iowait:.1f}% — CRITICAL threshold breached (>=30%) — target <5%")
        elif iowait >= 15:
            lignes.append(f"  {nom} IOWAIT: {iowait:.1f}% — WARNING threshold breached (>=15%) — NOT yet critical (<30%) — target <5%")

        steal = n.get("cpu_steal_pct", 0)
        if steal >= 20:
            lignes.append(f"  {nom} CPU STEAL: {steal:.1f}% — CRITICAL threshold breached (>=20%) — host-level contention (VMware Workstation), not a node/VM issue — target <2%")
        elif steal >= 10:
            lignes.append(f"  {nom} CPU STEAL: {steal:.1f}% — WARNING threshold breached (>=10%) — NOT yet critical (<20%) — host-level contention (VMware Workstation), not a node/VM issue — target <2%")

        read_lat  = n.get("disk_read_latency_ms", 0)
        write_lat = n.get("disk_write_latency_ms", 0)
        if read_lat >= 50 or write_lat >= 50:
            lignes.append(f"  {nom} DISK LATENCY: read={read_lat:.1f}ms write={write_lat:.1f}ms — CRITICAL (>=50ms) — target <10ms")
        elif read_lat >= 20 or write_lat >= 20:
            lignes.append(f"  {nom} DISK LATENCY: read={read_lat:.1f}ms write={write_lat:.1f}ms — WARNING (>=20ms) — target <10ms")

        net_err = n.get("net_errors_in", 0) + n.get("net_errors_out", 0)
        net_drop = n.get("net_drop_in", 0) + n.get("net_drop_out", 0)
        if net_err > 10:
            lignes.append(f"  {nom} NET ERRORS: {net_err:.0f}/s — CRITICAL (>10/s) — target 0")
        elif net_err > 0:
            lignes.append(f"  {nom} NET ERRORS: {net_err:.0f}/s — WARNING (>0) — target 0")
        if net_drop > 0:
            lignes.append(f"  {nom} NET DROPS: {net_drop:.0f}/s — WARNING — target 0")

        temp = n.get("cpu_temp_max_c", 0)
        if temp >= 85:
            lignes.append(f"  {nom} CPU TEMP: {temp:.0f}°C — CRITICAL threshold breached (>=85°C) — target <70°C")
        elif temp >= 75:
            lignes.append(f"  {nom} CPU TEMP: {temp:.0f}°C — WARNING threshold breached (>=75°C) — NOT yet critical (<85°C) — target <70°C")

        if not n.get("smart_ok", True):
            reallocated = n.get("smart_reallocated_sectors", 0)
            uncorr      = n.get("smart_uncorrectable", 0)
            lignes.append(f"  {nom} SMART: FAIL — reallocated sectors={reallocated} uncorrectable={uncorr} — REPLACE DISK IMMEDIATELY")

        if n.get("zfs_available"):
            zfs_hit = n.get("zfs_arc_hit_rate", 0)
            if zfs_hit < 70:
                lignes.append(f"  {nom} ZFS ARC: hit rate={zfs_hit:.1f}% — CRITICAL (<70%) — increase ARC size — target >90%")
            elif zfs_hit < 85:
                lignes.append(f"  {nom} ZFS ARC: hit rate={zfs_hit:.1f}% — WARNING (<85%) — target >90%")

        if not n.get("corosync_ok", True):
            quorum = "OK" if n.get("corosync_quorum_ok", True) else "LOST"
            lignes.append(f"  {nom} COROSYNC: DEGRADED — quorum={quorum} — CRITICAL: cluster may stop VMs")

    return "\n".join(lignes) if lignes else "  All metrics within normal range"


def _construire_menu_actions() -> str:
    if not ACTIONS_OK or not ACTION_META:
        return "  (no pre-approved one-click actions available in this environment)"
    lignes = []
    for action_id, meta in ACTION_META.items():
        params = ", ".join(meta.get("params", []))
        avertissement = f" WARNING: {meta['warning']}" if meta.get("warning") else ""
        lignes.append(
            f'  - action_id="{action_id}" ({meta["label"]}, risk={meta["risk"]}): '
            f'{meta["description"]}. Required action_params keys: {{{params}}}.{avertissement}'
        )
    return "\n".join(lignes)


GARDE_FOUS = {
    "service": "No action_id applies to restarting an arbitrary service (not yet in the executable catalog) -- describe diagnostic/fix steps as informational (action_id=null), using the diagnostic command context provided below.",
    "ram":     "FORBIDDEN: never suggest reducing VM RAM, maxmem, or vCPU in production -- this crashes running applications. Always set the warning field to state this explicitly when RAM is the dominant problem. TARGET: host RAM < 70%.",
    "disk":    "clean_logs is safe and reversible for cache/old logs. Deleting specific backups needs a human decision (real path, real size) -- describe as an informational step (action_id=null) with the real command, not a one-click action. TARGET: disk < 75%.",
    "cpu":     "If CPU STEAL is present in EXACT THRESHOLD STATUS, this is a host-level (VMware Workstation) issue -- do not attribute it to a specific VM or recommend migrating/limiting a VM for that specific cause. set_cpu_limit only for non-critical/dev VMs. TARGET: CPU < 75%.",
    "swap":    "Swap usage on a hypervisor node means RAM is genuinely exhausted -- treat the underlying cause as a RAM problem (see ram guidance) even though the anomaly is labeled swap. TARGET: swap 0%.",
    "iowait":  "No action_id applies directly -- storage bottleneck diagnosis needs a human to read iostat output first. Describe as informational steps. TARGET: iowait < 5%, latency < 10ms.",
    "temp":    "No action_id applies -- thermal issues need physical inspection. Describe as informational steps (sensors, dmesg throttle check, airflow). TARGET: < 70°C.",
    "vm":      "No action_id applies to restarting a stopped VM directly (not yet in the executable catalog) -- describe as an informational step with the real qm command. Investigate the stop cause (OOM, disk full) before restarting.",
    "quorum":  "No action_id applies -- quorum/Corosync issues need careful manual diagnosis (pvecm status, network check) before any corrective action. Never suggest 'pvecm expected 1' unless the anomaly explicitly confirms the other node is truly offline.",
    "network": "No action_id applies -- network hardware/driver issues need manual diagnosis (ip -s link, ethtool). Describe as informational steps.",
    "ai":      "This is a statistical anomaly (LSTM/Isolation Forest), not a specific metric breach -- correlate with EXACT THRESHOLD STATUS and CLUSTER STATE to explain what's actually happening, don't invent a cause not supported by the data above.",
    "fd":      "No action_id applies -- file descriptor exhaustion usually means a service is leaking open files/sockets. Investigate with lsof before any fix; restarting the offending service is a temporary workaround, not a root cause fix. TARGET: fd usage < 70%.",
}


# ══════════════════════════════════════════════════════════════════════════════
# Recherche documentaire par service — cache en mémoire
# ══════════════════════════════════════════════════════════════════════════════
# ← AJOUT : avant, un service détecté (Redis, MySQL...) n'avait le choix
# qu'entre une mesure générique (RAM/CPU/disk via process-exporter, voir
# vm_app_monitor.py) et, à défaut de mesure, une phrase statique écrite une
# fois pour toutes dans EXIGENCES_SERVICES ci-dessus -- exactement le même
# anti-pattern que l'ancien PROXMOX_DOCS_CONTEXT que rules_engine.py a déjà
# corrigé. rechercher_doc_service() vit directement dans web_search.py, à
# côté de rechercher_doc_proxmox() -- même fichier, mêmes conventions
# (cache, style d'appel Tavily), cherche la doc OFFICIELLE du service
# lui-même (redis.io, postgresql.org...), jamais Proxmox.
#
# Cache en mémoire côté web_search.py, indéfiniment (pas de TTL) -- la doc
# officielle d'un service change rarement d'un cycle à l'autre, et un
# service qui redéclenche une alerte (ré-escalade toutes les 30min, voir
# anomaly_detector.py) ne doit pas relancer une recherche identique à
# chaque fois. Générique par construction : n'importe quel service détecté
# (n'importe lequel dans CATALOGUE_SERVICES côté vm_app_monitor.py)
# obtient ce traitement automatiquement, aucune ligne à ajouter ici pour
# un service de plus.
_cache_doc_services: dict = {}


def _obtenir_doc_service(nom_service: str) -> str | None:
    """Retourne la doc officielle mise en cache pour ce service, la
    recherche si absente. Ne lève jamais d'exception -- None si la
    recherche échoue ou si web_search.py n'expose pas encore la fonction
    (ex: ancienne version du fichier pas encore mise à jour)."""
    if nom_service in _cache_doc_services:
        return _cache_doc_services[nom_service]

    doc = None
    try:
        from web_search import rechercher_doc_service
        doc = rechercher_doc_service(nom_service)
    except ImportError:
        pass  # fonction pas encore ajoutée côté web_search.py -- silencieux
    except Exception as e:
        print(f"[WebSearch] Erreur recherche service '{nom_service}': {e}")

    _cache_doc_services[nom_service] = doc
    return doc


def construire_prompt_specifique(anomalies: list, etat: dict, lstm: dict) -> str:
    anomalies_str        = "\n".join([f"- [{a['niveau']}] {a['message']}" for a in anomalies])
    types_classified, dominant = classifier_anomalies(anomalies)
    seuils_franchis       = calculer_seuils_franchis(etat)
    noeuds_ctx = ""
    for n in etat.get("noeuds", []):
        ram_free  = round(n.get("ram_total_gb", 0) - n.get("ram_used_gb", 0), 1)
        disk_free = round(n.get("disk_total_gb", 0) - n.get("disk_used_gb", 0), 1)
        noeuds_ctx += (
            f"  {n.get('nom','?')}: CPU={n.get('cpu_pct',0):.1f}% (cores={n.get('cpu_cores',0)}) "
            f"RAM={n.get('ram_pct',0):.1f}% ({n.get('ram_used_gb',0):.1f}/{n.get('ram_total_gb',0):.1f}GB FREE={ram_free}GB) "
            f"DISK={n.get('disk_pct',0):.1f}% ({n.get('disk_used_gb',0):.1f}/{n.get('disk_total_gb',0):.1f}GB FREE={disk_free}GB) "
            f"STATUS={n.get('statut','?')}\n"
        )
        l2_parts = []
        if n.get("swap_pct", 0) > 0:
            l2_parts.append(f"SWAP={n.get('swap_pct',0):.1f}%")
        if n.get("cpu_iowait_pct", 0) > 0:
            l2_parts.append(f"IOWAIT={n.get('cpu_iowait_pct',0):.1f}%")
        if n.get("cpu_steal_pct", 0) > 0:
            l2_parts.append(f"STEAL={n.get('cpu_steal_pct',0):.1f}%")
        if n.get("disk_read_latency_ms", 0) > 0:
            l2_parts.append(f"READ_LAT={n.get('disk_read_latency_ms',0):.1f}ms WRITE_LAT={n.get('disk_write_latency_ms',0):.1f}ms")
        if n.get("net_errors_in", 0) > 0 or n.get("net_errors_out", 0) > 0:
            l2_parts.append(f"NET_ERRORS={n.get('net_errors_in',0)+n.get('net_errors_out',0):.0f}/s")
        if n.get("load_avg_1m", 0) > 0:
            l2_parts.append(f"LOAD={n.get('load_avg_1m',0):.2f}")
        if l2_parts:
            noeuds_ctx += f"    L2: {' | '.join(l2_parts)}\n"
        l3_parts = []
        if n.get("cpu_temp_max_c", 0) > 0:
            l3_parts.append(f"TEMP={n.get('cpu_temp_max_c',0):.0f}°C")
        if not n.get("smart_ok", True):
            l3_parts.append(f"SMART=FAIL(reallocated={n.get('smart_reallocated_sectors',0)})")
        if n.get("zfs_available"):
            l3_parts.append(f"ZFS_ARC={n.get('zfs_arc_hit_rate',0):.0f}%hit")
        if not n.get("corosync_ok", True):
            l3_parts.append(f"COROSYNC=DEGRADED(quorum={'OK' if n.get('corosync_quorum_ok',True) else 'LOST'})")
        if l3_parts:
            noeuds_ctx += f"    L3: {' | '.join(l3_parts)}\n"

    EXIGENCES_SERVICES = {
        "PostgreSQL":    "typically wants >=1GB RAM for comfortable operation, more under real query load",
        "Docker":        "overhead varies with running containers; each container adds its own RAM/CPU footprint on top",
        "Nginx":         "lightweight, usually <100MB RAM even under moderate traffic",
        "Apache":        "moderate footprint, scales with worker processes/connections",
        "Redis":         "RAM-bound by design — dataset size determines requirement directly",
        "MySQL":         "typically wants >=512MB RAM minimum, more for InnoDB buffer pool efficiency",
        "MariaDB":       "typically wants >=512MB RAM minimum, more for InnoDB buffer pool efficiency",
        "MongoDB":       "typically wants >=1GB RAM, WiredTiger cache scales with available memory",
        "Elasticsearch": "JVM-based, typically wants >=2GB RAM heap minimum — heavy for a small VM",
        "RabbitMQ":      "moderate footprint, ~256-512MB RAM typical for light workloads",
        "Grafana":       "lightweight, usually <200MB RAM",
        "MinIO":         "moderate footprint, scales with concurrent object operations",
        "Prometheus":    "RAM scales with number of scraped series and retention — can grow significantly over time",
        "Alertmanager":  "lightweight, usually <100MB RAM",
    }
    vms_ctx = ""
    for v in etat.get("vms", []):
        services = v.get("services_detectes", [])
        metriques_services = v.get("metriques_services", {})
        services_str = f" | services: {', '.join(services)}" if services else ""
        vms_ctx += (
            f"  VM{v.get('vmid','?')} {v.get('nom','?')} on {v.get('noeud','?')}: "
            f"status={v.get('statut','?')} CPU={v.get('cpu_pct',0):.1f}% RAM={v.get('ram_pct',0):.1f}% "
            f"maxmem={v.get('maxmem_gb',0):.1f}GB{services_str}\n"
        )
        for s in services:
            m = metriques_services.get(s)
            if m:
                ligne = (
                    f"    {s} ACTUAL measured usage: RAM={m.get('ram_mb',0):.0f}MB "
                    f"CPU={m.get('cpu_pct',0):.1f}%"
                )
                if m.get("cpu_pct_vm") is not None:
                    ligne += f" ({m['cpu_pct_vm']:.1f}% of this VM's total vCPU capacity)"
                ligne += (
                    f" disk_read={m.get('disk_read_mbps',0):.2f}MB/s "
                    f"disk_write={m.get('disk_write_mbps',0):.2f}MB/s processes={m.get('num_procs',0)}\n"
                )
                vms_ctx += ligne
                cles_generiques = {"ram_mb", "cpu_pct", "cpu_pct_vm",
                                    "disk_read_mbps", "disk_write_mbps", "num_procs"}
                extras = {k: v for k, v in m.items() if k not in cles_generiques and v is not None}
                if extras:
                    extras_str = " | ".join(f"{k}={v}" for k, v in extras.items())
                    vms_ctx += f"    {s} internal metrics: {extras_str}\n"
            elif s in EXIGENCES_SERVICES:
                vms_ctx += f"    {s} sizing guidance (no live measurement yet): {EXIGENCES_SERVICES[s]}\n"
    vms_ctx = vms_ctx or "  No VM data\n"

    hote_ctx = ""
    hote = etat.get("hote_physique") if isinstance(etat.get("hote_physique"), dict) else {}
    if hote.get("disponible"):
        alloc = hote.get("vmware_allocation", {})
        hote_ctx = (
            f"  Physical host (Windows PC running VMware Workstation): "
            f"RAM {hote.get('ram_used_gb',0):.1f}/{hote.get('ram_total_gb',0):.1f}GB "
            f"({hote.get('ram_pct_used',0):.0f}% used, {hote.get('ram_available_gb',0):.1f}GB free) | "
            f"CPU {hote.get('cpu_pct_used',0):.0f}% used ({hote.get('cpu_cores',0)} cores) | "
            f"Disk {hote.get('disk_free_gb',0):.1f}GB free of {hote.get('disk_total_gb',0):.1f}GB\n"
            f"  Headroom currently allocatable to VMware beyond Windows' own reserve: "
            f"{alloc.get('total_allouable_ram_gb',0):.1f}GB RAM, "
            f"{alloc.get('total_allouable_cores',0)} cores, "
            f"{alloc.get('total_allouable_disk_gb',0):.1f}GB disk\n"
        )
    else:
        hote_ctx = "  Physical host metrics not available this cycle\n"

    hyp_metriques = etat.get("hyperviseur_metriques") if isinstance(etat.get("hyperviseur_metriques"), dict) else {}
    if hyp_metriques.get("disponible"):
        ram_alloc  = hyp_metriques.get("vms_ram_allouee_gb") or 0.0
        disk_alloc = hyp_metriques.get("vms_disk_allouee_gb") or 0.0
        hote_ctx += (
            f"  Virtualization layer itself ({hyp_metriques.get('vm_count',0)} VM(s) running under "
            f"{etat.get('hyperviseur',{}).get('produit','the hypervisor')}): "
            f"CPU {hyp_metriques.get('vms_cpu_pct_hote',0):.1f}% of total host CPU capacity "
            f"({hyp_metriques.get('vms_cpu_pct',0):.1f}% raw, summed per-core across "
            f"{hyp_metriques.get('vm_count',0)} VM process(es), not normalized) | "
            f"RAM allocated to VMs: {ram_alloc:.1f}GB | Disk allocated to VMs: {disk_alloc:.1f}GB | "
            f"{hyp_metriques.get('overhead_ram_mb',0):.0f}MB hypervisor software overhead (UI/services, "
            f"separate from the VMs' allocated RAM above)\n"
        )
        vmdk_reel = hyp_metriques.get("vmdk_reel_gb")
        if vmdk_reel is not None:
            ecart = disk_alloc - vmdk_reel
            if ecart > 0.5:
                hote_ctx += (
                    f"  Actual .vmdk size on physical disk (measured directly, not an estimate): "
                    f"{vmdk_reel:.1f}GB (vs {disk_alloc:.1f}GB allocated -- thin provisioning is saving "
                    f"~{ecart:.1f}GB right now)\n"
                )
            elif ecart < -0.5:
                hote_ctx += (
                    f"  Actual .vmdk size on physical disk (measured directly, not an estimate): "
                    f"{vmdk_reel:.1f}GB -- EXCEEDS the {disk_alloc:.1f}GB allocated by ~{-ecart:.1f}GB. "
                    f"This is a known, benign VMware behavior: thin-provisioned virtual disks grow as data is "
                    f"written but do not automatically shrink when that data is later deleted inside the guest. "
                    f"If reclaiming physical disk space matters, this can be recommended: VMware Workstation → "
                    f"shut down the VM → VM Settings → Hard Disk → Utilities → Compact, or `fstrim` run inside "
                    f"the guest if its virtual disk supports TRIM/discard. Not a data integrity issue.\n"
                )
            else:
                hote_ctx += (
                    f"  Actual .vmdk size on physical disk (measured directly, not an estimate): "
                    f"{vmdk_reel:.1f}GB -- close to the {disk_alloc:.1f}GB allocated, no meaningful thin-"
                    f"provisioning savings currently.\n"
                )
        if hyp_metriques.get("ram_non_mesurable"):
            hote_ctx += (
                "  NOTE: the RAM/Disk figures above are ALLOCATED capacity (what the hypervisor has "
                "reserved for these VMs, from their own reported total), not a LIVE measurement of actual "
                "host-side usage -- that live figure cannot be reliably read from the host process for this "
                "hypervisor type. Do NOT assume actual usage is lower than the allocated figure just because "
                "no live number is given. Rely on PHYSICAL HOST total RAM usage above as the source of truth "
                "for how much RAM is actually in use on this machine right now.\n"
            )
        marge = hyp_metriques.get("marge_reelle_ram_gb")
        if marge is not None:
            if marge < 0:
                hote_ctx += (
                    f"  REAL RAM HEADROOM: {marge:.1f}GB -- NEGATIVE. The Windows reserve target plus what's "
                    f"already allocated to the VMs already EXCEEDS total host RAM. This directly explains "
                    f"chronic host RAM pressure -- there is structurally NO safe margin left for growth "
                    f"without either freeing RAM elsewhere, adding physical RAM to the host, or lowering the "
                    f"Windows reserve target. Treat any recommendation to add a new VM or grow an existing "
                    f"one's RAM as infeasible right now without addressing this first.\n"
                )
            else:
                hote_ctx += (
                    f"  Real RAM headroom (host total minus Windows reserve minus RAM already allocated to "
                    f"VMs): {marge:.1f}GB\n"
                )

    commande_service = SERVICE_COMMANDS["default"]
    # ← AJOUT : initialisé à None même hors branche "service" -- défensif,
    # pour que le bloc de recherche service plus bas (if dominant=="service"
    # and nom_service:) ne dépende jamais implicitement de l'ordre
    # d'exécution ou d'une garantie de classifier_anomalies() qui pourrait
    # changer un jour.
    nom_service = None
    if dominant == "service" and types_classified.get("service"):
        cible = types_classified["service"][0].get("cible", "")
        nom_service = cible.split("/")[-1].strip().lower() if cible else ""
        commande_service = SERVICE_COMMANDS.get(nom_service, SERVICE_COMMANDS["default"])

    garde_fou = GARDE_FOUS.get(dominant, GARDE_FOUS["ram"])
    if dominant == "service":
        garde_fou += f"\nDiagnostic command for this exact service: {commande_service}"

    doc_context = ""
    if WEB_SEARCH_OK:
        try:
            doc_raw = rechercher_doc_proxmox(dominant)
            if doc_raw:
                doc_context = f"""
OFFICIAL PROXMOX DOCUMENTATION (fetched in real-time from pve.proxmox.com / forum.proxmox.com):
{doc_raw[:1000]}

Base your Causes and Steps on this documentation where it applies to the current situation --
prefer it over generic assumptions when they differ.
"""
                print(f"[WebSearch] Doc Proxmox chargée pour '{dominant}' ({len(doc_raw)} chars)")
        except Exception as e:
            print(f"[WebSearch] Erreur: {e}")

    # ← AJOUT : documentation du SERVICE lui-même (Redis, PostgreSQL...),
    # pas seulement Proxmox -- voir _obtenir_doc_service() plus haut pour
    # le contrat exact. Complémentaire au bloc Proxmox ci-dessus, jamais un
    # remplacement : l'un situe l'incident dans l'écosystème Proxmox,
    # l'autre informe sur ce que "RAM élevée" signifie concrètement POUR
    # CE service précis, avec ses propres seuils/bonnes pratiques.
    if dominant == "service" and nom_service:
        doc_service = _obtenir_doc_service(nom_service)
        if doc_service:
            doc_context += f"""
OFFICIAL {nom_service.upper()} DOCUMENTATION (fetched in real-time):
{doc_service[:1000]}

This is the service's OWN documentation, not Proxmox's -- prefer it for anything specific to
how {nom_service} itself reports, manages, or recommends handling this exact metric.
"""
            print(f"[WebSearch] Doc {nom_service} chargée ({len(doc_service)} chars)")

    hyperviseur_ctx = ""
    try:
        hyp = etat.get("hyperviseur", {})
        if hyp.get("type") and hyp["type"] != "unknown":
            hyperviseur_ctx = (
                f"Infrastructure type: Proxmox running on {hyp['type'].upper()} "
                f"({hyp['produit']})\n"
                f"RAM recommendation: {hyp.get('recommandation_ram', '')}\n"
                f"CPU recommendation: {hyp.get('recommandation_cpu', '')}\n"
                f"Disk recommendation: {hyp.get('recommandation_disk', '')}"
            )
    except Exception:
        pass

    menu_actions = _construire_menu_actions()

    prompt = f"""You are a senior Proxmox VE infrastructure engineer. Analyze this incident and generate a
precise, tailored recommendation -- grounded in the exact metrics and documentation below, not a
generic template. Respond ONLY with a valid JSON object, no markdown fences, no text before or after.

CLUSTER STATE:
{noeuds_ctx}
VMs:
{vms_ctx}
ANOMALIES:
{anomalies_str}
AI Score: {lstm['score']:.4f} / threshold {lstm['seuil']:.4f}

EXACT THRESHOLD STATUS — COPY THESE VALUES VERBATIM, NEVER CHANGE A NUMBER:
{seuils_franchis}

HYPERVISOR CONTEXT:
{hyperviseur_ctx}

PHYSICAL HOST (underlying Windows PC — determines real headroom for hardware-dependent recommendations):
{hote_ctx}

SAFETY CONSTRAINTS AND TARGET FOR THIS PROBLEM TYPE ({dominant}):
{garde_fou}

PRE-APPROVED ONE-CLICK ACTIONS (use action_id + action_params ONLY from this exact list when a step
matches one of these -- otherwise action_id must be null, the step stays informational):
{menu_actions}
{doc_context}

RULES:
1. pve1/pve2 are HYPERVISOR NODES — NEVER use pct commands on them
2. Copy threshold values verbatim from EXACT THRESHOLD STATUS above — never round up or invent
3. severity must be "CRITICAL" if a CRITICAL threshold was breached, "HIGH" if only WARNING was breached
4. target_node / target_vmid at the top level identify the PRIMARY resource this incident is about (from CLUSTER STATE, not guessed from prose)
5. "ACTUAL measured usage" lines are REAL, live measurements for that specific service -- use them directly. If a VM's RAM% is high but a detected service's actual usage is low, that service is NOT the cause -- say so explicitly in causes instead of blaming it by default
6. If CPU STEAL is present in EXACT THRESHOLD STATUS, treat it as a host-level (VMware Workstation) issue, never as a reason to resize/migrate a VM
7. Check PHYSICAL HOST headroom before proposing hardware-dependent steps: if allocatable RAM/cores is already near zero or negative, say so explicitly in causes and prioritize non-hardware steps
8. Every step's action_id must come from PRE-APPROVED ONE-CLICK ACTIONS above, with ALL of its required action_params keys filled with real values from CLUSTER STATE -- or action_id must be null
9. Provide at least one "immediate" step. Include "short_term"/"long_term" steps only when genuinely relevant to this specific incident
10. Max 4 steps total. causes: 1-3 bullet points. summary: 1 sentence
11. For steps WITHOUT an action_id: only use REAL, standard Proxmox/Linux commands (qm, pct, pvesh, pvecm, vzdump, pveam, pvesm, systemctl, journalctl, apt, standard bash utilities) -- NEVER invent a tool name or syntax that does not exist. If genuinely unsure of the exact correct syntax for something, describe the action in the "action" field in plain words and set "command" to null rather than guess at a command
12. Output PURE JSON only -- never add // or /* */ comments anywhere inside the JSON, even to note an assumption. If you need to explain an assumption (e.g. a default value you picked), put that explanation in the "action" text itself, not as a code comment -- a comment anywhere breaks the entire JSON and discards your whole response

Respond with EXACTLY this JSON shape (fill every field, use null where genuinely not applicable):
{{
  "severity": "CRITICAL" or "HIGH",
  "target_node": "pve1" or "pve2" or null,
  "target_vmid": 103 or null,
  "summary": "one sentence: what is wrong, the exact value, the threshold breached, the business risk",
  "causes": ["cause 1 grounded in the data above", "cause 2 if relevant"],
  "fix_title": "max 8 words, action-oriented",
  "warning": "explicit safety warning if this problem type has one (e.g. never reduce VM RAM), else null",
  "steps": [
    {{
      "phase": "immediate",
      "action": "one sentence describing this step",
      "command": "exact shell/Proxmox command, or null if not applicable",
      "risk": "low, medium, or high",
      "action_id": "an id from PRE-APPROVED ONE-CLICK ACTIONS above, or null",
      "action_params": {{"node": "pve2", "vmid": 103}} or null
    }}
  ]
}}"""
    return prompt, dominant


def obtenir_doc_url(dominant: str) -> str:
    return get_doc_url(dominant)


def _commande_reelle(action_id: str, params: dict) -> str | None:
    if action_id == "enable_ksm":
        return "echo 1 > /sys/kernel/mm/ksm/run"
    if action_id == "enable_balloon":
        return f"qm set {params.get('vmid','<vmid>')} --balloon {params.get('min_mb', 512)}"
    if action_id == "migrate_vm":
        return f"qm migrate {params.get('vmid','<vmid>')} {params.get('target_node','<target_node>')} --online"
    if action_id == "set_cpu_limit":
        return f"qm set {params.get('vmid','<vmid>')} --cpulimit {params.get('limit', 1.0)}"
    if action_id == "clean_logs":
        return "journalctl --vacuum-size=200M && apt-get clean -y"
    return None


def _valider_step(step: dict) -> dict:
    action_id = step.get("action_id")
    if not action_id or action_id not in ACTION_META:
        step["action_id"] = None
        step["action_params"] = None
        return step

    requis  = set(ACTION_META[action_id].get("params", []))
    params  = step.get("action_params") if isinstance(step.get("action_params"), dict) else {}
    fournis = {k for k, v in params.items() if v is not None and v != ""}
    if not requis.issubset(fournis):
        step["action_id"] = None
        step["action_params"] = None
    else:
        step["action_params"] = {k: params[k] for k in requis}
        vraie_commande = _commande_reelle(action_id, step["action_params"])
        if vraie_commande:
            step["command"] = vraie_commande
    return step


def _retirer_commentaires_js(texte: str) -> str:
    resultat = []
    dans_chaine = False
    echap = False
    i = 0
    n = len(texte)
    while i < n:
        c = texte[i]
        if dans_chaine:
            resultat.append(c)
            if echap:
                echap = False
            elif c == '\\':
                echap = True
            elif c == '"':
                dans_chaine = False
            i += 1
            continue
        if c == '"':
            dans_chaine = True
            resultat.append(c)
            i += 1
            continue
        if c == '/' and i + 1 < n and texte[i + 1] == '/':
            i += 2
            while i < n and texte[i] not in '{}[],"\n':
                i += 1
            continue
        resultat.append(c)
        i += 1
    return ''.join(resultat)


_RE_RESUME_SECOURS = re.compile(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"')
_RE_TITRE_SECOURS  = re.compile(r'"fix_title"\s*:\s*"((?:[^"\\]|\\.)*)"')


def parser_reponse_llm(reponse: str) -> dict:
    # ← AJOUT : journal explicite à CHAQUE chemin de sortie, avec le
    # modèle Groq actif au moment de l'appel (GROQ_MODEL reflète déjà
    # correctement le dernier modèle ayant réellement répondu, y compris
    # après une bascule de repli -- voir groq_client.py). Avant, seul le
    # tout dernier cas (JSON invalide même après nettoyage) était
    # journalisé ; les deux échecs précédents étaient totalement
    # silencieux -- impossible de savoir, depuis le log seul, LEQUEL des
    # 3 échecs possibles s'était produit sans deviner depuis des indices
    # indirects (comme on vient de le faire). But : la PROCHAINE fois
    # qu'un "AI response could not be parsed" apparaît, le log dira
    # explicitement pourquoi, sans avoir besoin de le redemander.
    from agent.groq_client import GROQ_MODEL as _modele_actif

    # ← AJOUT : filet de sécurité -- retire toute trace de raisonnement
    # interne (balises <think>...</think>) avant même de chercher le JSON.
    # Normalement inutile depuis que appeler_groq() envoie
    # reasoning_format="hidden" pour qwen (voir groq_client.py), mais un
    # bug documenté côté Groq (forum communautaire) montre que ce
    # paramètre peut ponctuellement ne pas être respecté -- sans ce
    # filet, quelques milliers de mots de raisonnement interne
    # ("Wait, the prompt says...", "Let's verify...") pollueraient le
    # texte examiné par la regex ci-dessous, reproduit à l'identique
    # dans les captures reçues.
    #
    # ← AJOUT (2e cas trouvé après coup) : balise <think> OUVERTE mais
    # jamais refermée -- se produit quand max_tokens coupe la réponse EN
    # PLEIN raisonnement, avant que le modèle n'ait pu écrire ni </think>
    # ni sa vraie réponse. Dans ce cas, re.sub ci-dessus ne retire RIEN
    # (il exige une paire ouverture+fermeture) -- pire, la regex JSON qui
    # suit peut attraper un fragment JSON présent DANS le raisonnement
    # (le modèle y esquisse parfois des exemples de structure, ex:
    # action_params={"node": "pve2"} en plein milieu d'une phrase),
    # produisant un résultat trompeur plutôt qu'un échec propre. Détecté
    # et traité en échec explicite AVANT toute tentative de parsing --
    # il n'y a rien de récupérable dans une réponse qui n'a jamais
    # dépassé son propre raisonnement.
    if '<think>' in reponse and '</think>' not in reponse:
        print(f"[Incident] ECHEC parsing -- balise <think> jamais refermee (modele: {_modele_actif}, longueur reponse: {len(reponse)} caracteres)")
        return {"_parse_failed": True, "_raw": reponse}
    reponse = re.sub(r'<think>.*?</think>', '', reponse, flags=re.DOTALL).strip()
    try:
        match = re.search(r'\{.*\}', reponse, re.DOTALL)
        if not match:
            print(f"[Incident] ECHEC parsing -- aucun JSON trouve dans la reponse (modele: {_modele_actif}, longueur reponse: {len(reponse)} caracteres, debut: {reponse[:120]!r})")
            return {"_parse_failed": True, "_raw": reponse}
        json_nettoye = _retirer_commentaires_js(match.group())
        try:
            donnees = json.loads(json_nettoye)
        except json.JSONDecodeError:
            # ← AJOUT : filet de sécurité -- certaines réponses du modèle
            # actuel (voir agent/config.py, changement de modèles Groq)
            # produisent un dict "à la Python" (guillemets simples,
            # virgule finale) plutôt que du JSON strict -- confirmé être
            # la cause exacte de "Expecting property name enclosed in
            # double quotes: line 1 column 2" observé en production
            # (reproduit à l'identique avec '{'a': 'b'}'). ast.literal_eval
            # accepte les deux styles de guillemets et les virgules
            # finales sans risque : il parse un littéral Python, n'exécute
            # jamais de code (contrairement à eval()).
            import ast
            donnees = ast.literal_eval(json_nettoye)
            if not isinstance(donnees, dict):
                raise ValueError("resultat de literal_eval n'est pas un dict")
            print(f"[Incident] Parsing reussi via repli ast.literal_eval (modele: {_modele_actif}) -- JSON non strict (guillemets simples ou virgule finale probable)")
    except Exception as e:
        print(f"[Incident] ECHEC parsing -- JSON invalide meme apres nettoyage: {e} (modele: {_modele_actif}, longueur reponse: {len(reponse)} caracteres)")
        m_resume = _RE_RESUME_SECOURS.search(reponse)
        m_titre  = _RE_TITRE_SECOURS.search(reponse)
        if m_resume:
            return {
                "_parse_failed": True,
                "_raw": m_resume.group(1),
                "summary": m_resume.group(1),
                "fix_title": m_titre.group(1) if m_titre else None,
            }
        return {"_parse_failed": True, "_raw": reponse}

    donnees["steps"] = [_valider_step(s) for s in donnees.get("steps", []) if isinstance(s, dict)]
    return donnees