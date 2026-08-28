"""
auth.py — Authentification et sécurité des comptes OpsPilot.

Conçu pour une équipe d'ingénieurs systèmes/infrastructure, pas un grand
public -- ces comptes donnent accès à des actions réelles sur
l'infrastructure (KSM, migration de VM, exécution de commandes via
action_executor.py). Chaque choix ci-dessous part de ce contexte précis,
pas d'un gabarit générique.

── Décisions de sécurité et pourquoi ────────────────────────────────────────

1. Mots de passe : bcrypt (jamais MD5/SHA1 seuls, jamais en clair). Le coût
   (12 rounds, le défaut de la librairie) est volontairement coûteux en
   calcul -- c'est le but, ça ralentit une attaque par force brute même si
   la base de données fuitait un jour.

2. Politique de mot de passe : LONGUEUR minimale (12 caractères), PAS de
   règle de complexité forcée (pas de "1 majuscule + 1 chiffre + 1
   symbole"). C'est la recommandation actuelle du NIST (SP 800-63B) --
   les règles de complexité forcée poussent vers des motifs prévisibles
   ("Password1!") sans vraiment protéger, alors qu'un mot de passe plus
   long est mathématiquement plus difficile à casser par force brute,
   quelle que soit sa "complexité" apparente.

3. Sessions : token aléatoire cryptographiquement sûr (secrets.token_urlsafe),
   stocké côté serveur dans PostgreSQL (table sessions), jamais un JWT
   auto-porteur -- un JWT ne peut pas être révoqué avant son expiration
   sans une liste de révocation qui annule justement l'avantage du JWT.
   Une session en base peut être invalidée instantanément (déconnexion,
   compromission suspectée) juste en supprimant la ligne. Expiration : 7
   jours -- assez long pour un usage quotidien par une équipe interne,
   sans forcer une reconnexion permanente.

4. Limitation des tentatives de connexion : en mémoire (pas en base,
   cohérent avec le reste du projet -- voir _derniere_alerte_par_cle dans
   anomaly_detector.py, même principe déjà accepté ici). 5 échecs
   consécutifs pour un email donné -> verrouillage 30 secondes (voir
   DUREE_VERROUILLAGE_S -- réduit depuis 15 min pour ne pas gêner le
   développement actif ; à remonter avant un vrai déploiement en
   production, 30s laisse un bruteforce quasi libre). Le compteur
   est réinitialisé après tout redémarrage du processus -- accepté comme
   compromis mineur, pas un vrai trou de sécurité (un redémarrage n'est
   pas déclenchable à volonté par un attaquant externe).

5. Inscription : nécessite un code d'invitation (SIGNUP_INVITE_CODE dans
   .env, secret partagé). Sans ça, n'importe qui tombant sur l'URL
   pourrait se créer un accès à des actions qui modifient réellement
   l'infrastructure -- l'inscription ouverte est inappropriée ici, à la
   différence d'un site grand public.

6. Réinitialisation de mot de passe : token à usage unique, expiration 1h
   -- pratique standard, court exprès (une fenêtre longue est un risque
   inutile si l'email est intercepté).

7. Messages d'erreur : "email ou mot de passe incorrect", jamais
   "utilisateur introuvable" séparé de "mot de passe incorrect" -- une
   réponse distincte pour chaque cas permet à un attaquant de découvrir
   quels emails ont un compte (énumération d'utilisateurs), même sans
   jamais deviner un mot de passe.
"""
import os
import re
import secrets
import threading
import time
from collections import deque
import bcrypt

# ══════════════════════════════════════════════════════════════════════════════
# Mots de passe
# ══════════════════════════════════════════════════════════════════════════════
LONGUEUR_MIN_MOT_DE_PASSE = 12

# Liste courte de mots de passe extrêmement communs -- pas exhaustive (ça
# n'est pas le rôle de ce fichier de remplacer une vraie liste type
# "10k-most-common-passwords"), juste un filet pour les cas les plus
# évidents qu'une simple règle de longueur ne rejette pas.
MOTS_DE_PASSE_INTERDITS = {
    "password123", "password1234", "azertyuiop12", "qwertyuiop12",
    "administrateur", "motdepasse123", "opspilot123456", "changeme12345",
}


def valider_force_mot_de_passe(mot_de_passe: str, email: str = "", nom: str = "") -> str | None:
    """
    Retourne None si le mot de passe est acceptable, sinon un message
    d'erreur explicite à renvoyer à l'utilisateur. Longueur avant tout
    (voir docstring du fichier) -- pas de règle de complexité forcée.
    """
    if not mot_de_passe or len(mot_de_passe) < LONGUEUR_MIN_MOT_DE_PASSE:
        return f"Password must be at least {LONGUEUR_MIN_MOT_DE_PASSE} characters long."
    if mot_de_passe.lower() in MOTS_DE_PASSE_INTERDITS:
        return "This password is too common. Please choose a different one."
    if email and mot_de_passe.lower() == email.lower():
        return "Password cannot be the same as your email."
    # ← CORRIGÉ : vérifiait le nom COMPLET avec l'espace ("arbia ayadi")
    # comme une seule sous-chaîne -- un mot de passe réel comme
    # "MotDePasseArbiaAyadi2026" ne contient jamais cet espace, donc ne
    # déclenchait jamais ce refus alors qu'il contient bien le nom.
    # Vérifie maintenant chaque partie du nom séparément (prénom, nom).
    if nom:
        for partie in nom.split():
            if len(partie) >= 4 and partie.lower() in mot_de_passe.lower():
                return "Password should not contain your name."
    return None


def hasher_mot_de_passe(mot_de_passe: str) -> str:
    """Hash bcrypt -- coût par défaut de la librairie (12 rounds), stocké
    tel quel en base (le sel est déjà inclus dans la sortie de bcrypt,
    pas besoin de le stocker séparément)."""
    return bcrypt.hashpw(mot_de_passe.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verifier_mot_de_passe(mot_de_passe: str, hash_stocke: str) -> bool:
    """Ne lève jamais d'exception -- un hash corrompu/invalide en base
    retourne False plutôt que de faire planter la route de login."""
    try:
        return bcrypt.checkpw(mot_de_passe.encode("utf-8"), hash_stocke.encode("utf-8"))
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Limitation des tentatives de connexion — en mémoire
# ══════════════════════════════════════════════════════════════════════════════
MAX_TENTATIVES_ECHOUEES   = 5
DUREE_VERROUILLAGE_S      = 30  # 30s

_tentatives_echouees: dict = {}   # email(lower) -> [timestamps des echecs recents]
_verrouille_jusqu_a:  dict = {}   # email(lower) -> timestamp de fin de verrouillage


def compte_verrouille(email: str) -> tuple:
    """Retourne (verrouille: bool, secondes_restantes: int)."""
    cle = email.lower().strip()
    fin = _verrouille_jusqu_a.get(cle)
    if fin and time.time() < fin:
        return True, int(fin - time.time())
    return False, 0


def enregistrer_echec_connexion(email: str):
    """Enregistre un échec ; verrouille le compte si le seuil est atteint.
    Fenêtre glissante de DUREE_VERROUILLAGE_S -- un échec trop ancien ne
    compte plus dans le total."""
    cle = email.lower().strip()
    maintenant = time.time()
    historique = [t for t in _tentatives_echouees.get(cle, []) if maintenant - t < DUREE_VERROUILLAGE_S]
    historique.append(maintenant)
    _tentatives_echouees[cle] = historique
    if len(historique) >= MAX_TENTATIVES_ECHOUEES:
        _verrouille_jusqu_a[cle] = maintenant + DUREE_VERROUILLAGE_S


def reinitialiser_tentatives(email: str):
    """Appelé après une connexion réussie -- repart de zéro."""
    cle = email.lower().strip()
    _tentatives_echouees.pop(cle, None)
    _verrouille_jusqu_a.pop(cle, None)


# ══════════════════════════════════════════════════════════════════════════════
# Sessions
# ══════════════════════════════════════════════════════════════════════════════
DUREE_SESSION_S = 7 * 24 * 3600  # 7 jours


def generer_token_session() -> str:
    """32 bytes cryptographiquement sûrs, encodés URL-safe -- pratique
    standard pour un identifiant de session (voir OWASP Session Management
    Cheat Sheet : >=128 bits d'entropie)."""
    return secrets.token_urlsafe(32)


# ══════════════════════════════════════════════════════════════════════════════
# Réinitialisation de mot de passe
# ══════════════════════════════════════════════════════════════════════════════
DUREE_TOKEN_RESET_S = 3600  # 1h -- volontairement court


def generer_token_reset() -> str:
    """
    ← MODIFIÉ : code court à 6 chiffres (ex: "042917"), copié depuis
    l'email et saisi à la main dans l'app -- au lieu d'un long token
    intégré dans un lien cliquable. Choix explicite : un code fonctionne
    identiquement quels que soient HOST/PORT/HTTPS configurés (rien à
    faire correspondre), contrairement à un lien qui dépend de
    DASHBOARD_URL étant exactement juste -- source de confusion
    récurrente tout au long de ce projet (HOST=0.0.0.0, passage en
    HTTPS, mkcert...).

    secrets.randbelow() -- générateur cryptographiquement sûr, jamais
    le module random. Espace de 1 000 000 de valeurs (000000-999999),
    combiné à DUREE_TOKEN_RESET_S (1h) : à l'échelle de ce projet (une
    poignée d'utilisateurs), le risque qu'une collision entre deux
    demandes de reset simultanées ait une conséquence réelle est
    négligeable -- pas justifié d'ajouter une vérification d'unicité
    supplémentaire pour ce volume. Le stockage (database.py,
    password_reset_tokens) reste inchangé : la colonne "token" accepte
    n'importe quelle chaîne, code court ou long token, sans distinction.
    """
    return f"{secrets.randbelow(1_000_000):06d}"


# ← AJOUT : limite globale sur les tentatives de vérification de code de
# reset -- gap trouvé en vérifiant ce flux, jamais couvert jusqu'ici.
# Pas par email : /api/auth/reset-password ne reçoit que {token,
# new_password} dans son payload actuel (voir auth_routes.py), pas
# l'email. Un code à 6 chiffres n'a "que" 1 000 000 de possibilités --
# sans frein, il serait testable en boucle sans limite pendant toute sa
# fenêtre de validité (1h). Avec 10 tentatives/min max (600/h), la
# probabilité de deviner un code précis tombe à 0.06% max sur toute sa
# durée de vie, plutôt que pratiquement garantie avec assez de temps et
# de patience. Fenêtre glissante globale, même principe que RateLimiter
# dans agent/groq_client.py, réimplémenté ici en local plutôt
# qu'importé -- auth.py ne doit pas dépendre du module agent/.
_tentatives_reset_code: deque = deque()
_lock_reset_code             = threading.Lock()
MAX_TENTATIVES_RESET_PAR_MIN = 10


def trop_de_tentatives_reset_code() -> bool:
    """True si la limite globale est atteinte -- l'appelant doit refuser
    la tentative SANS consommer le code (pas d'effet de bord avant ce
    contrôle). Enregistre la tentative actuelle si elle est acceptée."""
    with _lock_reset_code:
        maintenant = time.time()
        while _tentatives_reset_code and _tentatives_reset_code[0] < maintenant - 60:
            _tentatives_reset_code.popleft()
        if len(_tentatives_reset_code) >= MAX_TENTATIVES_RESET_PAR_MIN:
            return True
        _tentatives_reset_code.append(maintenant)
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Code d'invitation (inscription)
# ══════════════════════════════════════════════════════════════════════════════
def verifier_code_invitation(code_fourni: str) -> bool:
    """
    Compare au SIGNUP_INVITE_CODE défini dans .env -- secrets.compare_digest
    plutôt que == pour éviter une attaque par mesure de temps (un ==
    normal s'arrête au premier caractère different, ce qui peut fuiter des
    informations sur le code correct via le temps de réponse mesuré à
    grande échelle). Retourne toujours False si la variable n'est pas
    configurée -- pas d'inscription possible tant que ce n'est pas mis en
    place explicitement, jamais un repli silencieux vers une inscription
    ouverte.
    """
    code_attendu = os.getenv("SIGNUP_INVITE_CODE", "")
    if not code_attendu or not code_fourni:
        return False
    return secrets.compare_digest(code_fourni.strip(), code_attendu.strip())


# ══════════════════════════════════════════════════════════════════════════════
# Validation d'email — format simple, pas une regex RFC 5322 complète
# ══════════════════════════════════════════════════════════════════════════════
_RE_EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def email_valide(email: str) -> bool:
    return bool(email) and bool(_RE_EMAIL.match(email.strip()))