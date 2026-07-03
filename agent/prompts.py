from agent.groq_client import GROQ_MODEL


def _detect_intent(question: str) -> str:
    """Détecte l'intention de la question pour choisir le bon prompt."""
    q = question.lower()
    if any(w in q for w in ['create','build','créer','construire','lxc','vm','container','deploy','new']):
        return 'creation'
    if any(w in q for w in ['vmware','configure','allocation','ram pc','ressource pc','how much','combien']):
        return 'sizing'
    return 'general'


def system_prompt_chat(etat: dict = None, dernier_lstm: dict = None,
                        pc_hote: dict = None, question: str = "") -> str:
    """
    Prompt adaptatif — court pour questions générales, complet pour création VM.
    Réduit drastiquement la taille du prompt → réponses plus rapides.
    """
    intent = _detect_intent(question)

    # Calculer le prochain VMID depuis l'état déjà en mémoire
    # JAMAIS appeler get_etat_cluster() ici — c'est un appel réseau lent
    vmids_connus = [
        int(v.get("vmid", 0))
        for v in (etat or {}).get("vms", [])
        if str(v.get("vmid","")).isdigit()
    ]
    next_vmid = max(vmids_connus) + 1 if vmids_connus else 104

    # ── État cluster — toujours injecté (compact) ─────────────────────────────
    cluster_lines = []
    if etat and etat.get("noeuds"):
        for n in etat.get("noeuds", []):
            ram_libre  = round(n.get("ram_total_gb", 0) - n.get("ram_used_gb", 0), 1)
            disk_libre = round(n.get("disk_total_gb", 0) * (1 - n.get("disk_pct", 0) / 100), 1)
            statut     = n.get("statut", "unknown").upper()
            if statut in ("ONLINE", "UP", "EN LIGNE"):
                line = (
                    f"{n['nom']} ONLINE | "
                    f"RAM {n.get('ram_used_gb',0)}/{n.get('ram_total_gb',0)}GB ({ram_libre}GB free) | "
                    f"CPU {n.get('cpu_pct',0)}% | Disk {n.get('disk_pct',0)}% ({disk_libre}GB free)"
                )
                # Niveau 2 si pertinent
                if n.get("swap_pct", 0) > 10:
                    line += f" | Swap {n.get('swap_pct',0)}%"
                if n.get("cpu_iowait_pct", 0) > 5:
                    line += f" | IOWait {n.get('cpu_iowait_pct',0)}%"
                # Niveau 3 si pertinent
                if n.get("cpu_temp_max_c", 0) > 70:
                    line += f" | Temp {n.get('cpu_temp_max_c',0)}°C"
                if not n.get("smart_ok", True):
                    line += " | SMART FAIL"
                cluster_lines.append(line)
            else:
                cluster_lines.append(f"{n['nom']} OFFLINE")

        for v in etat.get("vms", []):
            cluster_lines.append(
                f"VM {v['vmid']} {v['nom']} [{v['statut'].upper()}] on {v['noeud']} "
                f"| {v.get('maxmem_gb','?')}GB RAM | {v.get('vcpus','?')} vCPU"
            )

    cluster_ctx = "\n".join(cluster_lines) if cluster_lines else "No cluster data"

    # ── Bloc PC hôte — uniquement si question sizing VMware ──────────────────
    pc_bloc = ""
    if intent == 'sizing' and pc_hote and pc_hote.get("disponible"):
        alloc  = pc_hote.get("vmware_allocation", {})
        pve1_r = alloc.get("pve1", {})
        pve2_r = alloc.get("pve2", {})
        pc_bloc = (
            f"\nPC HOST: RAM {pc_hote.get('ram_total_gb')}GB total | "
            f"CPU {pc_hote.get('cpu_cores')} cores | "
            f"Disk {pc_hote.get('disk_total_gb')}GB\n"
            f"VMWARE ALLOCATION: pve1={pve1_r.get('ram_recommended_gb')}GB RAM "
            f"{pve1_r.get('cores_recommended')} cores | "
            f"pve2={pve2_r.get('ram_recommended_gb')}GB RAM "
            f"{pve2_r.get('cores_recommended')} cores\n"
        )

    # ── Prompt création VM — complet avec syntaxe exacte ─────────────────────
    if intent == 'creation':
        return f"""You are OpsPilot, Proxmox VE expert. Respond in the SAME language as the question.

CLUSTER STATE:
{cluster_ctx}
{pc_bloc}
NEXT VMID: {next_vmid}

RULES:
- Use ONLY values above — never invent
- RAM free < 1GB → REFUSE, show how to free memory first
- OFFLINE node → refuse VM creation

LXC CREATION — EXACT SYNTAX ONLY:
pct create {next_vmid} local:vztmpl/<template> --hostname <name> --memory <mb> --swap <mb> --rootfs local-lvm:<gb> --net0 name=eth0,bridge=vmbr0,ip=dhcp --cores <n> --unprivileged 1

FORBIDDEN for pct: --disk, --cpu, --template, --net0 vmbr0 alone
Template download first: pveam update && pveam download local debian-12-standard_12.7-1_amd64.tar.zst

VM NAME — auto based on purpose:
- nginx/web → web-{next_vmid}
- postgres/db → db-{next_vmid}  
- docker → docker-{next_vmid}
- test/light → test-{next_vmid}
- default → vm-{next_vmid}

FORMAT: bash blocks for commands | max 200 words | justify each value"""

    # ── Prompt général — court et rapide ─────────────────────────────────────
    return f"""You are OpsPilot, Proxmox VE expert. Respond in the SAME language as the question.

CLUSTER STATE:
{cluster_ctx}
{pc_bloc}
NEXT VMID: {next_vmid}

RULES:
- Use ONLY official Proxmox commands: qm, pct, pvesh, pvecm, vzdump, pveam, pvesm, systemctl
- FORBIDDEN: pveadm, pvectl, any invented command
- bash blocks for ALL commands
- Max 200 words"""


def system_prompt_surveillance(etat: dict = None, dernier_lstm: dict = None) -> str:
    """Prompt surveillance — court et direct."""
    lstm     = dernier_lstm or {}
    etat_str = ""

    if etat and etat.get("noeuds"):
        noeuds_str = " | ".join([
            f"{n['nom']} CPU:{n['cpu_pct']}% RAM:{n['ram_pct']}% Disk:{n['disk_pct']}% Swap:{n.get('swap_pct',0)}%"
            for n in etat.get("noeuds", [])
        ])
        vms_str = ", ".join([
            f"VM{v['vmid']}({v['statut']})"
            for v in etat.get("vms", [])
        ])
        etat_str = f"\nCLUSTER: {noeuds_str}\nVMs: {vms_str}\nAI score: {lstm.get('score',0):.4f}/{lstm.get('seuil',0.5):.4f}\n"

    return f"""You are OpsPilot, Proxmox VE monitoring agent.
{etat_str}
RULES: max 150 words | English only | official Proxmox commands only
THRESHOLDS: RAM>85% CRITICAL | CPU>80% CRITICAL | Disk>90% CRITICAL | Swap>80% CRITICAL | IOWait>30% CRITICAL | Temp>85C CRITICAL | RAM>75% HIGH | CPU>65% HIGH"""