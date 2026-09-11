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
            # ← MODIFIÉ (trou constaté) : le chat ne recevait que
            # l'ALLOCATION de chaque VM (1GB RAM, 1 vCPU) -- jamais son
            # UTILISATION réelle, ni les services qui y tournent, ni les
            # métriques de ces services. Toutes ces données sont pourtant
            # déjà collectées et affichées sur la page Infrastructure.
            # Conséquence concrète : à la question "quels services tournent
            # sur linux-vm2 et combien consomment-ils ?", le modèle ne
            # pouvait que deviner. Et à une question de pression mémoire,
            # il ne voyait pas qu'une VM était à 91.8% pendant qu'une autre
            # était à 57% -- information décisive pour recommander une
            # action ciblée plutôt qu'un conseil générique.
            ligne = (
                # ← AJOUT : le TYPE de l'invité (qemu ou lxc) est
                # désormais transmis. Il était collecté par
                # metriques_proxmox.py mais jamais envoyé au modèle, qui
                # devait donc le deviner -- erreur réelle constatée : une
                # recommandation proposait "pct exec 101 -- systemctl
                # restart postgresql" alors que la VM 101 est une VM QEMU.
                # pct pilote les conteneurs LXC, qm les VMs QEMU : la
                # commande aurait échoué avec "CT 101 does not exist".
                # Le type par défaut est "qemu" : c'est ce que retourne
                # _decouvrir_vms() quand l'information manque, et la VM
                # QEMU est le cas très majoritaire sur ce cluster.
                f"{str(v.get('type', 'qemu')).upper()} {v['vmid']} {v['nom']} "
                f"[{v['statut'].upper()}] on {v['noeud']} "
                f"| allocated {v.get('maxmem_gb', v.get('ram_total_gb','?'))}GB RAM, {v.get('vcpus','?')} vCPU"
            )
            if v.get("ram_pct") is not None:
                ligne += f" | USING RAM {v.get('ram_pct')}%"
            if v.get("cpu_pct") is not None:
                ligne += f", CPU {v.get('cpu_pct')}%"
            if v.get("disk_pct") is not None:
                ligne += f", Disk {v.get('disk_pct')}%"
            cluster_lines.append(ligne)

            # Services détectés en direct sur cette VM, avec leur
            # consommation réelle quand elle est mesurée -- permet de
            # répondre précisément à "qu'est-ce qui consomme la mémoire ?"
            # au lieu d'un conseil générique.
            services = v.get("services_detectes") or []
            metriques = v.get("metriques_services") or {}
            if services:
                details = []
                for svc in services:
                    m = metriques.get(svc) or {}
                    if m.get("ram_mb") is not None:
                        details.append(f"{svc} ({m.get('ram_mb')}MB RAM, {m.get('cpu_pct', 0)}% CPU)")
                    else:
                        details.append(svc)
                cluster_lines.append(f"    services on {v['nom']}: " + ", ".join(details))

    cluster_ctx = "\n".join(cluster_lines) if cluster_lines else "No cluster data"

    # ── Bloc PC hôte — TOUJOURS fourni ────────────────────────────────────────
    # ← MODIFIÉ (confusion réelle observée) : ce bloc n'était ajouté que
    # pour les intentions 'sizing' et 'creation'. Une question de
    # diagnostic ordinaire (classée 'general') ne recevait donc AUCUNE
    # information sur la machine physique -- le modèle raisonnait comme si
    # pve1 et pve2 étaient des serveurs indépendants et conseillait
    # "ajoutez 1 Go de RAM à chaque nœud", sans savoir que ces nœuds sont
    # des VMs VMware sur un PC de 8 Go déjà saturé, où cette mémoire
    # n'existe tout simplement pas. Le contexte physique est pertinent pour
    # TOUTE question touchant aux ressources, pas seulement au
    # dimensionnement -- il est désormais toujours transmis.
    pc_bloc = ""
    if pc_hote and pc_hote.get("disponible"):
        alloc  = pc_hote.get("vmware_allocation", {})
        pve1_r = alloc.get("pve1", {})
        pve2_r = alloc.get("pve2", {})
        libre_gb = pc_hote.get("ram_available_gb")
        pc_bloc = (
            f"\nPHYSICAL HOST (the machine running everything): "
            f"RAM {pc_hote.get('ram_total_gb')}GB total"
            + (f", only {libre_gb}GB actually free" if libre_gb is not None else "")
            + f" | CPU {pc_hote.get('cpu_cores')} cores | "
            f"Disk {pc_hote.get('disk_total_gb')}GB\n"
            f"VMWARE ALLOCATION: pve1={pve1_r.get('ram_recommended_gb')}GB RAM "
            f"{pve1_r.get('cores_recommended')} cores | "
            f"pve2={pve2_r.get('ram_recommended_gb')}GB RAM "
            f"{pve2_r.get('cores_recommended')} cores\n"
            "IMPORTANT — VIRTUALISATION LAYERS: the Proxmox nodes are NOT physical servers. "
            "They are virtual machines running on the physical host above, which itself runs Windows. "
            "You therefore CANNOT 'add RAM to a node' independently: any RAM given to a node is taken "
            "from the physical host's total. If the physical host has little free RAM, the only real fix "
            "is to add a memory module to the physical machine, or to free RAM on it first — never simply "
            "'give more RAM to each node' as if they were separate servers.\n"
        )

    # ← AJOUT : règle d'arithmétique explicite. Erreur réelle constatée --
    # le modèle a répondu "après avoir ajouté 1 Go, vous aurez ~2.9 Go
    # libres par nœud", en confondant la CAPACITÉ TOTALE (1.9 + 1 = 2.9)
    # avec la MÉMOIRE LIBRE (0.2 + 1 = 1.2). Une erreur de ce type dans un
    # conseil de dimensionnement conduit tout droit à une décision d'achat
    # erronée, et décrédibilise l'ensemble de la réponse même quand le
    # raisonnement de fond est juste.
    # ← AJOUT : interdiction d'inventer. Deux erreurs réelles constatées
    # dans une même réponse -- un chiffre faux ("~2.9GB free per node",
    # obtenu en confondant total et libre) et une règle inexistante
    # présentée comme celle du système ("Rule: RAM free < 1 GB ->
    # REFUSE"). Dans les deux cas le modèle a comblé un vide au lieu
    # d'admettre qu'il ne disposait pas de l'information. Sur un outil de
    # supervision, un chiffre inventé est pire qu'une absence de réponse :
    # il peut conduire à une décision d'achat ou d'exploitation erronée.
    # ← AJOUT : sans cette règle, le modèle choisissait l'outil au hasard
    # entre qm et pct. Les deux existent sur Proxmox et se ressemblent,
    # mais ne s'adressent pas aux mêmes objets -- une commande qm sur un
    # conteneur, ou pct sur une VM, échoue systématiquement. La règle est
    # générique : elle vaut pour n'importe quel invité présent ou futur,
    # sans nommer aucune VM en particulier.
    regle_outil_invite = (
        "- Each guest above is labelled QEMU or LXC. Use the matching tool: 'qm' for QEMU guests, "
        "'pct' for LXC containers. Never mix them — 'pct exec' on a QEMU guest fails with "
        "'CT <id> does not exist', and 'qm' on a container fails the same way. "
        "To run a command INSIDE a QEMU guest, use 'qm guest exec <vmid> -- <command>' "
        "(requires the QEMU guest agent); if you are unsure the agent is installed, give the "
        "command to run over SSH inside the guest instead of guessing.\n"
    )

    regle_pas_d_invention = (
        "- USE ONLY THE NUMBERS GIVEN ABOVE. Never invent a metric, a threshold, or a system rule. "
        "If a value you need is not in the CLUSTER STATE above, say plainly that it is not available "
        "and point to the Infrastructure page — do not estimate it. When you state a threshold, it "
        "must be one shown above; never present your own reasoning as if it were a rule configured "
        "in this system.\n"
    )

    regle_arithmetique = (
        "- MEMORY ARITHMETIC: 'total' and 'free' are DIFFERENT quantities. Adding X GB to a node "
        "raises its TOTAL by X, and its FREE memory by X — it does NOT make the free memory equal "
        "to the new total. Example: a node at 1.7GB used / 1.9GB total has 0.2GB free; adding 1GB "
        "gives 2.9GB total and 1.2GB free, NEVER 2.9GB free. State both numbers separately and "
        "check your subtraction before giving any sizing advice.\n"
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
{regle_ram_cpu}{regle_arithmetique}{regle_pas_d_invention}{regle_outil_invite}

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
{regle_ram_cpu}{regle_arithmetique}{regle_pas_d_invention}{regle_outil_invite}
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