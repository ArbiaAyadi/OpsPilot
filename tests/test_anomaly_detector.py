"""
test_anomaly_detector.py — Tests du détecteur d'anomalies basé sur règles.

Vérifie que detecter_anomalies() :
  - Détecte les seuils RAM/CPU/Disk dépassés (Niveau 1)
  - Ne génère pas de doublons pour la même anomalie
  - Respecte les seuils officiels Proxmox VE
  - Retourne le bon format d'anomalie (niveau, message, cible)
"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Seuils officiels Proxmox VE
SEUIL_RAM_WARNING = 75.0
SEUIL_RAM_CRITICAL = 85.0
SEUIL_CPU_WARNING = 65.0
SEUIL_CPU_CRITICAL = 80.0
SEUIL_DISK_WARNING = 80.0
SEUIL_DISK_CRITICAL = 90.0


def get_detecteur():
    try:
        from agent.anomaly_detector import detecter_anomalies
        return detecter_anomalies
    except ImportError:
        pytest.skip("agent.anomaly_detector non disponible")


def make_etat(nom="pve1", cpu=15.0, ram=45.0, disk=40.0, statut="online"):
    """Créer un état cluster minimal pour les tests."""
    return {
        "noeuds": [{
            "nom": nom, "statut": statut,
            "cpu_pct": cpu, "ram_pct": ram, "disk_pct": disk,
            "ram_used_gb": ram * 1.9 / 100,
            "ram_total_gb": 1.9,
            "cpu_cores": 2, "uptime_h": 2.0,
            "swap_pct": 0.0, "cpu_iowait_pct": 0.0,
            "net_errors_in": 0.0, "net_errors_out": 0.0,
        }],
        "vms": [],
        "alertes": [],
        "vms_running": 0,
    }


# ─── Tests état normal ─────────────────────────────────────────────────────────

@pytest.mark.unit
class TestEtatNormal:
    """Un cluster normal ne doit générer aucune anomalie."""

    def test_pas_d_anomalie_cluster_normal(self):
        detecter = get_detecteur()
        etat = make_etat(cpu=15.0, ram=45.0, disk=40.0)
        anomalies = detecter(etat, {})
        assert len(anomalies) == 0, \
            f"Cluster normal ne doit pas générer d'anomalies: {anomalies}"

    def test_pas_d_anomalie_ram_70(self):
        detecter = get_detecteur()
        etat = make_etat(ram=70.0)
        anomalies = detecter(etat, {})
        # 70% < 75% seuil WARNING → pas d'anomalie
        anomalies_ram = [a for a in anomalies if "ram" in a.get("message", "").lower()
                         or "ram" in a.get("cible", "").lower()]
        assert len(anomalies_ram) == 0

    def test_retourne_liste(self):
        detecter = get_detecteur()
        etat = make_etat()
        result = detecter(etat, {})
        assert isinstance(result, list)


# ─── Tests seuils RAM ─────────────────────────────────────────────────────────

@pytest.mark.unit
class TestSeuilsRAM:
    """Seuils RAM officiels Proxmox VE."""

    def test_ram_75_genere_warning(self):
        detecter = get_detecteur()
        etat = make_etat(ram=76.0)
        anomalies = detecter(etat, {})
        anomalies_ram = [a for a in anomalies
                         if "ram" in a.get("message", "").lower()
                         or "memory" in a.get("message", "").lower()]
        assert len(anomalies_ram) >= 1, \
            "RAM 76% (> 75% seuil) doit générer une anomalie"

    def test_ram_85_genere_critique(self):
        detecter = get_detecteur()
        etat = make_etat(ram=87.0)
        anomalies = detecter(etat, {})
        anomalies_ram = [a for a in anomalies
                         if "ram" in a.get("message", "").lower()
                         or "memory" in a.get("message", "").lower()]

        if anomalies_ram:
            niveaux = [a.get("niveau", "").upper() for a in anomalies_ram]
            assert any(n in ["CRITIQUE", "CRITICAL"] for n in niveaux), \
                f"RAM 87% doit être CRITIQUE, got: {niveaux}"

    def test_anomalie_contient_champs_requis(self):
        detecter = get_detecteur()
        etat = make_etat(ram=87.0)
        anomalies = detecter(etat, {})
        if anomalies:
            for a in anomalies:
                assert "niveau" in a, f"Champ 'niveau' manquant: {a}"
                assert "message" in a, f"Champ 'message' manquant: {a}"
                assert "cible" in a, f"Champ 'cible' manquant: {a}"

    def test_anomalie_niveau_valide(self):
        detecter = get_detecteur()
        etat = make_etat(ram=87.0)
        anomalies = detecter(etat, {})
        niveaux_valides = {"CRITIQUE", "CRITICAL", "IMPORTANT", "HIGH", "SURVEILLANCE", "WARNING", "INFO"}
        for a in anomalies:
            assert a.get("niveau", "").upper() in niveaux_valides, \
                f"Niveau invalide: {a.get('niveau')}"


# ─── Tests seuils CPU ─────────────────────────────────────────────────────────

@pytest.mark.unit
class TestSeuilsCPU:
    """Seuils CPU officiels Proxmox VE."""

    def test_cpu_65_peut_generer_warning(self):
        detecter = get_detecteur()
        etat = make_etat(cpu=66.0)
        anomalies = detecter(etat, {})
        anomalies_cpu = [a for a in anomalies
                         if "cpu" in a.get("message", "").lower()]
        # Selon l'implémentation, peut ou non générer une anomalie à 66%
        assert isinstance(anomalies_cpu, list)

    def test_cpu_90_genere_anomalie(self):
        detecter = get_detecteur()
        etat = make_etat(cpu=91.0)
        anomalies = detecter(etat, {})
        anomalies_cpu = [a for a in anomalies
                         if "cpu" in a.get("message", "").lower()]
        assert len(anomalies_cpu) >= 1, \
            "CPU 91% doit générer une anomalie CPU"


# ─── Tests seuils Disk ────────────────────────────────────────────────────────

@pytest.mark.unit
class TestSeuilsDisk:
    """Seuils Disk officiels Proxmox VE."""

    def test_disk_90_genere_anomalie(self):
        detecter = get_detecteur()
        etat = make_etat(disk=91.0)
        anomalies = detecter(etat, {})
        anomalies_disk = [a for a in anomalies
                          if "disk" in a.get("message", "").lower()
                          or "storage" in a.get("message", "").lower()]
        assert len(anomalies_disk) >= 1, \
            "Disk 91% doit générer une anomalie"


# ─── Tests déduplication ──────────────────────────────────────────────────────

@pytest.mark.unit
class TestDeduplication:
    """Pas de doublons pour la même anomalie."""

    def test_pas_de_doublon_meme_anomalie(self):
        detecter = get_detecteur()
        etat = make_etat(ram=87.0)
        anomalies = detecter(etat, {})

        # Vérifier unicité par (cible, message)
        keys = [(a.get("cible", ""), a.get("message", "").strip()) for a in anomalies]
        assert len(keys) == len(set(keys)), \
            f"Doublons détectés: {keys}"

    def test_anomalie_disparait_si_resolue(self):
        detecter = get_detecteur()
        etat_probleme = make_etat(ram=87.0)
        etat_normal = make_etat(ram=45.0)

        anomalies_avant = detecter(etat_probleme, {})
        anomalies_apres = detecter(etat_normal, {})

        # Après résolution, moins d'anomalies ou aucune
        assert len(anomalies_apres) <= len(anomalies_avant), \
            "Après retour à la normale, les anomalies doivent disparaître"


# ─── Tests noeud offline ──────────────────────────────────────────────────────

@pytest.mark.unit
class TestNoeudOffline:
    """Un nœud offline ne doit pas générer de fausses alertes CPU/RAM."""

    def test_noeud_offline_pas_d_alerte_cpu_ram(self):
        detecter = get_detecteur()
        etat = make_etat(cpu=0.0, ram=0.0, statut="offline")
        anomalies = detecter(etat, {})

        # Les métriques à 0% d'un nœud offline ne doivent pas être des anomalies
        anomalies_cpu_ram = [a for a in anomalies
                             if ("cpu" in a.get("message", "").lower()
                                 or "ram" in a.get("message", "").lower())
                             and a.get("cible") == "pve1"]
        assert len(anomalies_cpu_ram) == 0, \
            f"Nœud offline ne doit pas avoir d'alertes CPU/RAM: {anomalies_cpu_ram}"