import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Groq
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODELS  = [
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "gemma2-9b-it",
]

# Surveillance
SURVEILLANCE_INTERVAL          = int(os.getenv("SURVEILLANCE_INTERVAL", "60"))
COOLDOWN_RAPPORT_S             = int(os.getenv("COOLDOWN_RAPPORT_S", "600"))
INTERVALLE_REGENERATION_REGLES = 86400

# ── Score ML ────────────────────────────────────────────────────────────────
# SCORE_MIN_LLM : seuil minimum pour déclencher une analyse LLM + email.
# Valeur 0.5 = seuls les scores réellement anormaux déclenchent une alerte.
#
# SCORE_PLANCHER_LSTM : le LSTM ne peut jamais générer un seuil adaptatif
# inférieur à cette valeur — évite les faux positifs quand il s'entraîne
# sur des métriques nulles (Proxmox éteint pendant l'apprentissage).
SCORE_MIN_LLM       = float(os.getenv("SCORE_MIN_ANALYSE_LLM", "0.5"))
SCORE_PLANCHER_LSTM = float(os.getenv("SCORE_PLANCHER_LSTM", "0.35"))

# ── Garde Proxmox offline ───────────────────────────────────────────────────
# Nombre minimum de nœuds avec statut "online" requis pour autoriser
# la génération d'un rapport ou l'envoi d'une notification.
# Si Proxmox est éteint, etat["noeuds"] est vide ou tous OFFLINE
# → aucun email, aucun rapport.
MIN_NOEUDS_ONLINE_POUR_ALERTE = int(os.getenv("MIN_NOEUDS_ONLINE", "1"))

# Chemins
BASE_DIR     = Path(__file__).parent.parent
RAPPORTS_DIR = BASE_DIR / "rapports"
DIST_DIR     = BASE_DIR / "dist"
RAPPORTS_DIR.mkdir(exist_ok=True)

# Serveur
HOST = "127.0.0.1"
PORT = 8088