import re as _re
import time
from datetime import datetime
from fastapi import APIRouter

import agent.surveillance as surveillance
from agent.rules_engine  import get_regles_ia, generer_regles_ia, get_derniere_generation, get_etat_fraicheur
from agent.report_writer import lister_rapports, lire_rapport, generer_pdf, compter_rapports, lire_rapport_structured
from agent.groq_client   import GROQ_MODEL, GROQ_OK, rate_limiter
from agent.config        import INTERVALLE_REGENERATION_REGLES
from agent.chat_history  import get_historique_complet, vider_historique

router = APIRouter()


@router.get("/api/cluster")
def api_cluster():
    if surveillance.dernier_etat:
        return surveillance.dernier_etat
    try:
        from proxmox_api import get_etat_cluster
        from agent.surveillance import _normaliser_etat
        return _normaliser_etat(get_etat_cluster())
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/conversations")
def api_conversations():
    from agent.chat_history import lister_conversations
    return {"conversations": lister_conversations()}


@router.post("/api/conversations")
def api_nouvelle_conversation():
    from agent.chat_history import nouvelle_conversation
    conv_id = nouvelle_conversation()
    return {"conv_id": conv_id}


@router.put("/api/conversations/{conv_id}")
def api_changer_conversation(conv_id: str):
    from agent.chat_history import changer_conversation
    ok = changer_conversation(conv_id)
    return {"ok": ok}


@router.delete("/api/conversations/{conv_id}")
def api_supprimer_conversation(conv_id: str):
    from agent.chat_history import supprimer_conversation
    supprimer_conversation(conv_id)
    return {"ok": True}


@router.get("/api/historique_chat")
def api_historique():
    return {"messages": get_historique_complet(), "count": len(get_historique_complet())}


@router.delete("/api/historique_chat")
def api_clear_chat():
    vider_historique()
    return {"ok": True}


@router.get("/api/allocation")
def api_allocation():
    try:
        from proxmox_api import get_allocation_vs_usage
        return get_allocation_vs_usage()
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/gpu")
def api_gpu():
    try:
        from proxmox_api import get_gpu_info
        return get_gpu_info()
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/vm/{node}/{vmid}")
def api_vm(node: str, vmid: int):
    try:
        from proxmox_api import get_vm_status, get_vm_config
        return {"status": get_vm_status(node, vmid), "config": get_vm_config(node, vmid)}
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/rapports")
def api_rapports(limit: int = 100, offset: int = 0):
    """
    ← MODIFIÉ (pagination) : accepte maintenant limit/offset en query
    params. Après des mois d'utilisation avec des milliers de rapports,
    tout charger d'un coup à chaque ouverture de page devient lent et
    inutile. Retourne aussi "total" pour que le frontend sache s'il reste
    des rapports plus anciens à charger.
    """
    return {
        "rapports": lister_rapports(limit=limit, offset=offset),
        "total":    compter_rapports(),
        "offset":   offset,
        "limit":    limit,
    }


@router.get("/api/rapports/{nom}/structured")
def api_rapport_structured(nom: str):
    """
    ← AJOUT : retourne le JSON complet (causes, steps, action_id,
    action_params) d'un rapport passé, si son .json compagnon existe --
    permet de récupérer l'analyse riche (boutons "Accepter & Exécuter"
    inclus) d'un incident historique à la demande, sans jamais tout
    charger au démarrage. Même vérification anti path traversal que les
    routes /api/rapports/{nom} et /api/rapports/{nom}/pdf.
    """
    from pathlib import Path as _Path
    from fastapi import HTTPException
    base  = _Path("rapports").resolve()
    cible = (base / nom).resolve()
    if not str(cible).startswith(str(base)):
        raise HTTPException(status_code=403, detail="Accès refusé")
    structured = lire_rapport_structured(nom)
    if structured is None:
        raise HTTPException(status_code=404, detail="Analyse complète non disponible pour ce rapport")
    return structured


@router.get("/api/rapports/{nom}")
def api_rapport(nom: str):
    # ── SÉCURITÉ : bloquer path traversal ──────────────────────────
    from pathlib import Path as _Path
    from fastapi import HTTPException   # ← CETTE LIGNE MANQUAIT
    base  = _Path("rapports").resolve()
    cible = (base / nom).resolve()
    if not str(cible).startswith(str(base)):
        raise HTTPException(status_code=403, detail="Accès refusé")
    if not cible.exists():
        raise HTTPException(status_code=404, detail="Rapport non trouvé")
    # ── Lecture + nettoyage artefacts LLM ────────────────────────
    data = lire_rapport(nom)
    if "contenu" in data and data["contenu"]:
        c = data["contenu"]
        c = _re.sub(r'```bash\s*\nCopy\s*\n',    '```bash\n', c)
        c = _re.sub(r'```bash\s*\ncopier\s*\n',  '```bash\n', c)
        c = c.replace('bash\nCopy\n',  '')
        c = c.replace('bash\nCopy',    '')
        c = c.replace('bashCopy\n',    '')
        c = c.replace('bashCopy',      '')
        c = c.replace('bashcopier',    '')
        c = c.replace('bash\ncopier',  '')
        data["contenu"] = c
    return data


# ← AJOUT : export PDF -- cette route n'existait pas dans le fichier reçu,
# c'est ce qui causait le téléchargement "vide"/corrompu : la requête ne
# matchait AUCUNE route existante (/api/rapports/{nom} ne matche pas un
# chemin avec un 2e segment /pdf), tombait donc probablement sur le
# catch-all qui sert l'app React (index.html), téléchargé tel quel avec
# une extension .pdf -- d'où un fichier que ni fpdf2 ni Edge ne reconnaissent
# comme un vrai PDF. Réutilise exactement la même vérification anti path
# traversal que la route juste au-dessus, plutôt que d'en réinventer une.
@router.get("/api/rapports/{nom}/pdf")
def api_rapport_pdf(nom: str):
    from pathlib import Path as _Path
    from fastapi import HTTPException, Response
    base  = _Path("rapports").resolve()
    cible = (base / nom).resolve()
    if not str(cible).startswith(str(base)):
        raise HTTPException(status_code=403, detail="Accès refusé")
    if not cible.exists():
        raise HTTPException(status_code=404, detail="Rapport non trouvé")
    try:
        pdf_bytes = generer_pdf(cible.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        # Polices DejaVu introuvables -- voir agent/report_writer.py FONT_DIR
        raise HTTPException(status_code=500, detail=str(e))
    except RuntimeError as e:
        # fpdf2 non installe
        raise HTTPException(status_code=500, detail=str(e))
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nom.replace(".md", ".pdf")}"'},
    )


@router.get("/api/regles")
def api_regles():
    """
    Retourne les règles actuellement en cache, PLUS un indicateur de fraîcheur :
    si Proxmox n'était pas joignable au dernier cycle de surveillance, ces
    règles sont marquées "regles_perimees": true — ce sont les dernières
    règles connues, pas une régénération sur un cluster actuellement vivant.
    """
    derniere  = get_derniere_generation()
    fraicheur = get_etat_fraicheur()
    return {
        "regles":                 get_regles_ia(),
        "count":                  len(get_regles_ia()),
        "derniere_generation":    derniere,
        "prochaine_regeneration": max(0, INTERVALLE_REGENERATION_REGLES - (time.time() - derniere)),
        "proxmox_accessible":     fraicheur["proxmox_accessible"],
        "regles_perimees":        fraicheur["regles_perimees"],
        "derniere_verification":  fraicheur["derniere_verification"],
    }


@router.post("/api/regles/regenerer")
async def api_regenerer_regles():
    if not surveillance.dernier_etat:
        return {"error": "Donnees cluster non disponibles"}
    regles = await generer_regles_ia(surveillance.dernier_etat)
    return {"regles": regles, "count": len(regles)}


@router.get("/api/db/stats")
def api_db_stats():
    try:
        from database import get_stats_db, DB_OK
        return {"db_ok": DB_OK, "stats": get_stats_db()}
    except Exception:
        return {"db_ok": False, "stats": {}}


@router.get("/api/anomalies/historique")
def api_anomalies(limit: int = 50):
    try:
        from database import get_anomalies
        return {"anomalies": get_anomalies(limit=limit)}
    except Exception:
        return {"anomalies": []}


@router.post("/api/notifications/test")
async def api_test_notif():
    try:
        from notifications import tester_notifications, get_config_notif
        result = await tester_notifications()
        return {"result": result, "config": get_config_notif()}
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/notifications/config")
def api_notif_config():
    try:
        from notifications import get_config_notif
        return get_config_notif()
    except Exception:
        return {}


@router.get("/api/status")
def api_status():
    lstm = surveillance.dernier_lstm
    return {
        "llm":           "groq",
        "groq_model":    GROQ_MODEL,
        "groq_ok":       GROQ_OK,
        "rate_slots":    rate_limiter.slots(),
        "ai_score":      lstm.get("score", 0.0),
        "ai_score_if":   lstm.get("score_if", 0.0),
        "ai_score_lstm": lstm.get("score_lstm", 0.0),
        "lstm_ready":    lstm.get("lstm_ready", False),
        "chat_messages": len(get_historique_complet()),
        "timestamp":     datetime.now().isoformat(),
    }

# ══════════════════════════════════════════════════════════════════════════════
# Actions de remédiation — Human-in-the-Loop Automation
# L'utilisateur accepte → l'agent exécute sur Proxmox
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/actions/catalogue")
def api_actions_catalogue():
    """Liste toutes les actions disponibles avec leur niveau de risque."""
    try:
        from action_executor import ACTION_META
        return {"actions": ACTION_META}
    except Exception as e:
        return {"error": str(e), "actions": {}}


@router.post("/api/actions/execute")
def api_action_execute(body: dict):
    """
    Exécute une action de remédiation après acceptation par l'utilisateur.
    Body: { "action_id": "enable_ksm", "params": {"node": "pve1"} }
    """
    from fastapi import HTTPException
    try:
        from action_executor import executer_action
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"action_executor non disponible: {e}")

    action_id = body.get("action_id", "")
    params    = body.get("params", {})
    if not action_id:
        raise HTTPException(status_code=400, detail="action_id requis")

    result = executer_action(action_id, params)
    return result


@router.get("/api/actions/historique")
def api_actions_historique():
    """Historique de toutes les actions exécutées par l'agent."""
    try:
        from action_executor import get_action_history
        return {"historique": get_action_history()}
    except Exception as e:
        return {"historique": [], "error": str(e)}