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


def _parse_markdown_to_html(text: str) -> str:
    """
    Convertit le Markdown basique en HTML pour l email.
    Gere tous les formats de blocs de code :
      - ```bash\\ncommande\\n```
      - ```bash commande```   (sur une ligne)
      - ``` commande ```      (sans langage)
    """
    import re

    # ── Blocs de code (priorité haute, avant tout autre remplacement) ──────
    # Pattern robuste : optionnellement "bash" ou autre langage, puis contenu
    def remplacer_bloc_code(m):
        code = m.group(1).strip()
        return (
            f'<pre style="background:#0f172a;color:#7dd3fc;padding:12px 16px;'
            f'border-radius:6px;font-size:12px;font-family:\'Courier New\',monospace;'
            f'overflow-x:auto;margin:8px 0;white-space:pre-wrap">{code}</pre>'
        )

    text = re.sub(
        r'```(?:bash|sh|shell|python|text)?\s*\n?([\s\S]*?)```',
        remplacer_bloc_code,
        text,
        flags=re.MULTILINE
    )

    # ── Gras ──────────────────────────────────────────────────────────────
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)

    # ── Code inline (apres les blocs pour eviter les conflits) ───────────
    text = re.sub(
        r'`([^`]+)`',
        r'<code style="background:#f1f5f9;padding:2px 6px;border-radius:3px;'
        r'font-family:monospace;font-size:12px">\1</code>',
        text
    )

    # ── Listes ────────────────────────────────────────────────────────────
    lines    = []
    in_list  = False
    for line in text.split('\n'):
        stripped = line.strip()
        if stripped.startswith('- '):
            if not in_list:
                lines.append('<ul style="margin:8px 0;padding-left:20px">')
                in_list = True
            lines.append(f'<li style="margin:3px 0;color:#374151">{stripped[2:]}</li>')
        else:
            if in_list:
                lines.append('</ul>')
                in_list = False
            if stripped:
                lines.append(f'<p style="margin:4px 0;color:#374151">{line}</p>')
    if in_list:
        lines.append('</ul>')

    return '\n'.join(lines)


def _parser_sections_llm(message: str) -> tuple:
    """
    Parse les sections ---INCIDENT--- et ---RECOMMENDATION--- du LLM.
    Supprime tous les marqueurs bruts avant le rendu HTML.
    """
    if '---RECOMMENDATION---' in message:
        parts    = message.split('---RECOMMENDATION---')
        incident = parts[0].replace('---INCIDENT---', '').strip()
        reco     = parts[1].strip() if len(parts) > 1 else ''
        return _parse_markdown_to_html(incident), _parse_markdown_to_html(reco)
    elif '---INCIDENT---' in message:
        return _parse_markdown_to_html(message.replace('---INCIDENT---', '').strip()), ''
    else:
        return _parse_markdown_to_html(message), ''


def _generer_html_alerte(titre: str, message: str, severite: str,
                          anomalies: list, etat: dict) -> str:

    # Extraire la sévérité réelle depuis le texte LLM (plus fiable que niv_max)
    # Le LLM écrit **Severity:** CRITICAL ou HIGH dans son analyse
    import re as _re
    sev_match = _re.search(r'\*\*Severity:\*\*\s*(CRITICAL|HIGH|MONITORING|IMPORTANT|CRITIQUE|SURVEILLANCE)', message, _re.IGNORECASE)
    if sev_match:
        sev_llm = sev_match.group(1).upper()
        # Normaliser vers nos valeurs internes
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

    incident_html, reco_html = _parser_sections_llm(message)

    anomalies_html = ""
    for a in (anomalies or []):
        niv       = a.get('niveau', a.get('level', '?'))
        niv_color = "#ef4444" if "CRIT" in str(niv).upper() else "#f97316" if "IMP" in str(niv).upper() else "#eab308"
        anomalies_html += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb">
            <span style="display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:700;background:{niv_color}18;color:{niv_color};border:1px solid {niv_color}40">{niv}</span>
          </td>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;color:#374151;font-size:13px">{a.get('message','')}</td>
        </tr>"""

    noeuds_html = ""
    def c(v, w, cr):
        return "#ef4444" if v >= cr else "#f97316" if v >= w else "#16a34a"
    # Nœuds ONLINE en premier, OFFLINE en dernier
    noeuds_tries = sorted(
        (etat or {}).get("noeuds", []),
        key=lambda n: 0 if str(n.get("statut", "")).lower() in ("online", "en ligne", "up") else 1
    )
    for n in noeuds_tries:
        stat = n.get("statut", "?")
        # Ne pas afficher les nœuds offline — leurs métriques sont toutes à 0
        # et polluent le tableau avec des lignes inutiles
        if str(stat).lower() not in ("online", "en ligne", "up"):
            noeuds_html += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;font-weight:600;color:#111827">{n.get('nom','?')}</td>
          <td colspan="4" style="padding:8px 12px;border-bottom:1px solid #e5e7eb;color:#9ca3af;font-style:italic;font-size:12px">Node unreachable — no metrics available</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb">
            <span style="color:#ef4444;font-weight:600;font-size:12px">OFFLINE</span>
          </td>
        </tr>"""
            continue
        cpu  = n.get("cpu_pct",  0)
        ram  = n.get("ram_pct",  0)
        disk = n.get("disk_pct", 0)
        swap = n.get("swap_pct", 0)
        noeuds_html += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;font-weight:600;color:#111827">{n.get('nom','?')}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;color:{c(cpu,65,80)};font-family:monospace">{cpu:.1f}%</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;color:{c(ram,75,85)};font-family:monospace">{ram:.1f}%</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;color:{c(disk,80,90)};font-family:monospace">{disk:.1f}%</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;color:{c(swap,20,50)};font-family:monospace">{swap:.1f}%</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb">
            <span style="color:#16a34a;font-weight:600;font-size:12px">ONLINE</span>
          </td>
        </tr>"""

    vms_down = [v for v in (etat or {}).get("vms", []) if v.get("statut") != "running"]
    vms_html = ""
    for v in vms_down:
        vms_html += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;font-weight:600">{v.get('nom','?')}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;color:#6b7280">VMID {v.get('vmid','?')}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;color:#6b7280">{v.get('noeud','?')}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;color:#ef4444;font-weight:600">STOPPED</td>
        </tr>"""

    ts = datetime.now().strftime("%d/%m/%Y at %H:%M:%S UTC")

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:24px 0">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;background:white;border-radius:12px;overflow:hidden;box-shadow:0 4px 16px rgba(0,0,0,0.12)">

  <tr><td style="background:{couleur};padding:24px 28px">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td>
        <div style="font-size:11px;color:rgba(255,255,255,0.7);font-weight:700;letter-spacing:0.1em;margin-bottom:6px">OPSPILOT INFRASTRUCTURE ALERT</div>
        <div style="font-size:20px;font-weight:800;color:white;margin-bottom:4px">{titre}</div>
        <div style="font-size:12px;color:rgba(255,255,255,0.8)">{ts}</div>
      </td>
      <td align="right" valign="top">
        <span style="display:inline-block;background:rgba(255,255,255,0.2);border:1px solid rgba(255,255,255,0.4);color:white;font-weight:800;font-size:12px;padding:4px 12px;border-radius:20px">{label_sev}</span>
      </td>
    </tr></table>
  </td></tr>

  <tr><td style="padding:24px 28px 0">
    <div style="font-size:11px;font-weight:700;color:#6b7280;letter-spacing:0.1em;margin-bottom:10px">INCIDENT ANALYSIS</div>
    <div style="background:#f8fafc;border-left:4px solid {couleur};padding:14px 16px;border-radius:0 8px 8px 0;font-size:13px;line-height:1.7">
      {incident_html or '<p style="color:#6b7280;margin:0">No analysis available.</p>'}
    </div>
  </td></tr>

  {'<tr><td style="padding:16px 28px 0"><div style="font-size:11px;font-weight:700;color:#6b7280;letter-spacing:0.1em;margin-bottom:10px">RECOMMENDED ACTION</div><div style="background:#f0fdf4;border-left:4px solid #16a34a;padding:14px 16px;border-radius:0 8px 8px 0;font-size:13px;line-height:1.7">' + reco_html + '</div></td></tr>' if reco_html else ''}

  {f"""<tr><td style="padding:20px 28px 0">
    <div style="font-size:11px;font-weight:700;color:#6b7280;letter-spacing:0.1em;margin-bottom:10px">DETECTED ANOMALIES ({len(anomalies)})</div>
    <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;border:1px solid #e5e7eb;border-radius:8px;font-size:13px">
      <thead><tr>
        <th style="text-align:left;padding:8px 12px;background:#f8fafc;color:#6b7280;font-size:11px;border-bottom:1px solid #e5e7eb;width:110px">SEVERITY</th>
        <th style="text-align:left;padding:8px 12px;background:#f8fafc;color:#6b7280;font-size:11px;border-bottom:1px solid #e5e7eb">MESSAGE</th>
      </tr></thead>
      <tbody>{anomalies_html}</tbody>
    </table>
  </td></tr>""" if anomalies else ''}

  {f"""<tr><td style="padding:20px 28px 0">
    <div style="font-size:11px;font-weight:700;color:#6b7280;letter-spacing:0.1em;margin-bottom:10px">CLUSTER STATE AT ALERT TIME</div>
    <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;border:1px solid #e5e7eb;border-radius:8px;font-size:13px">
      <thead><tr>
        <th style="text-align:left;padding:8px 12px;background:#f8fafc;color:#6b7280;font-size:11px;border-bottom:1px solid #e5e7eb">NODE</th>
        <th style="text-align:left;padding:8px 12px;background:#f8fafc;color:#6b7280;font-size:11px;border-bottom:1px solid #e5e7eb">CPU</th>
        <th style="text-align:left;padding:8px 12px;background:#f8fafc;color:#6b7280;font-size:11px;border-bottom:1px solid #e5e7eb">RAM</th>
        <th style="text-align:left;padding:8px 12px;background:#f8fafc;color:#6b7280;font-size:11px;border-bottom:1px solid #e5e7eb">DISK</th>
        <th style="text-align:left;padding:8px 12px;background:#f8fafc;color:#6b7280;font-size:11px;border-bottom:1px solid #e5e7eb">SWAP</th>
        <th style="text-align:left;padding:8px 12px;background:#f8fafc;color:#6b7280;font-size:11px;border-bottom:1px solid #e5e7eb">STATUS</th>
      </tr></thead>
      <tbody>{noeuds_html}</tbody>
    </table>
  </td></tr>""" if noeuds_html else ''}

  {f"""<tr><td style="padding:16px 28px 0">
    <div style="font-size:11px;font-weight:700;color:#6b7280;letter-spacing:0.1em;margin-bottom:10px">VMs NOT RUNNING</div>
    <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;border:1px solid #e5e7eb;border-radius:8px;font-size:13px">
      <thead><tr>
        <th style="text-align:left;padding:8px 12px;background:#f8fafc;color:#6b7280;font-size:11px;border-bottom:1px solid #e5e7eb">NAME</th>
        <th style="text-align:left;padding:8px 12px;background:#f8fafc;color:#6b7280;font-size:11px;border-bottom:1px solid #e5e7eb">VMID</th>
        <th style="text-align:left;padding:8px 12px;background:#f8fafc;color:#6b7280;font-size:11px;border-bottom:1px solid #e5e7eb">NODE</th>
        <th style="text-align:left;padding:8px 12px;background:#f8fafc;color:#6b7280;font-size:11px;border-bottom:1px solid #e5e7eb">STATUS</th>
      </tr></thead>
      <tbody>{vms_html}</tbody>
    </table>
  </td></tr>""" if vms_html else ''}

  <tr><td style="padding:20px 28px 24px">
    <div style="border-top:1px solid #e5e7eb;padding-top:16px">
      <div style="font-size:12px;color:#9ca3af">OpsPilot Enterprise — Infrastructure AI Platform</div>
      <div style="font-size:11px;color:#d1d5db;margin-top:2px">Automated anomaly detection · Groq LLM · Proxmox VE</div>
      <div style="font-size:11px;color:#d1d5db;margin-top:4px">{ts}</div>
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
    cle_cooldown = f"{severite}:{titre[:30]}"
    if not forcer and _en_cooldown(cle_cooldown):
        print(f"[Notif] Cooldown actif : {cle_cooldown}")
        return {"ntfy": False, "email": False, "raison": "cooldown"}
    _marquer_envoye(cle_cooldown)
    anomalies = anomalies or []
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
            url_action="http://localhost:8088"
        )

    if severite in ("CRITIQUE", "IMPORTANT") and SMTP_CONFIG["user"] and SMTP_CONFIG["to"]:
        html = _generer_html_alerte(titre, message, severite, anomalies, etat)
        resultats["email"] = await envoyer_email(
            sujet=f"[{severite}] {titre}",
            corps_html=html,
            corps_texte=f"OpsPilot Alert\n{titre}\n{message}\nSeverite: {severite}"
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