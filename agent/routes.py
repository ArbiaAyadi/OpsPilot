import re as _re
import time
from datetime import datetime
from fastapi import APIRouter, Depends

import agent.surveillance as surveillance
from agent.rules_engine  import get_regles_ia, generer_regles_ia, get_derniere_generation, get_etat_fraicheur, regles_services_actives
from agent.report_writer import lister_rapports, lire_rapport, generer_pdf, compter_rapports, lire_rapport_structured
from agent.groq_client   import (GROQ_MODEL, GROQ_OK, rate_limiter,
                                  tokens_utilises_aujourdhui, budget_journalier_restant,
                                  TOKENS_QUOTIDIENS_LIMITE, _fournisseurs_secours,
                                  DERNIER_FOURNISSEUR, groq_bloque_jusqua)
from agent.config        import INTERVALLE_REGENERATION_REGLES
from agent.chat_history  import get_historique_complet, vider_historique

# ← AJOUT : dependencies=[Depends(get_current_user)] protège TOUTES les
# routes de ce fichier d'un coup -- recommendations, rapports, règles,
# actions... tout nécessite désormais une session valide. auth_routes.py
# (login/signup/etc.) vit dans son PROPRE router, jamais inclus ici --
# c'est le seul endroit où l'utilisateur n'est pas encore authentifié,
# volontairement séparé pour ne jamais risquer de le protéger par erreur.
from auth_routes import get_current_user
router = APIRouter(dependencies=[Depends(get_current_user)])


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


# ← AJOUT : suppression d'un rapport -- n'existait pas du tout avant (aucune
# route DELETE sur /api/rapports/*). Incidents reste un historique en
# lecture seule pour l'ANALYSE (voir StructuredIncident côté frontend),
# mais un faux positif ou du bruit doit pouvoir être nettoyé -- même
# principe déjà accepté sur Recommendations (DELETE /api/recommendations/
# {id}). Même vérification anti path-traversal que les 3 routes
# /api/rapports/{nom}* ci-dessus, jamais réinventée différemment. Supprime
# aussi le .json compagnon (structured complet) s'il existe, pour ne rien
# laisser derrière.
@router.delete("/api/rapports/{nom}")
def api_rapport_delete(nom: str):
    from pathlib import Path as _Path
    from fastapi import HTTPException
    base  = _Path("rapports").resolve()
    cible = (base / nom).resolve()
    if not str(cible).startswith(str(base)):
        raise HTTPException(status_code=403, detail="Accès refusé")
    if not cible.exists():
        raise HTTPException(status_code=404, detail="Rapport non trouvé")
    try:
        cible.unlink()
        json_compagnon = cible.with_suffix(".json")
        if json_compagnon.exists():
            json_compagnon.unlink()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur suppression: {e}")
    return {"ok": True}


# ← AJOUT : depuis un incident (identifié par son nom de rapport), trouver
# la recommendation qui en a résulté -- répond à "savoir pour chaque
# incident quelle est sa recommendation" côté Incidents. 404 propre si
# aucune (incident sans recommendation associée, ex: rapport dégradé
# généré pendant une panne DB) plutôt qu'une liste vide ambiguë.
@router.get("/api/recommendations/by-report/{nom}")
def api_recommendation_by_report(nom: str):
    from fastapi import HTTPException
    from database import trouver_recommendation_par_rapport
    rec = trouver_recommendation_par_rapport(nom)
    if not rec:
        raise HTTPException(status_code=404, detail="Aucune recommendation liée à ce rapport")
    return {**(rec.get("donnees") or {}), "recommendation_id": rec["id"], "status": rec.get("statut")}


@router.get("/api/regles")
def api_regles():
    """
    Retourne les règles actuellement en cache, PLUS un indicateur de fraîcheur :
    si Proxmox n'était pas joignable au dernier cycle de surveillance, ces
    règles sont marquées "regles_perimees": true — ce sont les dernières
    règles connues, pas une régénération sur un cluster actuellement vivant.

    ← AJOUT : fusionne maintenant les seuils de service (regles_services_actives)
    avec les 22 règles nœud/VM -- ils étaient déjà réellement appliqués
    (vm_app_monitor.py) mais jamais visibles ici avant. "count" reflète le
    total fusionné.
    """
    derniere  = get_derniere_generation()
    fraicheur = get_etat_fraicheur()
    regles_service = regles_services_actives(surveillance.dernier_etat or {})
    toutes_regles  = get_regles_ia() + regles_service
    return {
        "regles":                 toutes_regles,
        "count":                  len(toutes_regles),
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


# ══════════════════════════════════════════════════════════════════════════════
# Recommendations — persistance de l'espace de travail actionnable
# ══════════════════════════════════════════════════════════════════════════════
# ← AJOUT : avant, suggestions ne vivait qu'en mémoire React (App.jsx),
# perdue à chaque rechargement de page ("la recommendation disparu très
# vite si je ne suis pas là"). Ne renvoie QUE les recommendations OUVERTES
# -- volontairement borné, pas un historique qui grossit ("si il va être
# un historique c'est très longue" -- l'historique complet reste sur
# Incidents). Ne touche PAS à /api/actions/execute ci-dessous (route de
# sécurité, aucune raison d'en changer le contrat) -- la résolution passe
# par sa propre route dédiée, déclenchée soit par un clic explicite sur la
# carte, soit automatiquement depuis le frontend quand une action réussit.
@router.get("/api/recommendations")
def api_recommendations():
    try:
        from database import get_recommendations_ouvertes
        ouvertes = get_recommendations_ouvertes()
        # donnees est l'objet suggestion complet tel que sauvegardé --
        # on y injecte juste l'id DB pour que le frontend puisse
        # appeler /resolve dessus plus tard sans ambiguïté.
        return {"recommendations": [
            {**r["donnees"], "recommendation_id": r["id"]} for r in ouvertes if r.get("donnees")
        ]}
    except Exception as e:
        return {"recommendations": [], "error": str(e)}


@router.post("/api/recommendations/{rec_id}/resolve")
def api_recommendation_resolve(rec_id: int):
    try:
        from database import marquer_recommendation_resolue
        marquer_recommendation_resolue(rec_id)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ← AJOUT : historique paginé + suppression définitive. Resolve (ci-dessus)
# garde une trace consultable ; delete la retire complètement, sans trace --
# même distinction que sur /api/rapports (historique) vs une suppression,
# jamais confondues.
@router.get("/api/recommendations/history")
def api_recommendations_history(limit: int = 50, offset: int = 0):
    try:
        from database import get_recommendations_resolues, compter_recommendations_resolues
        resolues = get_recommendations_resolues(limit=limit, offset=offset)
        return {
            "recommendations": [
                {**r["donnees"], "recommendation_id": r["id"], "resolu_at": r.get("resolu_at").isoformat() if r.get("resolu_at") else None}
                for r in resolues if r.get("donnees")
            ],
            "total":  compter_recommendations_resolues(),
            "offset": offset,
            "limit":  limit,
        }
    except Exception as e:
        return {"recommendations": [], "total": 0, "offset": offset, "limit": limit, "error": str(e)}


@router.delete("/api/recommendations/{rec_id}")
def api_recommendation_delete(rec_id: int):
    try:
        from database import supprimer_recommendation
        supprimer_recommendation(rec_id)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


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
    # ← AJOUT : consommation réelle de tokens du jour, par charge de
    # travail (surveillance vs chat -- compteurs séparés, voir
    # agent/groq_client.py). Chiffres RÉELS rapportés par l'API Groq sur
    # chaque appel, pas une estimation. Exposé ici plutôt que sur une
    # nouvelle route : /api/status est déjà interrogé périodiquement par
    # le frontend, aucun câblage supplémentaire nécessaire côté React.
    # Remis à zéro automatiquement au changement de jour.
    tokens_main = tokens_utilises_aujourdhui("main")
    tokens_chat = tokens_utilises_aujourdhui("chat")
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
        "tokens_limite_jour":     TOKENS_QUOTIDIENS_LIMITE,
        "tokens_utilises_jour":   tokens_main,
        "tokens_restants_jour":   budget_journalier_restant("main"),
        "tokens_utilises_chat":   tokens_chat,
        "tokens_pct_utilise":     round(tokens_main / TOKENS_QUOTIDIENS_LIMITE * 100, 1) if TOKENS_QUOTIDIENS_LIMITE else 0.0,
        # ← AJOUT : consommation des fournisseurs de SECOURS (Mistral,
        # Ollama...), chacun avec son quota indépendant. Permet de voir
        # d'un coup d'oeil si la bascule a eu lieu et combien il reste
        # côté secours -- sans ça, un basculement passait totalement
        # inaperçu depuis le tableau de bord.
        "fournisseurs_secours":   [
            {"nom": n, "tokens_utilises": tokens_utilises_aujourdhui(n)}
            for n in _fournisseurs_secours()
        ],
        "fournisseur_actif":      DERNIER_FOURNISSEUR,
        # ← AJOUT : Groq peut rejeter tous les appels (limitation au niveau
        # du compte) alors que notre compteur de tokens affiche un budget
        # sain -- il n'incrémente que sur les appels REUSSIS. Sans cette
        # information, le tableau de bord annonçait "HEALTHY" pendant
        # qu'aucune analyse n'aboutissait. Secondes restantes avant de
        # retenter, 0 si disponible.
        "groq_bloque_secondes":   max(0, int(groq_bloque_jusqua() - time.time())) if groq_bloque_jusqua() else 0,
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