
import threading
import time
from collections import deque
from agent.config import GROQ_API_KEY, GROQ_MODELS

# Etat global
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


class RateLimiter:
    """Compteur d'appels - ne bloque pas, sert a l'affichage UI."""

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


def appeler_groq(system_prompt: str, messages: list, user_message: str, max_tokens: int = 1200) -> str:
    """
    Envoie un message au LLM avec retry automatique.
    Essaie chaque modele de GROQ_MODELS en cascade.
    """
    global GROQ_MODEL

    if not GROQ_OK or not _groq_client:
        return (
            "Groq API non configure.\n"
            "1. Va sur https://console.groq.com/keys\n"
            "2. Cree une cle gratuite\n"
            "3. Ajoute GROQ_API_KEY=ta_cle dans .env\n"
            "4. Relance : python -m agent.main"
        )

    rate_limiter.enregistrer()

    msgs = [{"role": "system", "content": system_prompt}]
    for m in messages:
        if m.get("role") in ("user", "assistant") and m.get("content", "").strip():
            msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role": "user", "content": user_message})

    for modele in GROQ_MODELS:
        try:
            response = _groq_client.chat.completions.create(
                model=modele,
                messages=msgs,
                max_tokens=max_tokens,
                temperature=0.3,
                timeout=15,
                stream=False,
            )
            if modele != GROQ_MODEL:
                GROQ_MODEL = modele
                print(f"[Groq] Bascule sur {modele}")
            return response.choices[0].message.content

        except Exception as e:
            err = str(e).lower()
            if "429" in err or "rate_limit" in err or "too_many" in err:
                print(f"[Groq] Rate limit {modele} - attente 5s")
                time.sleep(5)
                continue
            elif "model_not_found" in err or "404" in err:
                print(f"[Groq] Modele {modele} indisponible")
                continue
            elif any(x in err for x in ["overloaded", "503", "502", "500", "unavailable", "capacity"]):
                print(f"[Groq] Serveur surcharge ({modele}) - attente 8s")
                time.sleep(8)
                continue
            elif "invalid_api_key" in err or "401" in err or "authentication" in err:
                return "Cle Groq invalide - verifier GROQ_API_KEY dans .env"
            elif "timeout" in err or "timed out" in err:
                print(f"[Groq] Timeout {modele}")
                continue
            else:
                print(f"[Groq] Erreur {modele}: {str(e)[:120]}")
                continue

    # Dernier recours
    print("[Groq] Tous les modeles ont echoue - retry dans 30s")
    time.sleep(30)
    try:
        resp = _groq_client.chat.completions.create(
            model=GROQ_MODELS[0],
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_message}],
            max_tokens=min(max_tokens, 800),
            temperature=0.3,
            timeout=20,
        )
        return resp.choices[0].message.content
    except Exception as e:
        print(f"[Groq] Retry final echoue: {e}")
        return "Groq temporarily unavailable. Please retry in a moment."