import asyncio
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "proxmox"))

from agent.config        import (SURVEILLANCE_INTERVAL, SCORE_MIN_LLM,
                                  COOLDOWN_RAPPORT_S, SCORE_PLANCHER_LSTM,
                                  MIN_NOEUDS_ONLINE_POUR_ALERTE)
from agent.groq_client   import appeler_groq, rate_limiter
from agent.prompts       import system_prompt_surveillance
from agent.anomaly_detector import detecter_anomalies
from agent.rules_engine  import generer_regles_ia, regles_necessitent_regeneration
from agent.report_writer import sauvegarder_rapport

dernier_etat  = {}
dernier_lstm  = {"score": 0.0, "seuil": 0.5, "drift": False,
                 "score_if": 0.0, "score_lstm": 0.0, "lstm_ready": False}
dernier_rapport_ts = 0.0
ws_queue: asyncio.Queue = None


def set_ws_queue(q: asyncio.Queue):
    global ws_queue
    ws_queue = q


def _proxmox_accessible(etat: dict) -> bool:
    """
    Retourne True uniquement si au moins MIN_NOEUDS_ONLINE_POUR_ALERTE noeuds
    sont en ligne. Bloque toutes les alertes si Proxmox est eteint.
    """
    noeuds = etat.get("noeuds", [])
    if not noeuds:
        return False
    online = sum(
        1 for n in noeuds
        if str(n.get("statut", "")).lower() in ("online", "en ligne", "up")
    )
    return online >= MIN_NOEUDS_ONLINE_POUR_ALERTE


def _normaliser_etat(etat: dict) -> dict:
    if not etat:
        return {}
    normalized = dict(etat)
    noeuds = etat.get("noeuds") or etat.get("nodes") or []
    noeuds_norm = []
    for n in noeuds:
        ram_used  = float(n.get("ram_used_gb")  or n.get("mem_used_gb")  or 0)
        ram_total = float(n.get("ram_total_gb") or n.get("mem_total_gb") or 0)
        n_norm = {
            "nom":           n.get("nom") or n.get("name") or n.get("node") or "unknown",
            "statut":        n.get("statut") or n.get("status") or "unknown",
            "cpu_pct":       float(n.get("cpu_pct") or n.get("cpu") or 0),
            "cpu_cores":     int(n["cpu_cores"]) if n.get("cpu_cores") else int(n.get("maxcpu", 0)),
            "ram_pct":       float(n.get("ram_pct") or n.get("mem_pct") or 0),
            "ram_used_gb":   ram_used,
            "ram_total_gb":  ram_total,
            "disk_pct":      float(n.get("disk_pct") or 0),
            "disk_used_gb":  float(n.get("disk_used_gb") or 0),
            "disk_total_gb": float(n.get("disk_total_gb") or 0),
            "net_in_mbps":   float(n.get("net_in_mbps") or 0),
            "net_out_mbps":  float(n.get("net_out_mbps") or 0),
            "uptime_h":      float(n.get("uptime_h") or (n.get("uptime", 0) / 3600)),
            "vms_running":   int(n.get("vms_running") or 0),
            "swap_pct":              float(n.get("swap_pct", 0)),
            "cpu_iowait_pct":        float(n.get("cpu_iowait_pct", 0)),
            "disk_read_iops":        float(n.get("disk_read_iops", 0)),
            "disk_write_iops":       float(n.get("disk_write_iops", 0)),
            "disk_read_latency_ms":  float(n.get("disk_read_latency_ms", 0)),
            "disk_write_latency_ms": float(n.get("disk_write_latency_ms", 0)),
            "net_errors_in":         float(n.get("net_errors_in", 0)),
            "net_errors_out":        float(n.get("net_errors_out", 0)),
            "net_drop_in":           float(n.get("net_drop_in", 0)),
            "net_drop_out":          float(n.get("net_drop_out", 0)),
            "cpu_temp_max_c":            float(n.get("cpu_temp_max_c", 0)),
            "smart_ok":                  bool(n.get("smart_ok", True)),
            "smart_reallocated_sectors": int(n.get("smart_reallocated_sectors", 0)),
            "smart_uncorrectable":       int(n.get("smart_uncorrectable", 0)),
            "smart_pending_sectors":     int(n.get("smart_pending_sectors", 0)),
            "smart_disks_monitored":     int(n.get("smart_disks_monitored", 0)),
            "zfs_arc_hit_rate":          float(n.get("zfs_arc_hit_rate", 0)),
            "zfs_arc_size_gb":           float(n.get("zfs_arc_size_gb", 0)),
            "zfs_available":             bool(n.get("zfs_available", False)),
            "corosync_ok":               bool(n.get("corosync_ok", True)),
            "corosync_quorum_ok":        bool(n.get("corosync_quorum_ok", True)),
            "fd_used_pct":               float(n.get("fd_used_pct", 0)),
            "load_avg_1m":               float(n.get("load_avg_1m", 0)),
            "procs_running":             int(n.get("procs_running", 0)),
            "net_available":             bool(n.get("net_available", False)),
            "io_available":              bool(n.get("io_available", False)),
            "hw_available":              bool(n.get("hw_available", False)),
            "smart_available":           bool(n.get("smart_available", False)),
        }
        if n_norm["ram_pct"] == 0 and ram_total > 0:
            n_norm["ram_pct"] = round(ram_used / ram_total * 100, 2)
        noeuds_norm.append(n_norm)
    normalized["noeuds"] = noeuds_norm
    vms = etat.get("vms") or []
    normalized["vms"] = [{
        "vmid":        str(v.get("vmid") or ""),
        "nom":         v.get("nom") or v.get("name") or str(v.get("vmid", "?")),
        "statut":      v.get("statut") or v.get("status") or "unknown",
        "noeud":       v.get("noeud") or v.get("node") or "unknown",
        "vcpus":       int(v.get("vcpus") or v.get("cpus") or 0),
        "maxmem_gb":   float(v.get("maxmem_gb") or (v.get("maxmem", 0) / 1e9)),
        "maxdisk_gb":  float(v.get("maxdisk_gb") or (v.get("maxdisk", 0) / 1e9)),
        "cpu_pct":     float(v.get("cpu_pct") or v.get("cpu", 0) * 100),
        "ram_pct":     float(v.get("ram_pct") or 0),
        "net_in_mbps": float(v.get("net_in_mbps") or 0),
        "net_out_mbps":float(v.get("net_out_mbps") or 0),
        "uptime_h":    float(v.get("uptime_h") or 0),
    } for v in vms]
    normalized["net_in_mbps"]  = round(sum(n.get("net_in_mbps", 0)  for n in noeuds_norm), 3)
    normalized["net_out_mbps"] = round(sum(n.get("net_out_mbps", 0) for n in noeuds_norm), 3)
    normalized["vms_running"]  = int(
        etat.get("vms_running") or
        sum(1 for v in normalized["vms"] if v["statut"] in ("running", "en cours")) or
        sum(n.get("vms_running", 0) for n in noeuds_norm)
    )
    if "alertes" not in normalized:
        normalized["alertes"] = etat.get("alerts") or []
    return normalized


def _classifier_anomalies(anomalies: list) -> tuple:
    types = {k: [] for k in ["ram","disk","cpu","swap","iowait","temp","vm","quorum","network","ai"]}
    for a in anomalies:
        msg   = a.get("message", "").lower()
        cible = a.get("cible", "").lower()
        t     = a.get("type", "")
        if t == "ai_score":                    types["ai"].append(a)
        elif "quorum" in msg:                  types["quorum"].append(a)
        elif "temp" in msg:                    types["temp"].append(a)
        elif "iowait" in msg or "latency" in msg: types["iowait"].append(a)
        elif "swap" in msg:                    types["swap"].append(a)
        elif "disk" in msg:                    types["disk"].append(a)
        elif "ram" in msg or "memory" in msg:  types["ram"].append(a)
        elif "cpu" in msg:                     types["cpu"].append(a)
        elif "vm" in msg or "vm" in cible:     types["vm"].append(a)
        elif "net" in msg:                     types["network"].append(a)
        else:
            if "ram" in cible:    types["ram"].append(a)
            elif "disk" in cible: types["disk"].append(a)
            elif "cpu" in cible:  types["cpu"].append(a)
            else:                 types["ram"].append(a)
    dominant = max((k for k in types if k != "ai"), key=lambda k: len(types[k]), default="ram")
    if not types[dominant]:
        dominant = "ram"
    return types, dominant


def _calculer_seuils_franchis(etat: dict) -> str:
    """
    Calcule pour chaque noeud quel seuil exact est franchi (WARNING ou CRITICAL).
    Evite que le LLM confonde WARNING et CRITICAL dans son analyse.
    Ex : RAM 82.3% est WARNING (>75%) mais pas encore CRITICAL (>85%).
    """
    lignes = []
    for n in etat.get("noeuds", []):
        nom = n.get("nom", "?")
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
        if swap >= 50:
            lignes.append(f"  {nom} SWAP: {swap:.1f}% — CRITICAL threshold breached (>=50%) — target 0%")
        elif swap >= 20:
            lignes.append(f"  {nom} SWAP: {swap:.1f}% — WARNING threshold breached (>=20%) — target 0%")
    return "\n".join(lignes) if lignes else "  All metrics within normal range"


def _construire_prompt_specifique(anomalies: list, etat: dict, lstm: dict) -> str:
    anomalies_str   = "\n".join([f"- [{a['niveau']}] {a['message']}" for a in anomalies])
    _, dominant     = _classifier_anomalies(anomalies)
    seuils_franchis = _calculer_seuils_franchis(etat)
    noeuds_ctx = ""
    for n in etat.get("noeuds", []):
        ram_free  = round(n.get("ram_total_gb", 0) - n.get("ram_used_gb", 0), 1)
        disk_free = round(n.get("disk_total_gb", 0) - n.get("disk_used_gb", 0), 1)
        noeuds_ctx += (
            f"  {n.get('nom','?')}: CPU={n.get('cpu_pct',0):.1f}% (cores={n.get('cpu_cores',0)}) "
            f"RAM={n.get('ram_pct',0):.1f}% ({n.get('ram_used_gb',0):.1f}/{n.get('ram_total_gb',0):.1f}GB FREE={ram_free}GB) "
            f"DISK={n.get('disk_pct',0):.1f}% ({n.get('disk_used_gb',0):.1f}/{n.get('disk_total_gb',0):.1f}GB FREE={disk_free}GB) "
            f"SWAP={n.get('swap_pct',0):.1f}% IOWAIT={n.get('cpu_iowait_pct',0):.1f}% "
            f"LOAD={n.get('load_avg_1m',0):.2f} TEMP={n.get('cpu_temp_max_c',0):.0f}C STATUS={n.get('statut','?')}\n"
        )
    vms_ctx = "".join(
        f"  VM{v.get('vmid','?')} {v.get('nom','?')} on {v.get('noeud','?')}: "
        f"status={v.get('statut','?')} CPU={v.get('cpu_pct',0):.1f}% RAM={v.get('ram_pct',0):.1f}% maxmem={v.get('maxmem_gb',0):.1f}GB\n"
        for v in etat.get("vms", [])
    ) or "  No VM data\n"
    specific = {
        "ram":     "IMMEDIATE COMMAND: ps aux --sort=-%mem | head -15\nTARGET: RAM < 70% | THRESHOLDS: WARNING 75% CRITICAL 85%\nLONG-TERM: qm set <vmid> --balloon <min_mb> && echo 1 > /sys/kernel/mm/ksm/run",
        "disk":    "IMMEDIATE COMMAND: du -sh /var/lib/vz/dump/* 2>/dev/null | sort -rh | head -10 && df -h /\nTARGET: DISK < 75% | THRESHOLDS: WARNING 80% CRITICAL 90%\nLONG-TERM: apt clean && journalctl --vacuum-size=500M -- keep last 2 backups",
        "cpu":     "IMMEDIATE COMMAND: top -b -n1 | head -20\nTARGET: CPU < 75% | THRESHOLDS: WARNING 80% CRITICAL 90%\nLONG-TERM: qm set <vmid> --cpulimit 1.0 or reduce vCPUs",
        "swap":    "IMMEDIATE COMMAND: free -h && swapon --show && dmesg | grep -i 'out of memory' | tail -5\nTARGET: SWAP 0% | THRESHOLDS: WARNING 20% CRITICAL 50%\nLONG-TERM: qm set <vmid> --balloon <min_mb>",
        "iowait":  "IMMEDIATE COMMAND: iostat -x 1 3\nTARGET: IOWAIT < 5% await < 10ms | THRESHOLDS: WARNING 10% CRITICAL 20%\nLONG-TERM: qm set <vmid> --ide0 local:<disk>,mbps_rd=100,mbps_wr=50",
        "temp":    "IMMEDIATE COMMAND: sensors && dmesg | grep -i 'throttl' | tail -5\nTARGET: TEMP < 70C | THRESHOLDS: WARNING 75C CRITICAL 85C\nLONG-TERM: apt install lm-sensors && sensors-detect",
        "vm":      "IMMEDIATE COMMAND: qm list && journalctl -u qmeventd --since '1 hour ago' | tail -20\nTARGET: All critical VMs running\nLONG-TERM: PVE GUI -> Datacenter -> HA -> Add for critical VMs",
        "quorum":  "IMMEDIATE COMMAND: pvecm status && corosync-cfgtool -s\nTARGET: Quorate: Yes\nLONG-TERM: pvecm qdevice setup <ip>",
        "network": "IMMEDIATE COMMAND: ip -s link show && ethtool eth0\nTARGET: 0 errors 0 drops\nLONG-TERM: verify switch port config",
        "ai":      "IMMEDIATE COMMAND: pvesh get /nodes/pve1/status && pvesh get /nodes/pve2/status\nTARGET: AI score < 0.50\nLONG-TERM: check recent changes (new VMs, updates)",
    }.get(dominant, "IMMEDIATE COMMAND: ps aux --sort=-%mem | head -15\nTARGET: RAM < 70%")

    return f"""You are a senior Proxmox VE infrastructure engineer. Generate a precise incident report.

CLUSTER STATE:
{noeuds_ctx}
VMs:
{vms_ctx}
ANOMALIES:
{anomalies_str}
AI Score: {lstm['score']:.4f} / threshold {lstm['seuil']:.4f}

EXACT THRESHOLD STATUS — COPY THESE VERBATIM, DO NOT CHANGE ANY NUMBER:
{seuils_franchis}

PROBLEM TYPE GUIDANCE:
{specific}

STRICT RULES — VIOLATION = WRONG ANSWER:
1. pve1/pve2 are HYPERVISOR NODES — NEVER use pct commands on them
2. COPY threshold values verbatim from EXACT THRESHOLD STATUS above — never round up or invent
3. If EXACT THRESHOLD STATUS says "WARNING threshold breached (>=75%)" — write WARNING, NOT CRITICAL
4. If EXACT THRESHOLD STATUS says "CRITICAL threshold breached (>=85%)" — write CRITICAL
5. **Severity** in ---INCIDENT--- MUST match: WARNING breach = HIGH, CRITICAL breach = CRITICAL
6. **target** in ---RECOMMENDATION--- MUST always be the value from PROBLEM TYPE GUIDANCE (e.g. <70% for RAM), NOT the threshold value
7. Use ONLY the command from PROBLEM TYPE GUIDANCE — no other command
8. Write ONLY the command inside the bash block — no comments, no labels
9. Max 200 words total

Format:
---INCIDENT---
**Severity:** [CRITICAL if metric>=critical_threshold, HIGH if metric>=warning_threshold only]
**Summary:** [node name] [metric] at [exact value from CLUSTER STATE] [breaches WARNING/CRITICAL] threshold ([threshold value]) — [business risk]
**Causes:**
- [copy from EXACT THRESHOLD STATUS verbatim]

---RECOMMENDATION---
**Fix title:** [max 8 words, action-oriented]
**Problem:** [exact value] exceeds [warning OR critical] threshold ([threshold]) on [node] — target: [target from PROBLEM TYPE GUIDANCE]
**Immediate action:**
```bash
[EXACT COMMAND from PROBLEM TYPE GUIDANCE]
```
[What this command shows — 1 sentence]
**Long-term:** [1 concrete Proxmox command with timeline]"""


async def analyser_anomalie_llm(anomalies: list, etat: dict) -> str:
    lstm   = dernier_lstm
    prompt = _construire_prompt_specifique(anomalies, etat, lstm)
    loop   = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: appeler_groq(system_prompt_surveillance(etat, lstm), [], prompt, 900),
    )


def _boucle_surveillance(loop: asyncio.AbstractEventLoop):
    global dernier_etat, dernier_lstm, dernier_rapport_ts

    try:
        from proxmox_api import get_etat_cluster
        PROXMOX_OK = True
    except Exception:
        PROXMOX_OK = False
        def get_etat_cluster(): return {}

    try:
        from metriques_proxmox import collecter_metriques_cluster
        PROMETHEUS_OK = True
    except Exception:
        PROMETHEUS_OK = False
        def collecter_metriques_cluster(): return {}

    _analyser = None
    try:
        from ml_analyser import MLAnalyseur
        _analyser = MLAnalyseur()
        print("[OK] AI detection engine - IF actif, LSTM en apprentissage")
    except Exception as e:
        print(f"[WARN] AI engine: {e}")

    etat_prec = {}
    print(f"[Monitoring] Demarre -- toutes les {SURVEILLANCE_INTERVAL}s")

    while True:
        try:
            if PROXMOX_OK:
                etat_raw     = get_etat_cluster()
                etat         = _normaliser_etat(etat_raw)
                dernier_etat = etat
            else:
                etat = dernier_etat

            # ── GARDE PRINCIPALE : Proxmox offline = zero alerte ─────────────
            if not _proxmox_accessible(etat):
                print("[Monitoring] Proxmox non accessible -- alertes et rapports suspendus")
                if ws_queue and etat:
                    asyncio.run_coroutine_threadsafe(ws_queue.put({
                        "type":      "etat_cluster",
                        "etat":      etat,
                        "lstm":      dernier_lstm,
                        "timestamp": datetime.now().isoformat(),
                    }), loop)
                time.sleep(SURVEILLANCE_INTERVAL)
                continue

            if etat:
                try:
                    from database import sauvegarder_metriques
                    sauvegarder_metriques(etat, dernier_lstm.get("score", 0.0))
                except Exception:
                    pass

            if etat and regles_necessitent_regeneration() and rate_limiter.slots() >= 5:
                print("[AI Rules] Generation automatique des regles (24h)...")
                asyncio.run_coroutine_threadsafe(generer_regles_ia(etat), loop).result(timeout=60)
            elif etat and etat_prec and rate_limiter.slots() >= 5:
                vms_avant    = {v["vmid"] for v in etat_prec.get("vms", [])}
                vms_apres    = {v["vmid"] for v in etat.get("vms", [])}
                noeuds_avant = {n["nom"] for n in etat_prec.get("noeuds", [])}
                noeuds_apres = {n["nom"] for n in etat.get("noeuds", [])}
                if (vms_avant != vms_apres) or (noeuds_avant != noeuds_apres):
                    print("[AI Rules] Changement infrastructure -- regeneration")
                    asyncio.run_coroutine_threadsafe(generer_regles_ia(etat), loop).result(timeout=60)

            metriques_prom = {}
            if PROMETHEUS_OK:
                try:
                    metriques_prom = collecter_metriques_cluster()
                except Exception as e:
                    print(f"[Prometheus] {e}")

            net_keys   = ("net_in_mbps", "net_out_mbps")
            io_keys    = ("cpu_iowait_pct", "disk_read_iops", "disk_write_iops")
            hw_keys    = ("cpu_temp_max_c",)
            smart_keys = ("smart_disks_monitored",)

            if etat and metriques_prom and isinstance(metriques_prom, dict):
                prom_noeuds = metriques_prom.get("noeuds", [])
                for n in etat.get("noeuds", []):
                    nom = (n.get("nom") or "").lower()
                    match = next(
                        (pn for pn in prom_noeuds
                         if str(pn.get("node", "")).lower() == nom
                         or nom in str(pn.get("node", "")).lower()),
                        None
                    )
                    if match:
                        for key in (
                            "swap_pct","swap_used_gb","swap_total_gb",
                            "cpu_iowait_pct","load_avg_1m","load_avg_5m","load_avg_15m",
                            "disk_read_iops","disk_write_iops","disk_read_mbps","disk_write_mbps",
                            "disk_read_latency_ms","disk_write_latency_ms",
                            "net_in_mbps","net_out_mbps","net_errors_in","net_errors_out",
                            "net_drop_in","net_drop_out","cpu_temp_max_c","cpu_temp_avg_c",
                            "disk_temp_max_c","smart_ok","smart_reallocated_sectors",
                            "smart_pending_sectors","smart_uncorrectable","smart_disks_monitored",
                            "zfs_arc_hit_rate","zfs_arc_size_gb","zfs_available",
                            "corosync_ok","corosync_quorum_ok","fd_used_pct","procs_running","procs_blocked",
                        ):
                            if key in match:
                                n[key] = match[key]
                        if match.get("uptime_h", 0) > 0:
                            n["uptime_h"] = match["uptime_h"]
                        n["net_available"]   = any(k in match for k in net_keys)
                        n["io_available"]    = any(k in match for k in io_keys)
                        n["hw_available"]    = any(k in match for k in hw_keys)
                        n["smart_available"] = any(k in match for k in smart_keys)
                    else:
                        n["net_available"] = n["io_available"] = n["hw_available"] = n["smart_available"] = False
                if "cluster" in metriques_prom:
                    etat["cluster"] = {**etat.get("cluster", {}), **metriques_prom["cluster"]}
                dernier_etat = etat

            if _analyser and etat:
                try:
                    noeuds   = etat.get("noeuds", [])
                    n_noeuds = max(len(noeuds), 1)
                    avg      = lambda k: sum(x.get(k, 0) for x in noeuds) / n_noeuds
                    total    = lambda k: sum(x.get(k, 0) for x in noeuds)
                    metriques_ml = {
                        "cpu_pct": avg("cpu_pct"), "ram_pct": avg("ram_pct"),
                        "disk_pct": avg("disk_pct"), "swap_pct": avg("swap_pct"),
                        "cpu_iowait_pct": avg("cpu_iowait_pct"),
                        "disk_read_iops": total("disk_read_iops"), "disk_write_iops": total("disk_write_iops"),
                        "disk_read_latency_ms": avg("disk_read_latency_ms"),
                        "disk_write_latency_ms": avg("disk_write_latency_ms"),
                        "net_in_mbps": total("net_in_mbps"), "net_out_mbps": total("net_out_mbps"),
                        "net_errors_in": total("net_errors_in"), "net_errors_out": total("net_errors_out"),
                        "net_drop_in": total("net_drop_in"), "net_drop_out": total("net_drop_out"),
                        "vms_running": etat.get("vms_running", 0),
                        "load_avg_1m": avg("load_avg_1m"),
                        "zfs_arc_hit_rate": avg("zfs_arc_hit_rate") or 95.0,
                        "cpu_temp_max_c": max((n.get("cpu_temp_max_c", 0) for n in noeuds), default=0),
                        "fd_used_pct": avg("fd_used_pct"),
                    }
                    if metriques_prom and isinstance(metriques_prom, dict):
                        for k in metriques_ml:
                            if k in metriques_prom:
                                metriques_ml[k] = metriques_prom[k]
                    score, seuil = _analyser.analyser(metriques_ml)
                    # ── Seuil plancher : evite les faux positifs si le LSTM
                    # s'est entraine sur des donnees nulles (Proxmox eteint).
                    seuil = max(float(seuil), SCORE_PLANCHER_LSTM)
                    ml_stats = _analyser.get_stats() if hasattr(_analyser, "get_stats") else {}
                    dernier_lstm = {
                        "score":      float(score),
                        "seuil":      seuil,
                        "drift":      ml_stats.get("lstm", {}).get("drift_detecte", False),
                        "score_if":   ml_stats.get("score_if", 0.0),
                        "score_lstm": ml_stats.get("score_lstm", 0.0),
                        "lstm_ready": ml_stats.get("lstm_ready", False),
                    }
                except Exception as e:
                    print(f"[AI Engine] {e}")

            nouvelles = detecter_anomalies(etat, etat_prec)
            if dernier_lstm["score"] >= SCORE_MIN_LLM:
                ai_score  = dernier_lstm["score"]
                ai_niveau = "CRITIQUE" if ai_score >= 0.8 else "IMPORTANT"
                nouvelles.append({
                    "niveau": ai_niveau, "cible": "cluster",
                    "message": f"AI score {ai_score:.4f} > threshold {dernier_lstm['seuil']:.4f}",
                    "type": "ai_score",
                })

            seen_keys = set()
            nouvelles_dedup = []
            for a in nouvelles:
                key = (a.get("cible", ""), a.get("message", "").lower().strip())
                if key not in seen_keys:
                    seen_keys.add(key)
                    nouvelles_dedup.append(a)
            nouvelles = nouvelles_dedup

            now = time.time()
            if nouvelles and (now - dernier_rapport_ts) >= COOLDOWN_RAPPORT_S:
                slots = rate_limiter.slots()
                print(f"[Monitoring] {len(nouvelles)} anomalie(s) -- slots: {slots}")
                if slots >= 5:
                    analyse = asyncio.run_coroutine_threadsafe(
                        analyser_anomalie_llm(nouvelles, etat), loop
                    ).result(timeout=30)
                else:
                    lignes  = "\n".join(f"- [{a['niveau']}] {a['message']}" for a in nouvelles)
                    analyse = f"**Anomalies** (quota reserve)\n\n{lignes}"

                etat_rapport       = etat if etat and etat.get("noeuds") else dernier_etat
                nom_rapport        = sauvegarder_rapport(nouvelles, analyse, etat_rapport, dernier_lstm["score"])
                dernier_rapport_ts = now

                if ws_queue:
                    asyncio.run_coroutine_threadsafe(ws_queue.put({
                        "type": "alerte", "role": "assistant",
                        "content": analyse, "anomalies": nouvelles,
                        "timestamp": datetime.now().isoformat(),
                        "rapport": nom_rapport, "lstm": dernier_lstm,
                    }), loop)

                try:
                    from notifications import envoyer_alerte
                    # Sévérité réelle = niveau max des anomalies métriques (hors score AI pur)
                    # Le score AI seul ne suffit pas à marquer l'email CRITIQUE
                    # si toutes les métriques réelles sont en WARNING
                    anomalies_metriques = [a for a in nouvelles if a.get("type") != "ai_score"]
                    anomalies_ai        = [a for a in nouvelles if a.get("type") == "ai_score"]
                    if any(a.get("niveau") == "CRITIQUE" for a in anomalies_metriques):
                        niv_max = "CRITIQUE"
                    elif anomalies_metriques:
                        niv_max = "IMPORTANT"
                    elif any(a.get("niveau") == "CRITIQUE" for a in anomalies_ai):
                        # Score AI CRITIQUE uniquement si pas d'anomalie métrique
                        niv_max = "CRITIQUE"
                    else:
                        niv_max = "IMPORTANT"
                    titre_notif = nouvelles[0].get("message", "Anomalie")[:60]
                    asyncio.run_coroutine_threadsafe(
                        envoyer_alerte(titre=titre_notif, message=analyse, severite=niv_max,
                                       anomalies=nouvelles, etat=etat), loop
                    )
                except Exception as e:
                    print(f"[Notif] {e}")

                try:
                    from database import sauvegarder_anomalie
                    for a in nouvelles:
                        sauvegarder_anomalie(niveau=a.get("niveau","INFO"), message=a.get("message",""),
                                             noeud=a.get("cible"), score=dernier_lstm["score"], rapport=nom_rapport)
                except Exception:
                    pass

            elif nouvelles:
                print(f"[Monitoring] Cooldown ({int(COOLDOWN_RAPPORT_S - (now - dernier_rapport_ts))}s)")

            if dernier_lstm.get("drift") and _analyser:
                _analyser.reentrainer()

            if ws_queue:
                asyncio.run_coroutine_threadsafe(ws_queue.put({
                    "type": "etat_cluster", "etat": etat,
                    "lstm": dernier_lstm, "timestamp": datetime.now().isoformat(),
                }), loop)

            etat_prec = etat

        except Exception as e:
            print(f"[Monitoring] Erreur: {e}")
            import traceback; traceback.print_exc()

        time.sleep(SURVEILLANCE_INTERVAL)


def demarrer(loop: asyncio.AbstractEventLoop):
    threading.Thread(target=_boucle_surveillance, args=(loop,), daemon=True).start()