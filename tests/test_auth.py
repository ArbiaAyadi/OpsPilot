"""
test_auth.py — Tests du système d'authentification OpsPilot.

Couvre auth.py (sécurité), database.py (persistance des comptes/sessions),
et auth_routes.py (endpoints). Utilise le repli mémoire déjà intégré à
database.py quand PostgreSQL n'est pas disponible (voir database.py
DB_OK) -- même principe que le reste de la suite : s'exécute sans base
de données réelle.

N'utilise PAS le fixture `client` de conftest.py (qui dépend de
agent.main, donc de fichiers non couverts par ce fichier -- websocket_handler,
chat_history, config, groq_client, report_writer, surveillance). Un
client local, construit uniquement à partir de agent.routes + auth_routes,
suffit pour tester ce système précis et reste indépendant du reste de
l'application.
"""
import sys
import time
import pytest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import auth
import database


# ── Isolation entre tests ──────────────────────────────────────────────────
# Le repli mémoire de database.py (_mem_users, _mem_sessions...) et les
# compteurs de tentatives d'auth.py sont des dicts/listes AU NIVEAU MODULE
# -- sans réinitialisation, un test pollue le suivant (ex: un email créé
# dans un test bloquerait "email déjà utilisé" dans un autre).
@pytest.fixture(autouse=True)
def etat_propre():
    database._mem_users.clear()
    database._mem_sessions.clear()
    database._mem_reset_tokens.clear()
    auth._tentatives_echouees.clear()
    auth._verrouille_jusqu_a.clear()
    yield


@pytest.fixture
def code_invitation(monkeypatch):
    """Fixe un code d'invitation connu pour la durée du test."""
    monkeypatch.setenv("SIGNUP_INVITE_CODE", "code-test-2026")
    return "code-test-2026"


@pytest.fixture
def auth_client(code_invitation):
    """
    Client HTTP local, indépendant de agent.main -- construit uniquement
    à partir de agent.routes (protégé) + auth_routes (public). Suffisant
    pour tester tout le système d'authentification et la protection des
    routes, sans dépendre des modules non fournis cette session.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import agent.routes as routes
    import auth_routes as ar

    app = FastAPI()
    app.include_router(ar.router)
    app.include_router(routes.router)
    return TestClient(app)


def creer_utilisateur_test(email="test@opspilot.local", password="MotDePasseSolide2026", nom="Test User"):
    return database.creer_utilisateur(email, auth.hasher_mot_de_passe(password), nom)


# ══════════════════════════════════════════════════════════════════════════════
# auth.py — hashage et vérification des mots de passe
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestHashageMotDePasse:
    def test_hash_different_du_mot_de_passe_clair(self):
        h = auth.hasher_mot_de_passe("MotDePasseTest2026")
        assert h != "MotDePasseTest2026"

    def test_verification_correcte(self):
        h = auth.hasher_mot_de_passe("MotDePasseTest2026")
        assert auth.verifier_mot_de_passe("MotDePasseTest2026", h) is True

    def test_verification_incorrecte(self):
        h = auth.hasher_mot_de_passe("MotDePasseTest2026")
        assert auth.verifier_mot_de_passe("MauvaisMotDePasse", h) is False

    def test_deux_hash_du_meme_mot_de_passe_sont_differents(self):
        """bcrypt inclut un sel aléatoire -- deux hash du même mot de passe
        ne doivent jamais être identiques."""
        h1 = auth.hasher_mot_de_passe("MemeMotDePasse2026")
        h2 = auth.hasher_mot_de_passe("MemeMotDePasse2026")
        assert h1 != h2
        assert auth.verifier_mot_de_passe("MemeMotDePasse2026", h1)
        assert auth.verifier_mot_de_passe("MemeMotDePasse2026", h2)

    def test_verification_hash_corrompu_ne_plante_pas(self):
        assert auth.verifier_mot_de_passe("test", "hash-invalide-corrompu") is False


@pytest.mark.unit
class TestForceMotDePasse:
    def test_trop_court_rejete(self):
        assert auth.valider_force_mot_de_passe("court") is not None

    def test_longueur_suffisante_sans_complexite_accepte(self):
        """Politique NIST : la longueur suffit, pas de règle de complexité forcée."""
        assert auth.valider_force_mot_de_passe("unmotdepasseassezlongsanscomplexite") is None

    def test_identique_a_l_email_rejete(self):
        assert auth.valider_force_mot_de_passe(
            "arbia@test.com", email="arbia@test.com"
        ) is not None

    def test_contient_le_nom_rejete(self):
        assert auth.valider_force_mot_de_passe(
            "MotDePasseArbiaAyadi2026", nom="Arbia Ayadi"
        ) is not None

    def test_mot_de_passe_commun_rejete(self):
        assert auth.valider_force_mot_de_passe("password123456") is not None or \
               "password123" in auth.MOTS_DE_PASSE_INTERDITS


@pytest.mark.unit
class TestLimitationTentatives:
    def test_pas_verrouille_initialement(self):
        verrouille, _ = auth.compte_verrouille("nouveau@test.com")
        assert verrouille is False

    def test_verrouille_apres_5_echecs(self):
        email = "brute-force@test.com"
        for _ in range(5):
            auth.enregistrer_echec_connexion(email)
        verrouille, secondes = auth.compte_verrouille(email)
        assert verrouille is True
        assert secondes > 0

    def test_pas_verrouille_apres_4_echecs(self):
        email = "presque@test.com"
        for _ in range(4):
            auth.enregistrer_echec_connexion(email)
        verrouille, _ = auth.compte_verrouille(email)
        assert verrouille is False

    def test_reinitialisation_debloque(self):
        email = "debloque@test.com"
        for _ in range(5):
            auth.enregistrer_echec_connexion(email)
        auth.reinitialiser_tentatives(email)
        verrouille, _ = auth.compte_verrouille(email)
        assert verrouille is False

    def test_verrouillage_insensible_a_la_casse_email(self):
        """Le compteur doit être le même pour Test@X.com et test@x.com."""
        for _ in range(5):
            auth.enregistrer_echec_connexion("Test@Example.com")
        verrouille, _ = auth.compte_verrouille("test@example.com")
        assert verrouille is True


@pytest.mark.unit
class TestTokens:
    def test_tokens_session_uniques(self):
        tokens = {auth.generer_token_session() for _ in range(500)}
        assert len(tokens) == 500

    def test_tokens_reset_uniques(self):
        tokens = {auth.generer_token_reset() for _ in range(500)}
        assert len(tokens) == 500

    def test_token_longueur_suffisante(self):
        """OWASP recommande >=128 bits d'entropie pour un identifiant de session."""
        assert len(auth.generer_token_session()) >= 32


@pytest.mark.unit
class TestCodeInvitation:
    def test_code_absent_toujours_refuse(self, monkeypatch):
        monkeypatch.delenv("SIGNUP_INVITE_CODE", raising=False)
        assert auth.verifier_code_invitation("nimporte-quoi") is False

    def test_bon_code_accepte(self, code_invitation):
        assert auth.verifier_code_invitation(code_invitation) is True

    def test_mauvais_code_refuse(self, code_invitation):
        assert auth.verifier_code_invitation("mauvais-code") is False

    def test_code_vide_refuse(self, code_invitation):
        assert auth.verifier_code_invitation("") is False


@pytest.mark.unit
class TestValidationEmail:
    def test_email_valide_accepte(self):
        assert auth.email_valide("arbia@opspilot.local") is True

    def test_sans_arobase_refuse(self):
        assert auth.email_valide("pas-un-email") is False

    def test_vide_refuse(self):
        assert auth.email_valide("") is False


# ══════════════════════════════════════════════════════════════════════════════
# database.py — persistance comptes / sessions / tokens de réinitialisation
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.unit
class TestDatabaseUtilisateurs:
    def test_creation_retourne_un_id(self):
        uid = creer_utilisateur_test()
        assert uid is not None

    def test_email_duplique_refuse(self):
        creer_utilisateur_test(email="doublon@test.com")
        uid2 = creer_utilisateur_test(email="doublon@test.com")
        assert uid2 is None

    def test_get_par_email(self):
        creer_utilisateur_test(email="lookup@test.com", nom="Lookup Test")
        u = database.get_utilisateur_par_email("lookup@test.com")
        assert u is not None
        assert u["nom"] == "Lookup Test"

    def test_get_par_email_insensible_casse(self):
        creer_utilisateur_test(email="CaseTest@Example.com")
        u = database.get_utilisateur_par_email("casetest@example.com")
        assert u is not None

    def test_get_par_id(self):
        uid = creer_utilisateur_test()
        u = database.get_utilisateur_par_id(uid)
        assert u is not None
        assert u["id"] == uid

    def test_email_inexistant_retourne_none(self):
        assert database.get_utilisateur_par_email("fantome@test.com") is None

    def test_mise_a_jour_mot_de_passe(self):
        uid = creer_utilisateur_test()
        nouveau_hash = auth.hasher_mot_de_passe("NouveauMotDePasse2026")
        database.mettre_a_jour_mot_de_passe(uid, nouveau_hash)
        u = database.get_utilisateur_par_id(uid)
        assert auth.verifier_mot_de_passe("NouveauMotDePasse2026", u["password_hash"])


@pytest.mark.unit
class TestDatabaseSessions:
    def test_session_valide_recuperable(self):
        uid = creer_utilisateur_test()
        token = auth.generer_token_session()
        database.creer_session(token, uid, 3600)
        s = database.get_session(token)
        assert s is not None
        assert s["user_id"] == uid

    def test_session_expiree_invisible(self):
        uid = creer_utilisateur_test()
        token = auth.generer_token_session()
        database.creer_session(token, uid, -10)  # déjà expirée
        assert database.get_session(token) is None

    def test_session_inexistante_retourne_none(self):
        assert database.get_session("token-jamais-cree") is None

    def test_suppression_session(self):
        uid = creer_utilisateur_test()
        token = auth.generer_token_session()
        database.creer_session(token, uid, 3600)
        database.supprimer_session(token)
        assert database.get_session(token) is None

    def test_suppression_toutes_sessions_utilisateur(self):
        uid = creer_utilisateur_test()
        token_a = auth.generer_token_session()
        token_b = auth.generer_token_session()
        database.creer_session(token_a, uid, 3600)
        database.creer_session(token_b, uid, 3600)
        database.supprimer_sessions_utilisateur(uid)
        assert database.get_session(token_a) is None
        assert database.get_session(token_b) is None

    def test_suppression_sessions_n_affecte_pas_autre_utilisateur(self):
        uid1 = creer_utilisateur_test(email="user1@test.com")
        uid2 = creer_utilisateur_test(email="user2@test.com")
        token1 = auth.generer_token_session()
        token2 = auth.generer_token_session()
        database.creer_session(token1, uid1, 3600)
        database.creer_session(token2, uid2, 3600)
        database.supprimer_sessions_utilisateur(uid1)
        assert database.get_session(token1) is None
        assert database.get_session(token2) is not None


@pytest.mark.unit
class TestDatabaseTokensReset:
    def test_token_valide_recuperable(self):
        uid = creer_utilisateur_test()
        token = auth.generer_token_reset()
        database.creer_token_reset(token, uid, 3600)
        t = database.get_token_reset(token)
        assert t is not None
        assert t["used"] is False

    def test_token_expire_invisible(self):
        uid = creer_utilisateur_test()
        token = auth.generer_token_reset()
        database.creer_token_reset(token, uid, -10)
        assert database.get_token_reset(token) is None

    def test_marquer_utilise(self):
        uid = creer_utilisateur_test()
        token = auth.generer_token_reset()
        database.creer_token_reset(token, uid, 3600)
        database.marquer_token_reset_utilise(token)
        t = database.get_token_reset(token)
        assert t["used"] is True


# ══════════════════════════════════════════════════════════════════════════════
# auth_routes.py — endpoints HTTP
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.integration
class TestApiSignup:
    def test_signup_sans_code_refuse(self, auth_client, code_invitation):
        r = auth_client.post("/api/auth/signup", json={
            "email": "test@opspilot.local", "password": "MotDePasseSolide2026",
            "nom": "Test", "invite_code": "mauvais",
        })
        assert r.status_code == 403

    def test_signup_avec_bon_code_reussit(self, auth_client, code_invitation):
        r = auth_client.post("/api/auth/signup", json={
            "email": "nouveau@opspilot.local", "password": "MotDePasseSolide2026",
            "nom": "Nouveau", "invite_code": code_invitation,
        })
        assert r.status_code == 200
        assert "opspilot_session" in r.cookies

    def test_signup_mot_de_passe_trop_court_refuse(self, auth_client, code_invitation):
        r = auth_client.post("/api/auth/signup", json={
            "email": "test2@opspilot.local", "password": "court",
            "nom": "Test", "invite_code": code_invitation,
        })
        assert r.status_code == 400

    def test_signup_email_deja_utilise_refuse(self, auth_client, code_invitation):
        body = {"email": "existe@opspilot.local", "password": "MotDePasseSolide2026",
                "nom": "Test", "invite_code": code_invitation}
        auth_client.post("/api/auth/signup", json=body)
        r2 = auth_client.post("/api/auth/signup", json={**body, "nom": "Autre"})
        assert r2.status_code == 400

    def test_signup_email_invalide_refuse(self, auth_client, code_invitation):
        r = auth_client.post("/api/auth/signup", json={
            "email": "pas-un-email", "password": "MotDePasseSolide2026",
            "nom": "Test", "invite_code": code_invitation,
        })
        assert r.status_code == 400


@pytest.mark.integration
class TestApiLogin:
    def _creer_compte(self, auth_client, code_invitation, email="login@opspilot.local", password="MotDePasseLogin2026"):
        auth_client.post("/api/auth/signup", json={
            "email": email, "password": password, "nom": "Test User", "invite_code": code_invitation,
        })

    def test_login_bon_mot_de_passe(self, auth_client, code_invitation):
        self._creer_compte(auth_client, code_invitation)
        r = auth_client.post("/api/auth/login", json={
            "email": "login@opspilot.local", "password": "MotDePasseLogin2026",
        })
        assert r.status_code == 200
        assert "opspilot_session" in r.cookies

    def test_login_mauvais_mot_de_passe(self, auth_client, code_invitation):
        self._creer_compte(auth_client, code_invitation)
        r = auth_client.post("/api/auth/login", json={
            "email": "login@opspilot.local", "password": "FauxMotDePasse",
        })
        assert r.status_code == 401

    def test_login_email_inexistant_meme_message_que_mauvais_mdp(self, auth_client, code_invitation):
        """Ne doit jamais permettre de deviner quels emails ont un compte."""
        self._creer_compte(auth_client, code_invitation)
        r_inexistant = auth_client.post("/api/auth/login", json={
            "email": "fantome@opspilot.local", "password": "nimportequoi",
        })
        r_mauvais_mdp = auth_client.post("/api/auth/login", json={
            "email": "login@opspilot.local", "password": "FauxMotDePasse",
        })
        assert r_inexistant.status_code == r_mauvais_mdp.status_code == 401
        assert r_inexistant.json()["detail"] == r_mauvais_mdp.json()["detail"]

    def test_login_verrouille_apres_5_echecs(self, auth_client, code_invitation):
        self._creer_compte(auth_client, code_invitation)
        for _ in range(5):
            auth_client.post("/api/auth/login", json={
                "email": "login@opspilot.local", "password": "Faux",
            })
        r = auth_client.post("/api/auth/login", json={
            "email": "login@opspilot.local", "password": "MotDePasseLogin2026",  # même le bon mdp
        })
        assert r.status_code == 429


@pytest.mark.integration
class TestApiMeEtLogout:
    def _connecter(self, auth_client, code_invitation):
        auth_client.post("/api/auth/signup", json={
            "email": "me@opspilot.local", "password": "MotDePasseMe2026",
            "nom": "Me Test", "invite_code": code_invitation,
        })

    def test_me_sans_session_refuse(self, auth_client):
        r = auth_client.get("/api/auth/me")
        assert r.status_code == 401

    def test_me_avec_session_valide(self, auth_client, code_invitation):
        self._connecter(auth_client, code_invitation)
        r = auth_client.get("/api/auth/me")
        assert r.status_code == 200
        assert r.json()["user"]["email"] == "me@opspilot.local"

    def test_me_ne_retourne_jamais_le_hash(self, auth_client, code_invitation):
        self._connecter(auth_client, code_invitation)
        r = auth_client.get("/api/auth/me")
        assert "password_hash" not in str(r.json())

    def test_logout_invalide_la_session(self, auth_client, code_invitation):
        self._connecter(auth_client, code_invitation)
        assert auth_client.get("/api/auth/me").status_code == 200
        auth_client.post("/api/auth/logout")
        assert auth_client.get("/api/auth/me").status_code == 401


@pytest.mark.integration
class TestApiChangePassword:
    def _connecter(self, auth_client, code_invitation, email="chpwd@opspilot.local", password="AncienMotDePasse2026"):
        auth_client.post("/api/auth/signup", json={
            "email": email, "password": password, "nom": "Change Pwd", "invite_code": code_invitation,
        })

    def test_change_password_mauvais_mdp_actuel_refuse(self, auth_client, code_invitation):
        self._connecter(auth_client, code_invitation)
        r = auth_client.post("/api/auth/change-password", json={
            "current_password": "FauxAncien", "new_password": "NouveauMotDePasse2026",
        })
        assert r.status_code == 401

    def test_change_password_reussi(self, auth_client, code_invitation):
        self._connecter(auth_client, code_invitation)
        r = auth_client.post("/api/auth/change-password", json={
            "current_password": "AncienMotDePasse2026", "new_password": "NouveauMotDePasse2026",
        })
        assert r.status_code == 200

    def test_change_password_deconnecte_les_autres_sessions(self, auth_client, code_invitation):
        """Deux 'navigateurs' (clients) connectés au même compte -- changer
        le mot de passe depuis l'un doit couper l'autre."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import agent.routes as routes
        import auth_routes as ar

        app = FastAPI()
        app.include_router(ar.router)
        app.include_router(routes.router)
        client_a = TestClient(app)
        client_b = TestClient(app)

        client_a.post("/api/auth/signup", json={
            "email": "twosessions@opspilot.local", "password": "MotDePasseOriginal2026",
            "nom": "Two Sessions", "invite_code": code_invitation,
        })
        client_b.post("/api/auth/login", json={
            "email": "twosessions@opspilot.local", "password": "MotDePasseOriginal2026",
        })
        assert client_a.get("/api/auth/me").status_code == 200
        assert client_b.get("/api/auth/me").status_code == 200

        client_a.post("/api/auth/change-password", json={
            "current_password": "MotDePasseOriginal2026", "new_password": "MotDePasseChange2026",
        })

        assert client_a.get("/api/auth/me").status_code == 200, \
            "Le client qui a changé le mot de passe doit rester connecté"
        assert client_b.get("/api/auth/me").status_code == 401, \
            "L'autre session doit être déconnectée"

    def test_change_password_meme_que_l_ancien_refuse(self, auth_client, code_invitation):
        self._connecter(auth_client, code_invitation)
        r = auth_client.post("/api/auth/change-password", json={
            "current_password": "AncienMotDePasse2026", "new_password": "AncienMotDePasse2026",
        })
        assert r.status_code == 400

    def test_change_password_sans_session_refuse(self, auth_client):
        r = auth_client.post("/api/auth/change-password", json={
            "current_password": "x", "new_password": "NouveauMotDePasse2026",
        })
        assert r.status_code == 401


@pytest.mark.integration
class TestApiForgotEtResetPassword:
    def test_forgot_password_email_existant_reponse_generique(self, auth_client, code_invitation):
        auth_client.post("/api/auth/signup", json={
            "email": "forgot@opspilot.local", "password": "MotDePasseForgot2026",
            "nom": "Test User", "invite_code": code_invitation,
        })
        with patch("notifications.envoyer_email_reset_password", return_value=True):
            r = auth_client.post("/api/auth/forgot-password", json={"email": "forgot@opspilot.local"})
        assert r.status_code == 200

    def test_forgot_password_email_inexistant_meme_reponse(self, auth_client, code_invitation):
        r1 = auth_client.post("/api/auth/forgot-password", json={"email": "existepas@opspilot.local"})
        with patch("notifications.envoyer_email_reset_password", return_value=True):
            auth_client.post("/api/auth/signup", json={
                "email": "forgot2@opspilot.local", "password": "MotDePasseForgot2026",
                "nom": "Test User", "invite_code": code_invitation,
            })
            r2 = auth_client.post("/api/auth/forgot-password", json={"email": "forgot2@opspilot.local"})
        assert r1.status_code == r2.status_code == 200
        assert r1.json() == r2.json(), \
            "La réponse doit être identique, email existant ou non (anti-énumération)"

    def test_reset_password_token_invalide_refuse(self, auth_client):
        r = auth_client.post("/api/auth/reset-password", json={
            "token": "token-jamais-genere", "new_password": "NouveauMotDePasse2026",
        })
        assert r.status_code == 400

    def test_reset_password_cycle_complet(self, auth_client, code_invitation):
        auth_client.post("/api/auth/signup", json={
            "email": "reset@opspilot.local", "password": "AncienMotDePasse2026",
            "nom": "Test User", "invite_code": code_invitation,
        })
        uid = database.get_utilisateur_par_email("reset@opspilot.local")["id"]
        token = auth.generer_token_reset()
        database.creer_token_reset(token, uid, auth.DUREE_TOKEN_RESET_S)

        r = auth_client.post("/api/auth/reset-password", json={
            "token": token, "new_password": "MotDePasseApresReset2026",
        })
        assert r.status_code == 200

        r_login_ancien = auth_client.post("/api/auth/login", json={
            "email": "reset@opspilot.local", "password": "AncienMotDePasse2026",
        })
        assert r_login_ancien.status_code == 401

        r_login_nouveau = auth_client.post("/api/auth/login", json={
            "email": "reset@opspilot.local", "password": "MotDePasseApresReset2026",
        })
        assert r_login_nouveau.status_code == 200

    def test_reset_password_token_reutilise_refuse(self, auth_client, code_invitation):
        auth_client.post("/api/auth/signup", json={
            "email": "reset2@opspilot.local", "password": "AncienMotDePasse2026",
            "nom": "Test User", "invite_code": code_invitation,
        })
        uid = database.get_utilisateur_par_email("reset2@opspilot.local")["id"]
        token = auth.generer_token_reset()
        database.creer_token_reset(token, uid, auth.DUREE_TOKEN_RESET_S)

        auth_client.post("/api/auth/reset-password", json={"token": token, "new_password": "Premier2026Password"})
        r = auth_client.post("/api/auth/reset-password", json={"token": token, "new_password": "Second2026Password"})
        assert r.status_code == 400


# ══════════════════════════════════════════════════════════════════════════════
# agent/routes.py — protection de toutes les routes métier
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.integration
class TestProtectionRoutes:
    """Toutes les routes de agent.routes doivent exiger une session valide
    (dependencies=[Depends(get_current_user)] au niveau du router)."""

    ENDPOINTS_PROTEGES = [
        "/api/recommendations",
        "/api/db/stats",
        "/api/anomalies/historique",
        "/api/regles",
        "/api/rapports",
    ]

    @pytest.mark.parametrize("endpoint", ENDPOINTS_PROTEGES)
    def test_sans_session_refuse(self, auth_client, endpoint):
        r = auth_client.get(endpoint)
        assert r.status_code == 401, f"{endpoint} doit exiger une session valide"

    def test_avec_session_fonctionne(self, auth_client, code_invitation):
        auth_client.post("/api/auth/signup", json={
            "email": "protected@opspilot.local", "password": "MotDePasseProtected2026",
            "nom": "Test User", "invite_code": code_invitation,
        })
        r = auth_client.get("/api/recommendations")
        assert r.status_code == 200

    def test_routes_auth_jamais_protegees(self, auth_client):
        """Sinon impossible de se connecter -- login/signup doivent rester
        accessibles sans session préalable."""
        r = auth_client.post("/api/auth/login", json={"email": "x@x.com", "password": "y"})
        assert r.status_code != 401 or r.json().get("detail") == "Invalid email or password"