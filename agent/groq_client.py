import os
import threading
import time
from collections import deque
from datetime import date
from agent.config import GROQ_API_KEY, GROQ_MODELS

GROQ_OK      = False
GROQ_MODEL   = GROQ_MODELS[0]
_groq_client = None

try:
    from groq import Groq
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY manquante dans .env")
    _groq_client = Groq(api_key=GROQ_API_KEY)
    GROQ_OK      = True
    print(f"[OK] Groq API - modele : {GROQ_MODEL}")
    print(f"[OK] Quota gratuit : 14 400 req/jour, 30 000 tokens/min")
except ImportError:
    print("[WARN] groq non installe - pip install groq --break-system-packages")
except Exception as e:
    print(f"[WARN] Groq API : {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Identifiant Groq séparé pour le chat -- isolation des charges de travail
# ══════════════════════════════════════════════════════════════════════════════
# ← AJOUT : permet de faire tourner l'assistant conversationnel sur un
# identifiant Groq DISTINCT de celui de la surveillance/des règles, pour
# que la consommation de l'un n'épuise jamais le budget de l'autre --
# pratique standard d'isolation des charges de travail (workload
# isolation) : une charge interactive (chat, utilisateur devant l'écran)
# et une charge de fond (surveillance automatique 24/7) n'ont ni le même
# profil ni la même criticité, et se disputer un même quota fait que la
# moins importante peut affamer la plus importante.
#
# ← IMPORTANT : ce mécanisme est prévu pour une SECONDE CLÉ LÉGITIME --
# offre payante (Dev Tier), compte d'organisation, compte universitaire.
# Il n'est PAS destiné à créer plusieurs comptes gratuits pour multiplier
# un quota gratuit : c'est contraire aux conditions d'utilisation de Groq
# (comme de la plupart des fournisseurs d'API), et le risque réel est la
# suspension des DEUX comptes, y compris le principal. La bonne réponse à
# un quota trop juste est la réduction de consommation (voir le suivi de
# budget juste en dessous, et le frein par cible dans
# agent/surveillance.py), pas le contournement.
#
# GROQ_API_KEY_CHAT retombe sur GROQ_API_KEY si absente du .env -- sans
# cette variable, _groq_client_chat pointe vers EXACTEMENT le même client
# que _groq_client : comportement du chat strictement inchangé.
GROQ_API_KEY_CHAT  = os.getenv("GROQ_API_KEY_CHAT", "") or GROQ_API_KEY
GROQ_OK_CHAT        = False
_groq_client_chat   = None

try:
    from groq import Groq as _GroqChat
    if GROQ_API_KEY_CHAT:
        _groq_client_chat = _GroqChat(api_key=GROQ_API_KEY_CHAT)
        GROQ_OK_CHAT       = True
        if GROQ_API_KEY_CHAT != GROQ_API_KEY:
            print(f"[OK] Groq API (chat) - identifiant separe actif")
        else:
            print(f"[OK] Groq API (chat) - meme identifiant que la surveillance (GROQ_API_KEY_CHAT non definie)")
except Exception as e:
    print(f"[WARN] Groq API (chat) : {e}")
    _groq_client_chat = _groq_client
    GROQ_OK_CHAT       = GROQ_OK


# ══════════════════════════════════════════════════════════════════════════════
# Suivi du budget journalier de tokens -- root cause du quota epuise
# ══════════════════════════════════════════════════════════════════════════════
# ← AJOUT : jusqu'ici, aucun suivi reel du nombre de tokens consommes
# -- la seule facon de savoir que le quota etait presque epuise etait de
# recevoir l'erreur 429 de Groq lui-meme, en pleine tentative, apres avoir
# deja perdu du temps dans la cascade de repli (3 modeles x 5s d'attente
# + 30s de pause + une derniere tentative). Capture maintenant
# response.usage.total_tokens (fourni par l'API Groq elle-meme sur
# CHAQUE reponse reussie -- pas une estimation) et l'accumule sur une
# journee. TOKENS_QUOTIDIENS_LIMITE vient de l'erreur reelle recue
# ("Limit 200000" pour openai/gpt-oss-20b) -- configurable par env au cas
# ou Groq change cette limite ou qu'un modele different ait une limite
# differente.
TOKENS_QUOTIDIENS_LIMITE = int(os.getenv("GROQ_TOKENS_QUOTIDIENS_LIMITE", "200000"))
_MARGE_SECURITE_TOKENS   = 6000  # au-dela de cette marge restante, on n'essaie meme plus -- evite de perdre du temps sur une tentative vouee a l'echec

# ← MODIFIÉ : deux compteurs séparés ("main" = surveillance/règles,
# "chat" = assistant conversationnel) plutôt qu'un seul -- nécessaire
# maintenant que le chat peut tourner sur un compte Groq séparé (voir
# _groq_client_chat plus haut) : son usage ne doit jamais être compté
# contre le budget de la surveillance, ni l'inverse. "main" reste la
# valeur par défaut partout -- aucun appelant existant (surveillance.py)
# n'a besoin de préciser le compte, son comportement est inchangé.
_lock_budget = threading.Lock()
_tokens_utilises_jour = {"main": 0, "chat": 0}
_date_suivi_courante   = date.today()


def _reinitialiser_si_nouveau_jour():
    global _date_suivi_courante
    aujourdhui = date.today()
    if aujourdhui != _date_suivi_courante:
        _tokens_utilises_jour["main"] = 0
        _tokens_utilises_jour["chat"] = 0
        _date_suivi_courante = aujourdhui


def _enregistrer_tokens(nb_tokens: int, compte: str = "main"):
    with _lock_budget:
        _reinitialiser_si_nouveau_jour()
        _tokens_utilises_jour[compte] = _tokens_utilises_jour.get(compte, 0) + max(0, nb_tokens)


def budget_journalier_restant(compte: str = "main") -> int:
    """Tokens restants estimes avant d'atteindre TOKENS_QUOTIDIENS_LIMITE
    aujourd'hui, POUR CE COMPTE PRÉCIS -- basé sur l'usage RÉEL rapporté
    par Groq sur chaque appel réussi, pas une estimation. compte="main"
    par défaut (surveillance/règles) -- surveillance.py n'a jamais besoin
    de préciser autre chose, comportement inchangé. Permet à
    surveillance.py de décider, AVANT même d'appeler cette fonction, s'il
    vaut la peine de tenter une analyse complète ou de réserver le budget
    restant aux seuls incidents CRITIQUE."""
    with _lock_budget:
        _reinitialiser_si_nouveau_jour()
        return max(0, TOKENS_QUOTIDIENS_LIMITE - _tokens_utilises_jour.get(compte, 0))


def tokens_utilises_aujourdhui(compte: str = "main") -> int:
    with _lock_budget:
        _reinitialiser_si_nouveau_jour()
        return _tokens_utilises_jour.get(compte, 0)


class RateLimiter:
    def __init__(self, max_per_minute: int = 30):
        self.max_per_minute = max_per_minute
        self._appels        = deque()
        self._lock          = threading.Lock()

    def enregistrer(self):
        with self._lock:
            now = time.time()
            while self._appels and self._appels[0] < now - 60:
                self._appels.popleft()
            self._appels.append(now)

    def slots(self) -> int:
        with self._lock:
            now = time.time()
            while self._appels and self._appels[0] < now - 60:
                self._appels.popleft()
            return max(0, self.max_per_minute - len(self._appels))


rate_limiter = RateLimiter(max_per_minute=30)


# ══════════════════════════════════════════════════════════════════════════════
# Secours multi-fournisseurs — bascule automatique quand Groq est indisponible
# ══════════════════════════════════════════════════════════════════════════════
# ← AJOUT : jusqu'ici, si Groq était épuisé ou en panne, l'analyse
# retournait simplement un message d'indisponibilité -- un point de
# défaillance unique. Un outil destiné à une entreprise ne doit pas
# s'arrêter parce qu'UN fournisseur externe a un problème.
#
# Cette couche est ENTIÈREMENT pilotée par .env, sans aucun fournisseur
# imposé dans le code : LLM_FALLBACK_PROVIDERS liste, dans l'ordre de
# priorité, les noms à essayer après Groq. Pour chaque nom <N>, trois
# variables optionnelles : LLM_<N>_URL, LLM_<N>_KEY, LLM_<N>_MODELS.
# Des valeurs par défaut sont fournies pour les fournisseurs courants,
# pour éviter d'avoir à retaper des URLs -- mais tout reste surchargeable,
# et un fournisseur inconnu fonctionne dès lors que son URL est donnée.
#
# Tous ces fournisseurs (Mistral, Cerebras, OpenRouter, Ollama...) exposent
# une API compatible OpenAI : un seul chemin de code générique suffit,
# via requests (déjà utilisé ailleurs dans le projet, aucun paquet à
# installer). Le chemin Groq existant n'est PAS touché -- il garde son SDK
# dédié, éprouvé et fonctionnel.
#
# ← OLLAMA : prévu dès maintenant, pour un déploiement en entreprise où le
# modèle tourne SUR SITE. Aucune donnée d'infrastructure (noms de nœuds,
# IP, configuration des VMs, topologie des services) ne quitte alors le
# réseau -- souvent le vrai critère bloquant en entreprise, bien avant la
# question du quota. Aucune clé, aucun quota, aucun coût par requête.
#
# Tant que LLM_FALLBACK_PROVIDERS est vide (défaut), rien ne change :
# comportement strictement identique à avant.
_URLS_CONNUES = {
    "mistral":    "https://api.mistral.ai/v1",
    "cerebras":   "https://api.cerebras.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama":     "http://localhost:11434/v1",
}
_MODELES_DEFAUT = {
    "mistral":    ["mistral-small-latest"],
    "cerebras":   ["llama-3.3-70b"],
    "openrouter": ["qwen/qwen3-8b:free"],
    "ollama":     ["qwen3:8b"],
}

DERNIER_FOURNISSEUR = "groq"   # lu par /api/status pour afficher qui a répondu


def _fournisseurs_secours() -> list:
    """Liste ordonnée des fournisseurs de secours configurés dans .env.
    Vide par défaut -- aucune bascule tant que rien n'est configuré."""
    brut = os.getenv("LLM_FALLBACK_PROVIDERS", "").strip()
    return [p.strip().lower() for p in brut.split(",") if p.strip()]


def _config_fournisseur(nom: str) -> dict | None:
    """Résout URL / clé / modèles pour un fournisseur, en combinant les
    variables .env et les valeurs par défaut connues. Retourne None si le
    fournisseur n'est pas exploitable (URL introuvable, ou clé requise
    mais absente) -- il est alors simplement ignoré, sans erreur."""
    maj = nom.upper()
    url = os.getenv(f"LLM_{maj}_URL", "").strip() or _URLS_CONNUES.get(nom, "")
    if not url:
        return None

    cle = os.getenv(f"LLM_{maj}_KEY", "").strip()
    # Ollama tourne en local sans authentification -- une clé absente y est
    # normale, alors qu'elle rend tout autre fournisseur inutilisable.
    if not cle and nom != "ollama":
        return None

    modeles_env = os.getenv(f"LLM_{maj}_MODELS", "").strip()
    modeles = ([m.strip() for m in modeles_env.split(",") if m.strip()]
               if modeles_env else _MODELES_DEFAUT.get(nom, []))
    if not modeles:
        return None

    return {"nom": nom, "url": url.rstrip("/"), "cle": cle, "modeles": modeles}


def _appeler_fournisseur_openai_compatible(cfg: dict, system_prompt: str, messages: list,
                                            user_message: str, max_tokens: int,
                                            format_json: bool = False) -> str | None:
    """Un appel vers une API compatible OpenAI. Retourne le texte de la
    réponse, ou None si ce fournisseur n'a pas abouti (l'appelant passe
    alors au suivant). Ne lève jamais -- une panne de secours ne doit
    jamais faire tomber la surveillance."""
    import requests

    msgs = [{"role": "system", "content": system_prompt}]
    for m in messages:
        if m.get("role") in ("user", "assistant") and m.get("content", "").strip():
            msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role": "user", "content": user_message})

    entetes = {"Content-Type": "application/json"}
    if cfg["cle"]:
        entetes["Authorization"] = f"Bearer {cfg['cle']}"

    for modele in cfg["modeles"]:
        try:
            corps = {
                "model": modele,
                "messages": msgs,
                "max_tokens": max_tokens,
                "temperature": 0.3,
                "stream": False,
            }
            # ← AJOUT : mode JSON strict. Les API compatibles OpenAI
            # (Mistral, Cerebras, Ollama...) acceptent response_format pour
            # CONTRAINDRE la sortie à du JSON syntaxiquement valide -- le
            # modèle ne peut alors plus entourer sa réponse de ```json, ni
            # ajouter une phrase d'introduction, ni laisser une virgule en
            # trop. C'est le principal écart de fiabilité entre un
            # fournisseur de secours et Groq sur ce projet : le
            # raisonnement est bon, c'est l'emballage qui cassait le
            # parsing. Activé uniquement pour les appels qui attendent
            # réellement du JSON (analyse d'incident, génération de
            # règles) -- jamais pour le chat, dont la réponse est du texte
            # libre destiné à être lu.
            if format_json:
                corps["response_format"] = {"type": "json_object"}
            r = requests.post(
                f"{cfg['url']}/chat/completions",
                headers=entetes,
                json=corps,
                # Généreux : un modèle auto-hébergé sur processeur peut
                # légitimement mettre une minute -- acceptable ici, une
                # analyse d'incident n'est pas sensible à la latence.
                timeout=int(os.getenv("LLM_FALLBACK_TIMEOUT_S", "120")),
            )
            if r.status_code != 200:
                print(f"[LLM:{cfg['nom']}] {modele} -> HTTP {r.status_code}")
                continue
            data = r.json()
            contenu = (data.get("choices") or [{}])[0].get("message", {}).get("content")
            if not contenu:
                print(f"[LLM:{cfg['nom']}] {modele} -> reponse vide")
                continue
            usage = data.get("usage") or {}
            _enregistrer_tokens(int(usage.get("total_tokens") or 0), cfg["nom"])
            print(f"[LLM] Bascule reussie sur {cfg['nom']} ({modele})")
            return contenu
        except Exception as e:
            print(f"[LLM:{cfg['nom']}] {modele} -> {str(e)[:120]}")
            continue
    return None


def _essayer_secours(system_prompt: str, messages: list, user_message: str, max_tokens: int,
                      format_json: bool = False) -> str | None:
    """Parcourt les fournisseurs de secours dans l'ordre configuré."""
    global DERNIER_FOURNISSEUR
    for nom in _fournisseurs_secours():
        cfg = _config_fournisseur(nom)
        if not cfg:
            print(f"[LLM] Fournisseur '{nom}' liste mais non configurable (URL ou cle manquante) -- ignore")
            continue
        reponse = _appeler_fournisseur_openai_compatible(cfg, system_prompt, messages, user_message,
                                                          max_tokens, format_json)
        if reponse:
            DERNIER_FOURNISSEUR = nom
            return reponse
    return None


def _appeler_groq_impl(system_prompt: str, messages: list, user_message: str, max_tokens: int,
                        client, compte: str, ok: bool, format_json: bool = False) -> str:
    """Implémentation partagée -- identique en tout point à l'ancienne
    appeler_groq() (mêmes modèles, même cascade de repli, même gestion de
    reasoning_format, même suivi de budget), paramétrée par le client Groq
    et le nom de compte à utiliser. appeler_groq() et appeler_groq_chat()
    ci-dessous ne sont que de fines enveloppes autour de cette fonction --
    aucune divergence de comportement entre les deux, seul le compte
    change."""
    global GROQ_MODEL

    if not ok or not client:
        return (
            "Groq API non configure.\n"
            "1. Va sur https://console.groq.com/keys\n"
            "2. Cree une cle gratuite\n"
            "3. Ajoute GROQ_API_KEY=ta_cle dans .env\n"
            "4. Relance : python -m agent.main"
        )

    restant = budget_journalier_restant(compte)
    if restant < _MARGE_SECURITE_TOKENS:
        print(f"[Groq:{compte}] Budget journalier quasi épuisé ({restant} tokens restants estimés, "
              f"limite {TOKENS_QUOTIDIENS_LIMITE}) -- appel évité, bascule vers le secours si configuré")
        # ← Le budget Groq est épuisé, mais les fournisseurs de secours ont
        # leur propre quota, indépendant -- c'est précisément le cas que
        # cette bascule existe pour couvrir.
        secours = _essayer_secours(system_prompt, messages, user_message, max_tokens, format_json)
        if secours:
            return secours
        return "Groq temporarily unavailable. Please retry in a moment."

    rate_limiter.enregistrer()

    msgs = [{"role": "system", "content": system_prompt}]
    for m in messages:
        if m.get("role") in ("user", "assistant") and m.get("content", "").strip():
            msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role": "user", "content": user_message})

    for modele in GROQ_MODELS:
        try:
            kwargs = dict(
                model=modele,
                messages=msgs,
                max_tokens=max_tokens,
                temperature=0.3,
                timeout=15,
                stream=False,
            )
            if modele.startswith("qwen/"):
                kwargs["reasoning_format"] = "hidden"
            response = client.chat.completions.create(**kwargs)
            if compte == "main" and modele != GROQ_MODEL:
                GROQ_MODEL = modele
                print(f"[Groq] Bascule sur {modele}")
            if getattr(response, "usage", None):
                _enregistrer_tokens(getattr(response.usage, "total_tokens", 0) or 0, compte)
            return response.choices[0].message.content

        except Exception as e:
            err = str(e).lower()
            if "429" in err or "rate_limit" in err or "too_many" in err:
                print(f"[Groq:{compte}] Rate limit {modele} - attente 5s")
                time.sleep(5)
                continue
            elif "model_not_found" in err or "404" in err:
                print(f"[Groq:{compte}] Modele {modele} indisponible")
                continue
            elif any(x in err for x in ["overloaded", "503", "502", "500", "unavailable", "capacity"]):
                print(f"[Groq:{compte}] Serveur surcharge ({modele}) - attente 8s")
                time.sleep(8)
                continue
            elif "invalid_api_key" in err or "401" in err or "authentication" in err:
                return "Cle Groq invalide - verifier GROQ_API_KEY dans .env"
            elif "timeout" in err or "timed out" in err:
                print(f"[Groq:{compte}] Timeout {modele}")
                continue
            else:
                print(f"[Groq:{compte}] Erreur {modele}: {str(e)[:120]}")
                continue

    print(f"[Groq:{compte}] Tous les modeles ont echoue - retry dans 30s")
    time.sleep(30)
    try:
        resp = client.chat.completions.create(
            model=GROQ_MODELS[0],
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_message}],
            max_tokens=min(max_tokens, 800),
            temperature=0.3,
            timeout=20,
        )
        if getattr(resp, "usage", None):
            _enregistrer_tokens(getattr(resp.usage, "total_tokens", 0) or 0, compte)
        return resp.choices[0].message.content
    except Exception as e:
        print(f"[Groq:{compte}] Retry final echoue: {e}")
        # ← Dernier recours avant d'abandonner : les fournisseurs de
        # secours. Couvre le cas d'une panne Groq (et non d'un simple
        # quota), où le budget restant est intact mais le service
        # inaccessible.
        secours = _essayer_secours(system_prompt, messages, user_message, max_tokens, format_json)
        if secours:
            return secours
        return "Groq temporarily unavailable. Please retry in a moment."


def appeler_groq(system_prompt: str, messages: list, user_message: str, max_tokens: int = 1200,
                  format_json: bool = False) -> str:
    """Surveillance, règles, incidents. format_json=True contraint les
    fournisseurs de SECOURS à produire du JSON syntaxiquement valide
    (response_format) -- sans effet sur le chemin Groq, dont le formatage
    est déjà fiable. Optionnel et par défaut désactivé : tous les
    appelants existants gardent exactement le même comportement."""
    return _appeler_groq_impl(system_prompt, messages, user_message, max_tokens,
                               _groq_client, "main", GROQ_OK, format_json)


def appeler_groq_chat(system_prompt: str, messages: list, user_message: str, max_tokens: int = 1200) -> str:
    """← AJOUT : même logique exacte que appeler_groq() (même cascade de
    modèles, même température, même gestion reasoning_format) -- utilisée
    UNIQUEMENT par le chat (websocket_handler.py), sur son propre
    identifiant Groq (_groq_client_chat) et son propre compteur de budget,
    pour que la charge interactive et la charge de surveillance de fond ne
    s'affament jamais l'une l'autre. Tant que GROQ_API_KEY_CHAT n'est pas
    définie dans .env, utilise le même identifiant que la surveillance :
    comportement du chat strictement inchangé."""
    return _appeler_groq_impl(system_prompt, messages, user_message, max_tokens,
                               _groq_client_chat, "chat", GROQ_OK_CHAT)