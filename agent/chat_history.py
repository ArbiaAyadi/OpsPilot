import json
import os
import time
from datetime import datetime

# ── Persistance sur disque ────────────────────────────────────────────────────
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONVERSATIONS_FILE = os.path.join(_BASE_DIR, 'conversations.json')

# ── Conversation active ───────────────────────────────────────────────────────
_conversations: dict = {}
_conv_active:   str  = None
_msg_counter:   int  = 0


def next_id() -> str:
    global _msg_counter
    _msg_counter += 1
    return f"msg_{_msg_counter}_{int(time.time())}"


def _conv_id_new() -> str:
    return f"conv_{int(time.time())}"


def sauvegarder_conversations():
    """Sauvegarde toutes les conversations sur disque (JSON)."""
    try:
        data = {
            "conversations": _conversations,
            "active":        _conv_active,
        }
        with open(CONVERSATIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        print(f"[Chat] Sauvegarde: {e}")


def charger_conversations():
    """
    Charge les conversations depuis le disque au démarrage.
    Ne restaure PAS la conversation active — l'utilisateur voit
    l'écran d'accueil et peut choisir une conversation dans la sidebar.
    """
    global _conversations, _conv_active
    try:
        if os.path.exists(CONVERSATIONS_FILE):
            with open(CONVERSATIONS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            _conversations = data.get("conversations", {})
            # Ne pas restaurer _conv_active — écran d'accueil au démarrage
            _conv_active = None
            print(f"[Chat] {len(_conversations)} conversation(s) chargée(s) depuis disque")
        else:
            print("[Chat] Aucune conversation sauvegardée — démarrage vide")
    except Exception as e:
        print(f"[Chat] Chargement: {e}")


# Charger automatiquement au démarrage du module
charger_conversations()


def get_conv_active() -> str:
    """Retourne l'ID de la conversation active, en crée une si nécessaire."""
    global _conv_active
    if _conv_active is None or _conv_active not in _conversations:
        _conv_active = _conv_id_new()
        _conversations[_conv_active] = []
    return _conv_active


def nouvelle_conversation() -> str:
    """Crée une nouvelle conversation et la rend active."""
    global _conv_active
    conv_id = _conv_id_new()
    _conversations[conv_id] = []
    _conv_active = conv_id
    sauvegarder_conversations()
    return conv_id


def changer_conversation(conv_id: str) -> bool:
    """Change la conversation active."""
    global _conv_active
    if conv_id in _conversations:
        _conv_active = conv_id
        sauvegarder_conversations()
        return True
    return False


def lister_conversations() -> list:
    """Liste toutes les conversations avec leur premier message."""
    result = []
    for conv_id, msgs in _conversations.items():
        premier = next((m for m in msgs if m["role"] == "user"), None)
        result.append({
            "id":      conv_id,
            "active":  conv_id == _conv_active,
            "count":   len(msgs),
            "preview": premier["content"][:60] if premier else "New conversation",
            "ts":      msgs[0]["timestamp"] if msgs else datetime.now().isoformat(),
        })
    return sorted(result, key=lambda x: x["ts"], reverse=True)


def supprimer_conversation(conv_id: str):
    """Supprime une conversation."""
    global _conv_active
    if conv_id in _conversations:
        del _conversations[conv_id]
    if _conv_active == conv_id:
        _conv_active = None
        get_conv_active()  # crée une nouvelle


def ajouter_message(role: str, content: str, msg_id: str = None) -> dict:
    """Ajoute un message à la conversation active et sauvegarde sur disque."""
    conv_id = get_conv_active()
    msg = {
        "id":        msg_id or next_id(),
        "role":      role,
        "content":   content,
        "timestamp": datetime.now().isoformat(),
        "edited":    False,
        "conv_id":   conv_id,
    }
    _conversations[conv_id].append(msg)
    # Limiter à 50 messages par conversation
    if len(_conversations[conv_id]) > 50:
        _conversations[conv_id].pop(0)
    # Sauvegarder sur disque
    sauvegarder_conversations()
    return msg


def editer_message(msg_id: str, nouveau_contenu: str) -> int:
    """Édite un message et supprime les messages suivants."""
    conv_id = get_conv_active()
    msgs    = _conversations.get(conv_id, [])
    for i, m in enumerate(msgs):
        if m["id"] == msg_id and m["role"] == "user":
            msgs[i]["content"]   = nouveau_contenu
            msgs[i]["edited"]    = True
            msgs[i]["timestamp"] = datetime.now().isoformat()
            del msgs[i + 1:]
            return i
    return -1


def get_messages_llm(jusqu_a_id: str = None, max_messages: int = 6) -> list:
    """
    Retourne les N derniers messages pour le LLM.
    Limité à 6 par défaut pour des réponses rapides.
    """
    conv_id = get_conv_active()
    msgs    = _conversations.get(conv_id, [])
    result  = []
    for m in msgs:
        result.append({"role": m["role"], "content": m["content"]})
        if jusqu_a_id and m["id"] == jusqu_a_id:
            break
    return result[-max_messages:]


def vider_historique():
    """Vide la conversation active."""
    conv_id = get_conv_active()
    if conv_id in _conversations:
        _conversations[conv_id].clear()
    sauvegarder_conversations()


def get_historique_complet() -> list:
    """Retourne les messages de la conversation active."""
    conv_id = get_conv_active()
    return _conversations.get(conv_id, [])


def charger_depuis_db(messages_db: list):
    """Charge l'historique depuis la DB au démarrage."""
    conv_id = get_conv_active()
    for m in messages_db:
        _conversations[conv_id].append({
            "id":        m.get("msg_id", next_id()),
            "role":      m.get("role"),
            "content":   m.get("content"),
            "timestamp": str(m.get("time", datetime.now().isoformat())),
            "edited":    m.get("edited", False),
            "conv_id":   conv_id,
        })