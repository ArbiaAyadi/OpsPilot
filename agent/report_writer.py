import re
from datetime import datetime
from agent.config import RAPPORTS_DIR
from agent.groq_client import GROQ_MODEL

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


def _nettoyer_analyse(texte: str) -> tuple[str, str, str]:
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
        # Supprimer tous les artefacts bash/Copy sous toutes leurs formes
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


def sauvegarder_rapport(anomalies: list, analyse: str, etat: dict, score: float) -> str:
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

    incident_txt, reco_txt, summary_brut = _nettoyer_analyse(analyse)

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
    # ONLINE en premier, OFFLINE en dernier
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
            # Nœud OFFLINE — ne pas afficher de métriques nulles
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

    # ── Score IA — ne plus afficher le seuil brut adaptatif (trop bas et confus) ──
    if score >= 0.8:
        score_interp = "CRITICAL — Highly abnormal cluster behavior"
    elif score >= 0.5:
        score_interp = "HIGH — Suspicious pattern detected"
    elif score >= 0.35:
        score_interp = "MODERATE — Early anomaly signal"
    else:
        score_interp = "NORMAL — Within expected range"

    contenu = f"""\
# Incident Report — {ts.strftime('%Y-%m-%d %H:%M:%S')}

> **Severity:** {sev_icon} {sev_label}  
> **AI Score:** {score:.4f} — {score_interp}  
> **Generated:** {ts.strftime('%d/%m/%Y at %H:%M:%S UTC')}  
> **Platform:** OpsPilot v6 — Groq {GROQ_MODEL}

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

## Thresholds Reference (Proxmox VE Official)

| Resource | Warning | Critical | Action Required              |
|----------|---------|----------|------------------------------|
| CPU      | > 65%   | > 80%    | Migrate VMs or scale         |
| RAM      | > 75%   | > 85%    | Risk of OOM kill             |
| Disk     | > 80%   | > 90%    | LVM-thin writes fail         |
| Swap     | > 50%   | > 80%    | RAM saturated                |
| I/O Wait | > 15%   | > 30%    | Storage bottleneck           |

---

*Report generated automatically by OpsPilot AI Infrastructure Monitor*  
*Do not reply to this report — open the OpsPilot dashboard for real-time status*
"""

    nom = f"report_{ts.strftime('%Y%m%d_%H%M%S')}_{niveau_raw}.md"
    (RAPPORTS_DIR / nom).write_text(contenu, encoding="utf-8")

    try:
        from database import sauvegarder_rapport_db
        sauvegarder_rapport_db(nom=nom, contenu=contenu, niveau=niveau_raw, score=score)
    except Exception:
        pass

    print(f"[Report] {nom}")
    return nom


def lister_rapports(limit: int = 30) -> list:
    RAPPORTS_DIR.mkdir(exist_ok=True)
    return [
        {
            "nom":    f.name,
            "taille": f.stat().st_size,
            "date":   __import__("datetime").datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
        }
        for f in sorted(RAPPORTS_DIR.glob("*.md"), reverse=True)[:limit]
    ]


def lire_rapport(nom: str) -> dict:
    chemin = RAPPORTS_DIR / nom
    if not chemin.exists():
        return {"error": "Not found"}
    return {"nom": nom, "contenu": chemin.read_text(encoding="utf-8")}