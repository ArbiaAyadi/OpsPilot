"""
test_ml.py — Tests de performance du moteur de détection ML.
"""
import pytest
import sys
from pathlib import Path
from copy import deepcopy

sys.path.insert(0, str(Path(__file__).parent.parent))


BASE = {
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

SCENARIOS_NORMAUX = [
    pytest.param({**BASE}, id="baseline_normal"),
    pytest.param({**BASE, "cpu_pct": 60.0, "load_avg_1m": 1.2}, id="cpu_modere"),
    pytest.param({**BASE, "ram_pct": 70.0}, id="ram_moderee"),
    pytest.param({**BASE, "swap_pct": 15.0}, id="swap_faible"),
]

SCENARIOS_ANOMALIE = [
    pytest.param({**BASE, "ram_pct": 92.0, "swap_pct": 60.0}, id="ram_critique_92pct"),
    pytest.param({**BASE, "cpu_pct": 95.0, "load_avg_1m": 9.0}, id="cpu_sature_95pct"),
    pytest.param(
        {**BASE, "disk_write_latency_ms": 200.0, "disk_read_latency_ms": 150.0, "cpu_iowait_pct": 45.0},
        id="io_storm_latence_200ms"
    ),
    pytest.param(
        {**BASE, "net_errors_in": 500.0, "net_errors_out": 300.0, "net_drop_in": 200.0},
        id="erreurs_reseau_massives"
    ),
    pytest.param({**BASE, "vms_running": 0.0, "ram_pct": 85.0}, id="vms_arretees_ram_elevee"),
    pytest.param({**BASE, "swap_pct": 85.0, "ram_pct": 95.0}, id="swap_et_ram_critiques"),
]


@pytest.mark.ml
class TestScorePlage:
    def test_score_dans_plage_normale(self, ml_analyser, metriques_normales):
        score, seuil = ml_analyser.analyser(metriques_normales)
        assert 0.0 <= float(score) <= 1.0, f"Score hors plage: {score}"
        assert 0.0 <= float(seuil) <= 1.0, f"Seuil hors plage: {seuil}"

    def test_score_dans_plage_extremes(self, ml_analyser):
        metriques_extremes = {k: 100.0 for k in BASE}
        score, seuil = ml_analyser.analyser(metriques_extremes)
        assert 0.0 <= float(score) <= 1.0

    def test_score_dans_plage_zeros(self, ml_analyser):
        metriques_zeros = {k: 0.0 for k in BASE}
        score, _ = ml_analyser.analyser(metriques_zeros)
        assert 0.0 <= float(score) <= 1.0

    def test_get_stats_retourne_dict(self, ml_analyser):
        stats = ml_analyser.get_stats()
        assert isinstance(stats, dict)
        assert "n_analyses" in stats or "lstm_ready" in stats


@pytest.mark.ml
@pytest.mark.parametrize("metriques", SCENARIOS_NORMAUX)
def test_pas_de_faux_positif(ml_analyser, metriques):
    score, seuil = ml_analyser.analyser(metriques)
    score = float(score)
    assert score < 0.5, (
        f"FAUX POSITIF détecté : score={score:.4f} pour un cluster normal. "
        f"Le modèle génère des alertes injustifiées."
    )


@pytest.mark.ml
@pytest.mark.parametrize("metriques", SCENARIOS_ANOMALIE)
def test_detection_anomalie(ml_analyser, metriques):
    score, seuil = ml_analyser.analyser(metriques)
    score = float(score)
    assert score >= 0.5, (
        f"FAUX NÉGATIF : score={score:.4f} pour une anomalie critique. "
        f"Le modèle a manqué une situation dangereuse."
    )


@pytest.mark.ml
class TestCoherence:
    def test_score_monte_avec_ram(self, ml_analyser):
        scores = []
        for ram in [45.0, 70.0, 92.0]:
            m = {**BASE, "ram_pct": ram}
            score, _ = ml_analyser.analyser(m)
            scores.append(float(score))

        assert scores[0] < scores[2], (
            f"Score non monotone avec RAM: {scores}. "
            f"RAM 92% devrait avoir un score plus élevé que RAM 45%."
        )

    def test_score_monte_avec_cpu(self, ml_analyser):
        scores = []
        for cpu in [15.0, 70.0, 95.0]:
            m = {**BASE, "cpu_pct": cpu, "load_avg_1m": cpu / 10}
            score, _ = ml_analyser.analyser(m)
            scores.append(float(score))

        assert scores[0] < scores[2], (
            f"Score non monotone avec CPU: {scores}."
        )

    def test_multiple_anomalies_score_plus_eleve(self, ml_analyser):
        score_une, _ = ml_analyser.analyser({**BASE, "ram_pct": 92.0})
        score_plusieurs, _ = ml_analyser.analyser({
            **BASE, "ram_pct": 92.0, "cpu_pct": 91.0,
            "swap_pct": 80.0, "disk_write_latency_ms": 200.0
        })

        assert float(score_plusieurs) >= float(score_une), (
            "Plusieurs anomalies devraient produire un score >= une seule anomalie."
        )


@pytest.mark.ml
@pytest.mark.slow
class TestPerformanceGlobale:
    def _evaluer_tous_scenarios(self, ml):
        vp, vn, fp, fn_count = 0, 0, 0, 0
        scenarios = [
            (BASE, False),
            ({**BASE, "cpu_pct": 60.0}, False),
            ({**BASE, "ram_pct": 70.0}, False),
            ({**BASE, "ram_pct": 92.0, "swap_pct": 60.0}, True),
            ({**BASE, "cpu_pct": 95.0, "load_avg_1m": 9.0}, True),
            ({**BASE, "disk_write_latency_ms": 200.0, "cpu_iowait_pct": 45.0}, True),
            ({**BASE, "net_errors_in": 500.0, "net_drop_in": 200.0}, True),
            ({**BASE, "vms_running": 0.0, "ram_pct": 85.0}, True),
        ]
        for metriques, est_anomalie in scenarios:
            score, _ = ml.analyser(metriques)
            detecte = float(score) >= 0.5
            if est_anomalie and detecte:     vp += 1
            elif not est_anomalie and not detecte: vn += 1
            elif est_anomalie and not detecte:     fn_count += 1
            elif not est_anomalie and detecte:     fp += 1

        return vp, vn, fp, fn_count

    def test_precision_minimale_80pct(self, ml_analyser):
        vp, vn, fp, fn_count = self._evaluer_tous_scenarios(ml_analyser)
        if vp + fp == 0:
            pytest.skip("Aucune alarme déclenchée, impossible de calculer la précision")
        precision = vp / (vp + fp) * 100
        assert precision >= 80.0, (
            f"Précision={precision:.1f}% < 80% seuil professionnel. "
            f"VP={vp}, FP={fp} — trop d'alarmes injustifiées."
        )

    def test_rappel_minimal_90pct(self, ml_analyser):
        vp, vn, fp, fn_count = self._evaluer_tous_scenarios(ml_analyser)
        if vp + fn_count == 0:
            pytest.skip("Aucune anomalie dans les scénarios de test")
        rappel = vp / (vp + fn_count) * 100
        assert rappel >= 90.0, (
            f"Rappel={rappel:.1f}% < 90% seuil professionnel. "
            f"VP={vp}, FN={fn_count} — des anomalies critiques sont manquées."
        )

    def test_exactitude_minimale_85pct(self, ml_analyser):
        vp, vn, fp, fn_count = self._evaluer_tous_scenarios(ml_analyser)
        total = vp + vn + fp + fn_count
        exactitude = (vp + vn) / total * 100
        assert exactitude >= 85.0, (
            f"Exactitude={exactitude:.1f}% < 85%. "
            f"Performance globale insuffisante pour la production."
        )