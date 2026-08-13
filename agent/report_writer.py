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
    ← NOUVEAU : construit Root Cause Analysis + Recommended Actions
    DIRECTEMENT depuis le JSON structuré du LLM (voir incident_prompt.py) --
    plus de reparsing fragile d'un texte markdown. Remplace
    _nettoyer_analyse() comme chemin PRINCIPAL ; celle-ci reste comme repli
    (voir plus bas) pour le seul cas où structured est absent ou n'a pas pu
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
    sauvegarder_rapport() ou que son parsing JSON a échoué
    (structured["_parse_failed"] = True), pour ne jamais perdre
    d'information même dans ce cas dégradé.
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


def sauvegarder_rapport(anomalies: list, analyse: str, etat: dict, score: float, structured: dict = None) -> str:
    """
    ← MODIFIÉ : nouveau paramètre optionnel "structured" (le JSON du LLM,
    voir incident_prompt.py + surveillance.py). Quand fourni et valide,
    c'est la SEULE source pour Root Cause Analysis / Recommended Actions --
    "analyse" (texte markdown) reste utilisé uniquement pour le résumé de
    repli et le cas où structured est absent/invalide.

    ← CORRECTION : avant ce changement, la section "Recommended Actions"
    de chaque nouveau rapport était VIDE -- _nettoyer_analyse() cherchait
    encore ---RECOMMENDATION---, un marqueur que le nouveau format JSON du
    LLM ne produit plus depuis le passage au JSON structuré. Confirmé en
    retraçant _rendre_markdown() (surveillance.py) : elle ne pose plus ce
    marqueur nulle part.

    Rôle de ce rapport, par rapport à la page Recommendations (React) :
    Recommendations vit UNIQUEMENT en mémoire du navigateur (useState([]),
    jamais rechargé au démarrage) -- actualiser la page la vide entièrement.
    Ce rapport .md, sauvegardé sur disque et listable via /api/rapports,
    est la seule trace PERSISTANTE d'un incident passé, consultable après
    un redémarrage de l'agent ou des jours plus tard. Une ligne de bas de
    page le rappelle explicitement dans le rapport lui-même (voir plus bas).
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

    # ── Choix du chemin : structured (nouveau, principal) ou texte (repli) ──
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

    # ── Tableau noeuds : nœuds ONLINE avec métriques, OFFLINE avec mention claire ──
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
                f"| {'—':12s} "
                f"| {'—':12s} "
                f"| {'—':10s} "
                f"| OFFLINE |\n"
            )

    # ── Tableau VMs ──
    vms_rows = ""
    for v in etat.get("vms", []):
        cpu = v.get("cpu_pct", 0)
        ram = v.get("ram_pct", 0)
        vms_rows += (
            f"| {str(v.get('nom','?'))[:20]:20s} "
            f"| {str(v.get('vmid','?')):6s} "
            f"| {str(v.get('noeud','?')):6s} "
            f"| {cpu:.1f}% "
            f"| {ram:.1f}% "
            f"| {str(v.get('statut','?')).upper():7s} |\n"
        )

    # ── Anomalies par sévérité ──
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
    # ← AJOUT : titre court et spécifique au problème (ex: "Enable KSM on
    # pve2 to free RAM"), déjà généré par le LLM pour chaque incident mais
    # jamais écrit nulle part dans le rapport jusqu'ici -- PageIncidents.jsx
    # n'avait donc que "summary" (la phrase complète) à afficher comme
    # titre ET comme détail, d'où la répétition. Généré automatiquement
    # par le LLM selon le problème précis (RAM, CPU, disque, quorum...),
    # jamais codé en dur ici.
    fix_title = (structured or {}).get("fix_title") if structured and not structured.get("_parse_failed") else None
    titre_ligne = f"\n> **Recommended Fix:** {fix_title}  " if fix_title else ""

    contenu = f"""\
# Incident Report — {ts.strftime('%Y-%m-%d %H:%M:%S')}

> **Severity:** {sev_icon} {sev_label}  
> **AI Score:** {score:.4f} — {score_interp}  
> **Generated:** {ts.strftime('%d/%m/%Y at %H:%M:%S UTC')}  
> **Platform:** OpsPilot v6 — Groq {GROQ_MODEL}{titre_ligne}{doc_ligne}

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

## Thresholds Reference (Minimum Professional Floor)

| Resource | Warning | Critical | Enterprise Action Required                                |
|----------|---------|----------|-----------------------------------------------------------|
| CPU      | > 65%   | > 80%    | Live-migrate VMs to less loaded node                      |
| RAM      | > 75%   | > 85%    | Enable KSM + ballooning, migrate VMs, or add physical RAM |
| Disk     | > 80%   | > 90%    | Clean backups/logs or expand storage pool                 |
| Swap     | > 50%   | > 80%    | RAM saturated — add physical RAM to the server            |
| I/O Wait | > 15%   | > 30%    | Add faster storage or redistribute VM I/O                 |

---

*Report generated automatically by OpsPilot AI Infrastructure Monitor*  
*Do not reply to this report — open the OpsPilot dashboard for real-time status*
"""

    nom = f"report_{ts.strftime('%Y%m%d_%H%M%S')}_{niveau_raw}.md"
    (RAPPORTS_DIR / nom).write_text(contenu, encoding="utf-8")

    # ← AJOUT : fichier .json compagnon avec le structured COMPLET (causes,
    # steps, action_id, action_params) -- jamais persisté nulle part avant
    # ce correctif, seulement rendu en texte dans le .md. Permet de
    # récupérer l'analyse riche (boutons "Accepter & Exécuter" inclus)
    # pour un incident passé, à la demande (voir lire_rapport_structured
    # plus bas), sans jamais reparser le texte du rapport -- le JSON
    # d'origine du LLM est simplement conservé tel quel.
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
    """
    Lit le JSON complet compagnon d'un rapport, si disponible -- None si le
    rapport a été généré avant ce correctif (pas de .json compagnon) ou si
    le parsing avait échoué au moment de la génération (rien de riche à
    offrir dans ce cas de toute façon). Utilisé pour la récupération à la
    demande de l'analyse complète (avec boutons d'action fonctionnels)
    d'un incident passé, sans jamais tout charger d'un coup au démarrage.
    """
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
# ← AJOUT : repli rétroactif -- les rapports générés AVANT l'ajout de la
# ligne "Recommended Fix" n'ont pas ce champ, mais ont TOUJOURS déjà la
# première action recommandée dans leur section "## Recommended Actions"
# (ex: "**[IMMEDIATE]** Enable KSM deduplication on pve2 to free RAM") --
# ce texte fait un excellent titre court, distinct du résumé, SANS
# attendre que de nouveaux incidents remplacent progressivement les
# anciens. Corrige donc rétroactivement quasiment tout l'historique
# existant, pas seulement les incidents créés après ce correctif.
_RE_PREMIERE_ACTION = re.compile(r'\*\*\[\w+\]\*\*\s*([^\n]+)')


def lister_rapports(limit: int = 100, offset: int = 0) -> list:
    """
    ← MODIFIÉ (pagination) : accepte maintenant offset en plus de limit --
    après des mois d'utilisation avec des milliers de rapports accumulés,
    tout charger d'un coup à chaque ouverture de page devient lent et
    inutile (la grande majorité ne sera jamais consultée). Le chargement
    initial reste rapide (100 par défaut), avec la possibilité de charger
    des lots suivants à la demande (bouton "Load more" côté frontend) via
    offset croissant, plutôt que tout récupérer en un bloc.

    Extrait toujours "resume" (## Executive Summary) et "titre"
    (Recommended Fix, ou à défaut la première action recommandée) --
    lecture légère, quelques regex par fichier, pas une reconstruction de
    l'analyse complète.
    """
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
            "date":   __import__("datetime").datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            "resume": resume,
            "titre":  titre,
        })
    return resultats


def compter_rapports() -> int:
    """Nombre total de rapports sur disque -- comptage léger (juste lister
    les noms de fichiers, pas les lire), utilisé par le frontend pour
    savoir s'il reste des rapports plus anciens à charger ("Load more")."""
    RAPPORTS_DIR.mkdir(exist_ok=True)
    return sum(1 for _ in RAPPORTS_DIR.glob("*.md"))


def lire_rapport(nom: str) -> dict:
    chemin = RAPPORTS_DIR / nom
    if not chemin.exists():
        return {"error": "Not found"}
    return {"nom": nom, "contenu": chemin.read_text(encoding="utf-8")}


# ══════════════════════════════════════════════════════════════════════════════
# Export PDF — testé et vérifié directement contre fpdf2 2.8.8 réellement
# installé (signatures des méthodes, gestion des glyphes) avant livraison,
# pas de syntaxe devinée de mémoire.
# ══════════════════════════════════════════════════════════════════════════════
# Utilise DejaVu Sans (licence libre, redistribuable -- Bitstream Vera
# License) plutôt qu'une liste de remplacements de caractères : le contenu
# vient en partie d'un LLM (résumé, causes), dont les caractères Unicode
# exacts (tirets cadratins, etc.) ne sont jamais garantis à l'avance -- une
# police Unicode réelle est robuste à ça, une liste de remplacements fixe
# ne l'est pas et recasserait au premier caractère imprévu.
#
# Fichiers requis (fournis à côté de ce fichier) : placer DejaVuSans.ttf,
# DejaVuSans-Bold.ttf, DejaVuSans-Oblique.ttf dans un dossier fonts/ à la
# racine du projet (sibling de agent/), ou définir FONTS_DIR dans .env.
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

# DejaVu Sans (regular) a ces glyphes, mais pas ses variantes Bold/Oblique
# (verifie : fpdf2 emet un warning "missing glyph" pour ces 3 precisement,
# sur ces 2 variantes precisement -- tout le reste, y compris warning/fleche
# et les tirets cadratins, passe sans probleme avec DejaVu, teste directement).
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
    """Rend un tableau via l'API table() native de fpdf2 -- largeurs de
    colonnes proportionnelles au contenu reel le plus long par colonne
    (evite qu'une colonne comme "Enterprise Action Required" se fasse
    tronquer par des largeurs egales)."""
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
    """
    Convertit le markdown d'un rapport (produit par sauvegarder_rapport
    ci-dessus) en PDF. Ne gere QUE les patterns que CE generateur produit
    (# titre, > citation, ## section, ### sous-section, tableaux |...|,
    blocs ```bash, listes -, liens [texte](url)) -- pas du markdown
    generaliste, mais suffisant et fiable pour ce cas d'usage precis.

    Leve FileNotFoundError si les polices DejaVu ne sont pas trouvees dans
    FONT_DIR -- l'appelant (routes.py) doit retourner une erreur HTTP claire
    dans ce cas plutot que laisser un traceback brut remonter.
    """
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