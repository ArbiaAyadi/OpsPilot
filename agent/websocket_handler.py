"""
websocket_handler.py — Gestion de la connexion WebSocket du chat assistant :
réception des questions, appel LLM avec contexte cluster + PC hôte,
correction post-traitement des erreurs systématiques du modèle.

← CORRECTION (3 bugs) :
1. Cache PC hôte dédoublonné -- ce fichier gardait son propre cache local
   (TTL 60s) EN PLUS de celui déjà présent dans
   metriques_pc_hote.collecter_ressources_pc_hote() (TTL 30s, ajouté
   séparément). Deux caches indépendants pour la même donnée pouvaient
   renvoyer des valeurs différentes au même instant selon lequel venait
   d'expirer. _get_pc_hote() délègue maintenant entièrement au cache de
   metriques_pc_hote.py -- une seule politique de cache, partagée avec
   surveillance.py qui appelle la même fonction à chaque cycle.
2. _detecter_langue() comparait par SOUS-CHAÎNE, pas par mot entier -- "de"
   matchait "deploy"/"node", "est" matchait "test". Concrètement, deux des
   boutons Quick Start de PageAssistant.jsx ("I want to deploy Docker on a
   Proxmox VM...", "...create a Kubernetes node...") étaient détectés
   comme français à cause de ces mots anglais -- un refus de ressources
   insuffisantes s'affichait alors en français à une question posée en
   anglais. Même correctif que _detect_intent() dans prompts.py :
   comparaison par mot entier (\\b...\\b). Même chose appliquée à
   _verifier_ressources_vm() (mots_creation/mots_vm/mots_lxc) -- "ct" comme
   mot-clé LXC matchait "correct", "select", "connect" en sous-chaîne.
3. Règle 8 de _corriger_reponse_llm() (qm set) forçait TOUT VMID différent
   de 101 vers 101 -- y compris un VMID réel et valide (ex: 103,
   linux-vm2). Une réponse correcte "qm set 103 --balloon 512" devenait
   "qm set 101 --balloon 512" : mauvaise VM, silencieusement. Corrigé pour
   ne réécrire que les VMID qui ne correspondent à AUCUNE VM réellement
   connue (liste déjà calculée dans repondre_question(), transmise en
   paramètre) -- un VMID halluciné retombe toujours sur 101 comme avant,
   un VMID réel n'est plus jamais touché.

← CORRECTION (4e bug, ajouté après-coup) :
4. handle_connection() n'avait AUCUNE protection autour de l'appel à
   repondre_question() -- si CETTE fonction (ou quoi que ce soit qu'elle
   appelle : system_prompt_chat, appeler_groq, _verifier_ressources_vm...)
   levait une exception non prévue, elle remontait jusqu'à la boucle
   "while True" de handle_connection(), qui la laisse passer jusqu'au
   try/except EXTÉRIEUR -- lequel retire le client de "clients" et
   TERMINE la boucle, donc ferme la connexion WebSocket entière. Résultat
   concret : une seule question qui déclenche une exception imprévue tue
   toute la session de chat en silence (rien d'affiché côté utilisateur,
   juste un print() côté serveur) -- ET rend tout message suivant sur
   cette même connexion (changer de conversation, poser une autre
   question) inopérant, puisque la connexion elle-même n'existe plus.
   Chaque bloc "question"/"edit_message" a maintenant son propre
   try/except : en cas d'erreur, un message "type: error" est renvoyé au
   client ET la boucle continue -- la connexion reste vivante pour tout
   ce qui suit.

Import re consolidé en un seul, en tête de fichier -- remplace l'ancien
"import re as _re" (jamais utilisé) et le second "import re as _re2" local
à _corriger_reponse_llm (redondant avec le premier).
"""
import asyncio
import re
from datetime import datetime
from fastapi import WebSocket, WebSocketDisconnect

from agent.chat_history import (
    ajouter_message, editer_message, get_messages_llm,
    vider_historique, get_historique_complet, charger_depuis_db, next_id,
    nouvelle_conversation, changer_conversation, supprimer_conversation, lister_conversations
)
from agent.groq_client  import appeler_groq_chat, GROQ_MODEL, GROQ_OK
from agent.prompts      import system_prompt_chat
import agent.surveillance as surveillance

clients: list = []


def _get_pc_hote() -> dict:
    try:
        from metriques_pc_hote import collecter_ressources_pc_hote
        return collecter_ressources_pc_hote()
    except Exception as e:
        print(f"[PC Host] {e}")
        return {"disponible": False}


def _contient_mot(texte: str, mots: list) -> bool:
    return any(re.search(rf'\b{re.escape(m)}\b', texte) for m in mots)


def _detecter_langue(question: str) -> str:
    mots_fr = ["créer", "construire", "ajouter", "veux", "je", "comment",
               "pourquoi", "quoi", "quel", "quelle", "mon", "ma", "les",
               "du", "de", "est", "sont", "pour", "avec", "sur", "dans",
               "combien", "puis-je", "peux"]
    return "fr" if _contient_mot(question.lower(), mots_fr) else "en"


def _verifier_ressources_vm(question: str, etat: dict) -> str | None:
    q = question.lower()

    mots_creation = ['create', 'build', 'add', 'new vm', 'créer', 'construire',
                     'ajouter', 'nouvelle vm', 'deploy', 'déployer', 'install',
                     'lancer', 'nouveau', 'faire']
    mots_vm       = ['vm', 'virtual machine', 'machine virtuelle', 'container',
                     'lxc', 'conteneur', 'instance', 'nœud', 'node']

    mots_lxc      = ['lxc', 'conteneur', 'container', 'ct']
    is_lxc      = _contient_mot(q, mots_lxc)
    is_creation = _contient_mot(q, mots_creation) and _contient_mot(q, mots_vm)
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
    RAM_MIN_GB  = 0.25 if is_lxc else 1.0
    DISK_MIN_GB = 5.0  if is_lxc else 10.0

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

        # ← AJOUT : VMID d'exemple pris parmi les VMs RÉELLEMENT présentes
        # sur CE nœud précis (nom) -- avant, "qm set 101" était codé en
        # dur partout, y compris quand le nœud concerné n'hébergeait même
        # pas la VM 101 (ex: RAM insuffisante détectée sur pve2, dont la
        # seule VM réelle est 103) -- l'exemple pointait alors vers une
        # VM sur le mauvais nœud, ou une VM qui n'existe pas du tout côté
        # cluster. Repli sur "<vmid>" (placeholder explicite) si aucune
        # VM n'est connue sur ce nœud, plutôt que d'inventer un nombre.
        vms_sur_ce_noeud = [v for v in (etat.get("vms") or []) if str(v.get("noeud","")).lower() == nom.lower()]
        vmid_exemple = vms_sur_ce_noeud[0].get("vmid", "<vmid>") if vms_sur_ce_noeud else "<vmid>"

        # ← AJOUT : étape de migration, en PREMIER -- l'ordre établi partout
        # ailleurs dans le projet est migration → KSM → ballooning → RAM
        # physique → nouveau nœud (voir prompts.py, incident_prompt.py).
        # Ce message de secours sautait directement à KSM/ballooning sans
        # jamais mentionner la migration, la incohérent avec le reste de
        # l'app. Cherche un autre nœud EN LIGNE comme cible -- absent si
        # aucun autre nœud n'est disponible (cluster à un seul nœud).
        autre_noeud_en_ligne = next(
            (n2.get("nom") for n2 in etat.get("noeuds", [])
             if n2.get("nom", "").lower() != nom.lower()
             and str(n2.get("statut","")).lower() in ("online","up","en ligne")),
            None
        )
        etape_migration_fr = (
            f"Migrez une VM existante vers un autre nœud (le plus rapide) :\n"
            f"```bash\nqm migrate {vmid_exemple} {autre_noeud_en_ligne} --online\n```\n"
        ) if autre_noeud_en_ligne and vmid_exemple != "<vmid>" else ""
        etape_migration_en = (
            f"Migrate an existing VM to another node (fastest option):\n"
            f"```bash\nqm migrate {vmid_exemple} {autre_noeud_en_ligne} --online\n```\n"
        ) if autre_noeud_en_ligne and vmid_exemple != "<vmid>" else ""

        if ram_libre < RAM_MIN_GB:
            if lang == "fr":
                return (f"❌ **RAM insuffisante sur {nom}**\n\n"
                        f"RAM libre : **{ram_libre}GB** — minimum requis : **{RAM_MIN_GB}GB**\n\n"
                        f"**Libérez de la RAM avant de créer une VM :**\n"
                        f"```bash\nps aux --sort=-%mem | head -15\n```\n"
                        f"{etape_migration_fr}"
                        f"Réduisez le balloon des VMs existantes sur {nom} :\n"
                        f"```bash\nqm set {vmid_exemple} --balloon 512\necho 1 > /sys/kernel/mm/ksm/run\n```\n"
                        f"Ressources actuelles : RAM {ram_libre}GB libre | Disk {disk_libre}GB libre")
            else:
                return (f"❌ **Not enough RAM on {nom}**\n\n"
                        f"RAM free: **{ram_libre}GB** — minimum required: **{RAM_MIN_GB}GB**\n\n"
                        f"**Free up RAM before creating a VM:**\n"
                        f"```bash\nps aux --sort=-%mem | head -15\n```\n"
                        f"{etape_migration_en}"
                        f"Reduce existing VM balloon on {nom}:\n"
                        f"```bash\nqm set {vmid_exemple} --balloon 512\necho 1 > /sys/kernel/mm/ksm/run\n```\n"
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


def _corriger_reponse_llm(reponse: str, next_vmid: int, vmids_connus: list = None) -> str:
    vmids_connus = vmids_connus or []

    if next_vmid <= 103:
        next_vmid = 104

    reponse = re.sub(
        r'(pct create\s+)\d+',
        lambda m: f"{m.group(1)}{next_vmid}",
        reponse
    )

    reponse = re.sub(
        r'(pct start\s+)\d+',
        lambda m: f"{m.group(1)}{next_vmid}",
        reponse
    )

    reponse = re.sub(
        r'(--hostname\s+\S*?)\d+\b',
        lambda m: m.group(0).rsplit(
            m.group(0).rstrip().split()[-1].lstrip('abcdefghijklmnopqrstuvwxyz-_'), 1
        )[0] + str(next_vmid),
        reponse
    )

    reponse = re.sub(r'\s*--disk\s+\S+', '', reponse)

    reponse = re.sub(r'--cpu\s+(\d+)', r'--cores \1', reponse)

    reponse = re.sub(
        r'--net0\s+(?!name=)(\w+)',
        r'--net0 name=eth0,bridge=\1,ip=dhcp',
        reponse
    )

    reponse = re.sub(r'\s*--template\s+\S+', '', reponse)

    def _corriger_qm_set(m):
        if int(m.group(1)) in vmids_connus:
            return m.group(0)
        return "qm set 101"
    reponse = re.sub(r'qm set\s+(\d{3,})', _corriger_qm_set, reponse)

    reponse = re.sub(
        r'pveam download local\s+(?!debian-12)(\S+)',
        'pveam download local debian-12-standard_12.7-1_amd64.tar.zst',
        reponse
    )

    reponse = re.sub(
        r'(--rootfs\s+\S+:)([01])(\s|$)',
        r'\g<1>2\3',
        reponse
    )

    # 11. Corriger "migrate <vmid> <node>" -- commande INEXISTANTE dans
    # Proxmox (le modèle l'invente, confirmé en observant une vraie
    # réponse : "migrate 105 pve1"). La vraie syntaxe est "qm migrate"
    # pour une VM QEMU (avec --online pour rester à chaud), "pct migrate"
    # pour un conteneur LXC -- on ne peut pas distinguer depuis le texte
    # seul, qm est le cas le plus courant dans ce projet donc le choix
    # par défaut le plus utile ; --online ajouté systématiquement (sans
    # effet néfaste si la VM est déjà arrêtée).
    reponse = re.sub(
        r'(?<!qm )(?<!pct )\bmigrate\s+(\d+)\s+(pve\d+)\b',
        r'qm migrate \1 \2 --online',
        reponse
    )

    return reponse


async def repondre_question(question: str, msg_id_edition: str = None) -> tuple:
    etat    = surveillance.dernier_etat
    pc_hote = _get_pc_hote()

    refus = _verifier_ressources_vm(question, etat)
    if refus:
        msg = ajouter_message("assistant", refus)
        return refus, msg["id"]

    system = system_prompt_chat(etat, surveillance.dernier_lstm, pc_hote, question)
    msgs_all = get_messages_llm(jusqu_a_id=msg_id_edition) if msg_id_edition else get_messages_llm()
    msgs = msgs_all[-10:] if len(msgs_all) > 10 else msgs_all

    vmids_connus = [
        int(v.get("vmid", 0))
        for v in (etat or {}).get("vms", [])
        if str(v.get("vmid","")).isdigit()
    ]
    next_vmid = max(vmids_connus) + 1 if vmids_connus else 104

    loop    = asyncio.get_event_loop()
    # ← MODIFIÉ : appeler_groq -> appeler_groq_chat. Mêmes paramètres
    # exacts (system, msgs, question, max_tokens=1200), même modèle en
    # cascade, même température -- seul le compte Groq change, pour ne
    # plus consommer le même budget journalier que la surveillance/les
    # règles. Tant que GROQ_API_KEY_CHAT n'est pas définie dans .env,
    # utilise le même compte qu'avant : comportement du chat inchangé.
    reponse = await loop.run_in_executor(
        None, lambda: appeler_groq_chat(system, msgs, question, max_tokens=1200)
    )

    reponse = _corriger_reponse_llm(reponse, next_vmid, vmids_connus)

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
                # ← AJOUT (4e correctif, voir docstring du fichier) : sans
                # ce try/except, une exception ici tuait toute la
                # connexion WebSocket -- voir le raisonnement complet en
                # tête de fichier.
                try:
                    reponse, msg_id_rep = await repondre_question(question)
                except Exception as e:
                    print(f"[WS] Erreur traitement question: {e}")
                    await ws.send_json({
                        "type":      "error",
                        "content":   "Une erreur est survenue lors du traitement de votre question. Réessayez.",
                        "timestamp": datetime.now().isoformat(),
                    })
                    continue
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
                # ← AJOUT : même filet de sécurité que le bloc "question".
                try:
                    reponse, msg_id_rep = await repondre_question(nouveau, msg_id_edition=msg_id)
                except Exception as e:
                    print(f"[WS] Erreur traitement edit_message: {e}")
                    await ws.send_json({
                        "type":      "error",
                        "content":   "Une erreur est survenue lors du traitement de votre modification. Réessayez.",
                        "timestamp": datetime.now().isoformat(),
                    })
                    continue
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