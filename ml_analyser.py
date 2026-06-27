
import os
import threading
import numpy as np
from pathlib import Path
from collections import deque

# Imports optionnels
try:
    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import StandardScaler
    IF_OK = True
except ImportError:
    IF_OK = False
    print("[ML] sklearn non installe -- pip install scikit-learn --break-system-packages")

try:
    import torch
    import torch.nn as nn
    TORCH_OK = True
except ImportError:
    TORCH_OK = False
    print("[ML] PyTorch non installe -- pip install torch --break-system-packages")

MODELE_PATH  = Path("lstm_ae_model.pt")
HISTORY_SIZE = 500
SEQ_LEN      = 20
N_FEATURES   = 18


# =============================================================================
# Niveau 1 : Seuils fixes par feature
# Detecte IMMEDIATEMENT une seule metrique critique.
# Sans ce niveau, l'IF rate les anomalies isolees dans 18 dimensions.
# =============================================================================
# Chaque entree : (seuil_warning, seuil_critique, score_warning, score_critique)
SEUILS = {
    "cpu_pct":               (65,  80,  0.40, 0.85),
    "ram_pct":               (75,  85,  0.40, 0.90),
    "disk_pct":              (80,  90,  0.40, 0.85),
    "swap_pct":              (50,  80,  0.40, 0.85),
    "cpu_iowait_pct":        (15,  30,  0.40, 0.85),
    "disk_read_latency_ms":  (10,  50,  0.40, 0.85),
    "disk_write_latency_ms": (10,  50,  0.40, 0.85),
    "cpu_temp_max_c":        (75,  85,  0.40, 0.85),
    "fd_used_pct":           (70,  90,  0.40, 0.85),
    "net_errors_in":         (5,   20,  0.35, 0.70),
    "net_errors_out":        (5,   20,  0.35, 0.70),
    "net_drop_in":           (5,   20,  0.35, 0.70),
    "net_drop_out":          (5,   20,  0.35, 0.70),
}
# Features inverses : plus petit = pire
SEUILS_INVERSES = {
    "zfs_arc_hit_rate":  (70, 40, 0.35, 0.70),  # < 70% warning, < 40% critique
    "vms_running":       (1,  0,  0.35, 0.80),   # 0 VM running = critique
}

def score_seuils(metriques: dict) -> float:
    """
    Score base sur les seuils officiels Proxmox.
    Garantit detection immediate de toute metrique critique isolee.
    """
    score_max = 0.0

    for feat, (warn, crit, s_warn, s_crit) in SEUILS.items():
        val = metriques.get(feat, 0) or 0
        if val >= crit:
            score_max = max(score_max, s_crit)
        elif val >= warn:
            # Interpolation lineaire entre warning et critique
            frac = (val - warn) / max(crit - warn, 1)
            score_max = max(score_max, s_warn + frac * (s_crit - s_warn))

    for feat, (warn, crit, s_warn, s_crit) in SEUILS_INVERSES.items():
        val = metriques.get(feat)
        if val is None:
            continue
        val = float(val)
        if val <= crit:
            score_max = max(score_max, s_crit)
        elif val < warn:
            frac = (warn - val) / max(warn - crit, 1)
            score_max = max(score_max, s_warn + frac * (s_crit - s_warn))

    return float(min(1.0, score_max))


# =============================================================================
# Niveau 2 : Isolation Forest
# Detecte les COMBINAISONS anormales de features
# =============================================================================
class IsolationForestAnalyseur:
    """
    Detecte les combinaisons anormales de metriques.
    Complement du niveau 1 (seuils) : capte les anomalies subtiles
    ou combinatoires que les seuils fixes ne voient pas.
    Ex : CPU=60% + RAM=72% + Load=6 + I/O wait=18% ensemble = anormal.
    """

    def __init__(self, contamination: float = 0.05):
        self.contamination   = contamination
        self.model           = None
        self.scaler          = StandardScaler() if IF_OK else None
        self.historique      = deque(maxlen=HISTORY_SIZE)
        self.entraine        = False
        self.n_retrainings   = 0
        self._lock           = threading.Lock()
        self._p5             = -0.10
        self._p50            = 0.05
        if IF_OK:
            self._entrainement_initial()

    def _entrainement_initial(self):
        np.random.seed(42)
        n = 500

        # Etats normaux d'un cluster Proxmox homelab 2 noeuds
        normales = np.column_stack([
            np.random.normal(20, 10, n).clip(0, 60),    # CPU 0-60%
            np.random.normal(50, 12, n).clip(20, 78),   # RAM 20-78%
            np.random.normal(42,  8, n).clip(10, 75),   # Disk 10-75%
            np.random.normal( 3,  3, n).clip(0, 18),    # Swap 0-18%
            np.random.normal( 2,  2, n).clip(0,  9),    # I/O wait 0-9%
            np.random.normal(100, 80, n).clip(0, 2000), # Read IOPS 0-2000
            np.random.normal( 80, 60, n).clip(0, 2000), # Write IOPS 0-2000
            np.random.normal(1.5, 1,  n).clip(0, 15),   # Read lat 0-15ms
            np.random.normal(3,   2,  n).clip(0, 25),   # Write lat 0-25ms
            np.random.normal(2,   2,  n).clip(0, 50),   # Net in 0-50 MB/s
            np.random.normal(1,   1,  n).clip(0, 50),   # Net out 0-50 MB/s
            np.zeros(n),                                  # Net errors = 0
            np.zeros(n),                                  # Net drops = 0
            np.random.choice([1, 2, 2, 3], n).astype(float),  # VMs: 1-3
            np.random.normal(0.5, 0.3, n).clip(0, 3),   # Load 0-3
            np.random.normal(90,  5,   n).clip(70, 100),# ZFS 70-100%
            np.random.normal(45,  8,   n).clip(25, 68), # Temp 25-68°C
            np.random.normal(10,  5,   n).clip(0, 35),  # FD 0-35%
        ])

        # Anomalies combinatoires (ce que les seuils seuls ne capturent pas)
        n_a = 80
        anomalies = np.column_stack([
            np.random.uniform(62, 90, n_a),   # CPU+RAM+Load ensemble elevés
            np.random.uniform(70, 90, n_a),
            np.random.uniform(42, 75, n_a),
            np.random.uniform(20, 70, n_a),   # Swap modere mais I/O aussi
            np.random.uniform(12, 45, n_a),
            np.random.uniform(3000, 8000, n_a),
            np.random.uniform(3000, 8000, n_a),
            np.random.uniform(30, 300, n_a),
            np.random.uniform(30, 300, n_a),
            np.random.uniform(200, 800, n_a),
            np.random.uniform(100, 600, n_a),
            np.random.uniform(8,  80, n_a),
            np.random.uniform(8,  80, n_a),
            np.zeros(n_a),
            np.random.uniform(6,  30, n_a),
            np.random.uniform(0,  35, n_a),
            np.random.uniform(80, 115, n_a),
            np.random.uniform(70, 100, n_a),
        ])

        contamination = n_a / (n + n_a)
        X = np.vstack([normales, anomalies])
        self.scaler.fit(X)
        X_scaled = self.scaler.transform(X)

        self.model = IsolationForest(
            contamination=contamination,
            n_estimators=300,
            max_samples='auto',
            random_state=42,
            n_jobs=-1,
        )
        self.model.fit(X_scaled)
        self.entraine = True

        # Calibrage du score sur les donnees d'entrainement
        decisions = self.model.decision_function(X_scaled)
        self._p5  = float(np.percentile(decisions, 5))
        self._p50 = float(np.percentile(decisions, 60))
        print(f"[IF] Isolation Forest initialise ({n} normaux + {n_a} anomalies)")

    def _vecteur(self, m: dict) -> np.ndarray:
        return np.array([[
            m.get("cpu_pct",                0),
            m.get("ram_pct",                0),
            m.get("disk_pct",               0),
            m.get("swap_pct",               0),
            m.get("cpu_iowait_pct",         0),
            min(m.get("disk_read_iops",     0), 10000),
            min(m.get("disk_write_iops",    0), 10000),
            min(m.get("disk_read_latency_ms",  0), 500),
            min(m.get("disk_write_latency_ms", 0), 500),
            min(m.get("net_in_mbps",        0), 1000),
            min(m.get("net_out_mbps",       0), 1000),
            min(m.get("net_errors_in",      0) + m.get("net_errors_out", 0), 200),
            min(m.get("net_drop_in",        0) + m.get("net_drop_out",   0), 200),
            m.get("vms_running",            0),
            min(m.get("load_avg_1m",        0), 32),
            m.get("zfs_arc_hit_rate",       95),
            min(m.get("cpu_temp_max_c",     0), 120),
            m.get("fd_used_pct",            0),
        ]])

    def analyser(self, metriques: dict) -> float:
        if not IF_OK or not self.entraine:
            return 0.0
        try:
            X  = self._vecteur(metriques)
            Xs = self.scaler.transform(X)
            d  = self.model.decision_function(Xs)[0]
            # Score calibre : 0 si normal, 1 si pire outlier
            score = max(0.0, min(1.0, (self._p50 - d) / max(self._p50 - self._p5, 1e-6)))
            with self._lock:
                self.historique.append(X[0])
            # Re-entrainement progressif toutes les 150 vraies observations
            if len(self.historique) % 150 == 0 and len(self.historique) >= 150:
                threading.Thread(target=self._reentrainer, daemon=True).start()
            return float(score)
        except Exception as e:
            print(f"[IF] Erreur: {e}")
            return 0.0

    def _reentrainer(self):
        try:
            with self._lock:
                X = np.array(list(self.historique))
            if len(X) < 50:
                return
            self.scaler.fit(X)
            self.model.fit(self.scaler.transform(X))
            decisions = self.model.decision_function(self.scaler.transform(X))
            self._p5  = float(np.percentile(decisions, 5))
            self._p50 = float(np.percentile(decisions, 60))
            self.n_retrainings += 1
            print(f"[IF] Re-entraine #{self.n_retrainings} sur {len(X)} points reels")
        except Exception as e:
            print(f"[IF] Erreur re-entrainement: {e}")

    def get_stats(self) -> dict:
        return {
            "modele":          "IsolationForest",
            "entraine":        self.entraine,
            "n_observations":  len(self.historique),
            "n_retrainings":   self.n_retrainings,
        }


# =============================================================================
# Calibrateur adaptatif + Detecteur de drift (inchanges)
# =============================================================================
class CalibratorAdaptatif:
    def __init__(self, fenetre: int = 500):
        self.erreurs      = deque(maxlen=fenetre)
        self.seuil_actuel = 0.5

    def mettre_a_jour(self, erreur: float):
        self.erreurs.append(erreur)
        if len(self.erreurs) >= 20:
            self.seuil_actuel = float(np.percentile(list(self.erreurs), 95) * 1.5)
            self.seuil_actuel = max(0.01, min(self.seuil_actuel, 10.0))

    def normaliser_score(self, erreur: float) -> float:
        return min(1.0, erreur / max(self.seuil_actuel, 0.001))


class DetecteurDrift:
    def __init__(self):
        self.ewma_courte    = None
        self.ewma_longue    = None
        self.compteur_drift = 0
        self.drift_detecte  = False

    def analyser(self, valeur: float) -> bool:
        if self.ewma_courte is None:
            self.ewma_courte = valeur
            self.ewma_longue = valeur
            return False
        self.ewma_courte = 0.1 * valeur + 0.9 * self.ewma_courte
        self.ewma_longue = 0.01 * valeur + 0.99 * self.ewma_longue
        ratio = self.ewma_courte / max(self.ewma_longue, 0.001)
        if ratio > 2.0:
            self.compteur_drift += 1
        else:
            self.compteur_drift = max(0, self.compteur_drift - 1)
        self.drift_detecte = self.compteur_drift >= 20
        return self.drift_detecte


# =============================================================================
# Niveau 3 : LSTM Autoencoder PyTorch
# =============================================================================
if TORCH_OK:
    class _LSTMAutoencoder(nn.Module):
        def __init__(self, n_features: int, seq_len: int, hidden: int = 32):
            super().__init__()
            self.seq_len = seq_len
            self.encoder = nn.LSTM(n_features, hidden, batch_first=True)
            self.decoder = nn.LSTM(hidden, hidden, batch_first=True)
            self.output  = nn.Linear(hidden, n_features)

        def forward(self, x):
            _, (h, _) = self.encoder(x)
            latent    = h.squeeze(0).unsqueeze(1).repeat(1, self.seq_len, 1)
            decoded, _= self.decoder(latent)
            return self.output(decoded)
else:
    class _LSTMAutoencoder:  # stub quand PyTorch absent
        pass


class LSTMAnalyseur:
    def __init__(self, seq_len: int = SEQ_LEN, n_features: int = N_FEATURES):
        self.seq_len    = seq_len
        self.n_features = n_features
        self.model      = None
        self.optimizer  = None
        self.historique = deque(maxlen=HISTORY_SIZE)
        self.entraine   = False
        self.calibreur  = CalibratorAdaptatif()
        self.drift      = DetecteurDrift()
        self._lock      = threading.Lock()
        if TORCH_OK:
            self._charger_ou_creer()

    def _creer(self):
        m   = _LSTMAutoencoder(self.n_features, self.seq_len)
        opt = torch.optim.Adam(m.parameters(), lr=1e-3)
        return m, opt

    def _charger_ou_creer(self):
        try:
            if MODELE_PATH.exists() and MODELE_PATH.stat().st_size > 500:
                m, opt  = self._creer()
                state   = torch.load(str(MODELE_PATH), map_location="cpu", weights_only=True)
                m.load_state_dict(state["model"])
                opt.load_state_dict(state["optimizer"])
                self.model, self.optimizer = m, opt
                self.entraine = True
                print(f"[LSTM] Modele PyTorch charge depuis {MODELE_PATH}")
            else:
                self.model, self.optimizer = self._creer()
                # Pre-entrainement synthetique immediat — meme principe que l'IF.
                # Le LSTM apprend les PATTERNS TEMPORELS normaux (stabilite,
                # continuite, absence de sauts brutaux) sur des sequences
                # synthetiques realistes. Ces patterns sont universels pour
                # tout cluster Proxmox stable. Le modele s'affinera ensuite
                # sur les vraies metriques de TON cluster specifique.
                self._pretrainement_synthetique()
        except Exception as e:
            print(f"[LSTM] Erreur chargement ({e}) - nouveau modele cree")
            self.model, self.optimizer = self._creer()
            self._pretrainement_synthetique()

    def _generer_sequences_synthetiques(self):
        """
        Genere des sequences temporelles realistes pour un cluster Proxmox homelab.
        
        Principe : les metriques normales evoluent LENTEMENT et de facon CONTINUE.
        Ex : CPU passe de 15% a 18% en quelques cycles, jamais de 15% a 91% d'un coup.
        C'est ce pattern de continuite que le LSTM apprend a reconnaitre comme "normal".
        Une vraie anomalie = rupture brutale de cette continuite.
        """
        np.random.seed(42)
        n_seq     = 400   # 400 sequences d'entrainement
        seq_len   = self.seq_len
        n_feat    = self.n_features
        sequences = []

        for _ in range(n_seq):
            # Point de depart aleatoire dans la plage normale
            base = np.array([
                np.random.uniform(0.05, 0.55),  # cpu_pct / 100
                np.random.uniform(0.25, 0.78),  # ram_pct / 100
                np.random.uniform(0.15, 0.72),  # disk_pct / 100
                np.random.uniform(0.00, 0.15),  # swap_pct / 100
                np.random.uniform(0.00, 0.08),  # cpu_iowait / 100
                np.random.uniform(0.00, 0.20),  # disk_read_iops / 10000
                np.random.uniform(0.00, 0.15),  # disk_write_iops / 10000
                np.random.uniform(0.00, 0.04),  # read_lat / 500
                np.random.uniform(0.00, 0.06),  # write_lat / 500
                np.random.uniform(0.00, 0.05),  # net_in / 1000
                np.random.uniform(0.00, 0.03),  # net_out / 1000
                0.0,                             # net_errors_in
                0.0,                             # net_errors_out
                np.random.uniform(0.05, 0.15),  # vms_running / 20 (1-3 VMs)
                np.random.uniform(0.00, 0.10),  # load_avg / 32
                np.random.uniform(0.75, 1.00),  # zfs_arc_hit / 100
                np.random.uniform(0.25, 0.60),  # cpu_temp / 120
                np.random.uniform(0.02, 0.30),  # fd_used / 100
            ], dtype=np.float32)

            # Generer une sequence de seq_len points avec evolution continue
            # (bruit gaussien petit = variation naturelle d'un cluster stable)
            seq    = np.zeros((seq_len, n_feat), dtype=np.float32)
            current = base.copy()
            for t in range(seq_len):
                # Variation douce a chaque pas de temps
                noise   = np.random.normal(0, 0.005, n_feat).astype(np.float32)
                current = np.clip(current + noise, 0.0, 1.0)
                # Contraindre les features qui doivent rester proches de zero
                current[11] = max(0, current[11] - 0.002)  # net_errors
                current[12] = max(0, current[12] - 0.002)  # net_drops
                seq[t] = current

            sequences.append(seq)

        return np.array(sequences, dtype=np.float32)

    def _pretrainement_synthetique(self):
        """
        Entraine le LSTM sur des sequences synthetiques pour etre operationnel
        immediatement, sans attendre les premieres heures de donnees reelles.
        Strategie identique a celle de l'Isolation Forest (donnees synthetiques
        realistes pour bootstrapper le modele, affinage sur vraies donnees ensuite).
        """
        if not TORCH_OK or self.model is None:
            return
        print("[LSTM] Pre-entrainement synthetique en cours...")
        X_np  = self._generer_sequences_synthetiques()
        X     = torch.tensor(X_np, dtype=torch.float32)
        loss_fn = torch.nn.MSELoss()
        self.model.train()
        prev_loss = float("inf")
        patience  = 0
        for epoch in range(50):
            self.optimizer.zero_grad()
            pred = self.model(X)
            loss = loss_fn(pred, X)
            loss.backward()
            self.optimizer.step()
            cur = loss.item()
            if prev_loss - cur < 1e-6:
                patience += 1
                if patience >= 5:
                    break
            else:
                patience = 0
            prev_loss = cur
        # Calibrer le seuil adaptatif sur les erreurs de reconstruction synthetiques
        self.model.eval()
        with torch.no_grad():
            pred = self.model(X)
        erreurs = torch.mean(torch.abs(X - pred), dim=[1, 2]).numpy()
        for e in erreurs:
            self.calibreur.mettre_a_jour(float(e))
        self.entraine = True
        print(f"[LSTM] Pre-entrainement termine — loss={cur:.6f} seuil={self.calibreur.seuil_actuel:.4f}")
        print(f"[LSTM] Operationnel immediatement, s'affinera sur les donnees reelles")

    def vecteur(self, m: dict) -> np.ndarray:
        return np.array([
            m.get("cpu_pct",               0) / 100,
            m.get("ram_pct",               0) / 100,
            m.get("disk_pct",              0) / 100,
            m.get("swap_pct",              0) / 100,
            m.get("cpu_iowait_pct",        0) / 100,
            min(m.get("disk_read_iops",    0), 10000) / 10000,
            min(m.get("disk_write_iops",   0), 10000) / 10000,
            min(m.get("disk_read_latency_ms",  0), 500) / 500,
            min(m.get("disk_write_latency_ms", 0), 500) / 500,
            min(m.get("net_in_mbps",       0), 1000) / 1000,
            min(m.get("net_out_mbps",      0), 1000) / 1000,
            min(m.get("net_errors_in",     0) + m.get("net_errors_out", 0), 100) / 100,
            min(m.get("net_drop_in",       0) + m.get("net_drop_out",   0), 100) / 100,
            min(m.get("vms_running",       0), 20) / 20,
            min(m.get("load_avg_1m",       0), 32) / 32,
            m.get("zfs_arc_hit_rate",      95) / 100,
            min(m.get("cpu_temp_max_c",    0), 120) / 120,
            m.get("fd_used_pct",           0) / 100,
        ], dtype=np.float32)

    def analyser(self, metriques: dict) -> tuple:
        v = self.vecteur(metriques)
        with self._lock:
            self.historique.append(v)
        if len(self.historique) < self.seq_len or not self.entraine or not TORCH_OK:
            return 0.0, False
        try:
            seq    = np.array(list(self.historique)[-self.seq_len:])
            X      = torch.tensor(seq, dtype=torch.float32).unsqueeze(0)
            self.model.eval()
            with torch.no_grad():
                X_pred = self.model(X)
            erreur = float(torch.mean(torch.abs(X - X_pred)).item())
            self.calibreur.mettre_a_jour(erreur)
            drift  = self.drift.analyser(erreur)
            score  = self.calibreur.normaliser_score(erreur)
            return float(score), drift
        except Exception as e:
            print(f"[LSTM] Erreur analyse: {e}")
            return 0.0, False

    def reentrainer(self):
        if not TORCH_OK or self.model is None:
            return
        with self._lock:
            data = np.array(list(self.historique))
        if len(data) < self.seq_len + 10:
            print(f"[LSTM] Pas assez de donnees ({len(data)}/{self.seq_len+10})")
            return
        seqs = [data[i:i+self.seq_len] for i in range(len(data) - self.seq_len)]
        X    = torch.tensor(np.array(seqs), dtype=torch.float32)
        self.model.train()
        loss_fn   = nn.MSELoss()
        prev_loss = float("inf")
        patience  = 0
        print(f"[LSTM] Entrainement sur {len(X)} sequences...")
        for epoch in range(30):
            self.optimizer.zero_grad()
            pred = self.model(X)
            loss = loss_fn(pred, X)
            loss.backward()
            self.optimizer.step()
            cur = loss.item()
            if prev_loss - cur < 1e-5:
                patience += 1
                if patience >= 5:
                    break
            else:
                patience = 0
            prev_loss = cur
        print(f"[LSTM] Loss finale: {cur:.6f}")
        try:
            torch.save({"model": self.model.state_dict(), "optimizer": self.optimizer.state_dict()}, str(MODELE_PATH))
            print(f"[LSTM] Sauvegarde -> {MODELE_PATH}")
        except Exception as e:
            print(f"[LSTM] Erreur sauvegarde: {e}")
        self.entraine = True

    def get_stats(self) -> dict:
        return {
            "modele":         "LSTM-Autoencoder-PyTorch",
            "entraine":       self.entraine,
            "n_echantillons": len(self.historique),
            "seuil_actuel":   round(self.calibreur.seuil_actuel, 6),
            "drift_detecte":  self.drift.drift_detecte,
        }


# =============================================================================
# Orchestrateur — combine les 3 niveaux
# =============================================================================
class MLAnalyseur:
    """
    Combine les 3 niveaux de detection.
    Score final = max(score_seuils, 0.7*score_if, 0.6*score_lstm)

    Avantage de cette architecture :
    - Seuils   : garantie de detection pour toute anomalie evidente
    - IF       : capture les combinaisons anormales multi-features
    - LSTM     : capture les derives temporelles subtiles
    """

    def __init__(self):
        print("[ML] Initialisation du moteur de detection hybride...")
        self.if_analyseur    = IsolationForestAnalyseur() if IF_OK else None
        self.lstm_analyseur  = LSTMAnalyseur() if TORCH_OK else None
        self._dernier_score_seuils = 0.0
        self._dernier_score_if     = 0.0
        self._dernier_score_lstm   = 0.0
        self._n_analyses           = 0
        print(f"[ML] Seuils  : actifs ({len(SEUILS)+len(SEUILS_INVERSES)} features surveillees)")
        print(f"[ML] IF      : {'actif' if self.if_analyseur else 'desactive'}")
        print(f"[ML] LSTM    : {'actif' if self.lstm_analyseur else 'desactive'}")

    def analyser(self, metriques: dict) -> tuple:
        """
        Returns (score_hybride [0-1], seuil).
        0 = normal, 1 = anomalie grave.
        """
        self._n_analyses += 1

        # Niveau 1 : seuils
        s_seuils = score_seuils(metriques)
        self._dernier_score_seuils = s_seuils

        # Niveau 2 : Isolation Forest
        s_if = 0.0
        if self.if_analyseur:
            s_if = self.if_analyseur.analyser(metriques)
            self._dernier_score_if = s_if

        # Niveau 3 : LSTM
        s_lstm = 0.0
        drift  = False
        if self.lstm_analyseur:
            s_lstm, drift = self.lstm_analyseur.analyser(metriques)
            self._dernier_score_lstm = s_lstm
            if drift:
                print("[ML] Drift detecte -> re-entrainement LSTM")
                threading.Thread(target=self.lstm_analyseur.reentrainer, daemon=True).start()

        # Score final : le max pondére garantit qu'un seul niveau suffit
        # pour declencher une alerte si son score est suffisamment eleve.
        score_hybride = max(
            s_seuils,          # poids 1.0 : garantie de detection
            0.7 * s_if,        # poids 0.7 : combinations anormales
            0.6 * s_lstm,      # poids 0.6 : tendances temporelles
        )
        score_hybride = float(min(1.0, max(0.0, score_hybride)))
        seuil = self.lstm_analyseur.calibreur.seuil_actuel if self.lstm_analyseur else 0.5

        # Re-entrainer LSTM periodiquement
        if self._n_analyses % 200 == 0 and self.lstm_analyseur:
            threading.Thread(target=self.lstm_analyseur.reentrainer, daemon=True).start()

        return score_hybride, seuil

    def reentrainer(self):
        if self.lstm_analyseur:
            self.lstm_analyseur.reentrainer()

    def get_stats(self) -> dict:
        stats = {
            "score_if":    round(self._dernier_score_if, 4),
            "score_lstm":  round(self._dernier_score_lstm, 4),
            "score_seuils":round(self._dernier_score_seuils, 4),
            "n_analyses":  self._n_analyses,
            "lstm_ready":  bool(self.lstm_analyseur and self.lstm_analyseur.entraine),
        }
        if self.if_analyseur:
            stats["if"] = self.if_analyseur.get_stats()
        if self.lstm_analyseur:
            stats["lstm"] = self.lstm_analyseur.get_stats()
        return stats

    def get_calibreur(self):
        return self.lstm_analyseur.calibreur if self.lstm_analyseur else CalibratorAdaptatif()