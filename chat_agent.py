"""
chat_agent.py — OpsPilot Agent v5.0 FINAL
LLM : Groq API (gratuit, 14400 req/jour, ultra-rapide)
- llama-3.1-8b-instant  : modèle principal (30K TPM gratuit)
- llama-3.3-70b-versatile : fallback qualité
- Interface OpenAI-compatible → pas de bugs de routing
- Rate limiter intégré
- Chat infra/VM + surveillance automatique
"""

import asyncio
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent / "proxmox"))

# ── Phase 3 : Base de données ────────────────────────────────────────────────
try:
    from database import (
        init_db, sauvegarder_metriques, sauvegarder_anomalie,
        sauvegarder_regles, get_regles_actives, get_anomalies,
        sauvegarder_message, get_chat_history, supprimer_chat_history,
        sauvegarder_rapport_db, get_rapports_db, get_stats_db, DB_OK
    )
    print("[OK] database.py chargé")
except Exception as e:
    DB_OK = False
    print(f"[WARN] database.py: {e}")
    def init_db(): return False
    def sauvegarder_metriques(*a, **k): pass
    def sauvegarder_anomalie(*a, **k): return -1
    def sauvegarder_regles(*a, **k): pass
    def get_regles_actives(): return []
    def get_anomalies(**k): return []
    def sauvegarder_message(*a, **k): pass
    def get_chat_history(**k): return []
    def supprimer_chat_history(): pass
    def sauvegarder_rapport_db(*a, **k): pass
    def get_rapports_db(**k): return []
    def get_stats_db(): return {}

# ── Phase 4 : Notifications ──────────────────────────────────────────────────
try:
    from notifications import envoyer_alerte, tester_notifications, get_config_notif
    print("[OK] notifications.py chargé")
except Exception as e:
    print(f"[WARN] notifications.py: {e}")
    async def envoyer_alerte(*a, **k): return {"ntfy": False, "email": False}
    async def tester_notifications(): return {"ntfy": False, "email": False}
    def get_config_notif(): return {}
sys.path.insert(0, str(Path(__file__).parent))

# ══════════════════════════════════════════════════════════════════════════════
# Groq API — OpenAI-compatible, gratuit, ultra-rapide
# Clé gratuite : https://console.groq.com/keys (pas de CB requise)
# ══════════════════════════════════════════════════════════════════════════════
try:
    from groq import Groq

    GROQ_KEY = os.getenv("GROQ_API_KEY", "").strip()
    if not GROQ_KEY:
        raise ValueError("GROQ_API_KEY manquante dans .env")

    _groq_client = Groq(api_key=GROQ_KEY)

    # Modèles Groq gratuits — du plus rapide au plus puissant
    GROQ_MODELS = [
        "llama-3.1-8b-instant",      # 30K TPM, ultra-rapide, parfait pour le chat
        "llama-3.3-70b-versatile",   # 6K TPM, plus puissant
        "gemma2-9b-it",              # 15K TPM, fallback
    ]
    GROQ_MODEL = GROQ_MODELS[0]
    GROQ_OK    = True
    print(f"[OK] Groq API — modèle : {GROQ_MODEL}")
    print(f"[OK] Quota gratuit : 14 400 req/jour, 30 000 tokens/min")

except ImportError:
    GROQ_OK    = False
    _groq_client = None
    GROQ_MODEL = "llama-3.1-8b-instant"
    GROQ_MODELS = [GROQ_MODEL]
    print("[WARN] groq non installé — lance : pip install groq --break-system-packages")
except Exception as e:
    GROQ_OK    = False
    _groq_client = None
    GROQ_MODEL = "llama-3.1-8b-instant"
    GROQ_MODELS = [GROQ_MODEL]
    print(f"[WARN] Groq API : {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Rate Limiter — 25 req/min (marge vs limite 30 de Groq gratuit)
# ══════════════════════════════════════════════════════════════════════════════
class RateLimiter:
    """Rate limiter léger — Groq gère lui-même les 429, on track juste pour l'UI."""
    def __init__(self, max_per_minute: int = 30):
        self.max_per_minute = max_per_minute
        self.appels         = deque()
        self._lock          = threading.Lock()

    def attendre(self):
        """Ne bloque PAS — enregistre juste l'appel pour le compteur UI."""
        with self._lock:
            now = time.time()
            while self.appels and self.appels[0] < now - 60:
                self.appels.popleft()
            self.appels.append(now)

    def slots(self) -> int:
        with self._lock:
            now = time.time()
            while self.appels and self.appels[0] < now - 60:
                self.appels.popleft()
            return max(0, self.max_per_minute - len(self.appels))


_rate_limiter = RateLimiter(max_per_minute=30)


# ══════════════════════════════════════════════════════════════════════════════
# Appel Groq — robuste avec fallback entre modèles
# ══════════════════════════════════════════════════════════════════════════════

def _appeler_groq(system_prompt: str, messages: list[dict], user_message: str, max_tokens: int = 2000) -> str:
    global GROQ_MODEL

    if not GROQ_OK or not _groq_client:
        return (
            "❌ Groq API non configuré.\n"
            "1. Va sur https://console.groq.com/keys\n"
            "2. Crée une clé gratuite (sans CB)\n"
            "3. Ajoute GROQ_API_KEY=ta_clé dans .env\n"
            "4. Relance : python chat_agent.py"
        )

    # Attendre un slot rate limit
    _rate_limiter.attendre()

    # Construire les messages au format OpenAI (compatible Groq)
    msgs = [{"role": "system", "content": system_prompt}]
    for m in messages:
        if m.get("role") in ("user", "assistant") and m.get("content", "").strip():
            msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role": "user", "content": user_message})

    # Essayer chaque modèle
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
            # Mettre à jour le modèle actif si on a changé
            if modele != GROQ_MODEL:
                GROQ_MODEL = modele
                print(f"[Groq] Basculé sur {modele}")
            return response.choices[0].message.content

        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit" in err.lower():
                print(f"[Groq] Rate limit sur {modele} — essai modèle suivant dans 3s")
                time.sleep(3)
                continue
            elif "model_not_found" in err.lower() or "404" in err:
                print(f"[Groq] Modèle {modele} non disponible — essai suivant")
                continue
            elif "invalid_api_key" in err.lower() or "401" in err:
                return (
                    "❌ Clé Groq invalide.\n"
                    "Va sur https://console.groq.com/keys et vérifie GROQ_API_KEY dans .env"
                )
            else:
                print(f"[Groq] Erreur {modele}: {err[:100]}")
                continue

    return "⚠️ Tous les modèles Groq sont temporairement indisponibles. Réessaie dans 1 minute."


# ══════════════════════════════════════════════════════════════════════════════
# Imports locaux
# ══════════════════════════════════════════════════════════════════════════════

try:
    from proxmox_api import (
        get_etat_cluster, get_vm_config, get_vm_status,
        get_allocation_vs_usage, get_gpu_info
    )
    PROXMOX_OK = True
    print("[OK] Proxmox API")
except Exception as e:
    PROXMOX_OK = False
    print(f"[WARN] Proxmox API: {e}")

try:
    from metriques_proxmox import collecter_metriques_cluster
    PROMETHEUS_OK = True
    print("[OK] Prometheus")
except Exception as e:
    PROMETHEUS_OK = False
    print(f"[WARN] Prometheus: {e}")

try:
    from ml_analyser import MLAnalyseur as MLAnalyser
    ML_OK = True
    print("[OK] AI detection engine — Isolation Forest + LSTM")
except Exception as e:
    ML_OK = False
    print(f"[WARN] AI engine: {e}")

try:
    from web_search_service import enrichir_contexte_pour_analyse, formater_contexte_pour_prompt
    WEB_SEARCH_OK = True
    print("[OK] Doc search")
except Exception as e:
    WEB_SEARCH_OK = False
    print(f"[WARN] Doc search: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════════════
SURVEILLANCE_INTERVAL = int(os.getenv("SURVEILLANCE_INTERVAL", "300"))
SCORE_MIN_LLM         = float(os.getenv("SCORE_MIN_ANALYSE_LLM", "0.5"))
COOLDOWN_RAPPORT_S    = int(os.getenv("COOLDOWN_RAPPORT_S", "600"))
RAPPORTS_DIR          = Path("./rapports")
DIST_DIR              = Path(__file__).parent / "dist"
RAPPORTS_DIR.mkdir(exist_ok=True)

# ══════════════════════════════════════════════════════════════════════════════
# État global
# ══════════════════════════════════════════════════════════════════════════════
ws_queue                  = asyncio.Queue()
clients: list[WebSocket]  = []
historique_chat: list[dict] = []
MSG_COUNTER               = 0
dernier_etat              = {}
dernier_lstm              = {"score": 0.0, "seuil": 0.5, "drift": False}
dernier_rapport           = 0.0
_analyser = None


def _next_id() -> str:
    global MSG_COUNTER
    MSG_COUNTER += 1
    return f"msg_{MSG_COUNTER}_{int(time.time())}"


def _init_ml():
    global _analyser
    if ML_OK:
        try:
            _analyser = MLAnalyser()
            print("[OK] AI model hybride initialisé — IF actif immédiatement, LSTM en apprentissage")
        except Exception as e:
            print(f"[WARN] AI model: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# System Prompts
# ══════════════════════════════════════════════════════════════════════════════

def _system_prompt_chat(etat: dict = None) -> str:
    """
    Injecte l'état RÉEL du cluster dans le prompt.
    Le LLM ne doit JAMAIS inventer des ressources — il lit ce qui est fourni.
    """
    # ── Construire l'état réel des nœuds ──────────────────────────────────
    noeuds_detail = ""
    ressources_dispo = ""
    vms_detail = ""

    if etat and etat.get("noeuds"):
        for n in etat.get("noeuds", []):
            ram_libre  = round(n.get("ram_total_gb", 0) - n.get("ram_used_gb", 0), 1)
            disk_libre = round(n.get("disk_total_gb", 0) * (1 - n.get("disk_pct", 0)/100), 0)
            cpu_libre  = round(100 - n.get("cpu_pct", 0), 1)
            noeuds_detail += (
                f"  {n['nom']} [{n['statut'].upper()}]\n"
                f"    CPU  : {n['cpu_pct']}% utilisé | {cpu_libre}% libre | {n.get('cpu_cores','?')} cores physiques\n"
                f"    RAM  : {n['ram_used_gb']}GB/{n['ram_total_gb']}GB utilisée | {ram_libre}GB LIBRE\n"
                f"    Disk : {n.get('disk_used_gb','?')}GB/{n.get('disk_total_gb','?')}GB | {disk_libre}GB libre\n"
                f"    Stockage : local-lvm (LVM-thin)\n\n"
            )
            ressources_dispo += f"  {n['nom']} → RAM libre: {ram_libre}GB | Disk libre: {disk_libre}GB | CPU libre: {cpu_libre}%\n"

        for v in etat.get("vms", []):
            vms_detail += (
                f"  VMID {v['vmid']} — {v['nom']} [{v['statut'].upper()}] sur {v['noeud']}\n"
                f"    vCPU: {v.get('vcpus','?')} | RAM allouée: {v.get('maxmem_gb','?')}GB | "
                f"Disk: {v.get('maxdisk_gb','?')}GB\n"
            )

    if etat:
        nd = noeuds_detail if noeuds_detail else "  Données cluster non disponibles"
        vd = vms_detail if vms_detail else "  Aucune VM détectée"
        rd = ressources_dispo if ressources_dispo else "  Non disponible"
        etat_bloc = (
            "\n## ÉTAT RÉEL DU CLUSTER (données live)\n"
            "### Nœuds Proxmox\n" + nd +
            "### VMs existantes\n" + vd +
            "### RESSOURCES DISPONIBLES POUR NOUVELLES VMs\n" + rd + "\n"
        )
    else:
        etat_bloc = "\n## CLUSTER : données non disponibles\n"

        return f"""You are OpsPilot, an expert Proxmox VE infrastructure assistant. Always respond in ENGLISH only.

## ABSOLUTE RULE — NEVER INVENT
Tu as accès aux données RÉELLES du cluster ci-dessous.
- Utilise UNIQUEMENT ces données pour conseiller les ressources
- Ne jamais inventer un VMID, une taille de disque, ou une quantité de RAM
- Si les données cluster sont absentes, dis-le clairement
- Le VMID à suggérer = max(VMIDs existants) + 1

## ROLE
Answer questions about: VM creation/config, resource sizing,
Proxmox administration, Linux on VMs, troubleshooting.
Off-topic → "I specialize in Proxmox/VM infrastructure."
{etat_bloc}
## FIXED INFRASTRUCTURE
- pve1 : 192.168.138.100 — Proxmox VE 9.1.1
- pve2 : 192.168.138.101 — Proxmox VE 9.1.1
- Network bridge: vmbr0
- Storage: local-lvm (LVM-thin) for VMs, local for ISOs

## FOR VM CREATION — mandatory response:
1. Recommended resources based on available RAM/CPU/DISK on the node
2. Suggested VMID (next available after existing ones)
3. COMPLETE and EXACT qm create command
4. Post-creation steps (start, cloud-init or ISO)
5. Warning if resources are insufficient

## FORMAT
- English only
- Use ```bash blocks for all commands
- Explain WHY each value was chosen
"""


def _system_prompt_surveillance(etat: dict = None) -> str:
    etat_str = ""
    if etat and etat.get("noeuds"):
        noeuds_str = "\n".join([
            f"  {n['nom']} [{n['statut'].upper()}] CPU:{n['cpu_pct']}% RAM:{n['ram_pct']}% ({n['ram_used_gb']}/{n['ram_total_gb']}GB) Disk:{n['disk_pct']}%"
            for n in etat.get("noeuds", [])
        ])
        vms_str = "\n".join([
            f"  VMID:{v['vmid']} {v['nom']} [{v['statut'].upper()}] sur {v['noeud']} | vCPU:{v.get('vcpus','?')} RAM:{v.get('maxmem_gb','?')}GB"
            for v in etat.get("vms", [])
        ])
        lstm = dernier_lstm
        etat_str = f"""
ÉTAT CLUSTER (live):
{noeuds_str}
VMs: {vms_str}
Score anomalie IA: {lstm['score']:.4f} / seuil {lstm['seuil']:.4f}
"""
    return f"""You are OpsPilot, an expert Proxmox VE monitoring agent.

ABSOLUTE RULES:
- NEVER start with "Incident Report" or any generic title
- SHORT report (max 150 words), direct, actionable
- ALWAYS respond in ENGLISH only
- Use official Proxmox/virtualization thresholds:
  * RAM > 85% → CRITICAL (OOM kill risk, excessive swap)
  * CPU > 80% sustained → CRITICAL (contention, latency)
  * Disk > 90% → CRITICAL (writes impossible, corruption)
  * RAM > 75% → IMPORTANT (preventive)
  * CPU > 65% → IMPORTANT (monitoring)
- Exact commands based on official Proxmox documentation
- Explain WHY each action is necessary
{etat_str}"""


# ══════════════════════════════════════════════════════════════════════════════
# Gestion historique avec édition
# ══════════════════════════════════════════════════════════════════════════════

def _get_messages_llm(jusqu_a_id: str = None) -> list[dict]:
    msgs = []
    for m in historique_chat:
        msgs.append({"role": m["role"], "content": m["content"]})
        if jusqu_a_id and m["id"] == jusqu_a_id:
            break
    return msgs[-8:]


def _ajouter_message(role: str, content: str, msg_id: str = None) -> dict:
    msg = {
        "id":        msg_id or _next_id(),
        "role":      role,
        "content":   content,
        "timestamp": datetime.now().isoformat(),
        "edited":    False,
    }
    historique_chat.append(msg)
    if len(historique_chat) > 100:
        historique_chat.pop(0)
    return msg


def _editer_message(msg_id: str, nouveau_contenu: str) -> int:
    for i, m in enumerate(historique_chat):
        if m["id"] == msg_id and m["role"] == "user":
            historique_chat[i]["content"]   = nouveau_contenu
            historique_chat[i]["edited"]    = True
            historique_chat[i]["timestamp"] = datetime.now().isoformat()
            del historique_chat[i + 1:]
            return i
    return -1


# ══════════════════════════════════════════════════════════════════════════════
# Répondre
# ══════════════════════════════════════════════════════════════════════════════

async def repondre_question(question: str, msg_id_edition: str = None) -> tuple[str, str]:
    etat   = dernier_etat if dernier_etat else (get_etat_cluster() if PROXMOX_OK else {})
    system = _system_prompt_chat(etat)
    msgs   = _get_messages_llm(jusqu_a_id=msg_id_edition) if msg_id_edition else _get_messages_llm()

    loop    = asyncio.get_event_loop()
    reponse = await loop.run_in_executor(
        None, lambda: _appeler_groq(system, msgs, question, max_tokens=1200)
    )
    msg_assistant = _ajouter_message("assistant", reponse)
    return reponse, msg_assistant["id"]


# ══════════════════════════════════════════════════════════════════════════════
# Analyser anomalie (surveillance)
# ══════════════════════════════════════════════════════════════════════════════

async def analyser_anomalie_llm(anomalies: list[dict], etat: dict) -> str:
    lstm          = dernier_lstm
    anomalies_str = "\n".join([f"- [{a['niveau']}] {a['message']}" for a in anomalies])
    prompt = f"""Anomalies detected on Proxmox cluster:
{anomalies_str}
AI Score: {lstm['score']:.4f} / threshold {lstm['seuil']:.4f}

Respond in ENGLISH ONLY. Use this EXACT format with two sections:

---INCIDENT---
**Severity:** [CRITICAL/IMPORTANT/MONITORING]
**Summary:** [1 sentence describing what is happening]
**Causes:**
- [root cause based on real metrics]
- [secondary cause if any]

---RECOMMENDATION---
**Fix title:** [short action title, max 8 words]
**Problem:** [1 line — what exactly is wrong with numbers]
**Immediate action:**
```bash
[exact Proxmox/Linux command that fixes the issue]
```
[1 line explaining what this command does]
**Long-term:** [1 preventive action]

Rules:
- Use ONLY official Proxmox commands (qm, pveum, pvenode, pvesm, pveadm)
- Include real values from metrics (actual %, actual GB)
- Max 200 words total
- No generic advice — be specific to this cluster"""

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: _appeler_groq(_system_prompt_surveillance(etat), [], prompt, 800)
    )


# ══════════════════════════════════════════════════════════════════════════════
# Rapport
# ══════════════════════════════════════════════════════════════════════════════

def _sauvegarder_rapport(anomalies: list, analyse: str, etat: dict, score: float) -> str:
    ts      = datetime.now()
    niveaux = [a.get("niveau", "") for a in anomalies]
    niveau  = "critique" if "CRITIQUE" in niveaux else "important" if "IMPORTANT" in niveaux else "surveillance"

    noeuds_str = "\n".join([
        f"| {n['nom']} | {n['cpu_pct']}% | {n['ram_pct']}% | {n['disk_pct']}% | {n['statut']} |"
        for n in etat.get("noeuds", [])
    ])
    vms_str = "\n".join([
        f"| {v['nom']} | {v['vmid']} | {v['noeud']} | {v['cpu_pct']}% | {v['ram_pct']}% | {v['statut']} |"
        for v in etat.get("vms", [])
    ])

    contenu = f"""# OpsPilot Rapport — {ts.strftime('%Y-%m-%d %H:%M:%S')}
**Sévérité :** {niveau.upper()}  
**Score IA :** {score:.4f}  
**LLM :** Groq {GROQ_MODEL}

## Anomalies
{chr(10).join(f'- [{a["niveau"]}] {a["message"]}' for a in anomalies)}

## Nœuds
| Nœud | CPU | RAM | Disk | Statut |
|------|-----|-----|------|--------|
{noeuds_str}

## VMs
| Nom | ID | Nœud | CPU | RAM | Statut |
|-----|----|------|-----|-----|--------|
{vms_str}

## Analyse
{analyse}

---
*OpsPilot — Groq {GROQ_MODEL} — {ts.isoformat()}*
"""
    nom = f"report_{ts.strftime('%Y%m%d_%H%M%S')}_{niveau}.md"
    (RAPPORTS_DIR / nom).write_text(contenu, encoding="utf-8")
    # Phase 3 : Persister aussi en DB
    sauvegarder_rapport_db(nom=nom, contenu=contenu, niveau=niveau, score=score)
    print(f"[Report] {nom}")
    return nom



# ══════════════════════════════════════════════════════════════════════════════
# Génération automatique de règles par le LLM (Intelligence adaptative)
# ══════════════════════════════════════════════════════════════════════════════

# Documentation Proxmox officielle intégrée dans le contexte
PROXMOX_DOCS_CONTEXT = """
## Seuils officiels Proxmox VE (documentation officielle + meilleures pratiques virtualisation)

### CPU
- Proxmox recommande de ne pas dépasser 80% d'utilisation CPU soutenue
- Au-delà de 80%, le scheduler Linux commence à créer de la contention entre VMs
- Seuil CRITIQUE : > 80% pendant plus de 5 minutes consécutives
- Seuil IMPORTANT : > 65% pendant plus de 15 minutes
- Baseline normale pour cluster Proxmox de développement : 10-40%

### RAM (Mémoire)
- Proxmox VE : ne jamais dépasser 85% d'utilisation RAM sur l'hyperviseur
- Au-delà : le kernel Linux active le swap, dégradant massivement les performances VMs
- OOM Killer se déclenche automatiquement à saturation complète
- Seuil CRITIQUE : > 85% (risque OOM kill, swap excessif)
- Seuil IMPORTANT : > 75% (surveillance renforcée)
- Baseline normale cluster Proxmox : 40-70%

### Disque (Storage)
- LVM-thin Proxmox : ne jamais dépasser 85% du pool (risque de corruption)
- Au-delà de 90% : nouvelles écritures impossibles, VMs peuvent se corrompre
- Seuil CRITIQUE : > 90% (écriture impossible)
- Seuil IMPORTANT : > 80% (planifier nettoyage urgent)
- Les snapshots Proxmox peuvent consommer rapidement l'espace LVM-thin

### Disponibilité VMs
- VM arrêtée de manière inattendue = CRITIQUE immédiat (vérifier HA)
- VM en état error = CRITIQUE (qm status <id> pour diagnostic)
- VM en état paused = IMPORTANT (ressources bloquées)

### Réseau (Network)
- Saturation NIC > 80% = IMPORTANT (risque de perte de paquets)
- Latence inter-nœuds Proxmox cluster > 10ms = IMPORTANT (corosync instable)

### Score anomalie LSTM
- Score > 0.8 = CRITIQUE (comportement très anormal)
- Score 0.5-0.8 = IMPORTANT (anomalie confirmée)
- Score 0.3-0.5 = SURVEILLANCE (début d'anomalie)
"""

# Règles stockées en mémoire (seront persistées en DB Phase 3)
_regles_ia: list[dict] = []
_derniere_generation_regles: float = 0.0
INTERVALLE_REGENERATION_REGLES = 86400  # 24h


async def generer_regles_ia(etat: dict) -> list[dict]:
    """
    Le LLM analyse les métriques réelles du cluster et génère
    des règles d'alerte contextuelles basées sur la doc Proxmox officielle.
    Appelé toutes les 24h ou au démarrage.
    """
    global _regles_ia, _derniere_generation_regles

    if not etat or not etat.get("noeuds"):
        return _regles_ia

    # Construire le contexte des métriques actuelles
    noeuds_ctx = ""
    for n in etat.get("noeuds", []):
        noeuds_ctx += (
            f"  {n['nom']}: CPU={n['cpu_pct']}% RAM={n['ram_pct']}% "
            f"({n['ram_used_gb']}/{n['ram_total_gb']}GB) Disk={n['disk_pct']}%\n"
        )

    vms_ctx = ""
    for v in etat.get("vms", []):
        vms_ctx += (
            f"  {v['nom']} (VMID:{v['vmid']}) sur {v['noeud']}: "
            f"statut={v['statut']} vCPU={v.get('vcpus','?')} RAM={v.get('maxmem_gb','?')}GB\n"
        )

    prompt = f"""You are a Proxmox VE expert. Analyze this cluster and generate adapted alert rules.

MÉTRIQUES ACTUELLES DU CLUSTER:
{noeuds_ctx}
VMs:
{vms_ctx}

{PROXMOX_DOCS_CONTEXT}

Generate exactly 8 JSON alert rules adapted to THIS specific cluster.
Respond ONLY with a valid JSON array, no text before or after:

[
  {{
    "id": "rule_001",
    "metric": "server.cpu_pct",
    "operateur": ">",
    "seuil": 80,
    "duree_min": 5,
    "severite": "CRITIQUE",
    "cible": "pve1,pve2",
    "titre": "CPU hyperviseur critique",
    "description": "CPU > 80% soutenu cause contention entre VMs",
    "action": "pvesh get /nodes/{{node}}/status | grep cpu",
    "source": "Documentation Proxmox VE officielle"
  }}
]

Adapt thresholds to observed real metrics. If CPU baseline is 40%, set IMPORTANT threshold at 65%, CRITICAL at 80%.
"""

    loop = asyncio.get_event_loop()
    reponse = await loop.run_in_executor(
        None,
        lambda: _appeler_groq(
            "You are a Proxmox VE expert. Generate ONLY valid JSON, no markdown, no explanation.",
            [],
            prompt,
            max_tokens=1500
        )
    )

    # Parser le JSON
    import json, re
    try:
        # Extraire le JSON même si entouré de texte
        json_match = re.search(r'\[.*?\]', reponse, re.DOTALL)
        if json_match:
            regles = json.loads(json_match.group())
            if isinstance(regles, list) and len(regles) > 0:
                _regles_ia = regles
                _derniere_generation_regles = time.time()
                print(f"[AI Rules] {len(regles)} règles générées")
                # Phase 3 : Persister les règles en DB
                sauvegarder_regles(regles)
                return regles
    except Exception as e:
        print(f"[AI Rules] Erreur parsing JSON: {e}")
        print(f"[AI Rules] Réponse brute: {reponse[:200]}")

    return _regles_ia


def get_regles_ia() -> list[dict]:
    """Retourne les règles actuelles."""
    return _regles_ia


def regles_necessitent_regeneration() -> bool:
    """True si les règles doivent être régénérées (toutes les 24h)."""
    return time.time() - _derniere_generation_regles > INTERVALLE_REGENERATION_REGLES


# ══════════════════════════════════════════════════════════════════════════════
# Thread surveillance
# ══════════════════════════════════════════════════════════════════════════════

def _normaliser_etat(etat: dict) -> dict:
    """
    Normalise la structure retournée par proxmox_api.get_etat_cluster()
    quelle que soit la version ou le format retourné.
    
    Garantit que etat contient toujours :
    - etat["noeuds"] : liste de nœuds avec cpu_pct, ram_pct, disk_pct, nom
    - etat["vms"]    : liste de VMs avec statut, nom, vmid, noeud
    - etat["alertes"]: liste d'alertes
    - etat["vms_running"] : int
    - etat["net_in_mbps"], etat["net_out_mbps"] : float
    """
    if not etat:
        return {}

    normalized = dict(etat)

    # ── Normaliser les nœuds ─────────────────────────────────────────────────
    # Supporter "noeuds", "nodes", "nœuds"
    noeuds = (
        etat.get("noeuds") or
        etat.get("nodes") or
        etat.get("nœuds") or
        []
    )

    noeuds_norm = []
    for n in noeuds:
        # Normaliser chaque nœud
        n_norm = {
            "nom":          n.get("nom") or n.get("name") or n.get("node") or "unknown",
            "statut":       n.get("statut") or n.get("status") or "unknown",
            "cpu_pct":      float(n.get("cpu_pct") or n.get("cpu") or 0),
            "cpu_cores":    int(n.get("cpu_cores") or n.get("maxcpu") or 0),
            "ram_pct":      float(n.get("ram_pct") or n.get("mem_pct") or 0),
            "ram_used_gb":  float(n.get("ram_used_gb") or n.get("mem_used_gb") or 0),
            "ram_total_gb": float(n.get("ram_total_gb") or n.get("mem_total_gb") or 0),
            "disk_pct":     float(n.get("disk_pct") or 0),
            "disk_used_gb": float(n.get("disk_used_gb") or 0),
            "disk_total_gb":float(n.get("disk_total_gb") or 0),
            "net_in_mbps":  float(n.get("net_in_mbps") or n.get("netin",  0) / 1e6 if n.get("netin")  else 0),
            "net_out_mbps": float(n.get("net_out_mbps") or n.get("netout", 0) / 1e6 if n.get("netout") else 0),
            "uptime_h":     float(n.get("uptime_h") or (n.get("uptime", 0) / 3600)),
            "vms_running":  int(n.get("vms_running") or 0),
            # Nouvelles ressources (0 par défaut si non disponibles)
            "swap_pct":           float(n.get("swap_pct", 0)),
            "cpu_iowait_pct":     float(n.get("cpu_iowait_pct", 0)),
            "disk_read_iops":     float(n.get("disk_read_iops", 0)),
            "disk_write_iops":    float(n.get("disk_write_iops", 0)),
            "disk_read_latency_ms":  float(n.get("disk_read_latency_ms", 0)),
            "disk_write_latency_ms": float(n.get("disk_write_latency_ms", 0)),
            "net_errors_in":      float(n.get("net_errors_in", 0)),
            "net_errors_out":     float(n.get("net_errors_out", 0)),
            "net_drop_in":        float(n.get("net_drop_in", 0)),
            "net_drop_out":       float(n.get("net_drop_out", 0)),
            "cpu_temp_max_c":     float(n.get("cpu_temp_max_c", 0)),
            "smart_ok":           bool(n.get("smart_ok", True)),
            "smart_reallocated_sectors": int(n.get("smart_reallocated_sectors", 0)),
            "smart_uncorrectable":int(n.get("smart_uncorrectable", 0)),
            "zfs_arc_hit_rate":   float(n.get("zfs_arc_hit_rate", 0)),
            "zfs_available":      bool(n.get("zfs_available", False)),
            "corosync_ok":        bool(n.get("corosync_ok", True)),
            "fd_used_pct":        float(n.get("fd_used_pct", 0)),
        }
        # Calculer ram_pct si manquant
        if n_norm["ram_pct"] == 0 and n_norm["ram_total_gb"] > 0:
            n_norm["ram_pct"] = round(n_norm["ram_used_gb"] / n_norm["ram_total_gb"] * 100, 2)

        noeuds_norm.append(n_norm)

    normalized["noeuds"] = noeuds_norm

    # ── Normaliser les VMs ────────────────────────────────────────────────────
    vms = etat.get("vms") or etat.get("machines") or []
    vms_norm = []
    for v in vms:
        vms_norm.append({
            "vmid":       str(v.get("vmid") or v.get("id") or ""),
            "nom":        v.get("nom") or v.get("name") or v.get("vmid") or "unknown",
            "statut":     v.get("statut") or v.get("status") or "unknown",
            "noeud":      v.get("noeud") or v.get("node") or "unknown",
            "vcpus":      int(v.get("vcpus") or v.get("cpus") or 0),
            "maxmem_gb":  float(v.get("maxmem_gb") or (v.get("maxmem", 0) / 1e9)),
            "maxdisk_gb": float(v.get("maxdisk_gb") or (v.get("maxdisk", 0) / 1e9)),
            "cpu_pct":    float(v.get("cpu_pct") or v.get("cpu", 0) * 100),
            "ram_pct":    float(v.get("ram_pct") or 0),
            "net_in_mbps": float(v.get("net_in_mbps") or v.get("netin",  0) / 1e6 if v.get("netin")  else 0),
            "net_out_mbps":float(v.get("net_out_mbps") or v.get("netout", 0) / 1e6 if v.get("netout") else 0),
        })
    normalized["vms"] = vms_norm

    # ── Calculer les métriques réseau agrégées ────────────────────────────────
    # C'est ce qui manquait : net_in/out agrégé sur tous les nœuds
    normalized["net_in_mbps"]  = round(sum(n.get("net_in_mbps",  0) for n in noeuds_norm), 3)
    normalized["net_out_mbps"] = round(sum(n.get("net_out_mbps", 0) for n in noeuds_norm), 3)

    # ── VMs running ──────────────────────────────────────────────────────────
    vms_running = (
        etat.get("vms_running") or
        sum(1 for v in vms_norm if v["statut"] in ("running", "en cours")) or
        sum(n.get("vms_running", 0) for n in noeuds_norm)
    )
    normalized["vms_running"] = int(vms_running)

    # ── Alertes ───────────────────────────────────────────────────────────────
    if "alertes" not in normalized:
        normalized["alertes"] = etat.get("alerts") or etat.get("alertes") or []

    return normalized


def _surveillance_thread(loop: asyncio.AbstractEventLoop):
    global dernier_etat, dernier_lstm, dernier_rapport
    etat_prec = {}
    print(f"[Monitoring] Démarré — toutes les {SURVEILLANCE_INTERVAL}s")

    while True:
        try:
            if PROXMOX_OK:
                etat_raw = get_etat_cluster()
                # Normaliser la structure quelle que soit la version de proxmox_api
                etat = _normaliser_etat(etat_raw)
                dernier_etat = etat
            else:
                etat = dernier_etat

            # Phase 3 : Persister les métriques en DB
            if etat:
                sauvegarder_metriques(etat, dernier_lstm.get("score", 0.0))

            # Générer/régénérer les règles IA toutes les 24h
            if etat and regles_necessitent_regeneration() and _rate_limiter.slots() >= 5:
                print("[AI Rules] Génération automatique des règles...")
                asyncio.run_coroutine_threadsafe(
                    generer_regles_ia(etat), loop
                ).result(timeout=60)

            metriques_prom = {}
            if PROMETHEUS_OK:
                try:    metriques_prom = collecter_metriques_cluster()
                except Exception as e: print(f"[Prometheus] {e}")

            score, seuil, drift = 0.0, 0.5, False
            if _analyser and etat:
                try:
                    # Construire un dict unifié de métriques pour ML
                    noeuds = etat.get("noeuds", [])
                    n_noeuds = max(len(noeuds), 1)
                    def avg(key): return sum(x.get(key,0) for x in noeuds) / n_noeuds
                    def total(key): return sum(x.get(key,0) for x in noeuds)

                    metriques_ml = {
                        # Ressources de base
                        "cpu_pct":              avg("cpu_pct"),
                        "ram_pct":              avg("ram_pct"),
                        "disk_pct":             avg("disk_pct"),
                        # Nouvelles ressources
                        "swap_pct":             avg("swap_pct"),
                        "cpu_iowait_pct":       avg("cpu_iowait_pct"),
                        "disk_read_iops":       total("disk_read_iops"),
                        "disk_write_iops":      total("disk_write_iops"),
                        "disk_read_latency_ms": avg("disk_read_latency_ms"),
                        "disk_write_latency_ms":avg("disk_write_latency_ms"),
                        "net_in_mbps":          total("net_in_mbps"),
                        "net_out_mbps":         total("net_out_mbps"),
                        "net_errors_in":        total("net_errors_in"),
                        "net_errors_out":       total("net_errors_out"),
                        "net_drop_in":          total("net_drop_in"),
                        "net_drop_out":         total("net_drop_out"),
                        "vms_running":          etat.get("vms_running", 0),
                    }
                    # Enrichir depuis Prometheus si disponible
                    if metriques_prom and isinstance(metriques_prom, dict):
                        for k in metriques_ml:
                            if k in metriques_prom:
                                metriques_ml[k] = metriques_prom[k]

                    score, seuil = _analyser.analyser(metriques_ml)
                    score, seuil = float(score), float(seuil)
                    
                    # Récupérer les stats des deux modèles
                    ml_stats = _analyser.get_stats() if hasattr(_analyser, "get_stats") else {}
                    drift = ml_stats.get("lstm", {}).get("drift_detecte", False)
                    
                    dernier_lstm = {
                        "score":      score,
                        "seuil":      seuil,
                        "drift":      drift,
                        "score_if":   ml_stats.get("score_if", 0.0),
                        "score_lstm": ml_stats.get("score_lstm", 0.0),
                        "lstm_ready": ml_stats.get("lstm_ready", False),
                    }
                except Exception as e:
                    print(f"[AI Engine] {e}")

            nouvelles = _detecter_anomalies(etat, etat_prec)
            if score >= SCORE_MIN_LLM:
                nouvelles.append({
                    "niveau": "IMPORTANT", "cible": "cluster",
                    "message": f"Score IA {score:.4f} dépasse seuil {seuil:.4f}", "type": "ai_score",
                })

            now = time.time()
            if nouvelles and (now - dernier_rapport) >= COOLDOWN_RAPPORT_S:
                slots = _rate_limiter.slots()
                print(f"[Monitoring] {len(nouvelles)} anomalie(s) — slots: {slots}")
                if slots >= 5:
                    analyse = asyncio.run_coroutine_threadsafe(
                        analyser_anomalie_llm(nouvelles, etat), loop
                    ).result(timeout=30)
                else:
                    lignes  = "\n".join(f"- [{a['niveau']}] {a['message']}" for a in nouvelles)
                    analyse = f"**Anomalies détectées** (quota réservé pour le chat)\n\n{lignes}"

                nom_rapport     = _sauvegarder_rapport(nouvelles, analyse, etat, score)
                dernier_rapport = now
                asyncio.run_coroutine_threadsafe(ws_queue.put({
                    "type": "alerte", "role": "assistant", "content": analyse,
                    "anomalies": nouvelles, "timestamp": datetime.now().isoformat(),
                    "rapport": nom_rapport, "lstm": dernier_lstm,
                }), loop)

                # Phase 4 : Envoyer notifications
                niv_max = "CRITIQUE" if any(a.get("niveau")=="CRITIQUE" for a in nouvelles) else "IMPORTANT"
                titre_notif = f"{nouvelles[0].get('message','Anomalie')[:60]}"
                asyncio.run_coroutine_threadsafe(
                    envoyer_alerte(
                        titre=titre_notif,
                        message=analyse[:300] if len(analyse)>300 else analyse,
                        severite=niv_max,
                        anomalies=nouvelles,
                        etat=etat
                    ), loop
                )

                # Phase 3 : Persister anomalies en DB
                for a in nouvelles:
                    sauvegarder_anomalie(
                        niveau=a.get("niveau","INFO"),
                        message=a.get("message",""),
                        noeud=a.get("cible"),
                        score=dernier_lstm.get("score", 0.0),
                        rapport=nom_rapport
                    )
            elif nouvelles:
                print(f"[Monitoring] Cooldown ({int(COOLDOWN_RAPPORT_S-(now-dernier_rapport))}s)")

            if drift and _analyser:
                _analyser.reentrainer()

            asyncio.run_coroutine_threadsafe(ws_queue.put({
                "type": "etat_cluster", "etat": etat,
                "lstm": dernier_lstm, "timestamp": datetime.now().isoformat(),
            }), loop)
            etat_prec = etat

        except Exception as e:
            print(f"[Monitoring] Erreur: {e}")
            import traceback; traceback.print_exc()

        time.sleep(SURVEILLANCE_INTERVAL)


def _detecter_anomalies(etat: dict, etat_prec: dict) -> list[dict]:
    anomalies    = []
    alertes_prec = {a["message"] for a in etat_prec.get("alertes", [])}
    for a in etat.get("alertes", []):
        if a["message"] not in alertes_prec:
            anomalies.append(a)
    vms_avant = {v["vmid"]: v for v in etat_prec.get("vms", [])}
    for vm in etat.get("vms", []):
        vid = vm["vmid"]
        if vid in vms_avant and vms_avant[vid]["statut"] == "running" and vm["statut"] == "stopped":
            anomalies.append({
                "niveau": "CRITIQUE", "cible": vm["nom"],
                "message": f"VM {vm['nom']} (ID:{vid}) stoppée inopinément sur {vm['noeud']}",
                "type": "vm_down",
            })

    # ── Swap ──────────────────────────────────────────────────────────────────
    for noeud in etat.get("noeuds", []):
        nom  = noeud.get("nom", noeud.get("node", "?"))
        swap = noeud.get("swap_pct", 0)
        if swap > 80:
            anomalies.append({"niveau":"CRITIQUE",  "cible":nom, "message":f"Node {nom} — Swap critical: {swap:.1f}% (RAM already saturated)"})
        elif swap > 50:
            anomalies.append({"niveau":"IMPORTANT", "cible":nom, "message":f"Node {nom} — Swap high: {swap:.1f}% (memory pressure)"})

        # I/O wait
        iowait = noeud.get("cpu_iowait_pct", 0)
        if iowait > 30:
            anomalies.append({"niveau":"CRITIQUE",  "cible":nom, "message":f"Node {nom} — CPU I/O wait critical: {iowait:.1f}% (storage bottleneck)"})
        elif iowait > 15:
            anomalies.append({"niveau":"IMPORTANT", "cible":nom, "message":f"Node {nom} — CPU I/O wait high: {iowait:.1f}%"})

        # Latence disque
        rl = noeud.get("disk_read_latency_ms", 0)
        wl = noeud.get("disk_write_latency_ms", 0)
        if rl > 50 or wl > 50:
            anomalies.append({"niveau":"CRITIQUE",  "cible":nom, "message":f"Node {nom} — Disk latency critical: read={rl}ms write={wl}ms"})
        elif rl > 10 or wl > 10:
            anomalies.append({"niveau":"IMPORTANT", "cible":nom, "message":f"Node {nom} — Disk latency high: read={rl}ms write={wl}ms"})

        # Température
        temp = noeud.get("cpu_temp_max_c", 0)
        if temp > 85:
            anomalies.append({"niveau":"CRITIQUE",  "cible":nom, "message":f"Node {nom} — CPU temperature critical: {temp}°C (throttling risk)"})
        elif temp > 75:
            anomalies.append({"niveau":"IMPORTANT", "cible":nom, "message":f"Node {nom} — CPU temperature high: {temp}°C"})

        # SMART disques
        if not noeud.get("smart_ok", True):
            uncorr  = noeud.get("smart_uncorrectable", 0)
            realloc = noeud.get("smart_reallocated_sectors", 0)
            pending = noeud.get("smart_pending_sectors", 0)
            if uncorr > 0:
                anomalies.append({"niveau":"CRITIQUE",  "cible":nom, "message":f"Node {nom} — DISK FAILURE IMMINENT: {uncorr} uncorrectable errors (backup now!)"})
            elif realloc > 0:
                anomalies.append({"niveau":"CRITIQUE",  "cible":nom, "message":f"Node {nom} — Disk degraded: {realloc} reallocated sectors (plan replacement)"})
            elif pending > 0:
                anomalies.append({"niveau":"IMPORTANT", "cible":nom, "message":f"Node {nom} — Disk: {pending} pending sectors"})

        # ZFS ARC
        zfs_hit = noeud.get("zfs_arc_hit_rate", 0)
        if noeud.get("zfs_available") and 0 < zfs_hit < 70:
            anomalies.append({"niveau":"IMPORTANT", "cible":nom, "message":f"Node {nom} — ZFS ARC hit rate low: {zfs_hit}% (add RAM for better I/O)"})

        # Erreurs réseau
        net_err  = noeud.get("net_errors_in", 0) + noeud.get("net_errors_out", 0)
        net_drop = noeud.get("net_drop_in", 0)  + noeud.get("net_drop_out", 0)
        if net_err > 10:
            anomalies.append({"niveau":"IMPORTANT", "cible":nom, "message":f"Node {nom} — Network errors: {net_err:.0f}/s (check NIC or cable)"})
        if net_drop > 10:
            anomalies.append({"niveau":"IMPORTANT", "cible":nom, "message":f"Node {nom} — Packet drops: {net_drop:.0f}/s (network saturation)"})

    # Corosync quorum
    if not etat.get("cluster", {}).get("corosync_quorum_ok", True):
        anomalies.append({"niveau":"CRITIQUE", "cible":"cluster", "message":"CLUSTER QUORUM LOST — All VMs at risk of automatic shutdown"})

    return anomalies


# ══════════════════════════════════════════════════════════════════════════════
# FastAPI + WebSocket
# ══════════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Phase 3 : Initialiser la base de données
    db_status = init_db()
    print(f"[OpsPilot] DB: {'PostgreSQL ✓' if db_status else 'Mémoire (fallback)'}")

    _init_ml()
    loop = asyncio.get_event_loop()
    threading.Thread(target=_surveillance_thread, args=(loop,), daemon=True).start()
    asyncio.create_task(_broadcaster())

    # Générer les règles IA au démarrage (ne pas attendre 24h)
    async def _generer_regles_au_demarrage():
        await asyncio.sleep(15)  # Attendre que Proxmox soit connecté
        if dernier_etat:
            print("[AI Rules] Génération initiale des règles au démarrage...")
            await generer_regles_ia(dernier_etat)
        else:
            print("[AI Rules] Pas de données cluster — règles statiques utilisées")
    asyncio.create_task(_generer_regles_au_demarrage())

    print(f"[OpsPilot] http://127.0.0.1:8088")
    print(f"[OpsPilot] Groq:{GROQ_MODEL if GROQ_OK else 'OFF'} | DB:{'PG' if db_status else 'RAM'} | Proxmox:{'OK' if PROXMOX_OK else 'OFF'}")
    yield


app = FastAPI(title="OpsPilot", version="5.0-groq", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.append(ws)

    await ws.send_json({
        "type": "init", "role": "assistant",
        "id": _next_id(), "timestamp": datetime.now().isoformat(),
        "content": "connected",
    })

    # Phase 3 : Restaurer historique depuis DB si mémoire vide
    if not historique_chat:
        hist_db = get_chat_history(limit=50)
        if hist_db:
            for m in hist_db:
                historique_chat.append({
                    "id": m.get("msg_id", _next_id()),
                    "role": m.get("role"),
                    "content": m.get("content"),
                    "timestamp": str(m.get("time", datetime.now().isoformat())),
                    "edited": m.get("edited", False),
                })

    if historique_chat:
        await ws.send_json({
            "type": "historique",
            "messages": historique_chat[-50:],
            "timestamp": datetime.now().isoformat(),
        })

    if dernier_etat:
        await ws.send_json({
            "type": "etat_cluster", "etat": dernier_etat,
            "lstm": dernier_lstm, "timestamp": datetime.now().isoformat(),
        })

    try:
        while True:
            data     = await ws.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "question":
                question = data.get("content", "").strip()
                if not question:
                    continue
                msg_user = _ajouter_message("user", question)
                sauvegarder_message("user", question, msg_user["id"])
                await ws.send_json({
                    "type": "message_user", "id": msg_user["id"],
                    "content": question, "timestamp": msg_user["timestamp"],
                })
                await ws.send_json({"type": "thinking"})
                reponse, msg_id_rep = await repondre_question(question)
                sauvegarder_message("assistant", reponse, msg_id_rep)
                await ws.send_json({
                    "type": "reponse", "role": "assistant", "id": msg_id_rep,
                    "content": reponse, "timestamp": datetime.now().isoformat(),
                    "lstm": dernier_lstm, "llm": "groq", "model": GROQ_MODEL,
                })

            elif msg_type == "edit_message":
                msg_id        = data.get("msg_id", "")
                nouveau_texte = data.get("content", "").strip()
                if not msg_id or not nouveau_texte:
                    continue
                idx = _editer_message(msg_id, nouveau_texte)
                if idx == -1:
                    await ws.send_json({"type": "error", "content": "Message introuvable."})
                    continue
                await ws.send_json({
                    "type": "message_edite", "msg_id": msg_id,
                    "content": nouveau_texte, "timestamp": datetime.now().isoformat(),
                    "truncated_after": msg_id,
                })
                await ws.send_json({"type": "thinking"})
                reponse, msg_id_rep = await repondre_question(nouveau_texte, msg_id_edition=msg_id)
                await ws.send_json({
                    "type": "reponse", "role": "assistant", "id": msg_id_rep,
                    "content": reponse, "timestamp": datetime.now().isoformat(),
                    "lstm": dernier_lstm, "llm": "groq", "model": GROQ_MODEL,
                })

            elif msg_type == "clear_history":
                historique_chat.clear()
                await ws.send_json({"type": "history_cleared", "timestamp": datetime.now().isoformat()})

    except WebSocketDisconnect:
        if ws in clients: clients.remove(ws)
    except Exception as e:
        print(f"[WS] {e}")
        if ws in clients: clients.remove(ws)


async def _broadcaster():
    while True:
        try:
            msg  = await asyncio.wait_for(ws_queue.get(), timeout=2.0)
            dead = []
            for ws in clients:
                try:    await ws.send_json(msg)
                except: dead.append(ws)
            for ws in dead:
                if ws in clients: clients.remove(ws)
        except asyncio.TimeoutError:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# REST API
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/cluster")
def api_cluster():
    if PROXMOX_OK:
        try:    return get_etat_cluster()
        except Exception as e: return {"error": str(e)}
    return dernier_etat or {"error": "Not connected"}

@app.get("/api/historique_chat")
def api_historique():
    return {"messages": historique_chat, "count": len(historique_chat)}

@app.delete("/api/historique_chat")
def api_clear():
    historique_chat.clear()
    return {"ok": True}

@app.get("/api/allocation")
def api_allocation():
    if PROXMOX_OK:
        try:    return get_allocation_vs_usage()
        except Exception as e: return {"error": str(e)}
    return {"error": "Not connected"}

@app.get("/api/gpu")
def api_gpu():
    if PROXMOX_OK:
        try:    return get_gpu_info()
        except Exception as e: return {"error": str(e)}
    return {"error": "Not connected"}

@app.get("/api/vm/{node}/{vmid}")
def api_vm(node: str, vmid: int):
    if not PROXMOX_OK: return {"error": "Not connected"}
    try:    return {"status": get_vm_status(node, vmid), "config": get_vm_config(node, vmid)}
    except Exception as e: return {"error": str(e)}

@app.get("/api/rapports")
def api_rapports():
    RAPPORTS_DIR.mkdir(exist_ok=True)
    return [
        {"nom": f.name, "taille": f.stat().st_size,
         "date": datetime.fromtimestamp(f.stat().st_mtime).isoformat()}
        for f in sorted(RAPPORTS_DIR.glob("*.md"), reverse=True)[:30]
    ]

@app.get("/api/rapports/{nom}")
def api_rapport(nom: str):
    chemin = RAPPORTS_DIR / nom
    if not chemin.exists(): return {"error": "Not found"}
    return {"nom": nom, "contenu": chemin.read_text(encoding="utf-8")}

@app.get("/api/regles")
def api_regles():
    """Retourne les règles générées par le LLM."""
    regles = get_regles_ia()
    return {
        "regles": regles,
        "count": len(regles),
        "derniere_generation": _derniere_generation_regles,
        "prochaine_regeneration": max(0, INTERVALLE_REGENERATION_REGLES - (time.time() - _derniere_generation_regles)),
    }

@app.post("/api/regles/regenerer")
async def api_regenerer_regles():
    """Force la régénération immédiate des règles IA."""
    if not dernier_etat:
        return {"error": "Données cluster non disponibles"}
    regles = await generer_regles_ia(dernier_etat)
    return {"regles": regles, "count": len(regles)}

@app.get("/api/db/stats")
def api_db_stats():
    """Statistiques base de données."""
    return {"db_ok": DB_OK, "stats": get_stats_db()}

@app.get("/api/anomalies/historique")
def api_anomalies_historique(limit: int = 50):
    """Historique des anomalies depuis la DB."""
    return {"anomalies": get_anomalies(limit=limit)}

@app.post("/api/notifications/test")
async def api_test_notif():
    """Teste les canaux de notification."""
    result = await tester_notifications()
    return {"result": result, "config": get_config_notif()}

@app.get("/api/notifications/config")
def api_notif_config():
    """Configuration des notifications."""
    return get_config_notif()

@app.get("/api/status")
def api_status():
    return {
        "llm":           "groq",
        "groq_model":    GROQ_MODEL,
        "groq_ok":       GROQ_OK,
        "rate_slots":    _rate_limiter.slots(),
        "rate_max":      _rate_limiter.max_per_minute,
        "proxmox":       PROXMOX_OK,
        "prometheus":    PROMETHEUS_OK,
        "ai_engine":     ML_OK and _analyser is not None,
        "ai_score":      dernier_lstm.get("score", 0.0),
        "ai_score_if":   dernier_lstm.get("score_if", 0.0),
        "ai_score_lstm": dernier_lstm.get("score_lstm", 0.0),
        "lstm_ready":    dernier_lstm.get("lstm_ready", False),
        "chat_messages": len(historique_chat),
        "clients":       len(clients),
        "reports":       len(list(RAPPORTS_DIR.glob("*.md"))),
        "timestamp":     datetime.now().isoformat(),
    }

if DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(DIST_DIR / "assets")), name="assets")

    @app.get("/")
    def idx(): return FileResponse(str(DIST_DIR / "index.html"))

    @app.get("/{path:path}")
    def spa(path: str):
        f = DIST_DIR / path
        return FileResponse(str(f)) if f.exists() and f.is_file() else FileResponse(str(DIST_DIR / "index.html"))
else:
    @app.get("/")
    def no_build(): return {"message": "Run: cd frontend && npm run build"}


if __name__ == "__main__":
    uvicorn.run("chat_agent:app", host="127.0.0.1", port=8088, reload=False, log_level="info")