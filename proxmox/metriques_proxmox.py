"""
metriques_proxmox.py — Collecte métriques complète via Prometheus + API Proxmox
Ressources monitorées :
  - CPU, RAM, Disk (existant)
  - I/O Disque (IOPS, latence, throughput)  ← NOUVEAU
  - Swap                                     ← NOUVEAU
  - Réseau (perte paquets, erreurs)          ← NOUVEAU
  - Processus (load, zombie, fd)             ← NOUVEAU
  - Cluster Proxmox (quorum, corosync)       ← NOUVEAU
"""

import os
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
PROXMOX_HOST   = os.getenv("PROXMOX_HOST", "")
PROXMOX_TOKEN  = os.getenv("PROXMOX_TOKEN_ID", "")
PROXMOX_SECRET = os.getenv("PROXMOX_TOKEN_SECRET", "")
TIMEOUT        = int(os.getenv("PROMETHEUS_TIMEOUT", "5"))


def _query(promql: str) -> list:
    try:
        r = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": promql},
            timeout=TIMEOUT
        )
        data = r.json()
        if data.get("status") == "success":
            return data["data"]["result"]
    except Exception as e:
        pass
    return []


def _val(result, default=0.0):
    try:
        return float(result["value"][1])
    except:
        return default


def _sum_query(promql: str) -> float:
    results = _query(promql)
    return sum(_val(r) for r in results)


def _first_val(promql: str, default=0.0) -> float:
    results = _query(promql)
    return _val(results[0], default) if results else default


# ══════════════════════════════════════════════════════════════════════════════
# Découverte automatique
# ══════════════════════════════════════════════════════════════════════════════
def _decouvrir_noeuds() -> list:
    noeuds = set()
    for metric in ["pve_cpu_usage_ratio", "pve_memory_usage_bytes"]:
        for r in _query(metric):
            node = r.get("metric", {}).get("node", "")
            if node:
                noeuds.add(node)
    try:
        r = requests.get(f"{PROMETHEUS_URL}/api/v1/targets", timeout=TIMEOUT)
        for t in r.json().get("data", {}).get("activeTargets", []):
            job = t.get("labels", {}).get("job", "")
            if "proxmox" in job.lower() or "pve" in job.lower():
                node = t.get("labels", {}).get("node",
                       t.get("labels", {}).get("instance", "").split(":")[0])
                if node:
                    noeuds.add(node)
    except:
        pass
    for r in _query('up{job=~".*proxmox.*|.*pve.*"}'):
        node = r.get("metric", {}).get("instance", "").split(":")[0]
        if node:
            noeuds.add(node)
    return list(noeuds)


def _decouvrir_vms() -> list:
    vms = {}
    for vm_type in ["qemu", "lxc"]:
        for r in _query(f'pve_cpu_usage_ratio{{type="{vm_type}"}}'):
            m = r.get("metric", {})
            vmid = m.get("vmid", "")
            if vmid:
                vms[vmid] = {
                    "vmid": vmid,
                    "name": m.get("name", f"vm-{vmid}"),
                    "node": m.get("node", "unknown"),
                    "type": vm_type,
                }
    return list(vms.values())


# ══════════════════════════════════════════════════════════════════════════════
# Métriques nœud — COMPLÈTES
# ══════════════════════════════════════════════════════════════════════════════
def _metriques_noeud(node: str) -> dict:
    m = {"node": node, "timestamp": datetime.now().isoformat()}

    # ── CPU ──────────────────────────────────────────────────────────────────
    m["cpu_pct"]   = round(_first_val(f'pve_cpu_usage_ratio{{node="{node}",type="node"}}') * 100, 2)
    m["cpu_cores"] = int(_first_val(f'pve_cpu_usage_limit{{node="{node}",type="node"}}'))

    # Load average (via node_exporter si disponible)
    load1  = _first_val(f'node_load1{{instance=~"{node}.*"}}')
    load5  = _first_val(f'node_load5{{instance=~"{node}.*"}}')
    load15 = _first_val(f'node_load15{{instance=~"{node}.*"}}')
    m["load_avg_1m"]  = round(load1,  2)
    m["load_avg_5m"]  = round(load5,  2)
    m["load_avg_15m"] = round(load15, 2)

    # I/O wait CPU (% CPU bloqué à attendre le disque)
    iowait = _first_val(f'rate(node_cpu_seconds_total{{instance=~"{node}.*",mode="iowait"}}[5m])')
    m["cpu_iowait_pct"] = round(iowait * 100, 2)

    # ── RAM ──────────────────────────────────────────────────────────────────
    ram_used  = _first_val(f'pve_memory_usage_bytes{{node="{node}",type="node"}}')
    ram_total = _first_val(f'pve_memory_size_bytes{{node="{node}",type="node"}}')
    m["ram_used_gb"]  = round(ram_used  / (1024**3), 2)
    m["ram_total_gb"] = round(ram_total / (1024**3), 2)
    m["ram_pct"]      = round((ram_used / ram_total * 100) if ram_total > 0 else 0, 2)

    # ── SWAP — NOUVEAU ────────────────────────────────────────────────────────
    swap_total = _first_val(f'node_memory_SwapTotal_bytes{{instance=~"{node}.*"}}')
    swap_free  = _first_val(f'node_memory_SwapFree_bytes{{instance=~"{node}.*"}}')
    swap_used  = max(0, swap_total - swap_free)
    m["swap_total_gb"] = round(swap_total / (1024**3), 2)
    m["swap_used_gb"]  = round(swap_used  / (1024**3), 2)
    m["swap_pct"]      = round((swap_used / swap_total * 100) if swap_total > 0 else 0, 2)

    # Swap in/out rate (si actif = problème sérieux)
    m["swap_in_rate"]  = round(_first_val(f'rate(node_vmstat_pswpin{{instance=~"{node}.*"}}[5m])'  ), 2)
    m["swap_out_rate"] = round(_first_val(f'rate(node_vmstat_pswpout{{instance=~"{node}.*"}}[5m])'  ), 2)

    # ── DISQUE ───────────────────────────────────────────────────────────────
    disk_used  = _first_val(f'pve_disk_usage_bytes{{node="{node}",type="node"}}')
    disk_total = _first_val(f'pve_disk_size_bytes{{node="{node}",type="node"}}')
    m["disk_used_gb"]  = round(disk_used  / (1024**3), 2)
    m["disk_total_gb"] = round(disk_total / (1024**3), 2)
    m["disk_pct"]      = round((disk_used / disk_total * 100) if disk_total > 0 else 0, 2)

    # ── I/O DISQUE — NOUVEAU ─────────────────────────────────────────────────
    # IOPS lecture/écriture
    m["disk_read_iops"]  = round(_sum_query(f'rate(node_disk_reads_completed_total{{instance=~"{node}.*"}}[5m])'  ), 1)
    m["disk_write_iops"] = round(_sum_query(f'rate(node_disk_writes_completed_total{{instance=~"{node}.*"}}[5m])'  ), 1)

    # Throughput lecture/écriture (MB/s)
    m["disk_read_mbps"]  = round(_sum_query(f'rate(node_disk_read_bytes_total{{instance=~"{node}.*"}}[5m])')  / (1024**2), 2)
    m["disk_write_mbps"] = round(_sum_query(f'rate(node_disk_written_bytes_total{{instance=~"{node}.*"}}[5m])') / (1024**2), 2)

    # Latence I/O (ms) — > 10ms = problème
    read_lat  = _sum_query(f'rate(node_disk_read_time_seconds_total{{instance=~"{node}.*"}}[5m])')
    write_lat = _sum_query(f'rate(node_disk_write_time_seconds_total{{instance=~"{node}.*"}}[5m])')
    read_ops  = max(_sum_query(f'rate(node_disk_reads_completed_total{{instance=~"{node}.*"}}[5m])'  ), 0.001)
    write_ops = max(_sum_query(f'rate(node_disk_writes_completed_total{{instance=~"{node}.*"}}[5m])'  ), 0.001)
    m["disk_read_latency_ms"]  = round((read_lat  / read_ops)  * 1000, 2)
    m["disk_write_latency_ms"] = round((write_lat / write_ops) * 1000, 2)

    # ── RÉSEAU ───────────────────────────────────────────────────────────────
    m["net_in_mbps"]  = round(_sum_query(f'rate(pve_network_receive_bytes_total{{node="{node}",type="node"}}[5m])')  / (1024**2), 3)
    m["net_out_mbps"] = round(_sum_query(f'rate(pve_network_transmit_bytes_total{{node="{node}",type="node"}}[5m])') / (1024**2), 3)

    # ── RÉSEAU AVANCÉ — NOUVEAU ───────────────────────────────────────────────
    # Erreurs réseau (erreurs/s — si > 0 = problème matériel)
    m["net_errors_in"]  = round(_sum_query(f'rate(node_network_receive_errs_total{{instance=~"{node}.*"}}[5m])'  ), 4)
    m["net_errors_out"] = round(_sum_query(f'rate(node_network_transmit_errs_total{{instance=~"{node}.*"}}[5m])'  ), 4)

    # Paquets perdus (drop/s — si > 0 = saturation réseau)
    m["net_drop_in"]  = round(_sum_query(f'rate(node_network_receive_drop_total{{instance=~"{node}.*"}}[5m])'  ), 4)
    m["net_drop_out"] = round(_sum_query(f'rate(node_network_transmit_drop_total{{instance=~"{node}.*"}}[5m])'  ), 4)

    # ── PROCESSUS — NOUVEAU ───────────────────────────────────────────────────
    m["procs_total"]   = int(_first_val(f'node_procs_running{{instance=~"{node}.*"}}') +
                              _first_val(f'node_procs_blocked{{instance=~"{node}.*"}}'  ))
    m["procs_running"] = int(_first_val(f'node_procs_running{{instance=~"{node}.*"}}'  ))
    m["procs_blocked"] = int(_first_val(f'node_procs_blocked{{instance=~"{node}.*"}}'  ))

    # File descriptors (si > 80% du max = risque "too many open files")
    fd_alloc = _first_val(f'node_filefd_allocated{{instance=~"{node}.*"}}')
    fd_max   = _first_val(f'node_filefd_maximum{{instance=~"{node}.*"}}'  )
    m["fd_used_pct"] = round((fd_alloc / fd_max * 100) if fd_max > 0 else 0, 2)

    # Uptime
    uptime_s = _first_val(f'pve_uptime_seconds{{node="{node}",type="node"}}')
    m["uptime_h"] = round(uptime_s / 3600, 1)

    # VMs sur ce nœud
    m["vms_running"] = int(_first_val(f'count(pve_cpu_usage_ratio{{node="{node}",type="qemu"}})'))

    return m


# ══════════════════════════════════════════════════════════════════════════════
# Métriques VM — COMPLÈTES
# ══════════════════════════════════════════════════════════════════════════════
def _metriques_vm(vmid: str, node: str = None, vm_type: str = "qemu") -> dict:
    nf = f',node="{node}"' if node else ""
    m  = {"vmid": vmid, "node": node or "unknown", "type": vm_type}

    # CPU
    r = _query(f'pve_cpu_usage_ratio{{vmid="{vmid}",type="{vm_type}"{nf}}}')
    if r:
        m["cpu_pct"] = round(_val(r[0]) * 100, 2)
        m["node"]    = r[0].get("metric", {}).get("node", node or "unknown")
        m["name"]    = r[0].get("metric", {}).get("name", f"vm-{vmid}")
    m["vcpus"] = int(_first_val(f'pve_cpu_usage_limit{{vmid="{vmid}",type="{vm_type}"{nf}}}'))

    # RAM
    ram_used  = _first_val(f'pve_memory_usage_bytes{{vmid="{vmid}",type="{vm_type}"{nf}}}')
    ram_total = _first_val(f'pve_memory_size_bytes{{vmid="{vmid}",type="{vm_type}"{nf}}}')
    m["ram_used_gb"]  = round(ram_used  / (1024**3), 2)
    m["ram_total_gb"] = round(ram_total / (1024**3), 2)
    m["ram_pct"]      = round((ram_used / ram_total * 100) if ram_total > 0 else 0, 2)

    # Disk
    disk_used  = _first_val(f'pve_disk_usage_bytes{{vmid="{vmid}",type="{vm_type}"{nf}}}')
    disk_total = _first_val(f'pve_disk_size_bytes{{vmid="{vmid}",type="{vm_type}"{nf}}}')
    m["disk_used_gb"]  = round(disk_used  / (1024**3), 2)
    m["disk_total_gb"] = round(disk_total / (1024**3), 2)
    m["disk_pct"]      = round((disk_used / disk_total * 100) if disk_total > 0 else 0, 2)

    # I/O Disque VM
    m["disk_read_iops"]  = round(_sum_query(f'rate(pve_disk_read_total{{vmid="{vmid}"{nf}}}[5m])'  ), 1)
    m["disk_write_iops"] = round(_sum_query(f'rate(pve_disk_write_total{{vmid="{vmid}"{nf}}}[5m])'  ), 1)

    # Réseau VM
    m["net_in_mbps"]  = round(_sum_query(f'rate(pve_network_receive_bytes_total{{vmid="{vmid}"{nf}}}[5m])')  / (1024**2), 3)
    m["net_out_mbps"] = round(_sum_query(f'rate(pve_network_transmit_bytes_total{{vmid="{vmid}"{nf}}}[5m])') / (1024**2), 3)

    # Uptime VM
    uptime_s = _first_val(f'pve_uptime_seconds{{vmid="{vmid}",type="{vm_type}"{nf}}}')
    m["uptime_h"] = round(uptime_s / 3600, 1)

    return m


# ══════════════════════════════════════════════════════════════════════════════
# Cluster Proxmox — Quorum + Corosync
# ══════════════════════════════════════════════════════════════════════════════
def _metriques_cluster() -> dict:
    m = {}

    # Quorum (via API Proxmox directe si configurée)
    if PROXMOX_HOST and PROXMOX_TOKEN and PROXMOX_SECRET:
        try:
            import urllib3
            urllib3.disable_warnings()
            r = requests.get(
                f"https://{PROXMOX_HOST}:8006/api2/json/cluster/status",
                headers={"Authorization": f"PVEAPIToken={PROXMOX_TOKEN}={PROXMOX_SECRET}"},
                verify=False,
                timeout=TIMEOUT
            )
            if r.status_code == 200:
                data = r.json().get("data", [])
                for item in data:
                    if item.get("type") == "cluster":
                        m["quorum_ok"]   = item.get("quorate", 0) == 1
                        m["nodes_total"] = item.get("nodes", 0)
                        m["nodes_online"]= item.get("nodes", 0)
                        m["cluster_name"]= item.get("name", "")
        except Exception as e:
            print(f"[Prometheus] Cluster status: {e}")

    # Nodes UP via Prometheus
    nodes_up = _query('sum(up{job=~".*proxmox.*|.*pve.*"})')
    m["nodes_prometheus_up"] = int(_val(nodes_up[0])) if nodes_up else 0

    return m


# ══════════════════════════════════════════════════════════════════════════════
# Collecte principale — TOUT
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# Température CPU — via node_exporter hwmon
# Critique : throttling automatique si > 85°C
# ══════════════════════════════════════════════════════════════════════════════
def _metriques_temperature(node: str) -> dict:
    """
    Collecte les températures CPU/système via node_exporter hwmon sensors.
    Requiert : node_exporter avec --collector.hwmon activé sur le nœud.
    
    Seuils officiels :
      > 75°C → IMPORTANT (attention)
      > 85°C → CRITIQUE  (throttling automatique du CPU)
      > 95°C → URGENT    (arrêt matériel imminent)
    """
    m = {}

    # Température CPU cores (hwmon via node_exporter)
    results = _query(
        f'node_hwmon_temp_celsius{{instance=~"{node}.*",chip=~".*coretemp.*|.*k10temp.*|.*cpu.*"}}' 
    )
    
    if results:
        temps = [_val(r) for r in results if _val(r) > 0]
        if temps:
            m["cpu_temp_max_c"]  = round(max(temps), 1)
            m["cpu_temp_avg_c"]  = round(sum(temps) / len(temps), 1)
            m["cpu_temp_cores"]  = len(temps)
        else:
            m["cpu_temp_max_c"]  = 0.0
            m["cpu_temp_avg_c"]  = 0.0
    else:
        # Fallback : chercher n'importe quel capteur de température
        results = _query(f'node_hwmon_temp_celsius{{instance=~"{node}.*"}}')
        temps = [_val(r) for r in results if 20 < _val(r) < 120]
        m["cpu_temp_max_c"] = round(max(temps), 1) if temps else 0.0
        m["cpu_temp_avg_c"] = round(sum(temps)/len(temps), 1) if temps else 0.0

    # Température critique hardware (seuil au-delà duquel le CPU throttle)
    results_crit = _query(
        f'node_hwmon_temp_crit_celsius{{instance=~"{node}.*"}}' 
    )
    m["cpu_temp_critical_c"] = round(_val(results_crit[0]), 1) if results_crit else 95.0

    # Température disques (via smartmon_exporter si installé)
    results_disk = _query(
        f'smartmon_temperature_celsius_raw_value{{instance=~"{node}.*"}}' 
    )
    disk_temps = [_val(r) for r in results_disk if _val(r) > 0]
    m["disk_temp_max_c"] = round(max(disk_temps), 1) if disk_temps else 0.0

    return m


# ══════════════════════════════════════════════════════════════════════════════
# Santé des disques — SMART via smartmon_exporter
# Critique : prédire une panne disque AVANT qu'elle arrive
# ══════════════════════════════════════════════════════════════════════════════
def _metriques_smart(node: str) -> dict:
    """
    Collecte les données SMART des disques via smartmon_exporter.
    Requiert : smartmon_exporter sur le nœud.
    
    Indicateurs SMART critiques :
      - Reallocated sectors > 0    → secteurs défectueux, disque mourant
      - Pending sectors > 0        → secteurs instables à réécrire
      - Uncorrectable errors > 0   → erreurs irrécupérables = URGENCE
      - Power on hours > 30000h    → disque vieux (environ 3.4 ans)
    """
    m = {
        "smart_ok":                  True,
        "smart_reallocated_sectors": 0,
        "smart_pending_sectors":     0,
        "smart_uncorrectable":       0,
        "smart_power_on_hours_max":  0,
        "smart_disks_monitored":     0,
    }

    # Secteurs réalloués (> 0 = disque en train de mourir)
    results = _query(f'smartmon_reallocated_sector_ct_raw_value{{instance=~"{node}.*"}}')
    if results:
        m["smart_disks_monitored"]     = len(results)
        m["smart_reallocated_sectors"] = int(sum(_val(r) for r in results))
        if m["smart_reallocated_sectors"] > 0:
            m["smart_ok"] = False

    # Secteurs en attente de réallocation
    results = _query(f'smartmon_current_pending_sector_raw_value{{instance=~"{node}.*"}}')
    m["smart_pending_sectors"] = int(sum(_val(r) for r in results))
    if m["smart_pending_sectors"] > 0:
        m["smart_ok"] = False

    # Erreurs irrécupérables
    results = _query(f'smartmon_offline_uncorrectable_raw_value{{instance=~"{node}.*"}}')
    m["smart_uncorrectable"] = int(sum(_val(r) for r in results))
    if m["smart_uncorrectable"] > 0:
        m["smart_ok"] = False

    # Heures de fonctionnement max (vieillissement)
    results = _query(f'smartmon_power_on_hours_raw_value{{instance=~"{node}.*"}}')
    if results:
        m["smart_power_on_hours_max"] = int(max(_val(r) for r in results))

    return m


# ══════════════════════════════════════════════════════════════════════════════
# Cache ZFS/ARC — spécifique Proxmox
# Critique : si ARC trop petit → toutes les I/O vont sur disque physique
# ══════════════════════════════════════════════════════════════════════════════
def _metriques_zfs(node: str) -> dict:
    """
    Collecte les métriques ZFS ARC (Adaptive Replacement Cache).
    Proxmox utilise ZFS par défaut — le cache ARC est crucial pour les perfs.
    
    Hit rate idéal : > 90%
    Hit rate < 70%  : ARC trop petit, ajouter de la RAM
    ARC size        : Proxmox alloue par défaut 50% de la RAM physique
    """
    m = {
        "zfs_arc_size_gb":    0.0,
        "zfs_arc_hit_rate":   0.0,
        "zfs_arc_miss_rate":  0.0,
        "zfs_arc_max_gb":     0.0,
        "zfs_available":      False,
    }

    # Taille ARC actuelle
    results = _query(f'node_zfs_arc_size{{instance=~"{node}.*"}}')
    if not results:
        # Essayer via procfs
        results = _query(f'node_zfs_arc_c{{instance=~"{node}.*"}}')
    
    if results:
        m["zfs_available"]  = True
        m["zfs_arc_size_gb"] = round(_val(results[0]) / (1024**3), 2)

    # Taille ARC max configurée
    results = _query(f'node_zfs_arc_c_max{{instance=~"{node}.*"}}')
    if results:
        m["zfs_arc_max_gb"] = round(_val(results[0]) / (1024**3), 2)

    # Hits (cache trouvé → pas d'accès disque)
    hits   = _first_val(f'rate(node_zfs_arc_hits{{instance=~"{node}.*"}}[5m])'  )
    misses = _first_val(f'rate(node_zfs_arc_misses{{instance=~"{node}.*"}}[5m])')
    total  = hits + misses

    if total > 0:
        m["zfs_arc_hit_rate"]  = round((hits   / total) * 100, 1)
        m["zfs_arc_miss_rate"] = round((misses / total) * 100, 1)

    return m


# ══════════════════════════════════════════════════════════════════════════════
# Santé cluster Proxmox — Corosync + Quorum
# Critique : perte quorum = arrêt automatique de TOUTES les VMs
# ══════════════════════════════════════════════════════════════════════════════
def _metriques_corosync(node: str) -> dict:
    """
    Vérifie la santé du cluster Proxmox via corosync_exporter.
    
    Quorum : nombre minimum de nœuds pour que le cluster fonctionne.
    Avec 2 nœuds → quorum = 2 → si 1 nœud tombe → cluster arrêté.
    
    Latence Corosync idéale : < 2ms entre nœuds
    > 10ms : instabilité possible
    > 50ms : perte de quorum imminente
    """
    m = {
        "corosync_ok":            True,
        "corosync_quorum_ok":     True,
        "corosync_members":       0,
        "corosync_members_ok":    0,
        "corosync_ring_latency_ms": 0.0,
        "corosync_available":     False,
    }

    # Membres du cluster
    results = _query(f'corosync_cluster_members_count{{instance=~"{node}.*"}}')
    if results:
        m["corosync_available"] = True
        m["corosync_members"]   = int(_val(results[0]))

    results = _query(f'corosync_cluster_members_votes{{instance=~"{node}.*"}}')
    if results:
        m["corosync_members_ok"] = int(_val(results[0]))

    # Quorum status
    results = _query(f'corosync_quorum_ok{{instance=~"{node}.*"}}')
    if results:
        m["corosync_quorum_ok"] = _val(results[0]) == 1
        if not m["corosync_quorum_ok"]:
            m["corosync_ok"] = False

    # Latence ring (communication inter-nœuds)
    results = _query(f'corosync_ring_avg_delay{{instance=~"{node}.*"}}')
    if results:
        m["corosync_ring_latency_ms"] = round(_val(results[0]) * 1000, 2)

    return m


# ══════════════════════════════════════════════════════════════════════════════
# Alimentation — IPMI/iDRAC via ipmi_exporter (si disponible)
# Important : détecter panne PSU avant coupure totale
# ══════════════════════════════════════════════════════════════════════════════
def _metriques_alimentation(node: str) -> dict:
    """
    Collecte les métriques d'alimentation via ipmi_exporter.
    Optionnel — uniquement sur serveurs avec IPMI/iDRAC/iLO.
    Non disponible sur VMs ou machines sans BMC.
    """
    m = {
        "power_watts":      0.0,
        "power_ok":         True,
        "ipmi_available":   False,
    }

    # Consommation électrique en watts
    results = _query(f'ipmi_power_watts{{instance=~"{node}.*"}}')
    if results:
        m["ipmi_available"] = True
        m["power_watts"]    = round(_val(results[0]), 1)

    # Statut PSU (Power Supply Unit)
    results = _query(f'ipmi_power_supply_status{{instance=~"{node}.*"}}')
    for r in results:
        if _val(r) != 1:  # 1 = OK, autre valeur = problème
            m["power_ok"] = False

    return m

def collecter_metriques_cluster() -> dict:
    """
    Collecte toutes les métriques :
    CPU, RAM, Disk, I/O, Swap, Réseau (avec erreurs/drops),
    Processus, Load average, Cluster Proxmox
    """
    timestamp = datetime.now().isoformat()

    noeuds_detectes = _decouvrir_noeuds()
    vms_detectees   = _decouvrir_vms()

    if noeuds_detectes:
        print(f"[Prometheus] Nœuds: {noeuds_detectes}")
    if vms_detectees:
        print(f"[Prometheus] VMs: {[v['vmid'] for v in vms_detectees]}")

    # Métriques nœuds — toutes les ressources
    metriques_noeuds = []
    for node in noeuds_detectes:
        try:
            m = _metriques_noeud(node)

            # Température CPU/Disque
            try:
                m.update(_metriques_temperature(node))
            except Exception as e:
                m["cpu_temp_max_c"] = 0.0

            # Santé disques SMART
            try:
                m.update(_metriques_smart(node))
            except Exception as e:
                m["smart_ok"] = True

            # Cache ZFS/ARC
            try:
                m.update(_metriques_zfs(node))
            except Exception as e:
                m["zfs_arc_hit_rate"] = 0.0

            # Cluster Corosync
            try:
                m.update(_metriques_corosync(node))
            except Exception as e:
                m["corosync_ok"] = True

            # Alimentation IPMI (optionnel)
            try:
                m.update(_metriques_alimentation(node))
            except Exception as e:
                m["power_watts"] = 0.0

            metriques_noeuds.append(m)
        except Exception as e:
            print(f"[Prometheus] Nœud {node}: {e}")

    # Métriques VMs
    metriques_vms = []
    for vm in vms_detectees:
        try:
            metriques_vms.append(_metriques_vm(vm["vmid"], vm.get("node"), vm.get("type","qemu")))
        except Exception as e:
            print(f"[Prometheus] VM {vm['vmid']}: {e}")

    # Métriques cluster
    cluster_info = _metriques_cluster()

    # Agrégations
    n = len(metriques_noeuds) or 1
    cluster = {
        **cluster_info,
        "total_nodes":            len(noeuds_detectes),
        "total_vms":              len(metriques_vms),
        # CPU
        "avg_cpu_pct":            round(sum(x.get("cpu_pct",0)         for x in metriques_noeuds) / n, 2),
        "avg_iowait_pct":         round(sum(x.get("cpu_iowait_pct",0)  for x in metriques_noeuds) / n, 2),
        # RAM & Swap
        "avg_ram_pct":            round(sum(x.get("ram_pct",0)         for x in metriques_noeuds) / n, 2),
        "avg_swap_pct":           round(sum(x.get("swap_pct",0)        for x in metriques_noeuds) / n, 2),
        # Disque
        "avg_disk_pct":           round(sum(x.get("disk_pct",0)        for x in metriques_noeuds) / n, 2),
        "total_disk_read_iops":   round(sum(x.get("disk_read_iops",0)  for x in metriques_noeuds), 1),
        "total_disk_write_iops":  round(sum(x.get("disk_write_iops",0) for x in metriques_noeuds), 1),
        "avg_read_latency_ms":    round(sum(x.get("disk_read_latency_ms",0)  for x in metriques_noeuds) / n, 2),
        "avg_write_latency_ms":   round(sum(x.get("disk_write_latency_ms",0) for x in metriques_noeuds) / n, 2),
        # Réseau
        "total_net_in_mbps":      round(sum(x.get("net_in_mbps",0)     for x in metriques_noeuds), 2),
        "total_net_out_mbps":     round(sum(x.get("net_out_mbps",0)    for x in metriques_noeuds), 2),
        "total_net_errors":       round(sum(x.get("net_errors_in",0)+x.get("net_errors_out",0) for x in metriques_noeuds), 4),
        "total_net_drops":        round(sum(x.get("net_drop_in",0)+x.get("net_drop_out",0)     for x in metriques_noeuds), 4),
        # Température
        "max_cpu_temp_c":         round(max((x.get("cpu_temp_max_c",0) for x in metriques_noeuds), default=0), 1),
        "max_disk_temp_c":        round(max((x.get("disk_temp_max_c",0) for x in metriques_noeuds), default=0), 1),
        # SMART
        "smart_all_ok":           all(x.get("smart_ok", True) for x in metriques_noeuds),
        "smart_reallocated_total":sum(x.get("smart_reallocated_sectors",0) for x in metriques_noeuds),
        # ZFS
        "avg_zfs_hit_rate":       round(sum(x.get("zfs_arc_hit_rate",0) for x in metriques_noeuds) / n, 1),
        "zfs_available":          any(x.get("zfs_available", False) for x in metriques_noeuds),
        # Corosync
        "corosync_ok":            all(x.get("corosync_ok", True) for x in metriques_noeuds),
        "corosync_quorum_ok":     all(x.get("corosync_quorum_ok", True) for x in metriques_noeuds),
        # Alimentation
        "total_power_watts":      round(sum(x.get("power_watts",0) for x in metriques_noeuds), 1),
    }

    return {
        "timestamp": timestamp,
        "noeuds":    metriques_noeuds,
        "vms":       metriques_vms,
        "cluster":   cluster,
    }


if __name__ == "__main__":
    import json
    print("Test collecte complète...")
    m = collecter_metriques_cluster()
    print(json.dumps(m, indent=2, ensure_ascii=False))