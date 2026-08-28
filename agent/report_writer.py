import re
import os
import json
from datetime import datetime
from agent.config import RAPPORTS_DIR
from agent.groq_client import GROQ_MODEL

try:
    from fpdf import FPDF
    FPDF_OK = True
except ImportError:
    FPDF_OK = False
    print("[Report] fpdf2 non installe -- export PDF desactive")
    print("[Report] Installe : pip install fpdf2 --break-system-packages")

_SEV_LABEL = {
    "critique":    "CRITICAL",
    "important":   "HIGH",
    "surveillance":"MONITORING",
    "critical":    "CRITICAL",
    "high":        "HIGH",
    "monitoring":  "MONITORING",
}
_SEV_ICON = {
    "CRITICAL":   "🔴",
    "HIGH":       "🟠",
    "MONITORING": "🟡",
}

_PHASE_LABEL = {"immediate": "IMMEDIATE", "short_term": "SHORT TERM", "long_term": "LONG TERM"}


def _rendre_depuis_structure(structured: dict) -> tuple:
    """
    Construit Root Cause Analysis + Recommended Actions DIRECTEMENT depuis
    le JSON structuré du LLM (voir incident_prompt.py) -- plus de reparsing
    fragile d'un texte markdown. Chemin PRINCIPAL ; _nettoyer_analyse()
    reste le repli pour le seul cas où structured est absent ou n'a pas pu
    être parsé (ancien format, ou échec JSON -- _parse_failed).
    """
    causes = structured.get("causes", []) or []
    incident_txt = "\n".join(f"- {c}" for c in causes) if causes else "_Analysis not available for this incident._"

    steps = structured.get("steps", []) or []
    morceaux = []
    if structured.get("warning"):
        morceaux.append(f"⚠️ **{structured['warning']}**\n")
    for step in steps:
        phase = _PHASE_LABEL.get(step.get("phase", ""), (step.get("phase") or "").upper())
        morceaux.append(f"**[{phase}]** {step.get('action','')}")
        if step.get("command"):
            morceaux.append(f"```bash\n{step['command']}\n```")
    reco_txt = "\n".join(morceaux) if morceaux else "_No specific actions recommended._"

    summary = structured.get("summary", "") or ""
    return incident_txt, reco_txt, summary


def _nettoyer_analyse(texte: str) -> tuple:
    """
    ← REPLI legacy : reparse l'ancien format texte libre
    (---INCIDENT---/---RECOMMENDATION---). N'est plus le chemin principal
    depuis que le LLM répond en JSON structuré (voir incident_prompt.py) --
    utilisé uniquement quand "structured" n'est pas fourni à
    sauvegarder_rapport() ou que son parsing JSON a échoué.
    """
    if not texte:
        return "", "", ""
    if "---RECOMMENDATION---" in texte:
        parts    = texte.split("---RECOMMENDATION---")
        incident = parts[0].replace("---INCIDENT---", "").strip()
        reco     = parts[1].strip() if len(parts) > 1 else ""
    elif "---INCIDENT---" in texte:
        blocs    = texte.split("---INCIDENT---")
        incident = "\n\n".join(b.strip() for b in blocs if b.strip())
        reco     = ""
    else:
        incident = texte.strip()
        reco     = ""

    summary_match = re.search(r'\*{0,2}Summary:\*{0,2}\s*(.+)', incident)
    summary_brut  = summary_match.group(1).strip() if summary_match else ""

    def nettoyer(t):
        import re as _re
        t = t.replace("bashcopier", "")
        t = t.replace("bash\ncopier", "")
        t = t.replace("bash\nCopy\n", "")
        t = t.replace("bash\nCopy", "")
        t = t.replace("bashCopy\n", "")
        t = t.replace("bashCopy", "")
        t = _re.sub(r'```bash\s*\nCopy\s*\n', '```bash\n', t)
        t = _re.sub(r'^bash\s*\nCopy\s*\n', '', t, flags=_re.MULTILINE)
        t = _re.sub(r'^bash\s*Copy\s*\n', '', t, flags=_re.MULTILINE)
        t = _re.sub(r'^bash\s*\n(?=[a-z/])', '', t, flags=_re.MULTILINE)
        t = _re.sub(r'```bash\s*\n?', '```bash\n', t)
        lignes = t.split("\n")
        result = []
        for l in lignes:
            s = l.strip()
            if s in ("---", "---INCIDENT---", "---RECOMMENDATION---"):
                continue
            if _re.match(r'^\*{0,2}Severity:\*{0,2}\s*(CRITICAL|HIGH|IMPORTANT|MONITORING|CRITIQUE|SURVEILLANCE)\s*$', s, _re.I):
                continue
            if s.startswith("Severity:") and "Summary:" in s:
                continue
            if _re.match(r'^\*{0,2}Summary:\*{0,2}\s*.+', s, _re.I):
                continue
            result.append(l)
        return "\n".join(result).strip()

    return nettoyer(incident), nettoyer(reco), summary_brut


# ══════════════════════════════════════════════════════════════════════════════
# Tableau des seuils — construit depuis les règles RÉELLEMENT appliquées
# ══════════════════════════════════════════════════════════════════════════════
# ← AMÉLIORATION 1 : ce tableau affichait des valeurs fixes ("CPU > 65% /
# > 80%") alors que depuis le branchement de anomaly_detector.py sur
# get_regles_ia(), ce sont les seuils générés par l'IA qui déclenchent
# réellement les alertes. Un rapport d'incident qui documente des seuils
# différents de ceux appliqués est trompeur -- surtout devant un client
# qui voudrait vérifier pourquoi telle alerte s'est déclenchée à telle
# valeur. Le tableau est maintenant construit depuis les règles actives,
# avec repli sur les planchers professionnels si aucune règle IA n'est
# encore disponible (premier démarrage, génération échouée).
_ACTIONS_PAR_METRIQUE = {
    "server.cpu_pct":               "Live-migrate VMs to a less loaded node",
    "server.ram_pct":               "Enable KSM + ballooning, migrate VMs, or add physical RAM",
    "server.disk_pct":              "Clean backups/logs or expand the storage pool",
    "server.swap_pct":              "RAM saturated — add physical RAM to the host",
    "server.cpu_iowait_pct":        "Add faster storage or redistribute VM I/O",
    "server.disk_read_latency_ms":  "Storage saturated — check for competing I/O or move to faster disks",
    "server.disk_write_latency_ms": "Storage saturated — check for competing I/O or move to faster disks",
    "server.load_avg_1m":           "Too many runnable processes — investigate top consumers",
    "server.cpu_steal_pct":         "Hypervisor contention — reduce load on the physical host",
    "server.fd_used_pct":           "Raise the file-descriptor limit or find the leaking service",
    "server.procs_blocked":         "Processes stuck on I/O — investigate the storage layer",
    "server.cpu_temp_max_c":        "Check cooling and airflow — throttling risk",
    "server.disk_temp_max_c":       "Check drive cooling — accelerated wear risk",
    "vm.cpu_pct":                   "Add vCPUs or migrate the VM to a less loaded node",
    "vm.ram_pct":                   "Enable ballooning or migrate the VM",
    "vm.disk_pct":                  "Extend the virtual disk or clean up files inside the guest",
}

_TABLEAU_SEUILS_REPLI = """\
| Resource | Warning | Critical | Enterprise Action Required                                |
|----------|---------|----------|-----------------------------------------------------------|
| CPU      | > 65%   | > 80%    | Live-migrate VMs to less loaded node                      |
| RAM      | > 75%   | > 85%    | Enable KSM + ballooning, migrate VMs, or add physical RAM |
| Disk     | > 80%   | > 90%    | Clean backups/logs or expand storage pool                 |
| Swap     | > 50%   | > 80%    | RAM saturated — add physical RAM to the server            |
| I/O Wait | > 15%   | > 30%    | Add faster storage or redistribute VM I/O                 |
"""


def _construire_tableau_seuils() -> tuple:
    """Construit le tableau des seuils depuis les règles réellement
    actives. Retourne (titre_section, tableau_markdown)."""
    try:
        from agent.rules_engine import get_regles_ia
        regles = get_regles_ia() or []
    except Exception:
        regles = []

    par_metrique = {}
    for r in regles:
        m = r.get("metric", "")
        s = r.get("seuil")
        if not m or not isinstance(s, (int, float)) or isinstance(s, bool):
            continue
        sev = str(r.get("severite", "")).upper()
        entree = par_metrique.setdefault(m, {"warn": None, "crit": None})
        if "CRIT" in sev:
            entree["crit"] = s
        elif "IMPORT" in sev or "HIGH" in sev:
            entree["warn"] = s

    lignes = [l for m, l in sorted(par_metrique.items())
              if (l["warn"] is not None or l["crit"] is not None) and m in _ACTIONS_PAR_METRIQUE]
    if not lignes:
        return ("Thresholds Reference (Minimum Professional Floor)", _TABLEAU_SEUILS_REPLI)

    def fmt(v, unite):
        if v is None:
            return "—"
        return f"> {v:g}{unite}"

    rangs = []
    for metrique, seuils in sorted(par_metrique.items()):
        if metrique not in _ACTIONS_PAR_METRIQUE:
            continue
        if seuils["warn"] is None and seuils["crit"] is None:
            continue
        unite = "%" if metrique.endswith("_pct") else "ms" if metrique.endswith("_ms") \
            else "°C" if metrique.endswith("_c") else ""
        nom_court = metrique.replace("server.", "Node ").replace("vm.", "VM ").replace("_pct", "").replace("_", " ")
        rangs.append(
            f"| {nom_court[:26]:26s} | {fmt(seuils['warn'], unite):9s} "
            f"| {fmt(seuils['crit'], unite):9s} | {_ACTIONS_PAR_METRIQUE[metrique][:56]:56s} |"
        )

    tableau = (
        "| Metric                     | Warning   | Critical  | Enterprise Action Required                               |\n"
        "|----------------------------|-----------|-----------|----------------------------------------------------------|\n"
        + "\n".join(rangs) + "\n"
    )
    return ("Active Thresholds (AI-generated, applied by the detection engine)", tableau)


def sauvegarder_rapport(anomalies: list, analyse: str, etat: dict, score: float,
                         structured: dict = None, recurrence: dict = None) -> str:
    """
    "structured" : le JSON du LLM (voir incident_prompt.py + surveillance.py).
    Quand fourni et valide, c'est la SEULE source pour Root Cause Analysis /
    Recommended Actions.

    ← AMÉLIORATIONS 3 et 4 : nouveau paramètre optionnel "recurrence",
    fourni par surveillance.py -- {"premiere_detection": <timestamp>,
    "occurrence": <int>}. Un rapport d'incident professionnel distingue
    QUAND LE PROBLÈME A COMMENCÉ de QUAND LE RAPPORT A ÉTÉ GÉNÉRÉ, et
    indique s'il s'agit d'une première occurrence ou d'une condition qui
    se répète -- deux informations qui changent complètement la lecture
    d'un incident (un pic ponctuel n'appelle pas la même réponse qu'une
    saturation chronique). Absent = comportement inchangé, ces lignes ne
    s'affichent simplement pas.

    Rôle de ce rapport, par rapport à la page Recommendations : ce .md,
    sauvegardé sur disque et listable via /api/rapports, est la trace
    PERSISTANTE d'un incident passé, consultable après un redémarrage.
    """
    ts      = datetime.now()
    niveaux = [str(a.get("niveau", "")).upper() for a in anomalies]

    niveau_ia   = "critique" if score >= 0.8 else "important" if score >= 0.5 else "surveillance"
    niveau_anom = (
        "critique"    if any("CRIT" in n for n in niveaux) else
        "important"   if any("IMP"  in n for n in niveaux) else
        "surveillance"
    )
    ordre      = ["surveillance", "important", "critique"]
    niveau_raw = niveau_ia if ordre.index(niveau_ia) >= ordre.index(niveau_anom) else niveau_anom
    sev_label  = _SEV_LABEL.get(niveau_raw, "MONITORING")
    sev_icon   = _SEV_ICON.get(sev_label, "🟡")

    if structured and not structured.get("_parse_failed"):
        incident_txt, reco_txt, summary_brut = _rendre_depuis_structure(structured)
        doc_url = structured.get("doc_url")
    else:
        incident_txt, reco_txt, summary_brut = _nettoyer_analyse(analyse)
        doc_url = None

    if summary_brut:
        summary = summary_brut
    elif anomalies:
        msgs    = [a.get("message","") for a in anomalies if "ai score" not in a.get("message","").lower()]
        summary = msgs[0] if msgs else anomalies[0].get("message","")
    else:
        summary = "Anomaly detected on the cluster."

    def sev_indicator(val, warn, crit):
        if val >= crit: return f"{val:.1f}% ⚠"
        if val >= warn: return f"{val:.1f}% ↑"
        return f"{val:.1f}%"

    noeuds_rows = ""
    noeuds_tries = sorted(
        etat.get("noeuds", []),
        key=lambda n: 0 if str(n.get("statut","")).lower() in ("online","en ligne","up") else 1
    )
    for n in noeuds_tries:
        stat   = n.get("statut", "?")
        online = str(stat).lower() in ("online", "en ligne", "up")
        nom    = n.get("nom", "?")
        if online:
            cpu  = n.get("cpu_pct",  0)
            ram  = n.get("ram_pct",  0)
            disk = n.get("disk_pct", 0)
            swap = n.get("swap_pct", 0)
            noeuds_rows += (
                f"| {nom:8s} | {sev_indicator(cpu,65,80):12s} "
                f"| {sev_indicator(ram,75,85):12s} "
                f"| {sev_indicator(disk,80,90):12s} "
                f"| {sev_indicator(swap,50,80):10s} "
                f"| ONLINE  |\n"
            )
        else:
            noeuds_rows += (
                f"| {nom:8s} | {'Unreachable':12s} "
                f"| {'—':12s} | {'—':12s} | {'—':10s} | OFFLINE |\n"
            )

    vms_rows = ""
    for v in etat.get("vms", []):
        cpu = v.get("cpu_pct", 0)
        ram = v.get("ram_pct", 0)
        vms_rows += (
            f"| {str(v.get('nom','?'))[:20]:20s} "
            f"| {str(v.get('vmid','?')):6s} "
            f"| {str(v.get('noeud','?')):6s} "
            f"| {cpu:.1f}% | {ram:.1f}% "
            f"| {str(v.get('statut','?')).upper():7s} |\n"
        )

    anomalies_critical = [a for a in anomalies if "CRIT" in str(a.get("niveau","")).upper()]
    anomalies_high     = [a for a in anomalies if "IMP"  in str(a.get("niveau","")).upper()]
    anomalies_other    = [a for a in anomalies if a not in anomalies_critical and a not in anomalies_high]

    def format_anomalies(lst):
        return "\n".join(f"- {a.get('message','')}" for a in lst) if lst else "_None_"

    if score >= 0.8:
        score_interp = "CRITICAL — Highly abnormal cluster behavior"
    elif score >= 0.5:
        score_interp = "HIGH — Suspicious pattern detected"
    elif score >= 0.35:
        score_interp = "MODERATE — Early anomaly signal"
    else:
        score_interp = "NORMAL — Within expected range"

    doc_ligne = f"\n> **Reference:** [{doc_url}]({doc_url})  " if doc_url else ""
    fix_title = (structured or {}).get("fix_title") if structured and not structured.get("_parse_failed") else None
    titre_ligne = f"\n> **Recommended Fix:** {fix_title}  " if fix_title else ""

    # ← AMÉLIORATIONS 3 et 4 : première détection + occurrence. Deux lignes
    # d'en-tête qui n'apparaissent que si l'information est disponible.
    recurrence_lignes = ""
    if recurrence:
        premiere = recurrence.get("premiere_detection")
        if premiere:
            dt_premiere = datetime.fromtimestamp(premiere)
            duree_min   = int((ts - dt_premiere).total_seconds() / 60)
            duree_txt   = (f"{duree_min // 60}h{duree_min % 60:02d}" if duree_min >= 60
                           else f"{duree_min} min")
            recurrence_lignes += (
                f"\n> **First detected:** {dt_premiere.strftime('%d/%m/%Y at %H:%M:%S')} "
                f"— ongoing for {duree_txt}  "
            )
        occurrence = recurrence.get("occurrence")
        if occurrence and occurrence > 1:
            recurrence_lignes += (
                f"\n> **Occurrence:** #{occurrence} — recurring condition, "
                f"not an isolated spike  "
            )

    # ← AMÉLIORATION 2 : le pied de page était dupliqué (la mention
    # "Do not reply" apparaissait deux fois, une fois dans le markdown et
    # une fois ajoutée par le rendu). Une seule occurrence désormais.
    titre_seuils, tableau_seuils = _construire_tableau_seuils()

    # ← AJOUT : fournisseur ayant réellement produit cette analyse. La
    # ligne "Platform" annonçait systématiquement Groq, même quand la
    # bascule vers un secours (Mistral, Ollama...) avait eu lieu --
    # information fausse dans un document qui sert de trace d'incident.
    fournisseur = (structured or {}).get("_fournisseur") or "groq"
    if fournisseur == "groq":
        ligne_plateforme = f"OpsPilot v6 — Groq {GROQ_MODEL}"
    else:
        ligne_plateforme = f"OpsPilot v6 — {fournisseur} (fallback provider)"

    contenu = f"""\
# Incident Report — {ts.strftime('%Y-%m-%d %H:%M:%S')}

> **Severity:** {sev_icon} {sev_label}  
> **AI Score:** {score:.2f} — {score_interp}  
> **Generated:** {ts.strftime('%d/%m/%Y at %H:%M:%S UTC')}  
> **Platform:** {ligne_plateforme}{titre_ligne}{recurrence_lignes}{doc_ligne}

---

## Executive Summary

{summary}

---

## Detected Anomalies

### 🔴 Critical
{format_anomalies(anomalies_critical)}

### 🟠 High
{format_anomalies(anomalies_high)}

### 🟡 Monitoring
{format_anomalies(anomalies_other)}

---

## Root Cause Analysis

{incident_txt if incident_txt else "_Analysis not available for this incident._"}

---

## Recommended Actions

{reco_txt if reco_txt else "_No specific actions recommended._"}

---

## Cluster State at Alert Time

### Hypervisors

| Node     | CPU          | RAM          | Disk         | Swap       | Status  |
|----------|--------------|--------------|--------------|------------|---------|
{noeuds_rows or "| No data  | —            | —            | —            | —          | —       |\n"}

> ⚠ = Critical threshold exceeded | ↑ = Warning threshold exceeded

### Virtual Machines

| Name                 | VMID   | Node   | CPU   | RAM   | Status  |
|----------------------|--------|--------|-------|-------|---------|
{vms_rows or "| No VMs               | —      | —      | —     | —     | —       |\n"}

---

## {titre_seuils}

{tableau_seuils}
---

*Report generated automatically by OpsPilot AI Infrastructure Monitor — do not reply. Open the OpsPilot dashboard for real-time status.*
"""

    nom = f"report_{ts.strftime('%Y%m%d_%H%M%S')}_{niveau_raw}.md"
    (RAPPORTS_DIR / nom).write_text(contenu, encoding="utf-8")

    if structured and not structured.get("_parse_failed"):
        try:
            nom_json = nom.replace(".md", ".json")
            (RAPPORTS_DIR / nom_json).write_text(json.dumps(structured, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[Report] Erreur sauvegarde JSON compagnon (non bloquant): {e}")

    try:
        from database import sauvegarder_rapport_db
        sauvegarder_rapport_db(nom=nom, contenu=contenu, niveau=niveau_raw, score=score)
    except Exception:
        pass

    print(f"[Report] {nom}")
    return nom


def lire_rapport_structured(nom_md: str) -> dict | None:
    """Lit le JSON complet compagnon d'un rapport, si disponible."""
    nom_json = nom_md.replace(".md", ".json")
    chemin = RAPPORTS_DIR / nom_json
    if not chemin.exists():
        return None
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except Exception:
        return None


_RE_RESUME_RAPPORT = re.compile(r'## Executive Summary\s*\n+([^\n]+)')
_RE_TITRE_RAPPORT  = re.compile(r'\*\*Recommended Fix:\*\*\s*([^\n]+?)\s*(?:\n|$)')
_RE_PREMIERE_ACTION = re.compile(r'\*\*\[\w+\]\*\*\s*([^\n]+)')


def lister_rapports(limit: int = 100, offset: int = 0) -> list:
    """Liste paginée des rapports, avec extraction légère du résumé et du titre."""
    RAPPORTS_DIR.mkdir(exist_ok=True)
    tous_fichiers = sorted(RAPPORTS_DIR.glob("*.md"), reverse=True)
    resultats = []
    for f in tous_fichiers[offset:offset + limit]:
        resume, titre = None, None
        try:
            contenu = f.read_text(encoding="utf-8")
            m_resume = _RE_RESUME_RAPPORT.search(contenu)
            if m_resume:
                resume = m_resume.group(1).strip()
            m_titre = _RE_TITRE_RAPPORT.search(contenu)
            if m_titre:
                titre = m_titre.group(1).strip()
            else:
                m_action = _RE_PREMIERE_ACTION.search(contenu)
                if m_action:
                    titre = m_action.group(1).strip()
        except Exception:
            pass
        resultats.append({
            "nom":    f.name,
            "taille": f.stat().st_size,
            "date":   datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            "resume": resume,
            "titre":  titre,
        })
    return resultats


def compter_rapports() -> int:
    RAPPORTS_DIR.mkdir(exist_ok=True)
    return sum(1 for _ in RAPPORTS_DIR.glob("*.md"))


def lire_rapport(nom: str) -> dict:
    chemin = RAPPORTS_DIR / nom
    if not chemin.exists():
        return {"error": "Not found"}
    return {"nom": nom, "contenu": chemin.read_text(encoding="utf-8")}


# ══════════════════════════════════════════════════════════════════════════════
# Export PDF — DejaVu Sans (licence libre) pour robustesse Unicode
# ══════════════════════════════════════════════════════════════════════════════
FONT_DIR = os.getenv(
    "FONTS_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")
)

COULEUR_PDF_TEXTE   = (35, 35, 40)
COULEUR_PDF_MUTED   = (120, 120, 128)
COULEUR_PDF_ACCENT  = (37, 99, 235)
COULEUR_PDF_BORDURE = (225, 225, 230)
COULEUR_PDF_CODE_BG = (244, 244, 248)
LARGEUR_PDF_UTILE   = 178  # mm, A4 moins marges 16+16

_PDF_GLYPHES_MANQUANTS = {"🔴": "(CRIT)", "🟠": "(HIGH)", "🟡": "(MON)"}

_RE_PDF_LIEN     = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
_RE_PDF_GRAS     = re.compile(r'\*\*(.+?)\*\*')
_RE_PDF_ITALIQUE = re.compile(r'(?<!\w)_([^_]+)_(?!\w)')


def _pdf_nettoyer_glyphes(texte: str) -> str:
    for glyphe, remplacement in _PDF_GLYPHES_MANQUANTS.items():
        texte = texte.replace(glyphe, remplacement)
    return texte


def _pdf_nettoyer_inline(texte: str) -> str:
    texte = _RE_PDF_LIEN.sub(r'\1 (\2)', texte)
    texte = _RE_PDF_GRAS.sub(r'\1', texte)
    texte = _RE_PDF_ITALIQUE.sub(r'\1', texte)
    return texte


class _RapportPDF(FPDF if FPDF_OK else object):
    def footer(self):
        self.set_y(-12)
        self.set_font('DejaVu', 'I', 7)
        self.set_text_color(*COULEUR_PDF_MUTED)
        self.cell(0, 8, text=f'OpsPilot Infrastructure AI  -  Page {self.page_no()}', align='C')


def _pdf_rendre_tableau(pdf, lignes_tableau: list):
    """Rend un tableau via l'API table() native de fpdf2, largeurs
    proportionnelles au contenu réel le plus long par colonne."""
    if not lignes_tableau:
        return
    n_cols  = len(lignes_tableau[0])
    max_len = [max((len(str(row[c])) if c < len(row) else 1) for row in lignes_tableau) for c in range(n_cols)]
    total   = sum(max_len) or 1
    col_widths = tuple(max(22, (l / total) * LARGEUR_PDF_UTILE) for l in max_len)

    pdf.set_font('DejaVu', '', 8.5)
    with pdf.table(lignes_tableau, col_widths=col_widths, text_align='LEFT',
                    line_height=5, padding=1.5) as table:
        pass
    pdf.ln(2)


def generer_pdf(contenu: str) -> bytes:
    """Convertit le markdown d'un rapport en PDF. Gère uniquement les
    patterns que ce générateur produit."""
    if not FPDF_OK:
        raise RuntimeError("fpdf2 non installe -- pip install fpdf2 --break-system-packages")

    pdf = _RapportPDF(format='A4', unit='mm')
    for style, fichier in (('', 'DejaVuSans.ttf'), ('B', 'DejaVuSans-Bold.ttf'), ('I', 'DejaVuSans-Oblique.ttf')):
        chemin_police = os.path.join(FONT_DIR, fichier)
        if not os.path.exists(chemin_police):
            raise FileNotFoundError(
                f"Police PDF manquante: {chemin_police} -- place les 3 fichiers "
                f"DejaVuSans*.ttf dans {FONT_DIR} (ou definis FONTS_DIR dans .env)"
            )
        pdf.add_font('DejaVu', style, chemin_police)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(16, 14, 16)
    pdf.add_page()

    lignes = _pdf_nettoyer_glyphes(contenu).split('\n')
    i = 0
    while i < len(lignes):
        s = lignes[i].strip()

        if s.startswith('```'):
            i += 1
            code_lignes = []
            while i < len(lignes) and not lignes[i].strip().startswith('```'):
                code_lignes.append(lignes[i])
                i += 1
            pdf.set_font('DejaVu', '', 9)
            pdf.set_text_color(20, 20, 20)
            pdf.set_fill_color(*COULEUR_PDF_CODE_BG)
            for cl in code_lignes:
                pdf.cell(LARGEUR_PDF_UTILE, 5.5, text=cl, fill=True, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
            i += 1
            continue

        if s.startswith('|'):
            lignes_tableau = []
            while i < len(lignes) and lignes[i].strip().startswith('|'):
                ligne_t = lignes[i].strip()
                if '---' not in ligne_t:
                    lignes_tableau.append([c.strip() for c in ligne_t.strip('|').split('|')])
                i += 1
            _pdf_rendre_tableau(pdf, lignes_tableau)
            continue

        if s.startswith('# '):
            pdf.set_font('DejaVu', 'B', 16)
            pdf.set_text_color(*COULEUR_PDF_TEXTE)
            pdf.multi_cell(0, 8, text=_pdf_nettoyer_inline(s[2:]), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
        elif s.startswith('## '):
            pdf.ln(3)
            pdf.set_font('DejaVu', 'B', 12)
            pdf.set_text_color(*COULEUR_PDF_ACCENT)
            pdf.multi_cell(0, 7, text=_pdf_nettoyer_inline(s[3:]), new_x="LMARGIN", new_y="NEXT")
            pdf.set_draw_color(*COULEUR_PDF_BORDURE)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + LARGEUR_PDF_UTILE, pdf.get_y())
            pdf.ln(2)
        elif s.startswith('### '):
            pdf.set_font('DejaVu', 'B', 10)
            pdf.set_text_color(*COULEUR_PDF_TEXTE)
            pdf.multi_cell(0, 6, text=_pdf_nettoyer_inline(s[4:]), new_x="LMARGIN", new_y="NEXT")
        elif s.startswith('>'):
            pdf.set_font('DejaVu', 'I', 9)
            pdf.set_text_color(*COULEUR_PDF_MUTED)
            texte = _pdf_nettoyer_inline(s.lstrip('>').strip())
            if texte:
                pdf.multi_cell(0, 5.5, text=texte, new_x="LMARGIN", new_y="NEXT")
        elif s.startswith('- '):
            pdf.set_font('DejaVu', '', 9.5)
            pdf.set_text_color(*COULEUR_PDF_TEXTE)
            pdf.multi_cell(0, 5.5, text=f'   -  {_pdf_nettoyer_inline(s[2:])}', new_x="LMARGIN", new_y="NEXT")
        elif s == '---':
            pdf.ln(1)
            pdf.set_draw_color(*COULEUR_PDF_BORDURE)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + LARGEUR_PDF_UTILE, pdf.get_y())
            pdf.ln(3)
        elif s.startswith('*') and s.endswith('*') and len(s) > 2:
            pdf.set_font('DejaVu', 'I', 8)
            pdf.set_text_color(*COULEUR_PDF_MUTED)
            pdf.multi_cell(0, 5, text=_pdf_nettoyer_inline(s.strip('*')), new_x="LMARGIN", new_y="NEXT")
        elif s == '':
            pdf.ln(2)
        else:
            pdf.set_font('DejaVu', '', 9.5)
            pdf.set_text_color(*COULEUR_PDF_TEXTE)
            pdf.multi_cell(0, 5.5, text=_pdf_nettoyer_inline(s), new_x="LMARGIN", new_y="NEXT")

        i += 1

    return bytes(pdf.output())