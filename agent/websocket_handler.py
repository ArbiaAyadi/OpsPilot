import asyncio
import re
from datetime import datetime
from fastapi import WebSocket, WebSocketDisconnect

from agent.chat_history  import (
    ajouter_message, editer_message, get_messages_llm,
    vider_historique, get_historique_complet, charger_depuis_db, next_id
)
from agent.groq_client   import appeler_groq, GROQ_MODEL, GROQ_OK
from agent.prompts       import system_prompt_chat
import agent.surveillance as surveillance

clients: list = []


def _verifier_ressources_vm(question: str, etat: dict) -> str | None:
    """
    Vérifie si la question concerne une création de VM et si les ressources
    sont suffisantes sur le nœud cible.
    Retourne un message de refus si insuffisant, None sinon.
    """
    q = question.lower()

    # Détecter intention de création VM
    mots_creation = ['create', 'build', 'add', 'new vm', 'créer', 'construire',
                     'ajouter', 'nouvelle vm', 'deploy', 'déployer', 'install']
    mots_vm       = ['vm', 'virtual machine', 'machine virtuelle', 'container', 'lxc']

    is_creation = any(m in q for m in mots_creation) and any(m in q for m in mots_vm)
    if not is_creation:
        return None

    if not etat or not etat.get("noeuds"):
        return None

    # Détecter le nœud cible mentionné dans la question
    noeuds_mentionnes = []
    for n in etat.get("noeuds", []):
        if n.get("nom", "").lower() in q:
            noeuds_mentionnes.append(n)

    # Si aucun nœud mentionné, prendre les nœuds online
    if not noeuds_mentionnes:
        noeuds_mentionnes = [n for n in etat.get("noeuds", [])
                             if str(n.get("statut","")).lower() in ("online","up","en ligne")]

    if not noeuds_mentionnes:
        return None

    # Vérifier les ressources du nœud cible
    for n in noeuds_mentionnes:
        nom    = n.get("nom", "?")
        statut = str(n.get("statut", "")).lower()

        # Nœud offline
        if statut not in ("online", "up", "en ligne"):
            lang = "fr" if any(m in q for m in ["créer","construire","ajouter","veux","je"]) else "en"
            if lang == "fr":
                return (f"❌ **Impossible de créer une VM sur {nom}**\n\n"
                        f"Le nœud **{nom}** est actuellement **OFFLINE** et inaccessible.\n\n"
                        f"**Solutions :**\n"
                        f"- Attendez que {nom} soit en ligne\n"
                        f"- Créez la VM sur **pve1** qui est ONLINE")
            else:
                return (f"❌ **Cannot create a VM on {nom}**\n\n"
                        f"Node **{nom}** is currently **OFFLINE** and unreachable.\n\n"
                        f"**Options:**\n"
                        f"- Wait for {nom} to come back online\n"
                        f"- Create the VM on **pve1** which is ONLINE")

        # RAM insuffisante — minimum 1GB libre requis pour créer une VM
        ram_libre  = round(n.get("ram_total_gb", 0) - n.get("ram_used_gb", 0), 1)
        disk_libre = round(n.get("disk_total_gb", 0) * (1 - n.get("disk_pct", 0) / 100), 1)
        RAM_MIN_GB = 1.0
        DISK_MIN_GB = 10.0

        lang = "fr" if any(m in q for m in ["créer","construire","ajouter","veux","je"]) else "en"

        if ram_libre < RAM_MIN_GB:
            if lang == "fr":
                return (f"❌ **RAM insuffisante sur {nom}**\n\n"
                        f"RAM libre : **{ram_libre}GB** — minimum requis : **{RAM_MIN_GB}GB**\n\n"
                        f"**Libérez de la RAM avant de créer une VM :**\n"
                        f"```bash\n"
                        f"ps aux --sort=-%mem | head -15\n"
                        f"```\n"
                        f"Identifiez les processus consommateurs et réduisez "
                        f"la mémoire allouée aux VMs existantes :\n"
                        f"```bash\n"
                        f"qm set 101 --balloon 512\n"
                        f"echo 1 > /sys/kernel/mm/ksm/run\n"
                        f"```\n"
                        f"Ressources actuelles sur {nom} : "
                        f"RAM {ram_libre}GB libre | Disk {disk_libre}GB libre")
            else:
                return (f"❌ **Not enough RAM on {nom}**\n\n"
                        f"RAM free: **{ram_libre}GB** — minimum required: **{RAM_MIN_GB}GB**\n\n"
                        f"**Free up RAM before creating a VM:**\n"
                        f"```bash\n"
                        f"ps aux --sort=-%mem | head -15\n"
                        f"```\n"
                        f"Identify memory consumers, then reduce existing VM balloon:\n"
                        f"```bash\n"
                        f"qm set 101 --balloon 512\n"
                        f"echo 1 > /sys/kernel/mm/ksm/run\n"
                        f"```\n"
                        f"Current resources on {nom}: "
                        f"RAM {ram_libre}GB free | Disk {disk_libre}GB free")

        if disk_libre < DISK_MIN_GB:
            if lang == "fr":
                return (f"❌ **Espace disque insuffisant sur {nom}**\n\n"
                        f"Disque libre : **{disk_libre}GB** — minimum requis : **{DISK_MIN_GB}GB**\n\n"
                        f"**Libérez de l'espace disque :**\n"
                        f"```bash\n"
                        f"du -sh /var/lib/vz/dump/* 2>/dev/null | sort -rh | head -10\n"
                        f"df -h /\n"
                        f"```\n"
                        f"Supprimez les anciens backups et nettoyez les logs.")
            else:
                return (f"❌ **Not enough disk space on {nom}**\n\n"
                        f"Disk free: **{disk_libre}GB** — minimum required: **{DISK_MIN_GB}GB**\n\n"
                        f"**Free up disk space first:**\n"
                        f"```bash\n"
                        f"du -sh /var/lib/vz/dump/* 2>/dev/null | sort -rh | head -10\n"
                        f"df -h /\n"
                        f"```\n"
                        f"Delete old backups and clean up logs.")

    return None  # Ressources suffisantes — laisser le LLM répondre


async def repondre_question(question: str, msg_id_edition: str = None) -> tuple:
    """Appelle le LLM et retourne (reponse, msg_id)."""
    etat   = surveillance.dernier_etat
    system = system_prompt_chat(etat, surveillance.dernier_lstm)
    msgs   = get_messages_llm(jusqu_a_id=msg_id_edition) if msg_id_edition else get_messages_llm()

    # Vérification ressources avant appel LLM
    refus = _verifier_ressources_vm(question, etat)
    if refus:
        msg = ajouter_message("assistant", refus)
        return refus, msg["id"]

    loop    = asyncio.get_event_loop()
    reponse = await loop.run_in_executor(
        None, lambda: appeler_groq(system, msgs, question, max_tokens=1200)
    )
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

    # Restaurer historique depuis DB si memoire vide
    if not get_historique_complet():
        try:
            from database import get_chat_history
            hist_db = get_chat_history(limit=50)
            if hist_db:
                charger_depuis_db(hist_db)
        except Exception:
            pass

    if get_historique_complet():
        await ws.send_json({
            "type":      "historique",
            "messages":  get_historique_complet()[-50:],
            "timestamp": datetime.now().isoformat(),
        })

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