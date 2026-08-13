import asyncio
import os
import time
from datetime import datetime

try:
    import aiosmtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    SMTP_OK = True
except ImportError:
    SMTP_OK = False
    print("[Notif] aiosmtplib non installe -- Email desactive")

try:
    import httpx
    HTTPX_OK = True
except ImportError:
    HTTPX_OK = False

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
NOTIF_OK     = bool(SMTP_CONFIG["user"] and SMTP_CONFIG["to"]) or bool(NTFY_CONFIG["topic"])
COOLDOWN_NOTIF = int(os.getenv("COOLDOWN_NOTIF_S", "300"))
_derniere_notif: dict = {}

# URL du dashboard -- utilisee UNIQUEMENT pour le lien "Click" des
# notifications ntfy (tap-to-open depuis un telephone). Retiree de l'email
# (bouton + version texte) : l'utilisateur travaille depuis un seul PC, un
# lien vers un dashboard externe dans un email ne mene nulle part d'utile.
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:8088")

PRIORITE_MAP = {
    "CRITIQUE":     ("urgent", "🔴", 5),
    "IMPORTANT":    ("high",   "🟠", 4),
    "SURVEILLANCE": ("default","🟡", 3),
    "NORMAL":       ("low",    "🟢", 1),
}


def _en_cooldown(cle: str) -> bool:
    return time.time() - _derniere_notif.get(cle, 0) < COOLDOWN_NOTIF


def _marquer_envoye(cle: str):
    _derniere_notif[cle] = time.time()


async def envoyer_ntfy(titre: str, message: str, severite: str = "IMPORTANT",
                        tags: list = None, url_action: str = None) -> bool:
    if not NTFY_CONFIG["topic"]:
        return False
    ntfy_url = f"{NTFY_CONFIG['url']}/{NTFY_CONFIG['topic']}"
    priorite, _, _ = PRIORITE_MAP.get(severite, ("default", "🔵", 3))
    titre_ascii = titre.encode("ascii", errors="ignore").decode("ascii").strip()
    headers = {
        "Title":    f"OpsPilot -- {titre_ascii}",
        "Priority": priorite,
        "Tags":     ",".join(tags or ["infrastructure", "proxmox", severite.lower()]),
        "Content-Type": "text/plain; charset=utf-8",
    }
    if NTFY_CONFIG["token"]:
        headers["Authorization"] = f"Bearer {NTFY_CONFIG['token']}"
    if url_action:
        headers["Click"] = url_action
    corps = f"[{severite}] {message}\n\n{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
    try:
        if HTTPX_OK:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(ntfy_url, content=corps.encode("utf-8"), headers=headers)
                if r.status_code == 200:
                    print(f"[Ntfy] OK : {titre}")
                    return True
                print(f"[Ntfy] Erreur HTTP {r.status_code}")
                return False
        else:
            import urllib.request
            req = urllib.request.Request(ntfy_url, data=corps.encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=10):
                print(f"[Ntfy] OK : {titre}")
                return True
    except Exception as e:
        print(f"[Ntfy] Erreur : {e}")
        return False


async def envoyer_email(sujet: str, corps_html: str, corps_texte: str = None) -> bool:
    if not SMTP_OK:
        return False
    if not SMTP_CONFIG["user"] or not SMTP_CONFIG["to"]:
        print("[Email] SMTP_USER ou ALERT_EMAIL non configure dans .env")
        return False
    try:
        msg            = MIMEMultipart("alternative")
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
        print(f"[Email] OK : {sujet}")
        return True
    except Exception as e:
        print(f"[Email] Erreur : {e}")
        return False


def _extraire_resume(message: str) -> str:
    """Extrait juste la ligne Summary du texte LLM -- pas toute l'analyse."""
    import re
    m = re.search(r'\*{0,2}Summary:\*{0,2}\s*(.+)', message)
    if m:
        return m.group(1).strip()
    # Pas de marqueur trouve (ex: mode "quota reserve") -- prendre la 1ere
    # ligne non vide, tronquee, plutot que tout le texte brut.
    for ligne in message.split("\n"):
        ligne = ligne.strip()
        if ligne and not ligne.startswith("**") and not ligne.startswith("-"):
            return ligne[:220]
    return message[:220]


def _generer_html_alerte(titre: str, message: str, severite: str,
                          anomalies: list, etat: dict) -> str:
    """
    Notification COURTE et autonome : quoi, gravite, quand -- rien d'autre.
    Aucun lien vers un dashboard externe : l'utilisateur travaille depuis un
    seul PC, un bouton "voir l'analyse complete" dans un email ne mene nulle
    part d'utile en pratique. L'email EST la notification, pas un renvoi
    vers autre chose.
    """
    import re as _re
    sev_match = _re.search(r'\*{0,2}Severity:\*{0,2}\s*(CRITICAL|HIGH|MONITORING|IMPORTANT|CRITIQUE|SURVEILLANCE)', message, _re.IGNORECASE)
    if sev_match:
        sev_llm = sev_match.group(1).upper()
        sev_llm = {"HIGH": "IMPORTANT", "MONITORING": "SURVEILLANCE", "CRITIQUE": "CRITIQUE"}.get(sev_llm, sev_llm)
        severite_effective = sev_llm
    else:
        severite_effective = severite

    couleur = {
        "CRITIQUE": "#ef4444", "CRITICAL": "#ef4444",
        "IMPORTANT": "#f97316", "HIGH": "#f97316",
        "SURVEILLANCE": "#eab308", "MONITORING": "#eab308",
    }.get(str(severite_effective).upper(), "#3b82f6")

    label_sev = {
        "CRITIQUE": "CRITICAL", "CRITICAL": "CRITICAL",
        "IMPORTANT": "HIGH",    "HIGH": "HIGH",
        "SURVEILLANCE": "MONITORING", "MONITORING": "MONITORING",
    }.get(str(severite_effective).upper(), severite_effective or "INFO")

    resume = _extraire_resume(message)

    # Liste compacte des anomalies -- messages seulement, pas de table detaillee
    anomalies_html = "".join(
        f'<li style="margin:5px 0;color:#374151;font-size:13px;line-height:1.5">{a.get("message","")}</li>'
        for a in (anomalies or [])[:6]
    )

    ts = datetime.now().strftime("%d/%m/%Y at %H:%M:%S UTC")

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:24px 0">
<tr><td align="center">
<table width="520" cellpadding="0" cellspacing="0" style="max-width:520px;background:white;border-radius:12px;overflow:hidden;box-shadow:0 4px 16px rgba(0,0,0,0.12)">

  <tr><td style="background:{couleur};padding:22px 26px">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td>
        <div style="font-size:11px;color:rgba(255,255,255,0.7);font-weight:700;letter-spacing:0.1em;margin-bottom:6px">OPSPILOT ALERT</div>
        <div style="font-size:18px;font-weight:800;color:white;line-height:1.3">{titre}</div>
      </td>
      <td align="right" valign="top">
        <span style="display:inline-block;background:rgba(255,255,255,0.2);border:1px solid rgba(255,255,255,0.4);color:white;font-weight:800;font-size:12px;padding:4px 12px;border-radius:20px">{label_sev}</span>
      </td>
    </tr></table>
  </td></tr>

  <tr><td style="padding:22px 26px 0">
    <div style="font-size:14px;color:#111827;line-height:1.6">{resume}</div>
  </td></tr>

  {f'''<tr><td style="padding:18px 26px 22px">
    <div style="font-size:11px;font-weight:700;color:#6b7280;letter-spacing:0.1em;margin-bottom:8px">DETECTED ({len(anomalies)})</div>
    <ul style="margin:0;padding-left:18px">{anomalies_html}</ul>
  </td></tr>''' if anomalies_html else ''}

  <tr><td style="padding:20px 26px 20px">
    <div style="border-top:1px solid #e5e7eb;padding-top:14px">
      <div style="font-size:11px;color:#9ca3af">OpsPilot Enterprise · {ts}</div>
    </div>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


async def envoyer_alerte(titre: str, message: str, severite: str,
                          anomalies: list = None, etat: dict = None,
                          forcer: bool = False) -> dict:
    anomalies = anomalies or []
    # Cle stable : cible+type de la 1ere anomalie (ex: "pve2:ram"), pas un
    # extrait du titre -- celui-ci contient un pourcentage qui change à
    # chaque cycle, ce qui cassait le cooldown.
    if anomalies:
        premiere = anomalies[0]
        cle_cooldown = f"{severite}:{premiere.get('cible','?')}:{premiere.get('type','?')}"
    else:
        cle_cooldown = f"{severite}:{titre[:30]}"
    if not forcer and _en_cooldown(cle_cooldown):
        print(f"[Notif] Cooldown actif : {cle_cooldown}")
        return {"ntfy": False, "email": False, "raison": "cooldown"}
    _marquer_envoye(cle_cooldown)
    resultats = {"ntfy": False, "email": False}

    corps_ntfy = message
    if anomalies:
        corps_ntfy += "\n\nAnomalies:\n" + "\n".join(
            [f"- [{a.get('niveau','?')}] {a.get('message','')}" for a in anomalies[:3]]
        )

    if NTFY_CONFIG["topic"]:
        resultats["ntfy"] = await envoyer_ntfy(
            titre=titre, message=corps_ntfy, severite=severite,
            tags=["proxmox", severite.lower(), "infrastructure"],
            url_action=DASHBOARD_URL
        )

    if severite in ("CRITIQUE", "IMPORTANT") and SMTP_CONFIG["user"] and SMTP_CONFIG["to"]:
        html = _generer_html_alerte(titre, message, severite, anomalies, etat)
        resultats["email"] = await envoyer_email(
            sujet=f"[{severite}] {titre}",
            corps_html=html,
            corps_texte=f"OpsPilot Alert\n{titre}\n{_extraire_resume(message)}\nSeverite: {severite}"
        )

    return resultats


async def tester_notifications() -> dict:
    return await envoyer_alerte(
        titre="Test OpsPilot",
        message="Ceci est un test de notification. Le systeme de monitoring est operationnel.",
        severite="SURVEILLANCE",
        forcer=True
    )


def get_config_notif() -> dict:
    return {
        "ntfy": {
            "configure": bool(NTFY_CONFIG["topic"]),
            "url":   f"{NTFY_CONFIG['url']}/{NTFY_CONFIG['topic']}" if NTFY_CONFIG["topic"] else None,
            "topic": NTFY_CONFIG["topic"],
        },
        "email": {
            "configure": bool(SMTP_CONFIG["user"] and SMTP_CONFIG["to"]),
            "smtp_host": SMTP_CONFIG["host"],
            "smtp_port": SMTP_CONFIG["port"],
            "from":      SMTP_CONFIG["user"],
            "to":        SMTP_CONFIG["to"],
        },
        "cooldown_s": COOLDOWN_NOTIF,
    }