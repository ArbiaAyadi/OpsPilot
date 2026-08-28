import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Groq
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
# ← CORRIGÉ (19 août 2026) : les 3 modèles précédents sont tous coupés.
# llama-3.1-8b-instant et llama-3.3-70b-versatile dépréciés par Groq le
# 16/08/2026 ; gemma2-9b-it déjà coupé depuis le 08/10/2025 (et son
# remplacement recommandé à l'époque était justement llama-3.1-8b-instant,
# lui-même maintenant mort aussi -- d'où l'échec en cascade des 3 modèles
# en même temps). Remplacements ci-dessous = ceux recommandés OFFICIELLEMENT
# par Groq (console.groq.com/docs/deprecations, vérifié directement) :
# - openai/gpt-oss-20b   remplace llama-3.1-8b-instant
# - openai/gpt-oss-120b  remplace llama-3.3-70b-versatile
# - qwen/qwen3.6-27b     alternative officielle également recommandée pour
#   llama-3.3-70b-versatile -- utilisé ici comme 3e repli, pour garder une
#   vraie diversité de famille de modèle plutôt que 2 variantes gpt-oss.
# Si Groq déprécie l'un de ces 3 à son tour, la même page confirme le
# remplacement à jour -- à revérifier là, pas à deviner.
GROQ_MODELS  = [
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "qwen/qwen3.6-27b",
]

# Surveillance
SURVEILLANCE_INTERVAL          = int(os.getenv("SURVEILLANCE_INTERVAL", "60"))
COOLDOWN_RAPPORT_S             = int(os.getenv("COOLDOWN_RAPPORT_S", "600"))
INTERVALLE_REGENERATION_REGLES = 86400

SCORE_MIN_LLM       = float(os.getenv("SCORE_MIN_ANALYSE_LLM", "0.5"))
SCORE_PLANCHER_LSTM = float(os.getenv("SCORE_PLANCHER_LSTM", "0.35"))

MIN_NOEUDS_ONLINE_POUR_ALERTE = int(os.getenv("MIN_NOEUDS_ONLINE", "1"))

BASE_DIR     = Path(__file__).parent.parent
RAPPORTS_DIR = BASE_DIR / "rapports"
DIST_DIR     = BASE_DIR / "dist"
RAPPORTS_DIR.mkdir(exist_ok=True)

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8088"))

# ← AJOUT : HTTPS optionnel -- désactivé par défaut (les deux valeurs sont
# vides), le serveur continue de tourner en HTTP exactement comme
# aujourd'hui tant que ces deux variables ne sont pas renseignées dans
# .env. Voir agent/main.py pour leur utilisation, et les instructions
# mkcert fournies séparément pour générer ces deux fichiers.
SSL_KEYFILE  = os.getenv("SSL_KEYFILE", "").strip()
SSL_CERTFILE = os.getenv("SSL_CERTFILE", "").strip()