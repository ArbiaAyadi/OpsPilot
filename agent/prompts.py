import re

from agent.groq_client import GROQ_MODEL


def _detect_intent(question: str) -> str:
    """
    Détecte l'intention de la question pour choisir le bon prompt.

    ← CORRECTION : comparaison par mot ENTIER (\\b...\\b), pas par
    sous-chaîne. Avant, 'vm' en sous-chaîne matchait "vm1", "VMs", "vmware"
    -- n'importe quelle question mentionnant une VM déjà existante
    (migration, diagnostic, optimisation mémoire...) tombait à tort dans
    l'intention 'creation' et recevait le prompt LXC (syntaxe pct create,
    NEXT VMID, règles de nommage) au lieu du prompt général. Confirmé
    concrètement sur 3 des 8 boutons "Quick Start" de PageAssistant.jsx :
    "migrate linux-vm1...", "optimize memory usage across VMs...", "A VM
    has permanently high CPU..." -- aucun n'est une demande de création,
    les trois étaient classées 'creation' avant ce correctif. Le bug ne
    provoque aucune erreur visible : juste une réponse hors-sujet, ce qui
    le rend invisible en usage normal.
    """
    q = question.lower()

    def _contient_mot(mots):
        return any(re.search(rf'\b{re.escape(m)}\b', q) for m in mots)

    if _contient_mot(['create', 'build', 'créer', 'construire', 'lxc', 'vm', 'container', 'deploy', 'new']):
        return 'creation'
    if _contient_mot(['vmware', 'configure', 'allocation', 'ram pc', 'ressource pc', 'how much', 'combien']):
        return 'sizing'
    return 'general'


def system_prompt_chat(etat: dict = None, dernier_lstm: dict = None,
                        pc_hote: dict = None, question: str = "") -> str:
    """
    Prompt adaptatif — court pour questions générales, complet pour création VM.
    Réduit drastiquement la taille du prompt → réponses plus rapides.

    ← CORRECTION (trouvé en examinant une vraie réponse du chat) : ni le
    prompt "creation" ni le prompt général ci-dessous ne mentionnaient
    JAMAIS KSM, le ballooning, ni l'interdiction de réduire la RAM/vCPU
    d'une VM existante -- alors que ces trois règles sont appliquées et
    affichées systématiquement partout ailleurs dans le projet (voir
    incident_prompt.py, GARDE_FOUS_SEUILS, tous les warnings "Never
    reduce VM RAM or vCPU in production" visibles sur chaque
    recommendation générée par surveillance.py). Le chat suit un chemin
    de prompt totalement séparé de l'analyse d'incidents -- rien ne lui
    transmettait cette règle avant ce correctif, d'où une vraie réponse
    contenant "pct set <vmid> --memory <new-mb>" comme option de
    remédiation, contredisant directement la philosophie affichée
    partout ailleurs dans l'app. Ajouté au prompt général (utilisé pour
    'general' ET 'sizing', les deux intentions où une question de
    pression RAM/CPU peut survenir) ainsi qu'au prompt de création (une
    question de création peut aussi mentionner libérer de la place sur
    un nœud saturé) -- concis, cohérent avec le style "RULES" existant,
    sans faire exploser la taille du prompt (l'objectif de rapidité
    documenté ci-dessus reste respecté).

    Corrige aussi, dans le même mouvement, la commande "migrate <vmid>
    <node>" que le modèle inventait (n'existe pas dans Proxmox) --
    la bonne syntaxe qm migrate ... --online est maintenant donnée
    explicitement dans le prompt lui-même, pas seulement corrigée après
    coup par regex dans websocket_handler.py.
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

    # ── Bloc PC hôte — question sizing OU création ────────────────────────────
    pc_bloc = ""
    if intent in ('sizing', 'creation') and pc_hote and pc_hote.get("disponible"):
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

    # ← AJOUT : règle RAM/CPU partagée par les deux prompts ci-dessous --
    # une seule définition, jamais dupliquée à la main dans les deux
    # branches (creation ET général).
    regle_ram_cpu = (
        "- NEVER suggest reducing an existing VM's RAM or vCPU as a solution — this can crash running applications\n"
        "- For RAM/CPU pressure on a node, suggest in this order: "
        "1) qm migrate <vmid> <target-node> --online  "
        "2) echo 1 > /sys/kernel/mm/ksm/run  "
        "3) qm set <vmid> --balloon <mb>  "
        "4) add physical RAM  5) add a cluster node"
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
{regle_ram_cpu}

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
{regle_ram_cpu}
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