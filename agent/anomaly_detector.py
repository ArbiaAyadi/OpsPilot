
def detecter_anomalies(etat: dict, etat_prec: dict) -> list:
    """
    Compare l'etat actuel avec le precedent et retourne
    la liste des anomalies detectees.
    """
    anomalies = []

    # ── Alertes Proxmox API ───────────────────────────────────────────────────
    alertes_prec = {a["message"] for a in etat_prec.get("alertes", [])}
    for a in etat.get("alertes", []):
        if a.get("message") and a["message"] not in alertes_prec:
            anomalies.append(a)

    # ── VMs stoppees inopinement ──────────────────────────────────────────────
    vms_avant = {v["vmid"]: v for v in etat_prec.get("vms", [])}
    for vm in etat.get("vms", []):
        vid = vm["vmid"]
        if (vid in vms_avant
                and vms_avant[vid]["statut"] == "running"
                and vm["statut"] == "stopped"):
            anomalies.append({
                "niveau":  "CRITIQUE",
                "cible":   vm["nom"],
                "message": f"VM {vm['nom']} (ID:{vid}) stoppee inopinement sur {vm['noeud']}",
                "type":    "vm_down",
            })

    # ── Verifications par noeud ───────────────────────────────────────────────
    for noeud in etat.get("noeuds", []):
        nom = noeud.get("nom", noeud.get("node", "?"))

        # NIVEAU 1 - CPU / RAM / Disk
        cpu = noeud.get("cpu_pct", 0)
        if cpu > 80:
            anomalies.append({"niveau": "CRITIQUE",  "cible": nom, "message": f"Node {nom} -- CPU critical: {cpu:.1f}%"})
        elif cpu > 65:
            anomalies.append({"niveau": "IMPORTANT", "cible": nom, "message": f"Node {nom} -- CPU high: {cpu:.1f}%"})

        ram = noeud.get("ram_pct", 0)
        if ram > 85:
            anomalies.append({"niveau": "CRITIQUE",  "cible": nom, "message": f"Node {nom} -- RAM critical: {ram:.1f}% ({noeud.get('ram_used_gb',0)}/{noeud.get('ram_total_gb',0)}GB)"})
        elif ram > 75:
            anomalies.append({"niveau": "IMPORTANT", "cible": nom, "message": f"Node {nom} -- RAM high: {ram:.1f}%"})

        disk = noeud.get("disk_pct", 0)
        if disk > 90:
            anomalies.append({"niveau": "CRITIQUE",  "cible": nom, "message": f"Node {nom} -- Disk critical: {disk:.1f}% ({noeud.get('disk_used_gb',0)}/{noeud.get('disk_total_gb',0)}GB)"})
        elif disk > 80:
            anomalies.append({"niveau": "IMPORTANT", "cible": nom, "message": f"Node {nom} -- Disk high: {disk:.1f}%"})

        # NIVEAU 2 - Swap
        swap = noeud.get("swap_pct", 0)
        if swap > 80:
            anomalies.append({"niveau": "CRITIQUE",  "cible": nom, "message": f"Node {nom} -- Swap critical: {swap:.1f}% (RAM already saturated)"})
        elif swap > 50:
            anomalies.append({"niveau": "IMPORTANT", "cible": nom, "message": f"Node {nom} -- Swap high: {swap:.1f}% (memory pressure)"})

        # NIVEAU 2 - I/O wait
        iowait = noeud.get("cpu_iowait_pct", 0)
        if iowait > 30:
            anomalies.append({"niveau": "CRITIQUE",  "cible": nom, "message": f"Node {nom} -- CPU I/O wait critical: {iowait:.1f}% (storage bottleneck)"})
        elif iowait > 15:
            anomalies.append({"niveau": "IMPORTANT", "cible": nom, "message": f"Node {nom} -- CPU I/O wait high: {iowait:.1f}%"})

        # NIVEAU 2 - Latence disque
        rl = noeud.get("disk_read_latency_ms", 0)
        wl = noeud.get("disk_write_latency_ms", 0)
        if rl > 50 or wl > 50:
            anomalies.append({"niveau": "CRITIQUE",  "cible": nom, "message": f"Node {nom} -- Disk latency critical: read={rl}ms write={wl}ms"})
        elif rl > 10 or wl > 10:
            anomalies.append({"niveau": "IMPORTANT", "cible": nom, "message": f"Node {nom} -- Disk latency high: read={rl}ms write={wl}ms"})

        # NIVEAU 2 - Erreurs reseau
        net_err  = noeud.get("net_errors_in", 0) + noeud.get("net_errors_out", 0)
        net_drop = noeud.get("net_drop_in", 0)   + noeud.get("net_drop_out", 0)
        if net_err > 10:
            anomalies.append({"niveau": "IMPORTANT", "cible": nom, "message": f"Node {nom} -- Network errors: {net_err:.0f}/s (check NIC or cable)"})
        if net_drop > 10:
            anomalies.append({"niveau": "IMPORTANT", "cible": nom, "message": f"Node {nom} -- Packet drops: {net_drop:.0f}/s (network saturation)"})

        # NIVEAU 3 - Temperature CPU
        temp = noeud.get("cpu_temp_max_c", 0)
        if temp > 85:
            anomalies.append({"niveau": "CRITIQUE",  "cible": nom, "message": f"Node {nom} -- CPU temperature critical: {temp}C (throttling risk)"})
        elif temp > 75:
            anomalies.append({"niveau": "IMPORTANT", "cible": nom, "message": f"Node {nom} -- CPU temperature high: {temp}C"})

        # NIVEAU 3 - SMART disques
        if not noeud.get("smart_ok", True):
            uncorr  = noeud.get("smart_uncorrectable", 0)
            realloc = noeud.get("smart_reallocated_sectors", 0)
            pending = noeud.get("smart_pending_sectors", 0)
            if uncorr > 0:
                anomalies.append({"niveau": "CRITIQUE",  "cible": nom, "message": f"Node {nom} -- DISK FAILURE IMMINENT: {uncorr} uncorrectable errors (backup now!)"})
            elif realloc > 0:
                anomalies.append({"niveau": "CRITIQUE",  "cible": nom, "message": f"Node {nom} -- Disk degraded: {realloc} reallocated sectors (plan replacement)"})
            elif pending > 0:
                anomalies.append({"niveau": "IMPORTANT", "cible": nom, "message": f"Node {nom} -- Disk: {pending} pending sectors"})

        # NIVEAU 3 - ZFS ARC
        zfs_hit = noeud.get("zfs_arc_hit_rate", 0)
        if noeud.get("zfs_available") and 0 < zfs_hit < 70:
            anomalies.append({"niveau": "IMPORTANT", "cible": nom, "message": f"Node {nom} -- ZFS ARC hit rate low: {zfs_hit}% (add RAM for better I/O)"})

    # NIVEAU 3 - Corosync quorum
    if not etat.get("cluster", {}).get("corosync_quorum_ok", True):
        anomalies.append({
            "niveau":  "CRITIQUE",
            "cible":   "cluster",
            "message": "CLUSTER QUORUM LOST -- All VMs at risk of automatic shutdown",
        })

    return anomalies