"""
etat_normalizer.py — Normalisation de l'etat brut du cluster (noeuds + VMs)
vers un format coherent, utilise par le reste de l'agent.

Extrait de surveillance.py (devenu trop long, ~700 lignes, plusieurs
responsabilites melangees) -- logique strictement identique, juste deplacee
dans son propre fichier pour que chaque module ait un role clair.

← CORRECTION : disk_read_iops/disk_write_iops (VMs) renommés en
disk_read_mbps/disk_write_mbps -- pve_exporter ne fournit qu'un débit en
octets/s par VM, jamais un vrai compte d'opérations. L'ancien nom laissait
croire à un vrai IOPS alors que les nombres (ex: 184801.5) étaient en
réalité des octets/s mal étiquetés. Correspond maintenant exactement aux
noms déjà produits par metriques_proxmox.py.

← AJOUT : cpu_steal_pct (noeuds) -- même traitement que cpu_iowait_pct
juste au-dessus, valeur par défaut 0 tant que la fusion Prometheus (dans
surveillance.py) n'a pas encore eu lieu ce cycle-ci. Sans ce champ par
défaut, tout code qui lirait n["cpu_steal_pct"] directement (plutôt que
n.get("cpu_steal_pct", 0)) risquerait un KeyError avant le premier cycle
Prometheus complet -- comportement cohérent avec tous les autres champs
niveau 2/3 de cette fonction, qui suivent tous ce même principe.
"""


def normaliser_etat(etat: dict) -> dict:
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
            "cpu_steal_pct":         float(n.get("cpu_steal_pct", 0)),
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
        "maxmem_gb":   float(v.get("ram_total_gb") or v.get("maxmem_gb") or (v.get("maxmem", 0) / 1e9)),
        "ram_used_gb": float(v.get("ram_used_gb") or 0),
        "ram_total_gb":float(v.get("ram_total_gb") or v.get("maxmem_gb") or (v.get("maxmem", 0) / 1e9)),
        "maxdisk_gb":  float(v.get("maxdisk_gb") or (v.get("maxdisk", 0) / 1e9)),
        "disk_used_gb":  float(v.get("disk_used_gb") or 0),
        "disk_total_gb": float(v.get("disk_total_gb") or 0),
        "disk_pct":      float(v.get("disk_pct") or 0),
        "disk_read_mbps":  float(v.get("disk_read_mbps") or 0),
        "disk_write_mbps": float(v.get("disk_write_mbps") or 0),
        "cpu_pct":     float(v.get("cpu_pct") or v.get("cpu", 0) * 100),
        "ram_pct":     float(v.get("ram_pct") or 0),
        "net_in_mbps": float(v.get("net_in_mbps") or 0),
        "net_out_mbps":float(v.get("net_out_mbps") or 0),
        "uptime_h":    float(v.get("uptime_h") or 0),
        "tags":        v.get("tags", ""),
        "services_detectes": v.get("services_detectes", []),
        "metriques_services": v.get("metriques_services", {}),
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