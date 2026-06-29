from agent.groq_client import GROQ_MODEL


def system_prompt_chat(etat: dict = None, dernier_lstm: dict = None,
                        pc_hote: dict = None) -> str:
    """
    Prompt pour le chat — injecte :
    - Ressources PC hôte Windows (metriques_pc_hote.py)
    - Etat cluster Proxmox niveaux 1+2+3
    """

    # ── Bloc PC Hôte ─────────────────────────────────────────────────────────
    pc_bloc = ""
    if pc_hote and pc_hote.get("disponible"):
        alloc   = pc_hote.get("vmware_allocation", {})
        pve1_r  = alloc.get("pve1", {})
        pve2_r  = alloc.get("pve2", {})
        reserve = alloc.get("windows_reserve", {})
        pc_bloc = f"""
## HOST PC RESOURCES (Windows — VMware Workstation host)
  RAM   : {pc_hote.get('ram_total_gb','?')}GB total | {pc_hote.get('ram_used_gb','?')}GB used | {pc_hote.get('ram_available_gb','?')}GB free
  CPU   : {pc_hote.get('cpu_cores','?')} cores | {pc_hote.get('cpu_pct_used','?')}% used
  Disk  : {pc_hote.get('disk_total_gb','?')}GB total | {pc_hote.get('disk_used_gb','?')}GB used | {pc_hote.get('disk_free_gb','?')}GB free

## OPTIMAL VMWARE ALLOCATION (safe — keeps {reserve.get('ram_gb','4')}GB RAM + {reserve.get('cores','2')} cores for Windows)
  pve1 (60%) : {pve1_r.get('ram_recommended_gb','?')}GB RAM ({pve1_r.get('ram_mb','?')}MB) | {pve1_r.get('cores_recommended','?')} cores | {pve1_r.get('disk_recommended_gb','?')}GB disk
  pve2 (40%) : {pve2_r.get('ram_recommended_gb','?')}GB RAM ({pve2_r.get('ram_mb','?')}MB) | {pve2_r.get('cores_recommended','?')} cores | {pve2_r.get('disk_recommended_gb','?')}GB disk
  Total allocatable : {alloc.get('total_allouable_ram_gb','?')}GB RAM | {alloc.get('total_allouable_cores','?')} cores | {alloc.get('total_allouable_disk_gb','?')}GB disk
"""
    elif pc_hote is not None and not pc_hote.get("disponible"):
        pc_bloc = "\n## HOST PC RESOURCES\n  windows_exporter not reachable — install and start the service\n"

    # ── Bloc Cluster Proxmox ─────────────────────────────────────────────────
    noeuds_detail    = ""
    ressources_dispo = ""
    vms_detail       = ""

    if etat and etat.get("noeuds"):
        for n in etat.get("noeuds", []):
            ram_libre  = round(n.get("ram_total_gb", 0) - n.get("ram_used_gb", 0), 1)
            disk_libre = round(n.get("disk_total_gb", 0) * (1 - n.get("disk_pct", 0) / 100), 1)
            cpu_libre  = round(100 - n.get("cpu_pct", 0), 1)
            statut     = n.get("statut", "unknown").upper()

            if statut in ("ONLINE", "UP", "EN LIGNE"):
                # Niveau 1 — CPU, RAM, Disk
                noeuds_detail += (
                    f"  {n['nom']} [ONLINE]\n"
                    f"    CPU  : {n.get('cpu_pct',0)}% used | {cpu_libre}% free | {n.get('cpu_cores','?')} cores\n"
                    f"    RAM  : {n.get('ram_used_gb',0)}GB/{n.get('ram_total_gb',0)}GB | {ram_libre}GB FREE\n"
                    f"    Disk : {n.get('disk_used_gb','?')}GB/{n.get('disk_total_gb','?')}GB | {disk_libre}GB free\n"
                )
                # Niveau 2 — Swap, I/O, Réseau
                swap = n.get("swap_pct", 0)
                iow  = n.get("cpu_iowait_pct", 0)
                if swap > 0 or iow > 0:
                    noeuds_detail += (
                        f"    Swap    : {swap}% ({n.get('swap_used_gb',0)}GB/{n.get('swap_total_gb',0)}GB)\n"
                        f"    IOWait  : {iow}% | Read latency: {n.get('disk_read_latency_ms',0)}ms | Write: {n.get('disk_write_latency_ms',0)}ms\n"
                    )
                net_err = n.get("net_errors_in", 0) + n.get("net_errors_out", 0)
                if net_err > 0:
                    noeuds_detail += f"    Net err : {net_err}/s\n"
                # Niveau 3 — Température, SMART, ZFS, Corosync
                temp = n.get("cpu_temp_max_c", 0)
                if temp > 0:
                    noeuds_detail += f"    Temp    : {temp}°C\n"
                if not n.get("smart_ok", True):
                    noeuds_detail += (
                        f"    SMART   : FAIL — reallocated={n.get('smart_reallocated_sectors',0)} "
                        f"uncorrectable={n.get('smart_uncorrectable',0)}\n"
                    )
                if n.get("zfs_available"):
                    noeuds_detail += f"    ZFS ARC : {n.get('zfs_arc_hit_rate',0)}% hit | {n.get('zfs_arc_size_gb',0)}GB\n"
                if not n.get("corosync_ok", True):
                    noeuds_detail += f"    Corosync: DEGRADED — quorum={'OK' if n.get('corosync_quorum_ok') else 'LOST'}\n"
                noeuds_detail += "\n"

                ressources_dispo += (
                    f"  {n['nom']} -> RAM free: {ram_libre}GB | Disk free: {disk_libre}GB | CPU free: {cpu_libre}%\n"
                )
            else:
                noeuds_detail    += f"  {n['nom']} [OFFLINE] — node unreachable, no metrics\n\n"
                ressources_dispo += f"  {n['nom']} -> OFFLINE\n"

        for v in etat.get("vms", []):
            vms_detail += (
                f"  VMID {v['vmid']} - {v['nom']} [{v['statut'].upper()}] on {v['noeud']}\n"
                f"    vCPU: {v.get('vcpus','?')} | RAM: {v.get('maxmem_gb','?')}GB | Disk: {v.get('maxdisk_gb','?')}GB\n"
            )

    # Calculer le prochain VMID depuis TOUTES les VMs (running + stopped)
    # Proxmox réserve les VMIDs même pour les VMs arrêtées
    # On force aussi le minimum à 100 et on ajoute les VMIDs connus fixes
    vmids_connus = [int(v.get("vmid", 0)) for v in (etat or {}).get("vms", []) if str(v.get("vmid","")).isdigit()]
    # Ajouter les VMIDs connus de l'infrastructure (linux-vm1=101, linux-vm2=103)
    vmids_fixes  = [101, 103]
    tous_vmids   = list(set(vmids_connus + vmids_fixes))
    next_vmid    = max(tous_vmids) + 1 if tous_vmids else 104

    etat_bloc = (
        "\n## LIVE CLUSTER STATE\n"
        "### Proxmox Nodes\n"      + (noeuds_detail  or "  No data\n")     +
        "### Existing VMs\n"       + (vms_detail      or "  No VMs\n")      +
        "### AVAILABLE RESOURCES\n"+ (ressources_dispo or "  Not available\n") +
        f"### NEXT VMID: {next_vmid}\n"
    ) if etat else "\n## CLUSTER: data not available yet\n"

    return f"""You are OpsPilot, an expert Proxmox VE infrastructure assistant.

## LANGUAGE RULE
Respond in the SAME language as the user's question.
- French question → French response
- English question → English response
- Technical terms (VMID, RAM, CPU, qm, pct, pvesh) always stay in English
{pc_bloc}{etat_bloc}
## YOUR ROLE
- VM/LXC creation and sizing (use LIVE CLUSTER STATE resources)
- VMware Workstation sizing for pve1/pve2 (use HOST PC RESOURCES + OPTIMAL VMWARE ALLOCATION)
- Proxmox VE best practices and troubleshooting
- Performance analysis (levels 1=CPU/RAM/Disk, 2=Swap/IO/Net, 3=Temp/SMART/ZFS/Corosync)

## ABSOLUTE RULES
1. Use ONLY real values from sections above — never invent
2. OFFLINE node → refuse VM creation
3. Official commands only: qm, pct, pvesh, pvecm, vzdump, pveam, pvesm, systemctl, standard Linux
4. FORBIDDEN: pveadm, pvectl, pve-node, any invented command
5. Next VMID: {next_vmid}
6. Storage: local-lvm | Bridge: vmbr0
7. Minimum VM: RAM >= 512MB, Disk >= 10GB, vCPU >= 1
8. RAM free < 1GB on target node → REFUSE, show how to free memory
9. Never allocate more than available
10. VMware sizing → use OPTIMAL VMWARE ALLOCATION values above
11. Always keep Windows reserve — never give everything to VMware

## VM CREATION STRUCTURE
1. Check node ONLINE
2. Show available resources
3. Recommend sizing with justification
4. VMID: {next_vmid}
5. Exact command (qm create or pct create)
6. Post-creation steps
7. Warning if insufficient

## FORMAT
- ```bash for ALL commands
- Max 350 words
- Justify resource values
- Use real values, never placeholders
"""


def system_prompt_surveillance(etat: dict = None, dernier_lstm: dict = None) -> str:
    """Prompt surveillance — inchangé."""
    lstm     = dernier_lstm or {}
    etat_str = ""

    if etat and etat.get("noeuds"):
        noeuds_str = "\n".join([
            f"  {n['nom']} [{n['statut'].upper()}] CPU:{n['cpu_pct']}% RAM:{n['ram_pct']}% "
            f"({n['ram_used_gb']}/{n['ram_total_gb']}GB) Disk:{n['disk_pct']}% Swap:{n.get('swap_pct',0)}%"
            for n in etat.get("noeuds", [])
        ])
        vms_str = "\n".join([
            f"  VMID:{v['vmid']} {v['nom']} [{v['statut'].upper()}] on {v['noeud']}"
            for v in etat.get("vms", [])
        ])
        etat_str = f"""
CLUSTER STATE (live):
{noeuds_str}
VMs: {vms_str}
AI anomaly score: {lstm.get('score', 0):.4f} / threshold {lstm.get('seuil', 0.5):.4f}
"""

    return f"""You are OpsPilot, an expert Proxmox VE monitoring agent.

RULES:
- SHORT report (max 150 words), direct, actionable
- Always respond in ENGLISH only
- Official Proxmox thresholds:
  * RAM > 85% CRITICAL | CPU > 80% CRITICAL | Disk > 90% CRITICAL
  * Swap > 80% CRITICAL | I/O wait > 30% CRITICAL | Temp > 85C CRITICAL
  * RAM > 75% IMPORTANT | CPU > 65% IMPORTANT
- Exact commands from official Proxmox docs only
- Never use pveadm or non-existent commands
- Explain WHY each action is necessary
{etat_str}"""