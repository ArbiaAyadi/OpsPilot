"""
notifications.py — OpsPilot Phase 4
Notifications temps réel : Email SMTP + Ntfy.sh

Config .env :
  # Email SMTP
  SMTP_HOST=smtp.gmail.com
  SMTP_PORT=587
  SMTP_USER=ton_email@gmail.com
  SMTP_PASSWORD=ton_app_password
  ALERT_EMAIL=admin@tondomaine.com

  # Ntfy.sh (push mobile gratuit — https://ntfy.sh)
  NTFY_URL=https://ntfy.sh
  NTFY_TOPIC=opspilot_alerts_ton_cluster
  NTFY_TOKEN=                           # optionnel si topic privé
  
Usage :
  pip install aiosmtplib --break-system-packages
"""

import asyncio
import os
import json
import time
import threading
from datetime import datetime
from typing import Optional

# ── Email SMTP ─────────────────────────────────────────────────────────────
try:
    import aiosmtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    SMTP_OK = True
except ImportError:
    SMTP_OK = False
    print("[Notif] aiosmtplib non installé — Email désactivé")
    print("[Notif] Installe : pip install aiosmtplib --break-system-packages")

# ── HTTP (pour Ntfy.sh) ────────────────────────────────────────────────────
try:
    import httpx
    HTTPX_OK = True
except ImportError:
    try:
        import urllib.request
        HTTPX_OK = False
    except:
        HTTPX_OK = False

# ══════════════════════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════════════════════
SMTP_CONFIG = {
    "host":     os.getenv("SMTP_HOST", "smtp.gmail.com"),
    "port":     int(os.getenv("SMTP_PORT", "587")),
    "user":     os.getenv("SMTP_USER", ""),
    "password": os.getenv("SMTP_PASSWORD", ""),
    "to":       os.getenv("ALERT_EMAIL", ""),
}

NTFY_CONFIG = {
    "url":   os.getenv("NTFY_URL", "https://ntfy.sh"),
    "topic": os.getenv("NTFY_TOPIC", "opspilot_alerts"),
    "token": os.getenv("NTFY_TOKEN", ""),
}

NOTIF_OK = bool(SMTP_CONFIG["user"] and SMTP_CONFIG["to"]) or bool(NTFY_CONFIG["topic"])

# Cooldown pour éviter le spam (en secondes)
_derniere_notif: dict = {}
COOLDOWN_NOTIF = int(os.getenv("COOLDOWN_NOTIF_S", "300"))  # 5 min entre 2 notifs similaires

# File de notifications (thread-safe)
_notif_queue: asyncio.Queue = None


# ══════════════════════════════════════════════════════════════════════════════
# Priorités et icônes
# ══════════════════════════════════════════════════════════════════════════════
PRIORITE_MAP = {
    "CRITIQUE":     ("urgent", "🔴", 5),
    "IMPORTANT":    ("high",   "🟠", 4),
    "SURVEILLANCE": ("default","🟡", 3),
    "NORMAL":       ("low",    "🟢", 1),
}


def _en_cooldown(cle: str) -> bool:
    """Vérifie si une notification similaire a déjà été envoyée récemment."""
    last = _derniere_notif.get(cle, 0)
    return time.time() - last < COOLDOWN_NOTIF


def _marquer_envoye(cle: str):
    """Marque une notification comme envoyée."""
    _derniere_notif[cle] = time.time()


# ══════════════════════════════════════════════════════════════════════════════
# Ntfy.sh — Push notifications mobile (GRATUIT)
# ══════════════════════════════════════════════════════════════════════════════
async def envoyer_ntfy(titre: str, message: str, severite: str = "IMPORTANT",
                        tags: list = None, url_action: str = None) -> bool:
    """
    Envoie une notification push via Ntfy.sh.
    
    Ntfy.sh est gratuit, open-source, et supporte :
    - Push mobile (iOS + Android via l app Ntfy)
    - Web notifications
    - Self-hosted si nécessaire
    
    Pour recevoir les alertes :
    1. Installe l app Ntfy sur ton téléphone
    2. Abonne-toi au topic : ntfy.sh/<NTFY_TOPIC>
    """
    if not NTFY_CONFIG["topic"]:
        return False

    ntfy_url = f"{NTFY_CONFIG['url']}/{NTFY_CONFIG['topic']}"
    priorite, emoji, _ = PRIORITE_MAP.get(severite, ("default", "🔵", 3))

    headers = {
        "Title":    f"{emoji} OpsPilot — {titre}",
        "Priority": priorite,
        "Tags":     ",".join(tags or ["infrastructure", "proxmox", severite.lower()]),
        "Content-Type": "text/plain; charset=utf-8",
    }
    if NTFY_CONFIG["token"]:
        headers["Authorization"] = f"Bearer {NTFY_CONFIG['token']}"
    if url_action:
        headers["Click"] = url_action

    corps = f"{message}\n\n⏰ {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"

    try:
        if HTTPX_OK:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(ntfy_url, content=corps.encode("utf-8"), headers=headers)
                if r.status_code == 200:
                    print(f"[Ntfy] ✓ Notification envoyée : {titre}")
                    return True
                else:
                    print(f"[Ntfy] ✗ Erreur HTTP {r.status_code}")
                    return False
        else:
            # Fallback urllib
            import urllib.request
            req = urllib.request.Request(ntfy_url, data=corps.encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                print(f"[Ntfy] ✓ Notification envoyée : {titre}")
                return True
    except Exception as e:
        print(f"[Ntfy] ✗ Erreur : {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Email SMTP
# ══════════════════════════════════════════════════════════════════════════════
async def envoyer_email(sujet: str, corps_html: str, corps_texte: str = None) -> bool:
    """
    Envoie un email d alertes via SMTP.
    Compatible Gmail (App Password), OVH, etc.
    """
    if not SMTP_OK:
        print("[Email] aiosmtplib non disponible")
        return False

    if not SMTP_CONFIG["user"] or not SMTP_CONFIG["to"]:
        print("[Email] SMTP_USER ou ALERT_EMAIL non configuré dans .env")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[OpsPilot] {sujet}"
        msg["From"]    = f"OpsPilot <{SMTP_CONFIG['user']}>"
        msg["To"]      = SMTP_CONFIG["to"]

        if corps_texte:
            msg.attach(MIMEText(corps_texte, "plain", "utf-8"))

        msg.attach(MIMEText(corps_html, "html", "utf-8"))

        await aiosmtplib.send(
            msg,
            hostname=SMTP_CONFIG["host"],
            port=SMTP_CONFIG["port"],
            username=SMTP_CONFIG["user"],
            password=SMTP_CONFIG["password"],
            start_tls=True,
            timeout=15,
        )
        print(f"[Email] ✓ Email envoyé : {sujet}")
        return True

    except Exception as e:
        print(f"[Email] ✗ Erreur : {e}")
        return False


def _generer_html_alerte(titre: str, message: str, severite: str,
                          anomalies: list, etat: dict) -> str:
    """Génère le HTML de l email d alerte."""
    couleur = {"CRITIQUE": "#ef4444", "IMPORTANT": "#f97316", "SURVEILLANCE": "#eab308"}.get(severite, "#3b82f6")
    emoji   = {"CRITIQUE": "🔴", "IMPORTANT": "🟠", "SURVEILLANCE": "🟡"}.get(severite, "🔵")

    anomalies_html = "".join([
        f"<li style='margin:4px 0;color:#374151'><strong>[{a.get('niveau','?')}]</strong> {a.get('message','')}</li>"
        for a in anomalies
    ])

    noeuds_html = ""
    for n in (etat or {}).get("noeuds", []):
        ram_pct = n.get("ram_pct", 0)
        cpu_pct = n.get("cpu_pct", 0)
        noeuds_html += f"""
        <tr>
            <td style='padding:8px;border-bottom:1px solid #e5e7eb'>{n.get("nom","?")}</td>
            <td style='padding:8px;border-bottom:1px solid #e5e7eb;color:{"#ef4444" if cpu_pct>80 else "#374151"}'>{cpu_pct}%</td>
            <td style='padding:8px;border-bottom:1px solid #e5e7eb;color:{"#ef4444" if ram_pct>85 else "#374151"}'>{ram_pct}%</td>
            <td style='padding:8px;border-bottom:1px solid #e5e7eb'>{n.get("statut","?")}</td>
        </tr>"""

    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f9fafb;padding:20px">
  <div style="max-width:600px;margin:0 auto;background:white;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1)">
    <div style="background:{couleur};padding:20px 24px">
      <h1 style="color:white;margin:0;font-size:18px">{emoji} OpsPilot — {titre}</h1>
      <p style="color:rgba(255,255,255,0.85);margin:6px 0 0;font-size:13px">{datetime.now().strftime("%d/%m/%Y à %H:%M:%S")}</p>
    </div>
    <div style="padding:24px">
      <div style="background:#f8fafc;border-left:4px solid {couleur};padding:12px 16px;border-radius:0 8px 8px 0;margin-bottom:20px">
        <p style="margin:0;color:#374151;font-size:14px">{message}</p>
      </div>
      {"<h3 style='font-size:14px;color:#111827;margin-bottom:8px'>Anomalies détectées</h3><ul style='margin:0;padding-left:20px'>" + anomalies_html + "</ul>" if anomalies_html else ""}
      {"<h3 style='font-size:14px;color:#111827;margin:20px 0 8px'>État du cluster</h3><table style='width:100%;border-collapse:collapse;font-size:13px'><thead><tr><th style='text-align:left;padding:8px;border-bottom:2px solid #e5e7eb;color:#6b7280'>Nœud</th><th style='text-align:left;padding:8px;border-bottom:2px solid #e5e7eb;color:#6b7280'>CPU</th><th style='text-align:left;padding:8px;border-bottom:2px solid #e5e7eb;color:#6b7280'>RAM</th><th style='text-align:left;padding:8px;border-bottom:2px solid #e5e7eb;color:#6b7280'>Statut</th></tr></thead><tbody>" + noeuds_html + "</tbody></table>" if noeuds_html else ""}
    </div>
    <div style="background:#f8fafc;padding:14px 24px;border-top:1px solid #e5e7eb">
      <p style="margin:0;font-size:12px;color:#9ca3af">OpsPilot Infrastructure AI · Surveillance automatique</p>
    </div>
  </div>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════════════════
# Interface principale — envoyer une alerte complète
# ══════════════════════════════════════════════════════════════════════════════
async def envoyer_alerte(titre: str, message: str, severite: str,
                          anomalies: list = None, etat: dict = None,
                          forcer: bool = False) -> dict:
    """
    Envoie une alerte via tous les canaux configurés.
    
    Args:
        titre:     Titre court de l alerte
        message:   Description de l incident
        severite:  CRITIQUE / IMPORTANT / SURVEILLANCE
        anomalies: Liste des anomalies détectées
        etat:      État actuel du cluster
        forcer:    Ignorer le cooldown anti-spam
    
    Returns:
        {"ntfy": bool, "email": bool}
    """
    cle_cooldown = f"{severite}:{titre[:30]}"
    if not forcer and _en_cooldown(cle_cooldown):
        print(f"[Notif] Cooldown actif pour : {cle_cooldown}")
        return {"ntfy": False, "email": False, "raison": "cooldown"}

    _marquer_envoye(cle_cooldown)
    anomalies = anomalies or []
    resultats = {"ntfy": False, "email": False}

    # Construire le corps texte pour Ntfy
    corps_ntfy = message
    if anomalies:
        corps_ntfy += "\n\nAnomalies:\n" + "\n".join([f"• [{a.get('niveau','?')}] {a.get('message','')}" for a in anomalies[:3]])

    # Envoyer Ntfy (toujours — push mobile immédiat)
    if NTFY_CONFIG["topic"]:
        resultats["ntfy"] = await envoyer_ntfy(
            titre=titre,
            message=corps_ntfy,
            severite=severite,
            tags=["proxmox", severite.lower(), "infrastructure"],
            url_action=f"http://localhost:8088"
        )

    # Envoyer Email (seulement pour CRITIQUE et IMPORTANT)
    if severite in ("CRITIQUE", "IMPORTANT") and SMTP_CONFIG["user"] and SMTP_CONFIG["to"]:
        html = _generer_html_alerte(titre, message, severite, anomalies, etat)
        texte = f"OpsPilot Alert\n{titre}\n{message}\nSévérité: {severite}"
        resultats["email"] = await envoyer_email(
            sujet=f"[{severite}] {titre}",
            corps_html=html,
            corps_texte=texte
        )

    return resultats


# ══════════════════════════════════════════════════════════════════════════════
# Test des notifications
# ══════════════════════════════════════════════════════════════════════════════
async def tester_notifications() -> dict:
    """Envoie une notification de test sur tous les canaux."""
    return await envoyer_alerte(
        titre="Test OpsPilot",
        message="Ceci est un test de notification. Le système de monitoring est opérationnel.",
        severite="SURVEILLANCE",
        forcer=True
    )


def get_config_notif() -> dict:
    """Retourne la configuration des notifications (sans mots de passe)."""
    return {
        "ntfy": {
            "configure": bool(NTFY_CONFIG["topic"]),
            "url": f"{NTFY_CONFIG['url']}/{NTFY_CONFIG['topic']}" if NTFY_CONFIG["topic"] else None,
            "topic": NTFY_CONFIG["topic"],
        },
        "email": {
            "configure": bool(SMTP_CONFIG["user"] and SMTP_CONFIG["to"]),
            "smtp_host": SMTP_CONFIG["host"],
            "smtp_port": SMTP_CONFIG["port"],
            "from": SMTP_CONFIG["user"],
            "to": SMTP_CONFIG["to"],
        },
        "cooldown_s": COOLDOWN_NOTIF,
    }