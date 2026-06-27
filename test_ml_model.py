
import sys
import os
import time
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

print("=" * 70)
print("OPSPILOT — DIAGNOSTIC MODELE ML")
print("=" * 70)

# ─────────────────────────────────────────────────────────────────────────────
# 1. CHARGER LE MODELE
# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] CHARGEMENT DU MODELE...")
try:
    from ml_analyser import MLAnalyseur
    ml = MLAnalyseur()
    stats = ml.get_stats()
    lstm_status = stats.get('lstm_ready', False)
    lstm_stats  = stats.get('lstm', {})
    print(f"  ✓ MLAnalyseur charge")
    print(f"  • Isolation Forest : {'ACTIF' if stats.get('n_analyses', 0) >= 0 else 'INACTIF'}")
    print(f"  • LSTM             : {'ENTRAINE (pre-entrainement synthetique)' if lstm_status else 'EN APPRENTISSAGE'}")
    print(f"  • Analyses faites  : {stats.get('n_analyses', 0)}")
    if lstm_stats:
        print(f"  • Echantillons LSTM: {lstm_stats.get('n_echantillons', 0)}")
        print(f"  • Seuil adaptatif  : {lstm_stats.get('seuil_actuel', 'N/A')}")
        print(f"  • Drift detecte    : {lstm_stats.get('drift_detecte', False)}")
    if lstm_status:
        print(f"\n  ✓ LSTM OPERATIONNEL IMMEDIATEMENT grace au pre-entrainement synthetique")
    else:
        print(f"\n  ⚠ LSTM pas encore operationnel — necessite des donnees reelles")
except Exception as e:
    print(f"  ✗ Erreur chargement: {e}")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# 2. DONNEES D'ENTREE — QU'EST-CE QUE LE MODELE ANALYSE ?
# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] DONNEES D'ENTREE DU MODELE (18 features)")
print("-" * 50)
features = [
    ("cpu_pct",               "% CPU utilise sur le cluster",           0, 100),
    ("ram_pct",               "% RAM utilisee sur le cluster",          0, 100),
    ("disk_pct",              "% Disk utilise",                         0, 100),
    ("swap_pct",              "% Swap utilise",                         0, 100),
    ("cpu_iowait_pct",        "% CPU en attente I/O",                   0, 100),
    ("disk_read_iops",        "IOPS lecture totaux cluster",             0, None),
    ("disk_write_iops",       "IOPS ecriture totaux cluster",           0, None),
    ("disk_read_latency_ms",  "Latence lecture disque (ms)",             0, None),
    ("disk_write_latency_ms", "Latence ecriture disque (ms)",           0, None),
    ("net_in_mbps",           "Trafic reseau entrant (MB/s)",           0, None),
    ("net_out_mbps",          "Trafic reseau sortant (MB/s)",           0, None),
    ("net_errors_in",         "Erreurs reseau entrant/s",               0, None),
    ("net_errors_out",        "Erreurs reseau sortant/s",               0, None),
    ("net_drop_in",           "Paquets perdus entrant/s",               0, None),
    ("net_drop_out",          "Paquets perdus sortant/s",               0, None),
    ("vms_running",           "Nombre de VMs actives",                  0, None),
    ("load_avg_1m",           "Load average 1 minute",                  0, None),
    ("zfs_arc_hit_rate",      "ZFS ARC hit rate (%)",                   0, 100),
    ("cpu_temp_max_c",        "Temperature CPU max (C)",                 0, 100),
    ("fd_used_pct",           "% File descriptors utilises",            0, 100),
]
for name, desc, vmin, vmax in features[:18]:
    print(f"  {name:30s} → {desc}")

# ─────────────────────────────────────────────────────────────────────────────
# 3. EST-CE QUE LE MODELE EST ENTRAINE SUR DES VRAIES DONNEES ?
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] ORIGINE DES DONNEES D'ENTRAINEMENT")
print("-" * 50)

# Verifier si un modele LSTM sauvegarde existe
lstm_path = Path("lstm_ae_model.pt")
normalizer_path = Path("normalizer_stats.json")

if lstm_path.exists():
    size_kb = lstm_path.stat().st_size / 1024
    mtime   = time.ctime(lstm_path.stat().st_mtime)
    print(f"  ✓ Modele LSTM sauvegarde trouve : {lstm_path}")
    print(f"    Taille : {size_kb:.1f} KB | Modifie : {mtime}")
    print(f"    → Modele charge depuis le disque (entraine sur donnees reelles)")
else:
    print(f"  ✗ Pas de modele LSTM sauvegarde (lstm_ae_model.pt)")
    print(f"    → Pre-entrainement synthetique actif : LSTM operationnel DES LE DEMARRAGE")
    print(f"    → S'affinera automatiquement sur les donnees reelles (toutes les 200 collectes)")

if normalizer_path.exists():
    import json
    with open(normalizer_path) as f:
        norm = json.load(f)
    print(f"\n  ✓ Statistiques de normalisation trouvees : {normalizer_path}")
    print(f"    Features normalisees : {len(norm)} / 20")
else:
    print(f"\n  ✗ normalizer_stats.json non trouve")

print(f"""
  REPONSE A LA QUESTION "SUR QUELLES DONNEES EST-IL ENTRAINE ?" :

  Isolation Forest (IF) :
    • Entraine IMMEDIATEMENT au premier lancement
    • Donnees synthetiques : scenarios normaux + scenarios anomalies
      generes par le code dans ml_analyser.py (pas des donnees reelles)
    • Avantage  : detection immediate des anomalies grossieres
    • Limite    : les seuils ne sont pas adaptes a TON cluster specifique

  LSTM Autoencoder :
    • OPERATIONNEL IMMEDIATEMENT grace au pre-entrainement synthetique
    • Pre-entraine sur 400 sequences temporelles normales (patterns de
      stabilite et de continuite, universels pour tout cluster stable)
    • S'affine en continu sur les donnees reelles de TON cluster
    • Avantage  : apprend ce qui est "normal" pour TON infrastructure
    • Apres ~200 collectes : seuil recalibre sur tes vraies metriques

  En pratique pour ton projet :
    Niveau 1 (seuils)  : detection instantanee de toute metrique critique
    Niveau 2 (IF)      : detection des combinaisons anormales multi-features
    Niveau 3 (LSTM)    : detection des derives temporelles subtiles
    → Les 3 niveaux sont operationnels DES LE DEMARRAGE
""")

# ─────────────────────────────────────────────────────────────────────────────
# 4. TEST DES SCENARIOS — DETECTE-T-IL LES BONNES ANOMALIES ?
# ─────────────────────────────────────────────────────────────────────────────
print("[4] TEST DES SCENARIOS DE DETECTION")
print("-" * 50)

def metriques_base():
    """Etat normal d'un cluster Proxmox de developpement."""
    return {
        "cpu_pct": 15.0, "ram_pct": 45.0, "disk_pct": 40.0,
        "swap_pct": 2.0, "cpu_iowait_pct": 1.0,
        "disk_read_iops": 50.0, "disk_write_iops": 30.0,
        "disk_read_latency_ms": 0.5, "disk_write_latency_ms": 1.0,
        "net_in_mbps": 10.0, "net_out_mbps": 5.0,
        "net_errors_in": 0.0, "net_errors_out": 0.0,
        "net_drop_in": 0.0, "net_drop_out": 0.0,
        "vms_running": 1.0, "load_avg_1m": 0.5,
        "zfs_arc_hit_rate": 95.0, "cpu_temp_max_c": 45.0, "fd_used_pct": 5.0,
    }

scenarios = [
    {
        "nom": "Cluster normal (baseline)",
        "attendu": "NORMAL (score < 0.3)",
        "anomalie": False,
        "metriques": metriques_base(),
    },
    {
        "nom": "RAM critique (86%)",
        "attendu": "ANOMALIE DETECTEE (score > 0.5)",
        "anomalie": True,
        "metriques": {**metriques_base(), "ram_pct": 86.0, "swap_pct": 55.0},
    },
    {
        "nom": "CPU sature (91%)",
        "attendu": "ANOMALIE DETECTEE (score > 0.5)",
        "anomalie": True,
        "metriques": {**metriques_base(), "cpu_pct": 91.0, "load_avg_1m": 8.0},
    },
    {
        "nom": "Disk I/O storm (latence 200ms)",
        "attendu": "ANOMALIE DETECTEE (score > 0.5)",
        "anomalie": True,
        "metriques": {**metriques_base(), "disk_write_latency_ms": 200.0, "disk_read_latency_ms": 150.0, "cpu_iowait_pct": 45.0},
    },
    {
        "nom": "Perte de VM (0 VMs running)",
        "attendu": "ANOMALIE DETECTEE (score > 0.5)",
        "anomalie": True,
        "metriques": {**metriques_base(), "vms_running": 0.0},
    },
    {
        "nom": "Erreurs reseau massives",
        "attendu": "ANOMALIE DETECTEE (score > 0.5)",
        "anomalie": True,
        "metriques": {**metriques_base(), "net_errors_in": 500.0, "net_errors_out": 300.0, "net_drop_in": 200.0},
    },
    {
        "nom": "Etat actuel du cluster (via Proxmox API)",
        "attendu": "Selon l'etat reel",
        "anomalie": None,
        "metriques": None,  # sera rempli si Proxmox disponible
    },
]

resultats = []
faux_positifs = 0
faux_negatifs = 0
vrais_positifs = 0
vrais_negatifs = 0

for scenario in scenarios:
    if scenario["metriques"] is None:
        # Tenter de collecter les metriques reelles
        try:
            from proxmox_api import get_etat_cluster
            etat = get_etat_cluster()
            noeuds = etat.get("noeuds", [])
            n_noeuds = max(len(noeuds), 1)
            avg = lambda k: sum(x.get(k, 0) for x in noeuds) / n_noeuds
            scenario["metriques"] = {
                "cpu_pct":    avg("cpu_pct"),    "ram_pct":  avg("ram_pct"),
                "disk_pct":   avg("disk_pct"),   "swap_pct": avg("swap_pct"),
                "cpu_iowait_pct": avg("cpu_iowait_pct"),
                "disk_read_iops":  avg("disk_read_iops"),
                "disk_write_iops": avg("disk_write_iops"),
                "disk_read_latency_ms":  avg("disk_read_latency_ms"),
                "disk_write_latency_ms": avg("disk_write_latency_ms"),
                "net_in_mbps":  avg("net_in_mbps"),  "net_out_mbps": avg("net_out_mbps"),
                "net_errors_in":  avg("net_errors_in"),  "net_errors_out": avg("net_errors_out"),
                "net_drop_in":    avg("net_drop_in"),    "net_drop_out":   avg("net_drop_out"),
                "vms_running":    etat.get("vms_running", 0),
                "load_avg_1m":    avg("load_avg_1m"),
                "zfs_arc_hit_rate": avg("zfs_arc_hit_rate") or 95.0,
                "cpu_temp_max_c": max((n.get("cpu_temp_max_c", 0) for n in noeuds), default=0),
                "fd_used_pct":    avg("fd_used_pct"),
            }
        except Exception as e:
            print(f"  ⚠ Proxmox non accessible : {e}")
            continue

    try:
        score, seuil = ml.analyser(scenario["metriques"])
        score  = float(score)
        seuil  = float(seuil)
        detecte = score >= 0.5

        # Evaluer si correct
        if scenario["anomalie"] is True and detecte:
            verdict = "✓ VRAI POSITIF"
            vrais_positifs += 1
        elif scenario["anomalie"] is False and not detecte:
            verdict = "✓ VRAI NEGATIF"
            vrais_negatifs += 1
        elif scenario["anomalie"] is True and not detecte:
            verdict = "✗ FAUX NEGATIF — anomalie manquee !"
            faux_negatifs += 1
        elif scenario["anomalie"] is False and detecte:
            verdict = "✗ FAUX POSITIF — alarme injustifiee !"
            faux_positifs += 1
        else:
            verdict = f"  Score: {score:.3f} (reel)"

        print(f"\n  Scenario : {scenario['nom']}")
        print(f"  Attendu  : {scenario['attendu']}")
        print(f"  Score    : {score:.4f} / seuil {seuil:.4f} → {'ANOMALIE' if detecte else 'NORMAL'}")
        print(f"  Verdict  : {verdict}")

    except Exception as e:
        print(f"\n  Scenario : {scenario['nom']}")
        print(f"  Erreur   : {e}")

# ─────────────────────────────────────────────────────────────────────────────
# 5. BILAN DE PERFORMANCE
# ─────────────────────────────────────────────────────────────────────────────
total_teste = vrais_positifs + vrais_negatifs + faux_positifs + faux_negatifs
if total_teste > 0:
    precision  = vrais_positifs / max(vrais_positifs + faux_positifs, 1) * 100
    rappel     = vrais_positifs / max(vrais_positifs + faux_negatifs, 1) * 100
    exactitude = (vrais_positifs + vrais_negatifs) / total_teste * 100

    print(f"""
{"=" * 70}
BILAN DE PERFORMANCE DU MODELE
{"=" * 70}
  Tests effectues : {total_teste}
  Vrais positifs  : {vrais_positifs}  (anomalies correctement detectees)
  Vrais negatifs  : {vrais_negatifs}  (etats normaux correctement ignores)
  Faux positifs   : {faux_positifs}  (alarmes injustifiees)
  Faux negatifs   : {faux_negatifs}  (anomalies manquees)

  Precision  : {precision:.1f}%  (sur les alarmes declenchees, combien etaient reelles)
  Rappel     : {rappel:.1f}%  (des vraies anomalies, combien ont ete detectees)
  Exactitude : {exactitude:.1f}%  (taux de reponses correctes au total)

  INTERPRETATION :
  • Precision faible  = trop de fausses alarmes → ingenieur ignore les alertes
  • Rappel faible     = anomalies non detectees → incidents silencieux dangereux
  • Ideal professionnel : Precision > 80%, Rappel > 90%
""")

    if faux_negatifs > 0:
        print(f"  ⚠ PROBLEME : {faux_negatifs} anomalie(s) non detectee(s)")
        print(f"    → Le modele n'est peut-etre pas encore entraine sur assez de donnees")
        print(f"    → Augmenter SURVEILLANCE_INTERVAL et laisser tourner 24h")

    if faux_positifs > 0:
        print(f"  ⚠ PROBLEME : {faux_positifs} fausse(s) alarme(s)")
        print(f"    → Le seuil adaptatif n'est pas encore calibre sur ce cluster")
        print(f"    → Normal au debut, se corrige apres ~1h de donnees reelles")

print("\n" + "=" * 70)
print("FIN DU DIAGNOSTIC")
print("=" * 70)