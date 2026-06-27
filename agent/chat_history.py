
import time
from datetime import datetime

# Etat global
historique_chat: list  = []
_msg_counter: int      = 0


def next_id() -> str:
    global _msg_counter
    _msg_counter += 1
    return f"msg_{_msg_counter}_{int(time.time())}"


def ajouter_message(role: str, content: str, msg_id: str = None) -> dict:
    """Ajoute un message et retourne le dict complet."""
    msg = {
        "id":        msg_id or next_id(),
        "role":      role,
        "content":   content,
        "timestamp": datetime.now().isoformat(),
        "edited":    False,
    }
    historique_chat.append(msg)
    # Limiter l'historique en memoire
    if len(historique_chat) > 100:
        historique_chat.pop(0)
    return msg


def editer_message(msg_id: str, nouveau_contenu: str) -> int:
    """
    Edite un message utilisateur et supprime les messages suivants.
    Retourne l'index du message edite, ou -1 si non trouve.
    """
    for i, m in enumerate(historique_chat):
        if m["id"] == msg_id and m["role"] == "user":
            historique_chat[i]["content"]   = nouveau_contenu
            historique_chat[i]["edited"]    = True
            historique_chat[i]["timestamp"] = datetime.now().isoformat()
            del historique_chat[i + 1:]
            return i
    return -1


def get_messages_llm(jusqu_a_id: str = None, max_messages: int = 8) -> list:
    """
    Retourne les derniers messages au format LLM (role + content).
    Si jusqu_a_id fourni, s'arrete a ce message.
    """
    msgs = []
    for m in historique_chat:
        msgs.append({"role": m["role"], "content": m["content"]})
        if jusqu_a_id and m["id"] == jusqu_a_id:
            break
    return msgs[-max_messages:]


def vider_historique():
    """Vide l'historique en memoire."""
    historique_chat.clear()


def get_historique_complet() -> list:
    """Retourne tout l'historique."""
    return historique_chat


def charger_depuis_db(messages_db: list):
    """Charge l'historique depuis la base de donnees au demarrage."""
    for m in messages_db:
        historique_chat.append({
            "id":        m.get("msg_id", next_id()),
            "role":      m.get("role"),
            "content":   m.get("content"),
            "timestamp": str(m.get("time", datetime.now().isoformat())),
            "edited":    m.get("edited", False),
        })