import asyncio
import time
from datetime import datetime
from fastapi import WebSocket, WebSocketDisconnect

from agent.chat_history import (
    ajouter_message, editer_message, get_messages_llm,
    vider_historique, get_historique_complet, charger_depuis_db, next_id
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
    RAM_MIN_GB  = 1.0
    DISK_MIN_GB = 10.0

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


async def repondre_question(question: str, msg_id_edition: str = None) -> tuple:
    """
    Appelle le LLM avec contexte complet :
    - Vérification ressources (sans appel LLM si refus)
    - Etat cluster Proxmox niveaux 1+2+3
    - Ressources PC hôte Windows (metriques_pc_hote.py)
    """
    etat    = surveillance.dernier_etat
    pc_hote = _get_pc_hote()

    # Vérification avant LLM — économise le quota Groq
    refus = _verifier_ressources_vm(question, etat)
    if refus:
        msg = ajouter_message("assistant", refus)
        return refus, msg["id"]

    system = system_prompt_chat(etat, surveillance.dernier_lstm, pc_hote)
    msgs   = get_messages_llm(jusqu_a_id=msg_id_edition) if msg_id_edition else get_messages_llm()

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