"""
auth_routes.py — Endpoints d'authentification OpsPilot.

Router SÉPARÉ de routes.py, volontairement -- protéger toutes les routes
"métier" (recommendations, rapports, actions...) se fait en ajoutant
dependencies=[Depends(get_current_user)] au niveau du router dans
routes.py ; ce fichier-ci reste le SEUL endroit où l'utilisateur n'est PAS
encore authentifié (signup/login) ou vient de le devenir (le reste). Le
séparer permet une revue de sécurité ciblée sur un seul fichier, plutôt
que noyé dans le reste de l'API.

Toute la logique de sécurité (hashage, validation de force, tokens,
limitation des tentatives, code d'invitation) vit dans auth.py -- ce
fichier ne fait qu'orchestrer : lire la requête, appeler auth.py et
database.py, répondre. Aucune décision de sécurité prise ici directement.
"""
import os
from fastapi import APIRouter, Request, Response, HTTPException, Depends

import auth
import database

router = APIRouter()

COOKIE_NAME = "opspilot_session"
# ← IMPORTANT : False par défaut pour correspondre à ton déploiement actuel
# (http://127.0.0.1:8088, confirmé dans tes logs -- pas de TLS). Un cookie
# "secure=True" n'est JAMAIS envoyé par le navigateur sur une connexion
# HTTP simple -- le laisser à True par défaut aurait cassé la connexion
# silencieusement (login qui semble réussir mais le cookie ne se pose
# jamais). Passe COOKIE_SECURE=true dans .env dès que ce projet tourne
# derrière un vrai HTTPS (obligatoire avant tout déploiement en dehors de
# ton poste de dev -- un cookie de session envoyé en clair est
# interceptable sur le réseau).
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"


def _poser_cookie_session(response: Response, token: str):
    response.set_cookie(
        key=COOKIE_NAME, value=token,
        max_age=auth.DUREE_SESSION_S, httponly=True,
        secure=COOKIE_SECURE, samesite="lax", path="/",
    )


def _utilisateur_public(u: dict) -> dict:
    """Jamais renvoyer password_hash vers le frontend, même par erreur --
    un seul endroit qui filtre, plutôt que de compter sur chaque route
    pour ne pas oublier un champ."""
    return {"id": u["id"], "email": u["email"], "nom": u["nom"], "created_at": str(u.get("created_at", ""))}


def get_current_user(request: Request) -> dict:
    """
    Dependency FastAPI -- à utiliser via Depends(get_current_user) sur
    toute route qui doit être protégée, ou au niveau du router entier
    (dependencies=[Depends(get_current_user)] dans APIRouter(...) côté
    routes.py) pour tout protéger d'un coup.
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    session = database.get_session(token)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired, please log in again")
    user = database.get_utilisateur_par_id(session["user_id"])
    if not user or not user.get("is_active", True):
        raise HTTPException(status_code=401, detail="Account disabled")
    return user


@router.post("/api/auth/signup")
def api_signup(body: dict, response: Response):
    email    = (body.get("email") or "").strip()
    password = body.get("password") or ""
    nom      = (body.get("nom") or "").strip()
    code     = body.get("invite_code") or ""

    # ← Code d'invitation vérifié AVANT tout le reste -- pas la peine de
    # valider un email/mot de passe si l'inscription elle-même n'est pas
    # autorisée. Message volontairement générique : ne pas confirmer si
    # c'est le code qui est faux vs. autre chose, cohérent avec le principe
    # déjà appliqué au login (ne jamais aider à deviner ce qui cloche).
    if not auth.verifier_code_invitation(code):
        raise HTTPException(status_code=403, detail="Invalid or missing invite code")

    if not auth.email_valide(email):
        raise HTTPException(status_code=400, detail="Please provide a valid email address")
    if not nom or len(nom) < 2:
        raise HTTPException(status_code=400, detail="Please provide your name")

    erreur_mdp = auth.valider_force_mot_de_passe(password, email=email, nom=nom)
    if erreur_mdp:
        raise HTTPException(status_code=400, detail=erreur_mdp)

    user_id = database.creer_utilisateur(email, auth.hasher_mot_de_passe(password), nom)
    if user_id is None:
        # ← Générique aussi : "compte déjà existant" confirmerait qu'un
        # email précis est enregistré (énumération d'utilisateurs).
        raise HTTPException(status_code=400, detail="Could not create account with these details")

    token = auth.generer_token_session()
    database.creer_session(token, user_id, auth.DUREE_SESSION_S)
    _poser_cookie_session(response, token)
    return {"ok": True, "user": _utilisateur_public(database.get_utilisateur_par_id(user_id))}


@router.post("/api/auth/login")
def api_login(body: dict, response: Response):
    email    = (body.get("email") or "").strip()
    password = body.get("password") or ""

    verrouille, secondes = auth.compte_verrouille(email)
    if verrouille:
        raise HTTPException(status_code=429, detail=f"Too many failed attempts. Try again in {secondes // 60 + 1} minute(s).")

    # ← Message d'erreur IDENTIQUE que l'email n'existe pas ou que le mot
    # de passe soit faux -- voir docstring d'auth.py, point 7.
    erreur_generique = HTTPException(status_code=401, detail="Invalid email or password")

    # ← AJOUT : panne DB distinguée d'un "utilisateur introuvable" --
    # message honnête au lieu de laisser croire à un mauvais mot de passe.
    # Ne compte volontairement PAS comme un échec de connexion
    # (enregistrer_echec_connexion non appelé) -- ce n'est pas elle qui
    # s'est trompée, ça ne doit pas consommer une tentative avant
    # verrouillage.
    try:
        user = database.get_utilisateur_par_email(email)
    except database.BaseDeDonneesIndisponible:
        raise HTTPException(status_code=503, detail="Service temporarily unavailable. Please try again in a moment.")
    if not user or not user.get("is_active", True):
        auth.enregistrer_echec_connexion(email)
        raise erreur_generique
    if not auth.verifier_mot_de_passe(password, user["password_hash"]):
        auth.enregistrer_echec_connexion(email)
        raise erreur_generique

    auth.reinitialiser_tentatives(email)
    token = auth.generer_token_session()
    database.creer_session(token, user["id"], auth.DUREE_SESSION_S)
    _poser_cookie_session(response, token)
    return {"ok": True, "user": _utilisateur_public(user)}


@router.post("/api/auth/logout")
def api_logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        database.supprimer_session(token)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/api/auth/me")
def api_me(user: dict = Depends(get_current_user)):
    return {"user": _utilisateur_public(user)}


@router.post("/api/auth/change-password")
def api_change_password(body: dict, response: Response, user: dict = Depends(get_current_user)):
    mdp_actuel   = body.get("current_password") or ""
    nouveau_mdp  = body.get("new_password") or ""

    if not auth.verifier_mot_de_passe(mdp_actuel, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    erreur = auth.valider_force_mot_de_passe(nouveau_mdp, email=user["email"], nom=user["nom"])
    if erreur:
        raise HTTPException(status_code=400, detail=erreur)
    if auth.verifier_mot_de_passe(nouveau_mdp, user["password_hash"]):
        raise HTTPException(status_code=400, detail="New password must be different from the current one")

    database.mettre_a_jour_mot_de_passe(user["id"], auth.hasher_mot_de_passe(nouveau_mdp))

    # ← Coupe TOUTES les sessions existantes (y compris celle-ci) puis en
    # recrée une fraîche pour la requête en cours -- déconnecte tout
    # navigateur/appareil déjà connecté avec l'ancien mot de passe, sans
    # pour autant forcer un nouveau login juste après avoir changé le mot
    # de passe avec succès (mauvaise expérience sinon).
    database.supprimer_sessions_utilisateur(user["id"])
    nouveau_token = auth.generer_token_session()
    database.creer_session(nouveau_token, user["id"], auth.DUREE_SESSION_S)
    _poser_cookie_session(response, nouveau_token)
    return {"ok": True}


@router.post("/api/auth/forgot-password")
async def api_forgot_password(body: dict):
    """
    Répond TOUJOURS la même chose, que l'email existe ou non, ET que
    l'envoi ait réussi ou échoué (panne SMTP, etc.) -- sinon cet endpoint
    devient lui-même un moyen de découvrir quels emails ont un compte ou
    d'apprendre des détails sur la configuration serveur.
    """
    email = (body.get("email") or "").strip()
    reponse_generique = {"ok": True, "message": "If that email is registered, a reset code has been sent."}

    if not auth.email_valide(email):
        return reponse_generique

    # ← AJOUT : panne DB distinguée, mais le message reste
    # DÉLIBÉRÉMENT générique dans les deux cas (sécurité : ne jamais
    # révéler si un compte existe) -- seul le log serveur distingue les
    # deux, pour le diagnostic.
    try:
        user = database.get_utilisateur_par_email(email)
    except database.BaseDeDonneesIndisponible:
        print(f"[Auth] Base indisponible pendant forgot-password pour {email} -- reponse generique inchangee")
        return reponse_generique
    if not user:
        return reponse_generique

    token = auth.generer_token_reset()
    database.creer_token_reset(token, user["id"], auth.DUREE_TOKEN_RESET_S)

    try:
        from notifications import envoyer_email_reset_password
        await envoyer_email_reset_password(user["email"], user["nom"], token)
    except Exception as e:
        # ← Ne fait jamais échouer la requête -- une panne SMTP ne doit
        # jamais devenir un moyen de sonder le serveur depuis l'extérieur.
        print(f"[Auth] Erreur envoi email reset: {e}")

    return reponse_generique


@router.post("/api/auth/reset-password")
def api_reset_password(body: dict):
    # ← AJOUT : voir trop_de_tentatives_reset_code() dans auth.py pour le
    # raisonnement complet -- vérifié en premier, avant toute logique,
    # pour ne jamais consommer le token sur une tentative refusée.
    if auth.trop_de_tentatives_reset_code():
        raise HTTPException(status_code=429, detail="Too many attempts. Please request a new code and try again shortly.")

    token       = body.get("token") or ""
    nouveau_mdp = body.get("new_password") or ""

    donnees_token = database.get_token_reset(token)
    if not donnees_token:
        raise HTTPException(status_code=400, detail="This reset code is invalid or has expired")
    if donnees_token.get("used"):
        raise HTTPException(status_code=400, detail="This reset code has already been used")

    user = database.get_utilisateur_par_id(donnees_token["user_id"])
    if not user:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired")

    erreur = auth.valider_force_mot_de_passe(nouveau_mdp, email=user["email"], nom=user["nom"])
    if erreur:
        raise HTTPException(status_code=400, detail=erreur)

    database.mettre_a_jour_mot_de_passe(user["id"], auth.hasher_mot_de_passe(nouveau_mdp))
    database.marquer_token_reset_utilise(token)
    # ← Plus agressif que change-password (qui garde la session en cours) :
    # ici, on ne sait PAS qui a demandé cette réinitialisation -- ça peut
    # être le vrai propriétaire du compte (a oublié son mot de passe) OU
    # quelqu'un ayant intercepté l'email. Toute session déjà ouverte,
    # légitime ou non, est coupée -- une reconnexion explicite est requise
    # partout après une réinitialisation.
    database.supprimer_sessions_utilisateur(user["id"])
    return {"ok": True}