import asyncio
import time
from datetime import datetime
from fastapi import WebSocket, WebSocketDisconnect

from agent.chat_history import (
    ajouter_message, editer_message, get_messages_llm,
    vider_historique, get_historique_complet, charger_depuis_db, next_id,
    nouvelle_conversation, changer_conversation, supprimer_conversation, lister_conversations
)
from agent.groq_client  import appeler_groq, GROQ_MODEL, GROQ_OK
from agent.prompts      import system_prompt_chat
import agent.surveillance as surveillance

clients: list = []

# ── Cache PC hôte (TTL 60s) ──────────────────────────────────────────────────
# Évite d'appeler windows_exporter à chaque question
_pc_hote_cache: dict  = {}
_pc_hote_ts:    float = 0.0
PC_HOTE_TTL_S         = 60


def _get_pc_hote() -> dict:
    """
    Retourne les ressources du PC hôte avec cache TTL 60s.
    Importe metriques_pc_hote uniquement ici — non bloquant si absent.
    """
    global _pc_hote_cache, _pc_hote_ts
    now = time.time()

    if now - _pc_hote_ts < PC_HOTE_TTL_S and _pc_hote_cache:
        return _pc_hote_cache

    try:
        from metriques_pc_hote import collecter_ressources_pc_hote
        _pc_hote_cache = collecter_ressources_pc_hote()
        _pc_hote_ts    = now
    except Exception as e:
        print(f"[PC Host] {e}")
        if not _pc_hote_cache:
            _pc_hote_cache = {"disponible": False}

    return _pc_hote_cache


def _detecter_langue(question: str) -> str:
    """Détecte si la question est en français ou en anglais."""
    mots_fr = ["créer", "construire", "ajouter", "veux", "je", "comment",
               "pourquoi", "quoi", "quel", "quelle", "mon", "ma", "les",
               "du", "de", "est", "sont", "pour", "avec", "sur", "dans",
               "quelle", "combien", "puis-je", "peux"]
    return "fr" if any(m in question.lower() for m in mots_fr) else "en"


def _verifier_ressources_vm(question: str, etat: dict) -> str | None:
    """
    Vérifie les ressources avant création VM/LXC.
    Intercepte avant l'appel LLM — zéro quota Groq consommé si refus.
    """
    q = question.lower()

    mots_creation = ['create', 'build', 'add', 'new vm', 'créer', 'construire',
                     'ajouter', 'nouvelle vm', 'deploy', 'déployer', 'install',
                     'lancer', 'nouveau', 'faire']
    mots_vm       = ['vm', 'virtual machine', 'machine virtuelle', 'container',
                     'lxc', 'conteneur', 'instance', 'nœud', 'node']

    mots_lxc      = ['lxc', 'conteneur', 'container', 'ct']
    is_lxc      = any(m in q for m in mots_lxc)
    is_creation = any(m in q for m in mots_creation) and any(m in q for m in mots_vm)
    if not is_creation:
        return None
    if not etat or not etat.get("noeuds"):
        return None

    noeuds_mentionnes = [n for n in etat.get("noeuds", [])
                         if n.get("nom", "").lower() in q]
    if not noeuds_mentionnes:
        noeuds_mentionnes = [n for n in etat.get("noeuds", [])
                             if str(n.get("statut","")).lower() in ("online","up","en ligne")]
    if not noeuds_mentionnes:
        return None

    lang        = _detecter_langue(question)
    # LXC beaucoup plus léger qu'une VM — minimums différents
    RAM_MIN_GB  = 0.25 if is_lxc else 1.0   # 256MB pour LXC, 1GB pour VM
    DISK_MIN_GB = 5.0  if is_lxc else 10.0  # 5GB pour LXC, 10GB pour VM

    for n in noeuds_mentionnes:
        nom    = n.get("nom", "?")
        statut = str(n.get("statut", "")).lower()

        if statut not in ("online", "up", "en ligne"):
            if lang == "fr":
                return (f"❌ **Impossible de créer une VM sur {nom}**\n\n"
                        f"Le nœud **{nom}** est **OFFLINE** et inaccessible.\n\n"
                        f"**Solutions :**\n"
                        f"- Attendez que {nom} soit en ligne\n"
                        f"- Créez la VM sur **pve1** qui est ONLINE")
            else:
                return (f"❌ **Cannot create a VM on {nom}**\n\n"
                        f"Node **{nom}** is **OFFLINE** and unreachable.\n\n"
                        f"**Options:**\n"
                        f"- Wait for {nom} to come back online\n"
                        f"- Create the VM on **pve1** which is ONLINE")

        ram_libre  = round(n.get("ram_total_gb", 0) - n.get("ram_used_gb", 0), 1)
        disk_libre = round(n.get("disk_total_gb", 0) * (1 - n.get("disk_pct", 0) / 100), 1)

        if ram_libre < RAM_MIN_GB:
            if lang == "fr":
                return (f"❌ **RAM insuffisante sur {nom}**\n\n"
                        f"RAM libre : **{ram_libre}GB** — minimum requis : **{RAM_MIN_GB}GB**\n\n"
                        f"**Libérez de la RAM avant de créer une VM :**\n"
                        f"```bash\nps aux --sort=-%mem | head -15\n```\n"
                        f"Réduisez le balloon des VMs existantes :\n"
                        f"```bash\nqm set 101 --balloon 512\necho 1 > /sys/kernel/mm/ksm/run\n```\n"
                        f"Ressources actuelles : RAM {ram_libre}GB libre | Disk {disk_libre}GB libre")
            else:
                return (f"❌ **Not enough RAM on {nom}**\n\n"
                        f"RAM free: **{ram_libre}GB** — minimum required: **{RAM_MIN_GB}GB**\n\n"
                        f"**Free up RAM before creating a VM:**\n"
                        f"```bash\nps aux --sort=-%mem | head -15\n```\n"
                        f"Reduce existing VM balloon:\n"
                        f"```bash\nqm set 101 --balloon 512\necho 1 > /sys/kernel/mm/ksm/run\n```\n"
                        f"Current: RAM {ram_libre}GB free | Disk {disk_libre}GB free")

        if disk_libre < DISK_MIN_GB:
            if lang == "fr":
                return (f"❌ **Espace disque insuffisant sur {nom}**\n\n"
                        f"Disque libre : **{disk_libre}GB** — minimum requis : **{DISK_MIN_GB}GB**\n\n"
                        f"**Libérez de l'espace :**\n"
                        f"```bash\ndu -sh /var/lib/vz/dump/* 2>/dev/null | sort -rh | head -10\ndf -h /\n```\n"
                        f"Supprimez les anciens backups.")
            else:
                return (f"❌ **Not enough disk on {nom}**\n\n"
                        f"Disk free: **{disk_libre}GB** — minimum required: **{DISK_MIN_GB}GB**\n\n"
                        f"**Free up disk space:**\n"
                        f"```bash\ndu -sh /var/lib/vz/dump/* 2>/dev/null | sort -rh | head -10\ndf -h /\n```\n"
                        f"Delete old backups and clean logs.")

    return None


import re as _re

def _corriger_reponse_llm(reponse: str, next_vmid: int) -> str:
    """
    Corrige les erreurs systématiques du LLM llama-3.1-8b-instant.
    Appliqué après chaque réponse avant envoi au frontend.
    """
    import re as _re2

    # Plancher VMID — pve2 OFFLINE cache linux-vm2 (103)
    # On force un minimum de 104 pour ne jamais suggérer 101/102/103
    if next_vmid <= 103:
        next_vmid = 104

    # 1. Corriger pct create <vmid> → pct create <next_vmid>
    reponse = _re2.sub(
        r'(pct create\s+)\d+',
        lambda m: f"{m.group(1)}{next_vmid}",
        reponse
    )

    # 2. Corriger pct start <vmid> → pct start <next_vmid>
    reponse = _re2.sub(
        r'(pct start\s+)\d+',
        lambda m: f"{m.group(1)}{next_vmid}",
        reponse
    )

    # 3. Corriger --hostname avec mauvais VMID
    reponse = _re2.sub(
        r'(--hostname\s+\S*?)\d+\b',
        lambda m: m.group(0).rsplit(
            m.group(0).rstrip().split()[-1].lstrip('abcdefghijklmnopqrstuvwxyz-_'), 1
        )[0] + str(next_vmid),
        reponse
    )

    # 4. Supprimer --disk dans pct create (paramètre invalide)
    reponse = _re2.sub(r'\s*--disk\s+\S+', '', reponse)

    # 5. Corriger --cpu X → --cores X
    reponse = _re2.sub(r'--cpu\s+(\d+)', r'--cores \1', reponse)

    # 6. Corriger --net0 vmbr0 seul → --net0 name=eth0,bridge=vmbr0,ip=dhcp
    reponse = _re2.sub(
        r'--net0\s+(?!name=)(\w+)',
        r'--net0 name=eth0,bridge=\1,ip=dhcp',
        reponse
    )

    # 7. Supprimer --template X (invalide pour pct create)
    reponse = _re2.sub(r'\s*--template\s+\S+', '', reponse)

    # 8. Corriger qm set sur VMIDs inexistants
    reponse = _re2.sub(
        r'qm set\s+(?!101\b)(\d{3,})',
        'qm set 101',
        reponse
    )

    # 9. Corriger pveam download avec template invalide
    reponse = _re2.sub(
        r'pveam download local\s+(?!debian-12)(\S+)',
        'pveam download local debian-12-standard_12.7-1_amd64.tar.zst',
        reponse
    )

    # 10. Corriger --rootfs local-lvm:1 → minimum 2GB
    reponse = _re2.sub(
        r'(--rootfs\s+\S+:)([01])(\s|$)',
        r'\g<1>2\3',
        reponse
    )

    return reponse


async def repondre_question(question: str, msg_id_edition: str = None) -> tuple:
    """
    Appelle le LLM avec contexte complet puis corrige les erreurs
    systématiques du modèle avant de retourner la réponse.
    """
    etat    = surveillance.dernier_etat
    pc_hote = _get_pc_hote()

    # Vérification avant LLM — économise le quota Groq
    refus = _verifier_ressources_vm(question, etat)
    if refus:
        msg = ajouter_message("assistant", refus)
        return refus, msg["id"]

    system = system_prompt_chat(etat, surveillance.dernier_lstm, pc_hote, question)
    # Limiter à 10 messages — évite les prompts trop longs qui ralentissent Groq
    msgs_all = get_messages_llm(jusqu_a_id=msg_id_edition) if msg_id_edition else get_messages_llm()
    msgs = msgs_all[-10:] if len(msgs_all) > 10 else msgs_all

    # Calculer next_vmid depuis l'état en mémoire — JAMAIS appeler get_etat_cluster()
    vmids_connus = [
        int(v.get("vmid", 0))
        for v in (etat or {}).get("vms", [])
        if str(v.get("vmid","")).isdigit()
    ]
    next_vmid = max(vmids_connus) + 1 if vmids_connus else 104

    loop    = asyncio.get_event_loop()
    reponse = await loop.run_in_executor(
        None, lambda: appeler_groq(system, msgs, question, max_tokens=1200)
    )

    # Corriger les erreurs systématiques du LLM
    reponse = _corriger_reponse_llm(reponse, next_vmid)

    msg = ajouter_message("assistant", reponse)
    return reponse, msg["id"]


async def handle_connection(ws: WebSocket):
    """Gere une connexion WebSocket complete."""
    await ws.accept()
    clients.append(ws)

    await ws.send_json({
        "type":      "init",
        "role":      "assistant",
        "id":        next_id(),
        "timestamp": datetime.now().isoformat(),
        "content":   "connected",
    })

    # Pas de restauration automatique au démarrage
    # L'utilisateur voit l'écran d'accueil et choisit dans la sidebar

    if surveillance.dernier_etat:
        await ws.send_json({
            "type":      "etat_cluster",
            "etat":      surveillance.dernier_etat,
            "lstm":      surveillance.dernier_lstm,
            "timestamp": datetime.now().isoformat(),
        })

    try:
        while True:
            data     = await ws.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "question":
                question = data.get("content", "").strip()
                if not question:
                    continue
                msg_user = ajouter_message("user", question)
                try:
                    from database import sauvegarder_message
                    sauvegarder_message("user", question, msg_user["id"])
                except Exception:
                    pass
                await ws.send_json({
                    "type":      "message_user",
                    "id":        msg_user["id"],
                    "content":   question,
                    "timestamp": msg_user["timestamp"],
                })
                await ws.send_json({"type": "thinking"})
                reponse, msg_id_rep = await repondre_question(question)
                try:
                    from database import sauvegarder_message
                    sauvegarder_message("assistant", reponse, msg_id_rep)
                except Exception:
                    pass
                await ws.send_json({
                    "type":      "reponse",
                    "role":      "assistant",
                    "id":        msg_id_rep,
                    "content":   reponse,
                    "timestamp": datetime.now().isoformat(),
                    "lstm":      surveillance.dernier_lstm,
                    "llm":       "groq",
                    "model":     GROQ_MODEL,
                })

            elif msg_type == "edit_message":
                msg_id  = data.get("msg_id", "")
                nouveau = data.get("content", "").strip()
                if not msg_id or not nouveau:
                    continue
                idx = editer_message(msg_id, nouveau)
                if idx == -1:
                    await ws.send_json({"type": "error", "content": "Message introuvable."})
                    continue
                await ws.send_json({
                    "type":            "message_edite",
                    "msg_id":          msg_id,
                    "content":         nouveau,
                    "timestamp":       datetime.now().isoformat(),
                    "truncated_after": msg_id,
                })
                await ws.send_json({"type": "thinking"})
                reponse, msg_id_rep = await repondre_question(nouveau, msg_id_edition=msg_id)
                await ws.send_json({
                    "type":      "reponse",
                    "role":      "assistant",
                    "id":        msg_id_rep,
                    "content":   reponse,
                    "timestamp": datetime.now().isoformat(),
                    "lstm":      surveillance.dernier_lstm,
                    "llm":       "groq",
                    "model":     GROQ_MODEL,
                })

            elif msg_type == "new_conversation":
                conv_id = nouvelle_conversation()
                await ws.send_json({
                    "type":          "conversation_created",
                    "conv_id":       conv_id,
                    "messages":      [],
                    "conversations": lister_conversations(),
                    "timestamp":     datetime.now().isoformat(),
                })

            elif msg_type == "switch_conversation":
                conv_id = data.get("conv_id", "")
                ok = changer_conversation(conv_id)
                if ok:
                    await ws.send_json({
                        "type":          "conversation_switched",
                        "conv_id":       conv_id,
                        "messages":      get_historique_complet(),
                        "conversations": lister_conversations(),
                        "timestamp":     datetime.now().isoformat(),
                    })

            elif msg_type == "delete_conversation":
                conv_id = data.get("conv_id", "")
                supprimer_conversation(conv_id)
                await ws.send_json({
                    "type":          "conversation_deleted",
                    "messages":      get_historique_complet(),
                    "conversations": lister_conversations(),
                    "timestamp":     datetime.now().isoformat(),
                })

            elif msg_type == "clear_history":
                vider_historique()
                try:
                    from database import supprimer_chat_history
                    supprimer_chat_history()
                except Exception:
                    pass
                await ws.send_json({
                    "type":      "history_cleared",
                    "timestamp": datetime.now().isoformat()
                })

    except WebSocketDisconnect:
        if ws in clients:
            clients.remove(ws)
    except Exception as e:
        print(f"[WS] {e}")
        if ws in clients:
            clients.remove(ws)


async def broadcaster(ws_queue: asyncio.Queue):
    """Diffuse les messages de la queue vers tous les clients."""
    while True:
        try:
            msg  = await asyncio.wait_for(ws_queue.get(), timeout=2.0)
            dead = []
            for ws in clients:
                try:
                    await ws.send_json(msg)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                if ws in clients:
                    clients.remove(ws)
        except asyncio.TimeoutError:
            pass