"""
test_ml_evaluation.py — Evaluation rigoureuse du moteur de detection
=====================================================================

DIFFERENCE AVEC test_ml_model.py
---------------------------------
test_ml_model.py valide 6 scenarios EXTREMES (RAM 86%, CPU 91%, latence
200ms). Tous sont detectes, d'ou 100% de precision -- mais ces cas sont
captes par la couche de SEUILS, qui est prioritaire par conception. Le
test mesure donc les seuils, pas les modeles.

Ce fichier evalue ce qui est reellement difficile :

  1. Des cas AMBIGUS -- RAM a 78%, latence a 40ms, charge inhabituelle
     mais non critique. C'est la que se joue la difference entre un bon
     detecteur et un seuil basique.

  2. La DECOMPOSITION par niveau -- pour chaque cas, le score des trois
     couches separement. On voit ainsi laquelle detecte quoi, et si les
     modeles apportent quelque chose au-dela des seuils.

  3. Des COURBES exploitables dans un rapport -- distribution des scores,
     matrice de confusion, contribution de chaque niveau.

L'objectif n'est PAS d'obtenir 100%. Un resultat autour de 85-90% sur
des cas varies vaut mieux qu'un 100% sur six cas choisis : il montre
qu'on a cherche les limites du modele.

Usage :
    python test_ml_evaluation.py              # evaluation + courbes
    python test_ml_evaluation.py --sans-courbes   # texte seulement
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

SANS_COURBES = "--sans-courbes" in sys.argv

print("=" * 72)
print("OPSPILOT — EVALUATION DU MOTEUR DE DETECTION")
print("=" * 72)

try:
    from ml_analyser import MLAnalyseur, score_seuils
except Exception as e:
    print(f"\n✗ Chargement impossible : {e}")
    sys.exit(1)

ml = MLAnalyseur()
print()


# ---------------------------------------------------------------------------
# Jeu de test
# ---------------------------------------------------------------------------
def base():
    """Etat de reference d'un cluster Proxmox sain, mesure sur ce projet."""
    return {
        "cpu_pct": 15.0, "ram_pct": 45.0, "disk_pct": 40.0,
        "swap_pct": 2.0, "cpu_iowait_pct": 1.0,
        "disk_read_iops": 50.0, "disk_write_iops": 30.0,
        "disk_read_latency_ms": 0.5, "disk_write_latency_ms": 1.0,
        "net_in_mbps": 10.0, "net_out_mbps": 5.0,
        "net_errors_in": 0.0, "net_errors_out": 0.0,
        "net_drop_in": 0.0, "net_drop_out": 0.0,
        "vms_running": 2.0, "load_avg_1m": 0.5,
        "zfs_arc_hit_rate": 95.0, "cpu_temp_max_c": 45.0, "fd_used_pct": 5.0,
    }


def cas(nom, categorie, anomalie, **modifs):
    return {"nom": nom, "categorie": categorie, "anomalie": anomalie,
            "metriques": {**base(), **modifs}}


# Vingt scenarios, repartis en quatre familles de difficulte croissante.
SCENARIOS = [
    # --- NORMAUX (le modele ne doit PAS alerter) ---------------------------
    cas("Cluster au repos", "normal", False),
    cas("Activite moderee", "normal", False,
        cpu_pct=35.0, ram_pct=58.0, load_avg_1m=1.2),
    cas("Pic de trafic reseau legitime", "normal", False,
        net_in_mbps=180.0, net_out_mbps=120.0, cpu_pct=40.0),
    cas("Sauvegarde nocturne (I/O eleve, sain)", "normal", False,
        disk_read_iops=1500.0, disk_write_iops=1200.0, cpu_iowait_pct=8.0),
    cas("Disque a 72% (surveille, pas critique)", "normal", False,
        disk_pct=72.0),

    # --- AMBIGUS NORMAUX -- le vrai test des faux positifs -----------------
    # Ces cas frolent les seuils sans les franchir. Un detecteur trop
    # sensible alerte ici, et l'ingenieur finit par ignorer ses alertes.
    cas("RAM a 78% -- sous le seuil critique", "ambigu-normal", False,
        ram_pct=78.0),
    cas("Latence 40ms -- elevee mais toleree", "ambigu-normal", False,
        disk_write_latency_ms=40.0, disk_read_latency_ms=35.0),
    cas("Swap a 45% apres redemarrage", "ambigu-normal", False,
        swap_pct=45.0, ram_pct=68.0),
    cas("CPU 62% soutenu -- charge normale", "ambigu-normal", False,
        cpu_pct=62.0, load_avg_1m=3.0),
    cas("Cache ZFS a 72% -- degrade sans gravite", "ambigu-normal", False,
        zfs_arc_hit_rate=72.0),

    # --- ANOMALIES EVIDENTES (celles de l'ancien test) ---------------------
    cas("RAM critique 88%", "evident", True,
        ram_pct=88.0, swap_pct=60.0),
    cas("CPU sature 93%", "evident", True,
        cpu_pct=93.0, load_avg_1m=12.0),
    cas("Tempete I/O -- latence 220ms", "evident", True,
        disk_write_latency_ms=220.0, disk_read_latency_ms=180.0, cpu_iowait_pct=48.0),
    cas("Toutes les VMs arretees", "evident", True,
        vms_running=0.0),
    cas("Erreurs reseau massives", "evident", True,
        net_errors_in=500.0, net_errors_out=300.0, net_drop_in=200.0),

    # --- AMBIGUS ANOMALIES -- le vrai test des faux negatifs ---------------
    # Aucune metrique ne franchit seule un seuil critique, mais leur
    # COMBINAISON est anormale. C'est precisement ce que l'Isolation
    # Forest doit capter et qu'un seuil ne peut pas voir.
    cas("Combinaison suspecte -- rien de critique isole", "ambigu-anomalie", True,
        cpu_pct=68.0, ram_pct=79.0, swap_pct=48.0,
        cpu_iowait_pct=14.0, load_avg_1m=7.0, disk_write_latency_ms=9.0),
    cas("Derive memoire lente + swap qui monte", "ambigu-anomalie", True,
        ram_pct=82.0, swap_pct=52.0, fd_used_pct=68.0),
    cas("Contention disque discrete", "ambigu-anomalie", True,
        disk_read_iops=6500.0, disk_write_iops=5800.0,
        disk_write_latency_ms=28.0, cpu_iowait_pct=22.0),
    cas("Fuite de descripteurs de fichiers", "ambigu-anomalie", True,
        fd_used_pct=88.0, load_avg_1m=5.0, ram_pct=74.0),
    cas("Surchauffe + charge combinees", "ambigu-anomalie", True,
        cpu_temp_max_c=82.0, cpu_pct=71.0, load_avg_1m=9.0),
]


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
SEUIL_DECISION = 0.5

print("[1] EVALUATION DES SCENARIOS")
print("-" * 72)
print(f"{'Scenario':<44}{'seuils':>8}{'IF':>7}{'LSTM':>7}{'final':>8}")
print("-" * 72)

resultats = []
categorie_courante = None

for s in SCENARIOS:
    if s["categorie"] != categorie_courante:
        categorie_courante = s["categorie"]
        libelle = {
            "normal":          "ETATS NORMAUX",
            "ambigu-normal":   "AMBIGUS NORMAUX (test des faux positifs)",
            "evident":         "ANOMALIES EVIDENTES",
            "ambigu-anomalie": "AMBIGUS ANOMALIES (test des faux negatifs)",
        }[categorie_courante]
        print(f"\n  -- {libelle} --")

    m = s["metriques"]

    # Score de chaque niveau, mesure separement.
    # score_seuils() est importe directement ; les deux autres passent par
    # les analyseurs, puis sont relus dans get_stats() -- c'est la seule
    # facon d'obtenir leur contribution individuelle sans modifier
    # ml_analyser.py.
    s_seuils = score_seuils(m)
    score_final, seuil_lstm = ml.analyser(m)
    stats = ml.get_stats()
    s_if   = stats.get("score_if", 0.0)
    s_lstm = stats.get("score_lstm", 0.0)

    detecte = score_final >= SEUIL_DECISION
    correct = (detecte == s["anomalie"])

    marque = "  " if correct else "✗ "
    print(f"{marque}{s['nom']:<42}{s_seuils:>8.3f}{s_if:>7.3f}{s_lstm:>7.3f}{score_final:>8.3f}")

    resultats.append({
        "nom": s["nom"], "categorie": s["categorie"],
        "anomalie_reelle": s["anomalie"], "detecte": detecte, "correct": correct,
        "score_seuils": round(s_seuils, 4),
        "score_if": round(s_if, 4),
        "score_lstm": round(s_lstm, 4),
        "score_final": round(score_final, 4),
    })


# ---------------------------------------------------------------------------
# Matrice de confusion
# ---------------------------------------------------------------------------
vp = sum(1 for r in resultats if r["anomalie_reelle"] and r["detecte"])
vn = sum(1 for r in resultats if not r["anomalie_reelle"] and not r["detecte"])
fp = sum(1 for r in resultats if not r["anomalie_reelle"] and r["detecte"])
fn = sum(1 for r in resultats if r["anomalie_reelle"] and not r["detecte"])

precision  = vp / max(vp + fp, 1) * 100
rappel     = vp / max(vp + fn, 1) * 100
exactitude = (vp + vn) / len(resultats) * 100
f1 = 2 * precision * rappel / max(precision + rappel, 1)

print("\n" + "=" * 72)
print("[2] MATRICE DE CONFUSION")
print("=" * 72)
print(f"""
                      PREDIT anomalie   PREDIT normal
  REEL anomalie            {vp:>3} (VP)        {fn:>3} (FN)
  REEL normal              {fp:>3} (FP)        {vn:>3} (VN)

  Precision  : {precision:5.1f}%   des alertes levees, combien etaient reelles
  Rappel     : {rappel:5.1f}%   des vraies anomalies, combien detectees
  Exactitude : {exactitude:5.1f}%   taux de decisions correctes
  Score F1   : {f1:5.1f}%   moyenne harmonique precision/rappel
""")

# Resultat par famille -- c'est ici que l'information est la plus utile.
print("[3] PERFORMANCE PAR FAMILLE DE DIFFICULTE")
print("-" * 72)
for cat, libelle in [
    ("normal",          "Normaux evidents"),
    ("ambigu-normal",   "Normaux ambigus"),
    ("evident",         "Anomalies evidentes"),
    ("ambigu-anomalie", "Anomalies ambigues"),
]:
    sous = [r for r in resultats if r["categorie"] == cat]
    ok = sum(1 for r in sous if r["correct"])
    taux = ok / len(sous) * 100
    barre = "#" * int(taux / 5)
    print(f"  {libelle:<24}{ok}/{len(sous)}  {taux:5.1f}%  {barre}")

# Contribution de chaque niveau : lequel declenche l'alerte ?
print("\n[4] CONTRIBUTION DE CHAQUE NIVEAU")
print("-" * 72)
anomalies = [r for r in resultats if r["anomalie_reelle"] and r["detecte"]]
par_seuils = sum(1 for r in anomalies if r["score_seuils"] >= SEUIL_DECISION)
par_if     = sum(1 for r in anomalies if r["score_seuils"] < SEUIL_DECISION
                 and 0.7 * r["score_if"] >= SEUIL_DECISION)
par_lstm   = sum(1 for r in anomalies if r["score_seuils"] < SEUIL_DECISION
                 and 0.7 * r["score_if"] < SEUIL_DECISION
                 and 0.6 * r["score_lstm"] >= SEUIL_DECISION)
print(f"  Detectees par les seuils seuls    : {par_seuils}/{len(anomalies)}")
print(f"  Detectees grace a Isolation Forest: {par_if}/{len(anomalies)}")
print(f"  Detectees grace au LSTM           : {par_lstm}/{len(anomalies)}")
print(f"""
  Lecture : si presque tout est detecte par les seuils, les modeles
  n'apportent encore rien de mesurable sur ce jeu de test -- ce qui est
  attendu tant que le LSTM n'a pas appris sur des donnees reelles
  (voir normalizer_stats.json : 3 features normalisees sur 20).
""")


# ---------------------------------------------------------------------------
# Courbes
# ---------------------------------------------------------------------------
if not SANS_COURBES:
    try:
        import matplotlib
        matplotlib.use("Agg")   # pas d'affichage interactif -- ecrit un fichier
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(2, 2, figsize=(13, 9))
        fig.suptitle("OpsPilot — Evaluation du moteur de detection", fontsize=14)

        # (a) Score final par scenario
        ax = axes[0, 0]
        couleurs = ["#2e7d32" if not r["anomalie_reelle"] else "#c62828" for r in resultats]
        ax.barh(range(len(resultats)), [r["score_final"] for r in resultats], color=couleurs)
        ax.axvline(SEUIL_DECISION, color="black", linestyle="--", linewidth=1,
                   label=f"seuil de decision ({SEUIL_DECISION})")
        ax.set_yticks(range(len(resultats)))
        ax.set_yticklabels([r["nom"][:32] for r in resultats], fontsize=6)
        ax.set_xlabel("score d'anomalie")
        ax.set_title("Score par scenario (vert = normal, rouge = anomalie)", fontsize=9)
        ax.legend(fontsize=7)
        ax.invert_yaxis()

        # (b) Distribution -- separation entre normaux et anomalies
        ax = axes[0, 1]
        sc_norm = [r["score_final"] for r in resultats if not r["anomalie_reelle"]]
        sc_anom = [r["score_final"] for r in resultats if r["anomalie_reelle"]]
        ax.hist([sc_norm, sc_anom], bins=10, range=(0, 1), stacked=False,
                color=["#2e7d32", "#c62828"], label=["normaux", "anomalies"])
        ax.axvline(SEUIL_DECISION, color="black", linestyle="--", linewidth=1)
        ax.set_xlabel("score d'anomalie")
        ax.set_ylabel("nombre de cas")
        ax.set_title("Distribution des scores", fontsize=9)
        ax.legend(fontsize=7)

        # (c) Contribution des trois niveaux
        ax = axes[1, 0]
        x = range(len(resultats))
        ax.plot(x, [r["score_seuils"] for r in resultats], "o-", label="seuils",
                markersize=3, linewidth=1)
        ax.plot(x, [0.7 * r["score_if"] for r in resultats], "s-", label="IF (x0.7)",
                markersize=3, linewidth=1)
        ax.plot(x, [0.6 * r["score_lstm"] for r in resultats], "^-", label="LSTM (x0.6)",
                markersize=3, linewidth=1)
        ax.axvspan(-0.5, 9.5, alpha=0.08, color="green")
        ax.axvspan(9.5, 19.5, alpha=0.08, color="red")
        ax.axhline(SEUIL_DECISION, color="black", linestyle="--", linewidth=1)
        ax.set_xlabel("scenario (0-9 normaux, 10-19 anomalies)")
        ax.set_ylabel("score pondere")
        ax.set_title("Contribution de chaque niveau", fontsize=9)
        ax.legend(fontsize=7)

        # (d) Performance par famille
        ax = axes[1, 1]
        familles, taux = [], []
        for c, lib in [("normal", "Normaux"), ("ambigu-normal", "Normaux\nambigus"),
                       ("evident", "Anomalies\nevidentes"), ("ambigu-anomalie", "Anomalies\nambigues")]:
            sous = [r for r in resultats if r["categorie"] == c]
            familles.append(lib)
            taux.append(sum(1 for r in sous if r["correct"]) / len(sous) * 100)
        barres = ax.bar(familles, taux, color=["#2e7d32", "#66bb6a", "#c62828", "#ef5350"])
        ax.set_ylim(0, 105)
        ax.set_ylabel("% de decisions correctes")
        ax.set_title("Performance par difficulte", fontsize=9)
        for b, t in zip(barres, taux):
            ax.text(b.get_x() + b.get_width() / 2, t + 2, f"{t:.0f}%",
                    ha="center", fontsize=8)

        plt.tight_layout()
        sortie = Path("evaluation_ml.png")
        plt.savefig(sortie, dpi=150, bbox_inches="tight")
        print(f"[5] COURBES ENREGISTREES -> {sortie.resolve()}")
        print("    Quatre graphiques exploitables directement dans le rapport.")

    except ImportError:
        print("[5] matplotlib absent -- courbes non generees")
        print("    pip install matplotlib")
    except Exception as e:
        print(f"[5] Erreur generation des courbes : {e}")


# Resultats bruts, pour un tableau dans le rapport
Path("evaluation_ml.json").write_text(
    json.dumps({
        "scenarios": resultats,
        "matrice": {"vp": vp, "vn": vn, "fp": fp, "fn": fn},
        "metriques": {"precision": round(precision, 1), "rappel": round(rappel, 1),
                      "exactitude": round(exactitude, 1), "f1": round(f1, 1)},
    }, indent=2, ensure_ascii=False),
    encoding="utf-8")
print(f"[6] RESULTATS BRUTS -> evaluation_ml.json")

print("\n" + "=" * 72)
print("FIN DE L'EVALUATION")
print("=" * 72)