"""
ml_analyser.py — Détection d'anomalies hybride
Combine deux modèles en parallèle :

  1. Isolation Forest  → fonctionne DÈS LE DÉMARRAGE, sans entraînement
                         Détecte les points aberrants statistiquement
                         
  2. LSTM Autoencoder  → apprend la SÉQUENCE temporelle normale
                         Détecte les patterns anormaux sur le temps
                         Devient précis après quelques heures de données

Score final = max(score_if, score_lstm) pondéré
"""

import json
import os
import time
import threading
import numpy as np
from pathlib import Path
from datetime import datetime
from collections import deque

# ── Imports optionnels ────────────────────────────────────────────────────────
try:
    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import StandardScaler
    IF_OK = True
except ImportError:
    IF_OK = False
    print("[ML] sklearn non installé — pip install scikit-learn --break-system-packages")

try:
    import tensorflow as tf
    TF_OK = True
except ImportError:
    TF_OK = False
    print("[ML] TensorFlow non installé — LSTM désactivé")

MODELE_PATH  = Path("lstm_ae_model.pt")
STATS_PATH   = Path("normalizer_stats.json")
HISTORY_SIZE = 500   # Points gardés en mémoire pour l'entraînement
SEQ_LEN      = 20    # Longueur séquence LSTM
N_FEATURES   = 14    # CPU, RAM, Disk, Swap, IOWait, DiskReadIOPS, DiskWriteIOPS, DiskReadLat, DiskWriteLat, Net_in, Net_out, NetErrors, NetDrops, VMs

# ══════════════════════════════════════════════════════════════════════════════
# Modèle 1 — Isolation Forest (RAPIDE, fonctionne dès le démarrage)
# ══════════════════════════════════════════════════════════════════════════════
class IsolationForestAnalyseur:
    """
    Détecte les anomalies IMMÉDIATEMENT sans période d'apprentissage.
    
    Principe : les anomalies sont des points "isolés" dans l'espace des données.
    Un point normal est entouré d'autres points similaires.
    Un point anormal est seul dans un espace différent.
    
    Score retourné :
      0.0  → parfaitement normal
      0.5  → suspect
      1.0  → anomalie confirmée
    """
    
    def __init__(self, contamination: float = 0.05):
        """
        contamination : fraction attendue d'anomalies dans les données
                        0.05 = on s'attend à 5% d'anomalies
        """
        self.contamination  = contamination
        self.model          = None
        self.scaler         = StandardScaler() if IF_OK else None
        self.historique     = deque(maxlen=HISTORY_SIZE)
        self.entraine       = False
        self.n_entraînements = 0
        self._lock          = threading.Lock()
        
        # Entraîner avec des données synthétiques pour démarrer immédiatement
        self._entraînement_initial()
    
    def _entraînement_initial(self):
        """
        Crée un modèle avec des données synthétiques réalistes pour Proxmox.
        Permet un scoring IMMÉDIAT dès le premier vrai point de données.
        """
        if not IF_OK:
            return
        
        np.random.seed(42)
        n = 300  # 300 points synthétiques
        
        # Données normales Proxmox — 14 features
        normales = np.column_stack([
            np.random.normal(25, 15, n).clip(0, 100),    # 1.  CPU %
            np.random.normal(55, 15, n).clip(0, 100),    # 2.  RAM %
            np.random.normal(45, 10, n).clip(0, 100),    # 3.  Disk %
            np.random.normal(5,  5,  n).clip(0, 100),    # 4.  Swap % (normal = faible)
            np.random.normal(3,  3,  n).clip(0, 100),    # 5.  I/O wait % (normal < 10%)
            np.random.normal(200,100, n).clip(0, 10000), # 6.  Read IOPS
            np.random.normal(150,80,  n).clip(0, 10000), # 7.  Write IOPS
            np.random.normal(2,  1,   n).clip(0, 500),   # 8.  Read latency ms (normal < 5ms)
            np.random.normal(3,  2,   n).clip(0, 500),   # 9.  Write latency ms (normal < 10ms)
            np.random.normal(1.5,1,   n).clip(0, 1000),  # 10. Net in MB/s
            np.random.normal(0.8,0.5, n).clip(0, 1000),  # 11. Net out MB/s
            np.zeros(n),                                  # 12. Net errors (normal = 0)
            np.zeros(n),                                  # 13. Net drops (normal = 0)
            np.random.normal(2,  1,   n).clip(0, 20),    # 14. VMs running
        ])

        # Anomalies synthétiques — 14 features
        n_anom = 15
        anomalies = np.column_stack([
            np.random.uniform(85, 100, n_anom),   # 1.  CPU critique
            np.random.uniform(88, 100, n_anom),   # 2.  RAM critique
            np.random.uniform(92, 100, n_anom),   # 3.  Disk plein
            np.random.uniform(60, 100, n_anom),   # 4.  Swap élevé = RAM saturée
            np.random.uniform(30, 80,  n_anom),   # 5.  I/O wait élevé = disque saturé
            np.random.uniform(5000,10000, n_anom), # 6.  IOPS lecture très élevé
            np.random.uniform(5000,10000, n_anom), # 7.  IOPS écriture très élevé
            np.random.uniform(50, 500, n_anom),   # 8.  Latence lecture élevée
            np.random.uniform(50, 500, n_anom),   # 9.  Latence écriture élevée
            np.random.uniform(500,1000, n_anom),  # 10. Réseau saturé
            np.random.uniform(300,1000, n_anom),  # 11. Réseau sortant saturé
            np.random.uniform(10, 100, n_anom),   # 12. Erreurs réseau
            np.random.uniform(10, 100, n_anom),   # 13. Paquets perdus
            np.random.uniform(0,  1,   n_anom),   # 14. VMs tombées
        ])
        
        X = np.vstack([normales, anomalies])
        
        self.model = IsolationForest(
            contamination=self.contamination,
            n_estimators=200,     # Plus d'arbres = plus précis
            max_samples='auto',
            random_state=42,
            n_jobs=-1,            # Utiliser tous les CPU
        )
        
        self.scaler.fit(X)
        self.model.fit(self.scaler.transform(X))
        self.entraine = True
        print(f"[IF] Isolation Forest initialisé avec {n} points synthétiques — prêt immédiatement")
    
    def _vecteur(self, metriques: dict) -> np.ndarray:
        """Convertit les métriques en vecteur numpy — 14 features."""
        return np.array([[
            metriques.get("cpu_pct",             0),           # 1. CPU %
            metriques.get("ram_pct",             0),           # 2. RAM %
            metriques.get("disk_pct",            0),           # 3. Disk %
            metriques.get("swap_pct",            0),           # 4. Swap %  ← NOUVEAU
            metriques.get("cpu_iowait_pct",      0),           # 5. I/O wait CPU %  ← NOUVEAU
            min(metriques.get("disk_read_iops",  0), 10000),   # 6. Read IOPS  ← NOUVEAU
            min(metriques.get("disk_write_iops", 0), 10000),   # 7. Write IOPS  ← NOUVEAU
            min(metriques.get("disk_read_latency_ms",  0), 500), # 8. Read latency ms  ← NOUVEAU
            min(metriques.get("disk_write_latency_ms", 0), 500), # 9. Write latency ms  ← NOUVEAU
            min(metriques.get("net_in_mbps",     0), 1000),    # 10. Net in MB/s
            min(metriques.get("net_out_mbps",    0), 1000),    # 11. Net out MB/s
            min(metriques.get("net_errors_in",   0) +
                metriques.get("net_errors_out",  0), 100),     # 12. Net errors  ← NOUVEAU
            min(metriques.get("net_drop_in",     0) +
                metriques.get("net_drop_out",    0), 100),     # 13. Net drops  ← NOUVEAU
            metriques.get("vms_running",         0),           # 14. VMs running
        ]])
    
    def analyser(self, metriques: dict) -> float:
        """
        Analyse un point de métriques et retourne un score d'anomalie [0, 1].
        
        Isolation Forest retourne :
          1  → normal (inlier)
         -1  → anomalie (outlier)
        
        On convertit en score [0, 1].
        """
        if not IF_OK or not self.entraine:
            return 0.0
        
        try:
            X = self._vecteur(metriques)
            X_scaled = self.scaler.transform(X)
            
            # Score de décision : plus négatif = plus anormal
            decision_score = self.model.decision_function(X_scaled)[0]
            prediction     = self.model.predict(X_scaled)[0]
            
            # Convertir en [0, 1]
            # decision_score est typiquement entre -0.5 et 0.5
            # On normalise : 0 = très normal, 1 = très anormal
            score = max(0.0, min(1.0, (-decision_score + 0.1) * 2))
            
            # Ajouter à l'historique pour réentraînement progressif
            with self._lock:
                self.historique.append(X[0])
            
            # Réentraîner avec les vraies données toutes les 100 observations
            if len(self.historique) % 100 == 0 and len(self.historique) >= 100:
                self._reentrainer()
            
            return float(score)
            
        except Exception as e:
            print(f"[IF] Erreur analyse: {e}")
            return 0.0
    
    def _reentrainer(self):
        """Réentraîne avec les vraies données observées."""
        try:
            with self._lock:
                X = np.array(list(self.historique))
            
            if len(X) < 50:
                return
            
            self.scaler.fit(X)
            self.model.fit(self.scaler.transform(X))
            self.n_entraînements += 1
            print(f"[IF] Réentraînement #{self.n_entraînements} avec {len(X)} points réels")
        except Exception as e:
            print(f"[IF] Erreur réentraînement: {e}")
    
    def get_stats(self) -> dict:
        return {
            "modele":          "IsolationForest",
            "entraine":        self.entraine,
            "n_observations":  len(self.historique),
            "n_retrainings":   self.n_entraînements,
            "contamination":   self.contamination,
        }


# ══════════════════════════════════════════════════════════════════════════════
# Modèle 2 — LSTM Autoencoder (PRÉCIS après apprentissage)
# ══════════════════════════════════════════════════════════════════════════════
class CalibratorAdaptatif:
    """Calcule le seuil dynamiquement au 95e percentile."""
    
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
    """Détecte si le comportement du cluster change durablement."""
    
    def __init__(self):
        self.ewma_courte    = None
        self.ewma_longue    = None
        self.compteur_drift = 0
        self.drift_detecte  = False
        self.ALPHA_COURT    = 0.1
        self.ALPHA_LONG     = 0.01
        self.SEUIL_DRIFT    = 2.0
        self.DUREE_DRIFT    = 20
    
    def analyser(self, valeur: float) -> bool:
        if self.ewma_courte is None:
            self.ewma_courte = valeur
            self.ewma_longue = valeur
            return False
        
        self.ewma_courte = self.ALPHA_COURT * valeur + (1 - self.ALPHA_COURT) * self.ewma_courte
        self.ewma_longue = self.ALPHA_LONG  * valeur + (1 - self.ALPHA_LONG)  * self.ewma_longue
        
        ratio = self.ewma_courte / max(self.ewma_longue, 0.001)
        
        if ratio > self.SEUIL_DRIFT:
            self.compteur_drift += 1
        else:
            self.compteur_drift = max(0, self.compteur_drift - 1)
        
        self.drift_detecte = self.compteur_drift >= self.DUREE_DRIFT
        return self.drift_detecte


class LSTMAnalyseur:
    """LSTM Autoencoder — apprend les patterns normaux sur le temps."""
    
    def __init__(self, seq_len: int = SEQ_LEN, n_features: int = N_FEATURES):
        self.seq_len      = seq_len
        self.n_features   = n_features
        self.model        = None
        self.historique   = deque(maxlen=HISTORY_SIZE)
        self.entraine     = False
        self.calibreur    = CalibratorAdaptatif()
        self.drift        = DetecteurDrift()
        self._lock        = threading.Lock()
        
        # Charger ou créer le modèle
        if TF_OK:
            self._charger_ou_creer()
    
    def _creer_modele(self):
        """Architecture LSTM Autoencoder."""
        if not TF_OK:
            return None
        
        inp = tf.keras.Input(shape=(self.seq_len, self.n_features))
        # Encodeur
        x = tf.keras.layers.LSTM(32, activation='tanh', return_sequences=False)(inp)
        x = tf.keras.layers.RepeatVector(self.seq_len)(x)
        # Décodeur
        x = tf.keras.layers.LSTM(32, activation='tanh', return_sequences=True)(x)
        out = tf.keras.layers.TimeDistributed(tf.keras.layers.Dense(self.n_features))(x)
        
        model = tf.keras.Model(inp, out)
        model.compile(optimizer='adam', loss='mse')
        return model
    
    def _charger_ou_creer(self):
        """Charger le modèle sauvegardé ou en créer un nouveau."""
        try:
            if MODELE_PATH.exists() and MODELE_PATH.stat().st_size > 1000:
                self.model = tf.keras.models.load_model(str(MODELE_PATH))
                self.entraine = True
                print(f"[LSTM] Modèle chargé depuis {MODELE_PATH}")
            else:
                self.model = self._creer_modele()
                print("[LSTM] Nouveau modèle créé — entraînement nécessaire")
        except Exception as e:
            print(f"[LSTM] Erreur chargement: {e}")
            self.model = self._creer_modele()
    
    def vecteur(self, metriques: dict) -> np.ndarray:
        """Convertit les métriques en vecteur normalisé [0,1] — 14 features."""
        return np.array([
            metriques.get("cpu_pct",             0) / 100,          # 1. CPU
            metriques.get("ram_pct",             0) / 100,          # 2. RAM
            metriques.get("disk_pct",            0) / 100,          # 3. Disk
            metriques.get("swap_pct",            0) / 100,          # 4. Swap
            metriques.get("cpu_iowait_pct",      0) / 100,          # 5. I/O wait
            min(metriques.get("disk_read_iops",  0), 10000) / 10000, # 6. Read IOPS
            min(metriques.get("disk_write_iops", 0), 10000) / 10000, # 7. Write IOPS
            min(metriques.get("disk_read_latency_ms",  0), 500) / 500, # 8. Read lat
            min(metriques.get("disk_write_latency_ms", 0), 500) / 500, # 9. Write lat
            min(metriques.get("net_in_mbps",     0), 1000) / 1000,  # 10. Net in
            min(metriques.get("net_out_mbps",    0), 1000) / 1000,  # 11. Net out
            min(metriques.get("net_errors_in",   0) +
                metriques.get("net_errors_out",  0), 100)  / 100,   # 12. Net errors
            min(metriques.get("net_drop_in",     0) +
                metriques.get("net_drop_out",    0), 100)  / 100,   # 13. Net drops
            min(metriques.get("vms_running",     0), 20)   / 20,    # 14. VMs
        ], dtype=np.float32)
    
    def analyser(self, metriques: dict) -> tuple[float, bool]:
        """
        Retourne (score_anomalie, drift_detecte).
        Score entre 0 et 1. Nécessite SEQ_LEN points minimum.
        """
        v = self.vecteur(metriques)
        
        with self._lock:
            self.historique.append(v)
        
        # Pas encore assez de données
        if len(self.historique) < self.seq_len or not self.entraine or not TF_OK:
            return 0.0, False
        
        try:
            # Construire la séquence
            seq = np.array(list(self.historique)[-self.seq_len:])
            X   = seq.reshape(1, self.seq_len, self.n_features)
            
            # Reconstruction
            X_pred = self.model.predict(X, verbose=0)
            erreur = float(np.mean(np.abs(X - X_pred)))
            
            self.calibreur.mettre_a_jour(erreur)
            drift = self.drift.analyser(erreur)
            score = self.calibreur.normaliser_score(erreur)
            
            return float(score), drift
            
        except Exception as e:
            print(f"[LSTM] Erreur analyse: {e}")
            return 0.0, False
    
    def reentrainer(self):
        """Entraîne le modèle sur les données accumulées."""
        if not TF_OK or self.model is None:
            return
        
        with self._lock:
            data = np.array(list(self.historique))
        
        if len(data) < self.seq_len + 10:
            print(f"[LSTM] Pas assez de données ({len(data)}/{self.seq_len + 10})")
            return
        
        # Construire les séquences d'entraînement
        sequences = []
        for i in range(len(data) - self.seq_len):
            sequences.append(data[i:i + self.seq_len])
        
        X = np.array(sequences)
        
        print(f"[LSTM] Entraînement sur {len(X)} séquences...")
        self.model.fit(X, X, epochs=10, batch_size=32, verbose=0,
                      validation_split=0.1,
                      callbacks=[tf.keras.callbacks.EarlyStopping(patience=3)])
        
        try:
            self.model.save(str(MODELE_PATH))
            print(f"[LSTM] Modèle sauvegardé → {MODELE_PATH}")
        except Exception as e:
            print(f"[LSTM] Erreur sauvegarde: {e}")
        
        self.entraine = True
    
    def get_stats(self) -> dict:
        return {
            "modele":        "LSTM-Autoencoder",
            "entraine":      self.entraine,
            "n_observations": len(self.historique),
            "seuil_adaptatif": self.calibreur.seuil_actuel,
            "drift_detecte": self.drift.drift_detecte,
        }


# ══════════════════════════════════════════════════════════════════════════════
# Orchestrateur Hybride — combine les deux modèles
# ══════════════════════════════════════════════════════════════════════════════
class MLAnalyseur:
    """
    Combine Isolation Forest + LSTM Autoencoder.
    
    Stratégie de score hybride :
    
      Phase 1 (peu de données) :
        → Score = score_IF uniquement
        → IF fonctionne immédiatement
    
      Phase 2 (LSTM entraîné) :
        → Score = 0.4 * score_IF + 0.6 * score_LSTM
        → LSTM plus précis sur les séquences temporelles
        → IF reste utile pour les pics soudains
    
      Drift détecté :
        → Réentraîner le LSTM automatiquement
    """
    
    def __init__(self):
        print("[ML] Initialisation du moteur de détection hybride...")
        
        self.if_analyseur   = IsolationForestAnalyseur() if IF_OK else None
        self.lstm_analyseur = LSTMAnalyseur() if TF_OK else None
        self.calibreur      = self.lstm_analyseur.calibreur if self.lstm_analyseur else CalibratorAdaptatif()
        
        self._dernier_score_if   = 0.0
        self._dernier_score_lstm = 0.0
        self._n_analyses         = 0
        
        print(f"[ML] IF: {'✓ actif' if self.if_analyseur else '✗ désactivé'}")
        print(f"[ML] LSTM: {'✓ actif' if self.lstm_analyseur else '✗ désactivé'}")
    
    def analyser(self, metriques: dict) -> tuple[float, float]:
        """
        Analyse les métriques avec les deux modèles.
        
        Returns:
            (score_hybride, seuil)
            score entre 0.0 (normal) et 1.0 (très anormal)
        """
        self._n_analyses += 1
        score_if   = 0.0
        score_lstm = 0.0
        drift      = False
        
        # ── Isolation Forest ─────────────────────────────────────────────────
        if self.if_analyseur:
            score_if = self.if_analyseur.analyser(metriques)
            self._dernier_score_if = score_if
        
        # ── LSTM ─────────────────────────────────────────────────────────────
        if self.lstm_analyseur:
            score_lstm, drift = self.lstm_analyseur.analyser(metriques)
            self._dernier_score_lstm = score_lstm
            
            # Drift → réentraîner le LSTM
            if drift:
                print("[ML] Drift détecté → réentraînement LSTM")
                threading.Thread(target=self.lstm_analyseur.reentrainer, daemon=True).start()
        
        # ── Score hybride ─────────────────────────────────────────────────────
        lstm_pret = self.lstm_analyseur and self.lstm_analyseur.entraine and score_lstm > 0
        
        if lstm_pret:
            # Les deux modèles disponibles → pondération
            score_hybride = 0.4 * score_if + 0.6 * score_lstm
        else:
            # LSTM pas encore prêt → IF uniquement
            score_hybride = score_if
        
        score_hybride = float(min(1.0, max(0.0, score_hybride)))
        seuil         = self.calibreur.seuil_actuel if self.lstm_analyseur else 0.5
        
        # Réentraîner LSTM périodiquement (toutes les 200 analyses)
        if self._n_analyses % 200 == 0 and self.lstm_analyseur:
            threading.Thread(target=self.lstm_analyseur.reentrainer, daemon=True).start()
        
        return score_hybride, seuil
    
    def reentrainer(self):
        """Réentraîne le LSTM (appelé par le thread surveillance)."""
        if self.lstm_analyseur:
            self.lstm_analyseur.reentrainer()
    
    def get_stats(self) -> dict:
        """Retourne les statistiques des deux modèles."""
        stats = {
            "score_if":    round(self._dernier_score_if, 4),
            "score_lstm":  round(self._dernier_score_lstm, 4),
            "n_analyses":  self._n_analyses,
            "lstm_ready":  bool(self.lstm_analyseur and self.lstm_analyseur.entraine),
        }
        if self.if_analyseur:
            stats["if"] = self.if_analyseur.get_stats()
        if self.lstm_analyseur:
            stats["lstm"] = self.lstm_analyseur.get_stats()
        return stats
    
    # Compatibilité avec l'ancien code
    @property
    def calibreur(self):
        return self.lstm_analyseur.calibreur if self.lstm_analyseur else CalibratorAdaptatif()
    
    @calibreur.setter
    def calibreur(self, val):
        self._calibreur_override = val