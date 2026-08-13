"""
database.py — OpsPilot Phase 3
PostgreSQL 16 + TimescaleDB — persistance complète des données

Tables :
  metrics_history    → métriques cluster toutes les 60s (hypertable TimescaleDB)
  anomalies          → incidents détectés
  alert_rules        → règles générées par le LLM
  recommendations    → recommandations avec statut (schéma créé, PAS ENCORE UTILISÉ --
                        aucune fonction n'écrit/lit cette table dans ce fichier,
                        confirmé en le relisant intégralement -- à brancher ou
                        retirer selon ce qui est décidé)
  chat_history       → historique conversations
  reports            → rapports markdown générés
  action_history     → ← AJOUT : actions de remédiation exécutées (Human-in-the-Loop,
                        voir action_executor.py). Avant cet ajout, cet historique ne
                        vivait qu'en mémoire (_action_history, une liste Python dans
                        action_executor.py) -- perdu à chaque redémarrage de l'agent.
                        Pour un outil comparé à PagerDuty/Datadog, la piste d'audit de
                        "quelle action a été exécutée, quand, avec quel résultat"
                        devrait survivre à un redémarrage, comme le reste.

Install :
  pip install psycopg2-binary --break-system-packages
  
Config .env :
  DB_HOST=192.168.138.133
  DB_PORT=5432
  DB_NAME=opspilot
  DB_USER=opspilot
  DB_PASSWORD=opspilot_secret
"""

import os
import json
import threading
import time
from datetime import datetime
from typing import Optional

try:
    import psycopg2
    import psycopg2.extras
    from psycopg2.pool import ThreadedConnectionPool
    PSYCOPG2_OK = True
except ImportError:
    PSYCOPG2_OK = False
    print("[DB] psycopg2 non installé — stockage mémoire uniquement")
    print("[DB] Installe : pip install psycopg2-binary --break-system-packages")

# ══════════════════════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════════════════════
DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "192.168.138.133"),
    "port":     int(os.getenv("DB_PORT", "5432")),
    "dbname":   os.getenv("DB_NAME", "opspilot"),
    "user":     os.getenv("DB_USER", "opspilot"),
    "password": os.getenv("DB_PASSWORD", "opspilot_secret"),
}

_pool: Optional[object] = None
DB_OK = False

# ── Fallback mémoire si PostgreSQL non disponible ──────────────────────────
_mem_metrics:         list = []
_mem_anomalies:       list = []
_mem_alert_rules:     list = []
_mem_recommendations: list = []
_mem_chat_history:    list = []
_mem_reports:         list = []
_mem_action_history:  list = []  # ← AJOUT : repli mémoire pour action_history
_mem_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════════════════════
# Connexion + initialisation
# ══════════════════════════════════════════════════════════════════════════════
def init_db() -> bool:
    """Initialise la connexion PostgreSQL et crée les tables si nécessaire."""
    global _pool, DB_OK

    if not PSYCOPG2_OK:
        print("[DB] Mode mémoire (psycopg2 non disponible)")
        return False

    try:
        _pool = ThreadedConnectionPool(1, 5, **DB_CONFIG)
        conn = _pool.getconn()
        cur  = conn.cursor()

        # Activer TimescaleDB si disponible
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;")
            conn.commit()
            TIMESCALE = True
            print("[DB] TimescaleDB activé")
        except Exception:
            conn.rollback()
            TIMESCALE = False
            print("[DB] TimescaleDB non disponible — PostgreSQL standard")

        # ── Créer les tables ─────────────────────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS metrics_history (
            time        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            noeud       TEXT NOT NULL,
            cpu_pct     FLOAT,
            ram_pct     FLOAT,
            ram_used_gb FLOAT,
            ram_total_gb FLOAT,
            disk_pct    FLOAT,
            disk_used_gb FLOAT,
            disk_total_gb FLOAT,
            statut      TEXT,
            lstm_score  FLOAT DEFAULT 0.0
        );
        """)

        # Convertir en hypertable TimescaleDB si disponible
        if TIMESCALE:
            try:
                cur.execute("""
                SELECT create_hypertable('metrics_history', 'time', 
                    if_not_exists => TRUE,
                    migrate_data => TRUE
                );
                """)
                print("[DB] metrics_history → hypertable TimescaleDB")
            except Exception as e:
                print(f"[DB] Hypertable déjà existante ou erreur: {e}")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS anomalies (
            id          SERIAL PRIMARY KEY,
            time        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            niveau      TEXT NOT NULL,
            message     TEXT NOT NULL,
            noeud       TEXT,
            vmid        INTEGER,
            score_lstm  FLOAT,
            rapport     TEXT,
            statut      TEXT DEFAULT 'OPEN',
            resolu_at   TIMESTAMPTZ
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS alert_rules (
            id          SERIAL PRIMARY KEY,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            metric      TEXT NOT NULL,
            operateur   TEXT NOT NULL,
            seuil       FLOAT NOT NULL,
            duree_min   INTEGER DEFAULT 0,
            severite    TEXT NOT NULL,
            cible       TEXT,
            titre       TEXT NOT NULL,
            description TEXT,
            action      TEXT,
            source      TEXT DEFAULT 'LLM',
            actif       BOOLEAN DEFAULT TRUE
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS recommendations (
            id          SERIAL PRIMARY KEY,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            titre       TEXT NOT NULL,
            description TEXT,
            severite    TEXT DEFAULT 'IMPORTANT',
            statut      TEXT DEFAULT 'OPEN',
            anomalie_id INTEGER REFERENCES anomalies(id),
            resolu_at   TIMESTAMPTZ
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id          SERIAL PRIMARY KEY,
            time        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            role        TEXT NOT NULL,
            content     TEXT NOT NULL,
            msg_id      TEXT UNIQUE,
            edited      BOOLEAN DEFAULT FALSE
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id          SERIAL PRIMARY KEY,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            nom         TEXT NOT NULL,
            contenu     TEXT NOT NULL,
            niveau      TEXT DEFAULT 'surveillance',
            score_lstm  FLOAT
        );
        """)

        # ← AJOUT : action_history -- actions de remédiation exécutées
        # (Human-in-the-Loop, voir action_executor.py). params en JSONB --
        # les paramètres varient par action (node/vmid/target_node/limit/
        # min_mb selon l'action_id), JSONB est le type PostgreSQL naturel
        # pour une forme de données variable comme celle-ci.
        cur.execute("""
        CREATE TABLE IF NOT EXISTS action_history (
            id          SERIAL PRIMARY KEY,
            time        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            action_id   TEXT NOT NULL,
            params      JSONB,
            success     BOOLEAN NOT NULL,
            message     TEXT
        );
        """)

        # Index pour performances
        cur.execute("CREATE INDEX IF NOT EXISTS idx_metrics_noeud ON metrics_history(noeud, time DESC);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_anomalies_statut ON anomalies(statut, time DESC);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rules_actif ON alert_rules(actif);")
        # ← AJOUT : index pour action_history, même schéma que les autres --
        # les requêtes de lecture sont toujours "les N plus récentes"
        cur.execute("CREATE INDEX IF NOT EXISTS idx_actions_time ON action_history(time DESC);")

        conn.commit()
        cur.close()
        _pool.putconn(conn)

        DB_OK = True
        print(f"[DB] ✓ PostgreSQL connecté → {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}")
        return True

    except Exception as e:
        DB_OK = False
        print(f"[DB] ✗ PostgreSQL non disponible : {e}")
        print("[DB] Mode mémoire activé (fallback)")
        return False


def get_conn():
    """Obtient une connexion du pool."""
    if _pool:
        return _pool.getconn()
    return None


def release_conn(conn):
    """Libère une connexion vers le pool."""
    if _pool and conn:
        _pool.putconn(conn)


# ══════════════════════════════════════════════════════════════════════════════
# Métriques
# ══════════════════════════════════════════════════════════════════════════════
def sauvegarder_metriques(etat: dict, lstm_score: float = 0.0):
    """Sauvegarde les métriques de chaque nœud."""
    if not etat or not etat.get("noeuds"):
        return

    if DB_OK:
        conn = get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            for n in etat.get("noeuds", []):
                cur.execute("""
                INSERT INTO metrics_history 
                    (noeud, cpu_pct, ram_pct, ram_used_gb, ram_total_gb,
                     disk_pct, disk_used_gb, disk_total_gb, statut, lstm_score)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    n.get("nom"), n.get("cpu_pct"), n.get("ram_pct"),
                    n.get("ram_used_gb"), n.get("ram_total_gb"),
                    n.get("disk_pct"), n.get("disk_used_gb"), n.get("disk_total_gb"),
                    n.get("statut"), lstm_score
                ))
            conn.commit()
            cur.close()
        except Exception as e:
            print(f"[DB] Erreur métriques: {e}")
            conn.rollback()
        finally:
            release_conn(conn)
    else:
        with _mem_lock:
            for n in etat.get("noeuds", []):
                _mem_metrics.append({
                    "time": datetime.now().isoformat(),
                    "noeud": n.get("nom"),
                    "cpu_pct": n.get("cpu_pct"),
                    "ram_pct": n.get("ram_pct"),
                    "lstm_score": lstm_score,
                })
                if len(_mem_metrics) > 10000:
                    _mem_metrics.pop(0)


def get_metriques_historique(noeud: str = None, limit: int = 60) -> list:
    """Récupère l'historique des métriques."""
    if DB_OK:
        conn = get_conn()
        if not conn:
            return []
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            if noeud:
                cur.execute("""
                SELECT * FROM metrics_history 
                WHERE noeud = %s 
                ORDER BY time DESC LIMIT %s
                """, (noeud, limit))
            else:
                cur.execute("""
                SELECT DISTINCT ON (noeud) noeud, cpu_pct, ram_pct, disk_pct, statut, time, lstm_score
                FROM metrics_history 
                ORDER BY noeud, time DESC
                """)
            rows = cur.fetchall()
            cur.close()
            return [dict(r) for r in rows]
        except Exception as e:
            print(f"[DB] Erreur get métriques: {e}")
            return []
        finally:
            release_conn(conn)
    else:
        with _mem_lock:
            if noeud:
                return [m for m in _mem_metrics if m.get("noeud") == noeud][-limit:]
            return _mem_metrics[-limit:]


# ══════════════════════════════════════════════════════════════════════════════
# Anomalies
# ══════════════════════════════════════════════════════════════════════════════
def sauvegarder_anomalie(niveau: str, message: str, noeud: str = None,
                          vmid: int = None, score: float = 0.0, rapport: str = None) -> int:
    """Sauvegarde une anomalie détectée. Retourne l'ID."""
    if DB_OK:
        conn = get_conn()
        if not conn:
            return -1
        try:
            cur = conn.cursor()
            cur.execute("""
            INSERT INTO anomalies (niveau, message, noeud, vmid, score_lstm, rapport)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """, (niveau, message, noeud, vmid, score, rapport))
            anomalie_id = cur.fetchone()[0]
            conn.commit()
            cur.close()
            return anomalie_id
        except Exception as e:
            print(f"[DB] Erreur anomalie: {e}")
            conn.rollback()
            return -1
        finally:
            release_conn(conn)
    else:
        with _mem_lock:
            aid = len(_mem_anomalies) + 1
            _mem_anomalies.append({
                "id": aid, "time": datetime.now().isoformat(),
                "niveau": niveau, "message": message,
                "noeud": noeud, "score_lstm": score,
                "rapport": rapport, "statut": "OPEN"
            })
            return aid


def get_anomalies(limit: int = 50, statut: str = None) -> list:
    """Récupère les anomalies."""
    if DB_OK:
        conn = get_conn()
        if not conn:
            return []
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            if statut:
                cur.execute("SELECT * FROM anomalies WHERE statut=%s ORDER BY time DESC LIMIT %s", (statut, limit))
            else:
                cur.execute("SELECT * FROM anomalies ORDER BY time DESC LIMIT %s", (limit,))
            rows = cur.fetchall()
            cur.close()
            return [dict(r) for r in rows]
        except Exception as e:
            print(f"[DB] Erreur get anomalies: {e}")
            return []
        finally:
            release_conn(conn)
    else:
        with _mem_lock:
            data = _mem_anomalies if not statut else [a for a in _mem_anomalies if a.get("statut") == statut]
            return list(reversed(data[-limit:]))


def resoudre_anomalie(anomalie_id: int):
    """Marque une anomalie comme résolue."""
    if DB_OK:
        conn = get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            cur.execute("""
            UPDATE anomalies SET statut='RESOLVED', resolu_at=NOW() WHERE id=%s
            """, (anomalie_id,))
            conn.commit()
            cur.close()
        except Exception as e:
            print(f"[DB] Erreur résolution: {e}")
        finally:
            release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════════
# Règles d'alerte
# ══════════════════════════════════════════════════════════════════════════════
def sauvegarder_regles(regles: list):
    """Sauvegarde les règles générées par le LLM (remplace les existantes)."""
    if not regles:
        return

    if DB_OK:
        conn = get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            # Désactiver les anciennes règles LLM
            cur.execute("UPDATE alert_rules SET actif=FALSE WHERE source='LLM'")
            # Insérer les nouvelles
            for r in regles:
                cur.execute("""
                INSERT INTO alert_rules 
                    (metric, operateur, seuil, duree_min, severite, cible, titre, description, action, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'LLM')
                """, (
                    r.get("metric", ""), r.get("operateur", ">"),
                    float(r.get("seuil", 80)), int(r.get("duree_min", 0)),
                    r.get("severite", "IMPORTANT"), r.get("cible", ""),
                    r.get("titre", ""), r.get("description", ""),
                    r.get("action", "")
                ))
            conn.commit()
            cur.close()
            print(f"[DB] {len(regles)} règles sauvegardées")
        except Exception as e:
            print(f"[DB] Erreur règles: {e}")
            conn.rollback()
        finally:
            release_conn(conn)
    else:
        with _mem_lock:
            _mem_alert_rules.clear()
            _mem_alert_rules.extend(regles)


def get_regles_actives() -> list:
    """Récupère les règles d'alerte actives."""
    if DB_OK:
        conn = get_conn()
        if not conn:
            return []
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM alert_rules WHERE actif=TRUE ORDER BY severite, created_at DESC")
            rows = cur.fetchall()
            cur.close()
            return [dict(r) for r in rows]
        except Exception as e:
            print(f"[DB] Erreur get règles: {e}")
            return []
        finally:
            release_conn(conn)
    else:
        with _mem_lock:
            return list(_mem_alert_rules)


# ══════════════════════════════════════════════════════════════════════════════
# Chat history
# ══════════════════════════════════════════════════════════════════════════════
def sauvegarder_message(role: str, content: str, msg_id: str, edited: bool = False):
    """Sauvegarde un message de conversation."""
    if DB_OK:
        conn = get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            cur.execute("""
            INSERT INTO chat_history (role, content, msg_id, edited)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (msg_id) DO UPDATE SET content=%s, edited=%s
            """, (role, content, msg_id, edited, content, edited))
            conn.commit()
            cur.close()
        except Exception as e:
            print(f"[DB] Erreur chat: {e}")
        finally:
            release_conn(conn)


def get_chat_history(limit: int = 100) -> list:
    """Récupère l'historique de conversation."""
    if DB_OK:
        conn = get_conn()
        if not conn:
            return []
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
            SELECT * FROM (
                SELECT * FROM chat_history ORDER BY time DESC LIMIT %s
            ) sub ORDER BY time ASC
            """, (limit,))
            rows = cur.fetchall()
            cur.close()
            return [dict(r) for r in rows]
        except Exception as e:
            print(f"[DB] Erreur get chat: {e}")
            return []
        finally:
            release_conn(conn)
    return []


def supprimer_chat_history():
    """Efface tout l'historique de conversation."""
    if DB_OK:
        conn = get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM chat_history")
            conn.commit()
            cur.close()
        except Exception as e:
            print(f"[DB] Erreur suppression chat: {e}")
        finally:
            release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════════
# Rapports
# ══════════════════════════════════════════════════════════════════════════════
def sauvegarder_rapport_db(nom: str, contenu: str, niveau: str, score: float):
    """Sauvegarde un rapport en base."""
    if DB_OK:
        conn = get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            cur.execute("""
            INSERT INTO reports (nom, contenu, niveau, score_lstm)
            VALUES (%s, %s, %s, %s)
            """, (nom, contenu, niveau, score))
            conn.commit()
            cur.close()
        except Exception as e:
            print(f"[DB] Erreur rapport: {e}")
        finally:
            release_conn(conn)


def get_rapports_db(limit: int = 30) -> list:
    """Récupère les rapports depuis la DB."""
    if DB_OK:
        conn = get_conn()
        if not conn:
            return []
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT nom, niveau, score_lstm, created_at FROM reports ORDER BY created_at DESC LIMIT %s", (limit,))
            rows = cur.fetchall()
            cur.close()
            return [dict(r) for r in rows]
        except Exception as e:
            print(f"[DB] Erreur get rapports: {e}")
            return []
        finally:
            release_conn(conn)
    return []


# ══════════════════════════════════════════════════════════════════════════════
# Historique des actions de remédiation (Human-in-the-Loop)
# ══════════════════════════════════════════════════════════════════════════════
# ← AJOUT : avant, action_executor.py gardait cet historique UNIQUEMENT
# dans _action_history (liste Python en mémoire) -- perdu à chaque
# redémarrage de l'agent. Même schéma try/except + repli mémoire que le
# reste de ce fichier -- rien de nouveau conceptuellement, juste appliqué
# à cette donnée précise qui ne l'avait pas encore.
def sauvegarder_action(action_id: str, params: dict, success: bool, message: str):
    """Sauvegarde une action de remédiation exécutée (Human-in-the-Loop)."""
    if DB_OK:
        conn = get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            cur.execute("""
            INSERT INTO action_history (action_id, params, success, message)
            VALUES (%s, %s, %s, %s)
            """, (action_id, psycopg2.extras.Json(params or {}), success, message))
            conn.commit()
            cur.close()
        except Exception as e:
            print(f"[DB] Erreur action: {e}")
            conn.rollback()
        finally:
            release_conn(conn)
    else:
        with _mem_lock:
            _mem_action_history.append({
                "time":      datetime.now().isoformat(),
                "action_id": action_id,
                "params":    params,
                "success":   success,
                "message":   message,
            })


def get_action_history_db(limit: int = 50) -> list:
    """Récupère l'historique des actions de remédiation exécutées."""
    if DB_OK:
        conn = get_conn()
        if not conn:
            return []
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM action_history ORDER BY time DESC LIMIT %s", (limit,))
            rows = cur.fetchall()
            cur.close()
            return [dict(r) for r in rows]
        except Exception as e:
            print(f"[DB] Erreur get actions: {e}")
            return []
        finally:
            release_conn(conn)
    else:
        with _mem_lock:
            return list(reversed(_mem_action_history[-limit:]))


# ══════════════════════════════════════════════════════════════════════════════
# Stats & Dashboard
# ══════════════════════════════════════════════════════════════════════════════
def get_stats_db() -> dict:
    """Statistiques globales pour le dashboard."""
    if not DB_OK:
        return {}

    conn = get_conn()
    if not conn:
        return {}
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Métriques moyennes dernières 24h
        cur.execute("""
        SELECT noeud,
               ROUND(AVG(cpu_pct)::numeric, 1)  as avg_cpu,
               ROUND(MAX(cpu_pct)::numeric, 1)  as max_cpu,
               ROUND(AVG(ram_pct)::numeric, 1)  as avg_ram,
               ROUND(MAX(ram_pct)::numeric, 1)  as max_ram
        FROM metrics_history
        WHERE time > NOW() - INTERVAL '24 hours'
        GROUP BY noeud
        """)
        stats_noeuds = cur.fetchall()

        # Nombre d'anomalies par sévérité
        cur.execute("""
        SELECT niveau, COUNT(*) as count
        FROM anomalies
        WHERE time > NOW() - INTERVAL '24 hours'
        GROUP BY niveau
        """)
        stats_anomalies = cur.fetchall()

        # Total messages chat
        cur.execute("SELECT COUNT(*) as total FROM chat_history")
        nb_messages = cur.fetchone()

        cur.close()
        return {
            "noeuds_stats": [dict(r) for r in stats_noeuds],
            "anomalies_24h": [dict(r) for r in stats_anomalies],
            "nb_messages_chat": nb_messages["total"] if nb_messages else 0,
        }
    except Exception as e:
        print(f"[DB] Erreur stats: {e}")
        return {}
    finally:
        release_conn(conn)