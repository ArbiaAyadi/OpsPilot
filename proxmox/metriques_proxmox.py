"""
metriques_proxmox.py — Collecte métriques complète via Prometheus + API Proxmox
Ressources monitorées :
  - CPU, RAM, Disk (existant)
  - I/O Disque (IOPS, latence, throughput)
  - Swap
  - Réseau (perte paquets, erreurs)
  - Processus (load, zombie, fd)
  - Cluster Proxmox (quorum, corosync)
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
    """
    ← CORRECTION MAJEURE : pve_exporter n'expose ni "vmid", ni "type", ni
    "name" comme labels séparés (confirmé par requête directe sur /pve) —
    tout est encodé dans un seul champ "id", au format "qemu/101" ou
    "lxc/205". L'ancien filtre type="qemu" ne correspondait donc jamais à
    rien : cette fonction n'a jamais retourné aucune VM depuis le début,
    silencieusement — indépendamment de tous les autres correctifs déjà
    appliqués sur les métriques individuelles.
    """
    vms = {}
    for r in _query('pve_cpu_usage_ratio'):
        m  = r.get("metric", {})
        rid = m.get("id", "")
        if "/" not in rid:
            continue
        vm_type, vmid = rid.split("/", 1)
        if vm_type not in ("qemu", "lxc") or not vmid:
            continue
        vms[vmid] = {
            "vmid": vmid,
            "name": f"vm-{vmid}",   # pve_exporter n'expose pas de nom réel ;
                                     # le vrai nom vient de proxmox_api.py,
                                     # déjà utilisé ailleurs dans le projet.
            "node": m.get("node", "unknown"),
            "type": vm_type,
        }
    return list(vms.values())


# ══════════════════════════════════════════════════════════════════════════════
# Métriques nœud — COMPLÈTES
# ══════════════════════════════════════════════════════════════════════════════
def _metriques_noeud(node: str) -> dict:
    m = {"node": node, "timestamp": datetime.now().isoformat()}

    # ── CPU (pve_exporter) ────────────────────────────────────────────────────
    # ← CORRECTION : "type" n'existe pas comme label sur cet exportateur
    # (confirmé par requête directe) — l'identifiant réel est le champ "id",
    # au format "node/pve1". Le label "node" existant est conservé en plus :
    # il indique quel exportateur a répondu (pve1 ou pve2, les deux voient
    # tout le cluster) — sans lui, _sum_query compterait la même valeur deux
    # fois, une fois par exportateur.
    m["cpu_pct"]   = round(_first_val(f'pve_cpu_usage_ratio{{id="node/{node}",node="{node}"}}') * 100, 2)
    m["cpu_cores"] = int(_first_val(f'pve_cpu_usage_limit{{id="node/{node}",node="{node}"}}'))

    # Load average — node_exporter. Le label "node" est posé par Prometheus
    # lui-même (fichier de découverte file_sd_configs généré par
    # pve_discovery.py), pas deviné depuis une IP : instance=~"{node}.*" ne
    # matchait jamais (node_exporter s'identifie par IP:port, pas par nom),
    # node="{node}" matche toujours puisque c'est exactement le label que
    # pve_discovery.py pose sur chaque cible.
    load1  = _first_val(f'node_load1{{node="{node}"}}')
    load5  = _first_val(f'node_load5{{node="{node}"}}')
    load15 = _first_val(f'node_load15{{node="{node}"}}')
    m["load_avg_1m"]  = round(load1,  2)
    m["load_avg_5m"]  = round(load5,  2)
    m["load_avg_15m"] = round(load15, 2)

    iowait = _first_val(f'rate(node_cpu_seconds_total{{node="{node}",mode="iowait"}}[5m])')
    m["cpu_iowait_pct"] = round(iowait * 100, 2)

    # ← AJOUT : CPU steal — % de temps où cette VM/ce nœud aurait voulu
    # utiliser le CPU mais l'hyperviseur physique (hôte VMware Workstation)
    # a donné le cœur à quelqu'un d'autre. Même requête que iowait, juste
    # mode="steal" au lieu de mode="iowait" : node_cpu_seconds_total expose
    # les deux modes de la même façon, aucune raison structurelle pour que
    # l'un fonctionne et pas l'autre. Distinction critique pour la
    # recommandation RAM/CPU : un steal élevé signifie que le problème est
    # la contention sur l'hôte physique, pas un manque de ressources
    # allouées à cette VM/ce nœud — ajouter du CPU virtuel n'y changerait
    # rien, contrairement à ce qu'un load average élevé pourrait suggérer
    # seul.
    steal = _first_val(f'rate(node_cpu_seconds_total{{node="{node}",mode="steal"}}[5m])')
    m["cpu_steal_pct"] = round(steal * 100, 2)

    # ── RAM (pve_exporter — même correction que CPU ci-dessus) ───────────────
    ram_used  = _first_val(f'pve_memory_usage_bytes{{id="node/{node}",node="{node}"}}')
    ram_total = _first_val(f'pve_memory_size_bytes{{id="node/{node}",node="{node}"}}')
    m["ram_used_gb"]  = round(ram_used  / (1024**3), 2)
    m["ram_total_gb"] = round(ram_total / (1024**3), 2)
    m["ram_pct"]      = round((ram_used / ram_total * 100) if ram_total > 0 else 0, 2)

    # ── SWAP ─────────────────────────────────────────────────────────────────
    swap_total = _first_val(f'node_memory_SwapTotal_bytes{{node="{node}"}}')
    swap_free  = _first_val(f'node_memory_SwapFree_bytes{{node="{node}"}}')
    swap_used  = max(0, swap_total - swap_free)
    m["swap_total_gb"] = round(swap_total / (1024**3), 2)
    m["swap_used_gb"]  = round(swap_used  / (1024**3), 2)
    m["swap_pct"]      = round((swap_used / swap_total * 100) if swap_total > 0 else 0, 2)

    m["swap_in_rate"]  = round(_first_val(f'rate(node_vmstat_pswpin{{node="{node}"}}[5m])'  ), 2)
    m["swap_out_rate"] = round(_first_val(f'rate(node_vmstat_pswpout{{node="{node}"}}[5m])'  ), 2)

    # ── DISQUE nœud (pve_exporter — même correction que CPU/RAM) ─────────────
    disk_used  = _first_val(f'pve_disk_usage_bytes{{id="node/{node}",node="{node}"}}')
    disk_total = _first_val(f'pve_disk_size_bytes{{id="node/{node}",node="{node}"}}')
    m["disk_used_gb"]  = round(disk_used  / (1024**3), 2)
    m["disk_total_gb"] = round(disk_total / (1024**3), 2)
    m["disk_pct"]      = round((disk_used / disk_total * 100) if disk_total > 0 else 0, 2)

    # ── I/O DISQUE (node_exporter) ───────────────────────────────────────────
    m["disk_read_iops"]  = round(_sum_query(f'rate(node_disk_reads_completed_total{{node="{node}"}}[5m])'  ), 1)
    m["disk_write_iops"] = round(_sum_query(f'rate(node_disk_writes_completed_total{{node="{node}"}}[5m])'  ), 1)

    m["disk_read_mbps"]  = round(_sum_query(f'rate(node_disk_read_bytes_total{{node="{node}"}}[5m])')  / (1024**2), 2)
    m["disk_write_mbps"] = round(_sum_query(f'rate(node_disk_written_bytes_total{{node="{node}"}}[5m])') / (1024**2), 2)

    read_lat  = _sum_query(f'rate(node_disk_read_time_seconds_total{{node="{node}"}}[5m])')
    write_lat = _sum_query(f'rate(node_disk_write_time_seconds_total{{node="{node}"}}[5m])')
    read_ops  = max(_sum_query(f'rate(node_disk_reads_completed_total{{node="{node}"}}[5m])'  ), 0.001)
    write_ops = max(_sum_query(f'rate(node_disk_writes_completed_total{{node="{node}"}}[5m])'  ), 0.001)
    m["disk_read_latency_ms"]  = round((read_lat  / read_ops)  * 1000, 2)
    m["disk_write_latency_ms"] = round((write_lat / write_ops) * 1000, 2)

    # ── RÉSEAU nœud — basculé de pve_exporter vers node_exporter ─────────────
    # Confirmé par requête directe : pve_network_receive_bytes_total n'existe
    # qu'avec id="qemu/..." — jamais id="node/...". pve_exporter n'expose
    # simplement pas le débit réseau au niveau nœud, contrairement au niveau
    # VM où ça fonctionne déjà. Cible vmbr0 précisément (confirmé présent par
    # curl sur pve1) — PAS un simple device!="lo" : un paquet à destination
    # d'une VM traverse tap101i0 → fwbr101i0 → fwln101i0/fwpr101p0 → vmbr0 →
    # nic0, cinq interfaces pour un seul paquet. Sommer tout sauf lo aurait
    # compté chaque paquet plusieurs fois. vmbr0 est le pont qui porte l'IP
    # du nœud lui-même (192.168.138.100) — la référence standard Proxmox
    # pour mesurer le trafic d'un nœud, une seule interface, pas de somme.
    m["net_in_mbps"]  = round(_first_val(f'rate(node_network_receive_bytes_total{{node="{node}",device="vmbr0"}}[5m])')  / (1024**2), 3)
    m["net_out_mbps"] = round(_first_val(f'rate(node_network_transmit_bytes_total{{node="{node}",device="vmbr0"}}[5m])') / (1024**2), 3)

    # ── RÉSEAU AVANCÉ (node_exporter) ────────────────────────────────────────
    m["net_errors_in"]  = round(_sum_query(f'rate(node_network_receive_errs_total{{node="{node}"}}[5m])'  ), 4)
    m["net_errors_out"] = round(_sum_query(f'rate(node_network_transmit_errs_total{{node="{node}"}}[5m])'  ), 4)
    m["net_drop_in"]  = round(_sum_query(f'rate(node_network_receive_drop_total{{node="{node}"}}[5m])'  ), 4)
    m["net_drop_out"] = round(_sum_query(f'rate(node_network_transmit_drop_total{{node="{node}"}}[5m])'  ), 4)

    # ── PROCESSUS (node_exporter) ────────────────────────────────────────────
    m["procs_total"]   = int(_first_val(f'node_procs_running{{node="{node}"}}') +
                              _first_val(f'node_procs_blocked{{node="{node}"}}'  ))
    m["procs_running"] = int(_first_val(f'node_procs_running{{node="{node}"}}'  ))
    m["procs_blocked"] = int(_first_val(f'node_procs_blocked{{node="{node}"}}'  ))

    fd_alloc = _first_val(f'node_filefd_allocated{{node="{node}"}}')
    fd_max   = _first_val(f'node_filefd_maximum{{node="{node}"}}'  )
    m["fd_used_pct"] = round((fd_alloc / fd_max * 100) if fd_max > 0 else 0, 2)

    uptime_s = _first_val(f'pve_uptime_seconds{{id="node/{node}",node="{node}"}}')
    m["uptime_h"] = round(uptime_s / 3600, 1)

    # vms_running : compte les entrées dont "id" commence par "qemu/" ou
    # "lxc/", rapportées par CE nœud spécifiquement (node="{node}" évite de
    # compter les VMs vues par l'exportateur d'un autre nœud du cluster).
    m["vms_running"] = int(_first_val(
        f'count(pve_cpu_usage_ratio{{id=~"qemu/.*|lxc/.*",node="{node}"}})'
    ))

    return m


# ══════════════════════════════════════════════════════════════════════════════
# Métriques VM — COMPLÈTES
# ══════════════════════════════════════════════════════════════════════════════
def _metriques_vm(vmid: str, node: str = None, vm_type: str = "qemu") -> dict:
    nf = f',node="{node}"' if node else ""
    m  = {"vmid": vmid, "node": node or "unknown", "type": vm_type, "name": f"vm-{vmid}"}

    # CPU — ← CORRECTION MAJEURE : ni "vmid" ni "type" n'existent comme labels
    # sur pve_exporter (confirmé par requête directe) — seul "id" existe, au
    # format "qemu/101". nf ajoute ",node=..." quand connu, pour éviter de
    # compter deux fois la même VM vue par les deux exportateurs du cluster.
    r = _query(f'pve_cpu_usage_ratio{{id="{vm_type}/{vmid}"{nf}}}')
    if r:
        m["cpu_pct"] = round(_val(r[0]) * 100, 2)
        m["node"]    = r[0].get("metric", {}).get("node", node or "unknown")
    m["vcpus"] = int(_first_val(f'pve_cpu_usage_limit{{id="{vm_type}/{vmid}"{nf}}}'))

    # RAM
    ram_used  = _first_val(f'pve_memory_usage_bytes{{id="{vm_type}/{vmid}"{nf}}}')
    ram_total = _first_val(f'pve_memory_size_bytes{{id="{vm_type}/{vmid}"{nf}}}')
    m["ram_used_gb"]  = round(ram_used  / (1024**3), 2)
    m["ram_total_gb"] = round(ram_total / (1024**3), 2)
    m["ram_pct"]      = round((ram_used / ram_total * 100) if ram_total > 0 else 0, 2)

    # Disque — via node_exporter DANS la VM. Repose maintenant sur le label
    # "vmid" (posé par pve_discovery.py sur chaque cible node_exporter d'une
    # VM) plutôt que sur le nom — élimine toute dépendance à une
    # correspondance exacte de chaîne entre deux sources différentes
    # (pve_exporter et pve_discovery.py), qui pouvait diverger sans bruit.
    # vmid est un identifiant numérique déjà disponible ici, sans comparaison.
    disk_total = _first_val(f'node_filesystem_size_bytes{{vmid="{vmid}",mountpoint="/"}}')
    disk_avail = _first_val(f'node_filesystem_avail_bytes{{vmid="{vmid}",mountpoint="/"}}')
    disk_used  = max(0.0, disk_total - disk_avail)
    m["disk_used_gb"]  = round(disk_used  / (1024**3), 2)
    m["disk_total_gb"] = round(disk_total / (1024**3), 2)
    m["disk_pct"]      = round((disk_used / disk_total * 100) if disk_total > 0 else 0, 2)

    # I/O Disque VM — ← CORRECTION FINALE : renommé disk_read_iops/write_iops
    # → disk_read_mbps/write_mbps, ET converti en Mo/s (division par 1024**2,
    # oubliée volontairement avant pour ne pas casser trois fichiers d'un
    # coup). pve_exporter ne fournit qu'un débit en octets/s par VM, jamais
    # un vrai compte d'opérations — contrairement à node_exporter au niveau
    # nœud, qui donne un vrai IOPS. Garder l'ancien nom aurait affiché un
    # débit sous une étiquette "IOPS", avec des nombres à 5-6 chiffres
    # (confirmé dans le dashboard : 184801.5, 58477.1) qui semblent alarmants
    # sans l'être — juste une mauvaise unité. Le nom correspond maintenant
    # exactement à disk_read_mbps/write_mbps déjà utilisés au niveau nœud
    # dans ce même fichier, pour une convention cohérente partout.
    m["disk_read_mbps"]  = round(_sum_query(f'rate(pve_disk_read_bytes_total{{id="{vm_type}/{vmid}"{nf}}}[5m])'    ) / (1024**2), 3)
    m["disk_write_mbps"] = round(_sum_query(f'rate(pve_disk_written_bytes_total{{id="{vm_type}/{vmid}"{nf}}}[5m])' ) / (1024**2), 3)

    # Réseau VM — même correction
    m["net_in_mbps"]  = round(_sum_query(f'rate(pve_network_receive_bytes_total{{id="{vm_type}/{vmid}"{nf}}}[5m])')  / (1024**2), 3)
    m["net_out_mbps"] = round(_sum_query(f'rate(pve_network_transmit_bytes_total{{id="{vm_type}/{vmid}"{nf}}}[5m])') / (1024**2), 3)

    uptime_s = _first_val(f'pve_uptime_seconds{{id="{vm_type}/{vmid}"{nf}}}')
    m["uptime_h"] = round(uptime_s / 3600, 1)

    return m


# ══════════════════════════════════════════════════════════════════════════════
# Cluster Proxmox — Quorum + Corosync
# ══════════════════════════════════════════════════════════════════════════════
def _metriques_cluster() -> dict:
    m = {}

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

    nodes_up = _query('sum(up{job=~".*proxmox.*|.*pve.*"})')
    m["nodes_prometheus_up"] = int(_val(nodes_up[0])) if nodes_up else 0

    return m


# ══════════════════════════════════════════════════════════════════════════════
# Température CPU — via node_exporter hwmon
# ══════════════════════════════════════════════════════════════════════════════
def _metriques_temperature(node: str) -> dict:
    """
    Requiert node_exporter --collector.hwmon sur le nœud (label "node" posé
    par pve_discovery.py, cf. _metriques_noeud). Dans une VM VMware imbriquée,
    hwmon peut simplement n'exposer aucun capteur réel — à vérifier une fois
    la cible confirmée UP, sans trop y compter.
    """
    m = {}

    results = _query(
        f'node_hwmon_temp_celsius{{node="{node}",chip=~".*coretemp.*|.*k10temp.*|.*cpu.*"}}'
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
        results = _query(f'node_hwmon_temp_celsius{{node="{node}"}}')
        temps = [_val(r) for r in results if 20 < _val(r) < 120]
        m["cpu_temp_max_c"] = round(max(temps), 1) if temps else 0.0
        m["cpu_temp_avg_c"] = round(sum(temps)/len(temps), 1) if temps else 0.0

    results_crit = _query(f'node_hwmon_temp_crit_celsius{{node="{node}"}}')
    m["cpu_temp_critical_c"] = round(_val(results_crit[0]), 1) if results_crit else 95.0

    # smartmon_exporter n'est pas encore déployé (cf. audit) — cette ligne
    # reste à 0 tant qu'il ne l'est pas, ce fix ne la corrige pas à lui seul.
    results_disk = _query(f'smartmon_temperature_celsius_raw_value{{node="{node}"}}')
    disk_temps = [_val(r) for r in results_disk if _val(r) > 0]
    m["disk_temp_max_c"] = round(max(disk_temps), 1) if disk_temps else 0.0

    return m


# ══════════════════════════════════════════════════════════════════════════════
# Santé des disques — SMART via smartmon_exporter (exportateur non déployé)
# ══════════════════════════════════════════════════════════════════════════════
def _metriques_smart(node: str) -> dict:
    """
    Nécessite smartmon_exporter — pas encore installé/scrapé. Restera à 0
    jusque-là, et probablement structurellement non pertinent ici : les
    disques de pve1/pve2 sont des vmdk VMware, pas des disques physiques.
    """
    m = {
        "smart_ok": True, "smart_reallocated_sectors": 0, "smart_pending_sectors": 0,
        "smart_uncorrectable": 0, "smart_power_on_hours_max": 0, "smart_disks_monitored": 0,
    }

    results = _query(f'smartmon_reallocated_sector_ct_raw_value{{node="{node}"}}')
    if results:
        m["smart_disks_monitored"]     = len(results)
        m["smart_reallocated_sectors"] = int(sum(_val(r) for r in results))
        if m["smart_reallocated_sectors"] > 0:
            m["smart_ok"] = False

    results = _query(f'smartmon_current_pending_sector_raw_value{{node="{node}"}}')
    m["smart_pending_sectors"] = int(sum(_val(r) for r in results))
    if m["smart_pending_sectors"] > 0:
        m["smart_ok"] = False

    results = _query(f'smartmon_offline_uncorrectable_raw_value{{node="{node}"}}')
    m["smart_uncorrectable"] = int(sum(_val(r) for r in results))
    if m["smart_uncorrectable"] > 0:
        m["smart_ok"] = False

    results = _query(f'smartmon_power_on_hours_raw_value{{node="{node}"}}')
    if results:
        m["smart_power_on_hours_max"] = int(max(_val(r) for r in results))

    return m


# ══════════════════════════════════════════════════════════════════════════════
# Cache ZFS/ARC — spécifique Proxmox
# ══════════════════════════════════════════════════════════════════════════════
def _metriques_zfs(node: str) -> dict:
    """
    Hit rate idéal > 90%. Ce cluster utilise LVM-thin, pas ZFS (confirmé via
    lvs -a) — zfs_available restera donc False ici, correctement, grâce au
    garde-fou déjà en place ci-dessous. Rien à corriger sur cette fonction.
    """
    m = {
        "zfs_arc_size_gb": 0.0, "zfs_arc_hit_rate": 0.0, "zfs_arc_miss_rate": 0.0,
        "zfs_arc_max_gb": 0.0, "zfs_available": False,
    }

    results = _query(f'node_zfs_arc_size{{node="{node}"}}')
    if not results:
        results = _query(f'node_zfs_arc_c{{node="{node}"}}')
    if results:
        taille = round(_val(results[0]) / (1024**3), 2)
        m["zfs_arc_size_gb"] = taille
        # zfs_available = True seulement si le cache ARC contient réellement
        # des données. Le module kernel ZFS de Proxmox VE expose toujours
        # node_zfs_arc_size même sans pool actif (LVM-thin uniquement) —
        # sans ce garde-fou, "ZFS available" est un faux positif systématique.
        m["zfs_available"] = taille > 0.01

    results = _query(f'node_zfs_arc_c_max{{node="{node}"}}')
    if results:
        m["zfs_arc_max_gb"] = round(_val(results[0]) / (1024**3), 2)

    hits   = _first_val(f'rate(node_zfs_arc_hits{{node="{node}"}}[5m])'  )
    misses = _first_val(f'rate(node_zfs_arc_misses{{node="{node}"}}[5m])')
    total  = hits + misses
    if total > 0:
        m["zfs_arc_hit_rate"]  = round((hits   / total) * 100, 1)
        m["zfs_arc_miss_rate"] = round((misses / total) * 100, 1)

    return m


# ══════════════════════════════════════════════════════════════════════════════
# Santé cluster Proxmox — Corosync + Quorum
# ══════════════════════════════════════════════════════════════════════════════
def _metriques_corosync(node: str) -> dict:
    """
    ← RÉÉCRIT ENTIÈREMENT : les noms d'origine (corosync_cluster_members_count,
    corosync_quorum_ok, corosync_ring_avg_delay...) n'ont jamais existé --
    supposés avant qu'un vrai exportateur ne soit installé, jamais vérifiés.
    Confirmé par requête directe sur pve1 (paquet Debian
    prometheus-hacluster-exporter, service systemd ha_cluster_exporter,
    port 9664) : tout est préfixé "ha_cluster_corosync_", pas "corosync_".

    Différence honnête à connaître : cet exportateur n'expose PAS de latence
    d'anneau (aucun équivalent à corosync_ring_avg_delay) -- seulement un
    compte d'erreurs (ha_cluster_corosync_ring_errors). corosync_ring_latency_ms
    reste donc à 0.0 en permanence, pas par bug, par absence réelle de cette
    donnée précise chez cet exportateur.
    """
    m = {
        "corosync_ok": True, "corosync_quorum_ok": True, "corosync_members": 0,
        "corosync_members_ok": 0, "corosync_ring_latency_ms": 0.0, "corosync_available": False,
        "corosync_ring_errors": 0,
    }

    # Un membre = une série avec un label "node" (celui du membre, ex.
    # local="true"/"false") ; compter les séries donne le nombre de membres,
    # sommer leurs votes donne le total de votes actifs pour CE nœud.
    results = _query(f'ha_cluster_corosync_member_votes{{node="{node}"}}')
    if results:
        m["corosync_available"]  = True
        m["corosync_members"]    = len(results)
        m["corosync_members_ok"] = int(sum(_val(r) for r in results))

    results = _query(f'ha_cluster_corosync_quorate{{node="{node}"}}')
    if results:
        m["corosync_quorum_ok"] = _val(results[0]) == 1
        if not m["corosync_quorum_ok"]:
            m["corosync_ok"] = False

    results = _query(f'ha_cluster_corosync_ring_errors{{node="{node}"}}')
    if results:
        m["corosync_ring_errors"] = int(_val(results[0]))
        if m["corosync_ring_errors"] > 0:
            m["corosync_ok"] = False

    return m


# ══════════════════════════════════════════════════════════════════════════════
# Alimentation — IPMI (structurellement non applicable ici)
# ══════════════════════════════════════════════════════════════════════════════
def _metriques_alimentation(node: str) -> dict:
    """
    Nécessite ipmi_exporter ET un vrai contrôleur IPMI/BMC physique. pve1 et
    pve2 sont des VMs VMware Workstation, pas des serveurs physiques — cette
    fonction restera à 0 en permanence, structurellement, comme les alertes
    GPU SVGA II déjà supprimées pour la même raison.
    """
    m = {"power_watts": 0.0, "power_ok": True, "ipmi_available": False}

    results = _query(f'ipmi_power_watts{{node="{node}"}}')
    if results:
        m["ipmi_available"] = True
        m["power_watts"]    = round(_val(results[0]), 1)

    results = _query(f'ipmi_power_supply_status{{node="{node}"}}')
    for r in results:
        if _val(r) != 1:
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

    metriques_noeuds = []
    for node in noeuds_detectes:
        try:
            m = _metriques_noeud(node)
            try:
                m.update(_metriques_temperature(node))
            except Exception:
                m["cpu_temp_max_c"] = 0.0
            try:
                m.update(_metriques_smart(node))
            except Exception:
                m["smart_ok"] = True
            try:
                m.update(_metriques_zfs(node))
            except Exception:
                m["zfs_arc_hit_rate"] = 0.0
            try:
                m.update(_metriques_corosync(node))
            except Exception:
                m["corosync_ok"] = True
            try:
                m.update(_metriques_alimentation(node))
            except Exception:
                m["power_watts"] = 0.0
            metriques_noeuds.append(m)
        except Exception as e:
            print(f"[Prometheus] Nœud {node}: {e}")

    metriques_vms = []
    for vm in vms_detectees:
        try:
            metriques_vms.append(_metriques_vm(vm["vmid"], vm.get("node"), vm.get("type","qemu")))
        except Exception as e:
            print(f"[Prometheus] VM {vm['vmid']}: {e}")

    cluster_info = _metriques_cluster()

    n = len(metriques_noeuds) or 1
    cluster = {
        **cluster_info,
        "total_nodes":            len(noeuds_detectes),
        "total_vms":              len(metriques_vms),
        "avg_cpu_pct":            round(sum(x.get("cpu_pct",0)         for x in metriques_noeuds) / n, 2),
        "avg_iowait_pct":         round(sum(x.get("cpu_iowait_pct",0)  for x in metriques_noeuds) / n, 2),
        "avg_steal_pct":          round(sum(x.get("cpu_steal_pct",0)   for x in metriques_noeuds) / n, 2),
        "avg_ram_pct":            round(sum(x.get("ram_pct",0)         for x in metriques_noeuds) / n, 2),
        "avg_swap_pct":           round(sum(x.get("swap_pct",0)        for x in metriques_noeuds) / n, 2),
        "avg_disk_pct":           round(sum(x.get("disk_pct",0)        for x in metriques_noeuds) / n, 2),
        "total_disk_read_iops":   round(sum(x.get("disk_read_iops",0)  for x in metriques_noeuds), 1),
        "total_disk_write_iops":  round(sum(x.get("disk_write_iops",0) for x in metriques_noeuds), 1),
        "avg_read_latency_ms":    round(sum(x.get("disk_read_latency_ms",0)  for x in metriques_noeuds) / n, 2),
        "avg_write_latency_ms":   round(sum(x.get("disk_write_latency_ms",0) for x in metriques_noeuds) / n, 2),
        "total_net_in_mbps":      round(sum(x.get("net_in_mbps",0)     for x in metriques_noeuds), 2),
        "total_net_out_mbps":     round(sum(x.get("net_out_mbps",0)    for x in metriques_noeuds), 2),
        "total_net_errors":       round(sum(x.get("net_errors_in",0)+x.get("net_errors_out",0) for x in metriques_noeuds), 4),
        "total_net_drops":        round(sum(x.get("net_drop_in",0)+x.get("net_drop_out",0)     for x in metriques_noeuds), 4),
        "max_cpu_temp_c":         round(max((x.get("cpu_temp_max_c",0) for x in metriques_noeuds), default=0), 1),
        "max_disk_temp_c":        round(max((x.get("disk_temp_max_c",0) for x in metriques_noeuds), default=0), 1),
        "smart_all_ok":           all(x.get("smart_ok", True) for x in metriques_noeuds),
        "smart_reallocated_total":sum(x.get("smart_reallocated_sectors",0) for x in metriques_noeuds),
        "avg_zfs_hit_rate":       round(sum(x.get("zfs_arc_hit_rate",0) for x in metriques_noeuds) / n, 1),
        "zfs_available":          any(x.get("zfs_available", False) for x in metriques_noeuds),
        "corosync_ok":            all(x.get("corosync_ok", True) for x in metriques_noeuds),
        "corosync_quorum_ok":     all(x.get("corosync_quorum_ok", True) for x in metriques_noeuds),
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