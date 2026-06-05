
import numpy as np


# Valeurs maximales attendues pour normaliser chaque métrique
MAXIMA = {
    "cpu_pct":       100.0,
    "ram_pct":       100.0,
    "disk_pct":      100.0,
    "net_recv_mb":   10_000.0,   # 10 GB max de cumul
    "net_sent_mb":   10_000.0,
    "process_count": 1_000.0,
}

# Ordre fixe des features → le LSTM attend toujours le même ordre
FEATURES = list(MAXIMA.keys())


class Preprocesseur:
    """Convertit un dict métriques → vecteur numpy float32 normalisé."""

    def transformer(self, metriques: dict) -> np.ndarray:
        vecteur = []
        for feature in FEATURES:
            valeur = float(metriques.get(feature, 0.0))
            maximum = MAXIMA[feature]
            # Clamp entre 0 et 1
            vecteur.append(min(1.0, max(0.0, valeur / maximum)))
        return np.array(vecteur, dtype=np.float32)

    @property
    def n_features(self) -> int:
        return len(FEATURES)