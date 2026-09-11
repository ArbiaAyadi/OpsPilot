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
    # ← CORRIGÉ (exposition réelle) : la valeur par défaut était le mot
    # de passe RÉEL de la base. Ce dépôt étant public, il était lisible
    # par n'importe qui sur GitHub -- une valeur par défaut n'est pas un
    # secret, c'est du code source. Un identifiant ne doit JAMAIS avoir
    # de valeur par défaut : mieux vaut un démarrage qui échoue avec un
    # message clair qu'un démarrage silencieux avec un mot de passe
    # publié. Même principe que PROXMOX_TOKEN_SECRET et SSH_PASSWORD,
    # déjà à "" ailleurs dans ce projet -- cette ligne était la seule
    # exception.
    "password": os.getenv("DB_PASSWORD", ""),
    # ← AJOUT : sans timeout explicite, une tentative de connexion vers un
    # hôte injoignable (coupure réseau, VM éteinte...) peut rester bloquée
    # bien plus longtemps que ça avant d'échouer (dépend du système) --
    # 5s cohérent avec les timeouts déjà utilisés ailleurs dans le projet
    # pour Proxmox. Combiné au correctif de get_conn() plus bas : échoue
    # vite et proprement plutôt que de laisser une requête pendre.
    "connect_timeout": int(os.getenv("DB_CONNECT_TIMEOUT_S", "5")),
}

# ← AJOUT : sans mot de passe, la connexion échouera avec une erreur
# d'authentification peu explicite ("password authentication failed"),
# qui oriente vers un mauvais mot de passe alors qu'il est simplement
# absent. Ce message le dit d'emblée, au démarrage.
if not DB_CONFIG["password"]:
    print("[DB] ⚠ DB_PASSWORD absent de .env — la connexion PostgreSQL va echouer")
    print("[DB]   Ajoute : DB_PASSWORD=<mot_de_passe> dans ton fichier .env")

_pool: Optional[object] = None
DB_OK = False


class BaseDeDonneesIndisponible(Exception):
    """← AJOUT : levée par get_utilisateur_par_email() quand la connexion
    à la base échoue réellement -- distincte d'un retour None qui signifie
    "requête exécutée avec succès, aucune ligne correspondante". Sans
    cette distinction, une panne DB passagère pendant une tentative de
    connexion produisait EXACTEMENT le même message "Invalid email or
    password" qu'un mot de passe réellement faux (voir auth_routes.py,
    `if not user: ... erreur_generique` traitait les deux cas pareil) --
    la personne n'avait alors aucun moyen de savoir que son mot de passe
    était en fait correct et que c'était la base qui était temporairement
    injoignable. N'affecte QUE get_utilisateur_par_email() pour l'instant
    -- portée volontairement limitée au point d'entrée concerné plutôt
    que changé partout dans ce fichier d'un coup."""
    pass

# ── Fallback mémoire si PostgreSQL non disponible ──────────────────────────
_mem_metrics:         list = []
_mem_anomalies:       list = []
_mem_alert_rules:     list = []
_mem_recommendations: list = []
_mem_chat_history:    list = []
_mem_reports:         list = []
_mem_action_history:  list = []  # ← AJOUT : repli mémoire pour action_history
# ← AJOUT : repli mémoire pour les échantillons de features ML (voir plus
# bas, sauvegarder_echantillon_ml/get_echantillons_ml_propres) -- même
# principe que le reste du fichier.
_mem_ml_samples:      list = []
_mem_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════════════════════
# Connexion + initialisation
# ══════════════════════════════════════════════════════════════════════════════
# ← AJOUT : chaque instruction DDL (CREATE TABLE/INDEX/EXTENSION) passe
# maintenant par ce helper plutôt que par un cur.execute() nu. Avant, une
# seule erreur de permission sur UNE table (ex: "must be owner of table
# metrics_history" sur l'index idx_metrics_noeud, table créée par un autre
# rôle par le passé) faisait planter TOUT init_db() -- y compris les 6
# autres tables qui, elles, auraient très bien pu être créées. Chaque appel
# committe ou rollback pour lui-même : l'échec de l'un n'empêche plus les
# suivants de s'exécuter sur la même connexion. Vérifié avec un vrai
# PostgreSQL local reproduisant exactement ce scénario (table possédée par
# un autre rôle) : avant ce correctif, seule metrics_history existait après
# le crash (les autres tables, pourtant créées sans erreur, n'étaient
# jamais committées) ; après, les 3 tables testées persistent et sont
# effectivement utilisables (INSERT confirmé), malgré l'échec de l'index
# sur metrics_history qui, lui, reste un vrai problème de permission à
# corriger côté base (voir plus bas).
def _exec_safe(cur, conn, sql: str, label: str) -> bool:
    """Exécute une instruction DDL isolément. Retourne True si elle a réussi."""
    try:
        cur.execute(sql)
        conn.commit()
        return True
    except Exception as e:
        _safe_rollback(conn)
        print(f"[DB] ⚠ {label} : {e}")
        return False


def init_db() -> bool:
    """Initialise la connexion PostgreSQL et crée les tables si nécessaire."""
    global _pool, DB_OK

    if not PSYCOPG2_OK:
        print("[DB] Mode mémoire (psycopg2 non disponible)")
        return False

    conn = None
    try:
        _pool = ThreadedConnectionPool(1, 5, **DB_CONFIG)
        conn = _pool.getconn()
        cur  = conn.cursor()

        # Activer TimescaleDB si disponible
        TIMESCALE = _exec_safe(cur, conn, "CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;", "extension timescaledb")
        print("[DB] TimescaleDB activé" if TIMESCALE else "[DB] TimescaleDB non disponible — PostgreSQL standard")

        # ── Créer les tables ─────────────────────────────────────────────────
        _exec_safe(cur, conn, """
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
        """, "table metrics_history")

        # Convertir en hypertable TimescaleDB si disponible
        if TIMESCALE:
            if _exec_safe(cur, conn, """
                SELECT create_hypertable('metrics_history', 'time', 
                    if_not_exists => TRUE,
                    migrate_data => TRUE
                );
                """, "hypertable metrics_history"):
                print("[DB] metrics_history → hypertable TimescaleDB")

        _exec_safe(cur, conn, """
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
        """, "table anomalies")

        _exec_safe(cur, conn, """
        CREATE TABLE IF NOT EXISTS alert_rules (
            id          SERIAL PRIMARY KEY,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            metric      TEXT NOT NULL,
            operateur   TEXT NOT NULL,
            seuil       FLOAT,
            duree_min   INTEGER DEFAULT 0,
            severite    TEXT NOT NULL,
            cible       TEXT,
            titre       TEXT NOT NULL,
            description TEXT,
            action      TEXT,
            source      TEXT DEFAULT 'LLM',
            actif       BOOLEAN DEFAULT TRUE
        );
        """, "table alert_rules")
        # ← CORRIGÉ : seuil n'est plus NOT NULL -- certaines règles
        # (vm.status="stopped", cluster.quorum="lost") ont un seuil
        # textuel par conception (voir rules_engine.SEUILS_PLANCHER, qui
        # documente déjà ces deux métriques comme "pas de plancher
        # numérique"). ALTER séparé pour rester compatible avec une table
        # déjà existante sur un déploiement antérieur à ce correctif.
        _exec_safe(cur, conn, "ALTER TABLE alert_rules ALTER COLUMN seuil DROP NOT NULL;", "colonne alert_rules.seuil nullable")

        _exec_safe(cur, conn, """
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
        """, "table recommendations")
        # ← AJOUT : colonne JSONB pour l'objet suggestion COMPLET (structured
        # LLM, cible, VMID, toutes les métriques niveau 1+2+3) -- même
        # principe déjà utilisé pour ml_training_samples : les colonnes
        # typées ci-dessus (titre/severite/statut) restent pour filtrer/trier
        # simplement (WHERE statut='OPEN' sans jamais parser de JSON), le
        # JSONB porte tout le reste, sans avoir à faire évoluer le schéma SQL
        # à chaque nouveau champ ajouté côté frontend. ADD COLUMN séparé
        # (pas juste dans le CREATE TABLE ci-dessus) pour rester compatible
        # avec une table déjà existante sur un déploiement antérieur au
        # branchement réel de cette fonctionnalité.
        _exec_safe(cur, conn, "ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS donnees JSONB;", "colonne recommendations.donnees")
        _exec_safe(cur, conn, "CREATE INDEX IF NOT EXISTS idx_recommendations_statut ON recommendations(statut, created_at DESC);", "index idx_recommendations_statut")

        # ══════════════════════════════════════════════════════════════════
        # Authentification — comptes, sessions, réinitialisation de mot de
        # passe. Voir auth.py pour toute la logique de sécurité (hashage,
        # validation, tokens) -- ce fichier ne fait que stocker/lire.
        # ══════════════════════════════════════════════════════════════════
        _exec_safe(cur, conn, """
        CREATE TABLE IF NOT EXISTS users (
            id            SERIAL PRIMARY KEY,
            email         TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            nom           TEXT NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            is_active     BOOLEAN DEFAULT TRUE
        );
        """, "table users")

        # ← Session stockée côté serveur (pas un JWT auto-porteur) -- voir
        # auth.py pour le raisonnement complet. ON DELETE CASCADE : la
        # suppression d'un compte retire automatiquement ses sessions,
        # jamais une session orpheline pointant vers un user_id disparu.
        _exec_safe(cur, conn, """
        CREATE TABLE IF NOT EXISTS sessions (
            token        TEXT PRIMARY KEY,
            user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at   TIMESTAMPTZ NOT NULL,
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """, "table sessions")
        _exec_safe(cur, conn, "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);", "index idx_sessions_user")

        _exec_safe(cur, conn, """
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            token      TEXT PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMPTZ NOT NULL,
            used       BOOLEAN DEFAULT FALSE
        );
        """, "table password_reset_tokens")

        _exec_safe(cur, conn, """
        CREATE TABLE IF NOT EXISTS chat_history (
            id          SERIAL PRIMARY KEY,
            time        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            role        TEXT NOT NULL,
            content     TEXT NOT NULL,
            msg_id      TEXT UNIQUE,
            edited      BOOLEAN DEFAULT FALSE
        );
        """, "table chat_history")

        _exec_safe(cur, conn, """
        CREATE TABLE IF NOT EXISTS reports (
            id          SERIAL PRIMARY KEY,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            nom         TEXT NOT NULL,
            contenu     TEXT NOT NULL,
            niveau      TEXT DEFAULT 'surveillance',
            score_lstm  FLOAT
        );
        """, "table reports")

        # ← AJOUT : action_history -- actions de remédiation exécutées
        # (Human-in-the-Loop, voir action_executor.py). params en JSONB --
        # les paramètres varient par action (node/vmid/target_node/limit/
        # min_mb selon l'action_id), JSONB est le type PostgreSQL naturel
        # pour une forme de données variable comme celle-ci.
        _exec_safe(cur, conn, """
        CREATE TABLE IF NOT EXISTS action_history (
            id          SERIAL PRIMARY KEY,
            time        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            action_id   TEXT NOT NULL,
            params      JSONB,
            success     BOOLEAN NOT NULL,
            message     TEXT
        );
        """, "table action_history")

        # Index pour performances -- chacun isolé : l'échec de l'un (ex:
        # permission insuffisante) ne bloque plus les suivants.
        _exec_safe(cur, conn, "CREATE INDEX IF NOT EXISTS idx_metrics_noeud ON metrics_history(noeud, time DESC);", "index idx_metrics_noeud")
        _exec_safe(cur, conn, "CREATE INDEX IF NOT EXISTS idx_anomalies_statut ON anomalies(statut, time DESC);", "index idx_anomalies_statut")
        _exec_safe(cur, conn, "CREATE INDEX IF NOT EXISTS idx_rules_actif ON alert_rules(actif);", "index idx_rules_actif")
        # ← AJOUT : index pour action_history, même schéma que les autres --
        # les requêtes de lecture sont toujours "les N plus récentes"
        _exec_safe(cur, conn, "CREATE INDEX IF NOT EXISTS idx_actions_time ON action_history(time DESC);", "index idx_actions_time")

        # ← AJOUT : ml_training_samples -- un vecteur de features par cycle
        # de surveillance (le même dict que celui envoyé au LSTM), avec un
        # marqueur "contamine" (une anomalie a-t-elle été détectée ce même
        # cycle). Sert de base à un ré-entraînement sur données RÉELLES du
        # cluster plutôt que sur les ~500 derniers points en mémoire
        # seulement (perdus à chaque redémarrage) -- voir
        # get_echantillons_ml_propres() plus bas, qui exclut aussi une
        # fenêtre autour de chaque point contaminé (pas juste le point
        # exact), pour ne jamais apprendre à traiter un incident réel
        # comme un fonctionnement normal.
        _exec_safe(cur, conn, """
        CREATE TABLE IF NOT EXISTS ml_training_samples (
            id          SERIAL PRIMARY KEY,
            time        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            vecteur     JSONB NOT NULL,
            contamine   BOOLEAN NOT NULL DEFAULT FALSE
        );
        """, "table ml_training_samples")
        _exec_safe(cur, conn, "CREATE INDEX IF NOT EXISTS idx_ml_samples_time ON ml_training_samples(time DESC);", "index idx_ml_samples_time")
        _exec_safe(cur, conn, "CREATE INDEX IF NOT EXISTS idx_ml_samples_contamine ON ml_training_samples(contamine, time);", "index idx_ml_samples_contamine")

        cur.close()
        _pool.putconn(conn)

        DB_OK = True
        print(f"[DB] ✓ PostgreSQL connecté → {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}")
        return True

    except Exception as e:
        DB_OK = False
        # ← CORRIGÉ : fuite de connexion -- avant, en cas d'échec ici (host
        # injoignable, mauvais identifiants -- une vraie panne de connexion,
        # plus une erreur de permission sur une table précise puisque
        # celles-ci sont maintenant absorbées par _exec_safe ci-dessus),
        # `conn` n'était jamais ni rollback ni rendue au pool : une des 5
        # connexions max du pool restait "consommée" pour de bon jusqu'au
        # redémarrage du process.
        if conn:
            try:
                _safe_rollback(conn)
                if _pool:
                    _pool.putconn(conn)
            except Exception:
                pass
        print(f"[DB] ✗ PostgreSQL non disponible : {e}")
        print("[DB] Mode mémoire activé (fallback)")
        return False


def get_conn():
    """
    Obtient une connexion du pool.

    ← CORRIGÉ : _pool.getconn() n'était protégé par AUCUN try/except --
    si l'hôte PostgreSQL devient injoignable (coupure réseau, VM éteinte),
    tenter d'ÉTABLIR une connexion neuve échoue avec OperationalError, qui
    remontait alors brute jusqu'aux appelants. Deux d'entre eux
    (agent/main.py ws_endpoint, auth_routes.py get_current_user)
    n'ont pas de try/except sur database.get_session() -- résultat : une
    coupure réseau vers la base plantait le WebSocket ET toute requête
    authentifiée (500/exception ASGI non rattrapée), confirmé dans les
    traces reçues. Retourne maintenant None dans ce cas -- exactement le
    même contrat que "if not conn: return None" déjà géré par
    get_session() et toutes les autres fonctions de ce fichier ; aucun
    appelant n'a besoin d'être modifié, ils traitent déjà cette valeur
    correctement (repli "non authentifié" propre plutôt qu'un crash).
    """
    if not _pool:
        return None
    try:
        return _pool.getconn()
    except Exception as e:
        print(f"[DB] Connexion impossible : {e}")
        return None


def release_conn(conn):
    """
    Libère une connexion vers le pool.

    ← CORRIGÉ : sans ce contrôle, une connexion tuée côté serveur
    ("server closed the connection unexpectedly" -- panne réseau,
    redémarrage PostgreSQL, coupure SSL...) était remise dans le pool
    TELLE QUELLE. La requête SUIVANTE qui récupérait cette même
    connexion via get_conn() échouait alors immédiatement avec la même
    erreur -- une seule connexion cassée pouvait ainsi faire échouer en
    silence des fonctions sans rapport (sessions, recommendations,
    métriques...), chacune protégée par son propre except Exception qui
    avale l'erreur. psycopg2 marque conn.closed = 2 quand une connexion a
    été fermée suite à une erreur de connexion (0 = ouverte,
    1 = fermée volontairement) -- vérifié directement en tuant une
    connexion côté serveur pendant une requête avant ce correctif.
    Une connexion dans cet état est fermée et écartée plutôt que
    remise en circulation ; le pool en recrée une nouvelle à la demande
    suivante.
    """
    if not conn:
        return
    if _pool:
        if conn.closed:
            try:
                conn.close()
            except Exception:
                pass
            try:
                _pool.putconn(conn, close=True)
            except Exception:
                pass
        else:
            _pool.putconn(conn)


def _safe_rollback(conn):
    """
    ← AJOUT : rollback() sur une connexion déjà fermée par le serveur lève
    elle-même une exception (InterfaceError: connection already closed)
    -- vérifié directement en tuant une connexion en plein milieu d'une
    requête. Sans protection, cette 2e exception remonte NON rattrapée
    (elle n'est pas dans le except Exception qui l'appelle, seulement
    dans le finally implicite du bloc appelant) et provoque le 500 --
    c'est exactement ce qui apparaît dans la trace de supprimer_session.
    Utilisé à la place de conn.rollback() nu dans les blocs except de
    tout le fichier -- un seul endroit protégé plutôt que 19 blocs
    try/except individuels à dupliquer.
    """
    try:
        conn.rollback()
    except Exception:
        pass


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
            _safe_rollback(conn)
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
            _safe_rollback(conn)
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
    """
    Sauvegarde les règles générées par le LLM (remplace les existantes).

    ← CORRIGÉ : avant, float(r.get("seuil", 80)) était appelé pour LES 15
    règles dans UNE SEULE transaction -- la règle vm.status ("seuil":
    "stopped", une valeur textuelle par conception, voir
    rules_engine.SEUILS_PLANCHER qui documente déjà "vm.status"/
    "cluster.quorum" comme "pas de plancher numérique") faisait lever
    ValueError, capturé par le except générique, qui faisait ROLLBACK DE
    TOUTE LA TRANSACTION -- les 14 autres règles, pourtant valides,
    disparaissaient avec elle. Chaque règle est maintenant isolée dans son
    propre commit/rollback (même principe que _exec_safe ailleurs dans ce
    fichier) : l'échec d'une seule ne bloque plus les autres. seuil
    n'est converti en float que s'il l'est réellement -- sinon NULL dans
    la colonne numérique, la vraie valeur ("stopped", "lost") restant
    lisible dans titre/description, déjà du texte libre.
    """
    if not regles:
        return

    if DB_OK:
        conn = get_conn()
        if not conn:
            return
        try:
            try:
                cur = conn.cursor()
                cur.execute("UPDATE alert_rules SET actif=FALSE WHERE source='LLM'")
                conn.commit()
                cur.close()
            except Exception as e:
                print(f"[DB] Erreur desactivation anciennes regles: {e}")
                _safe_rollback(conn)

            n_sauvegardees = 0
            for r in regles:
                try:
                    cur = conn.cursor()
                    seuil_brut = r.get("seuil", 80)
                    try:
                        seuil = float(seuil_brut)
                    except (TypeError, ValueError):
                        seuil = None
                    try:
                        duree = int(r.get("duree_min", 0))
                    except (TypeError, ValueError):
                        duree = 0
                    cur.execute("""
                    INSERT INTO alert_rules 
                        (metric, operateur, seuil, duree_min, severite, cible, titre, description, action, source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'LLM')
                    """, (
                        r.get("metric", ""), r.get("operateur", ">"),
                        seuil, duree,
                        r.get("severite", "IMPORTANT"), r.get("cible", ""),
                        r.get("titre", ""), r.get("description", ""),
                        r.get("action", "")
                    ))
                    conn.commit()
                    cur.close()
                    n_sauvegardees += 1
                except Exception as e:
                    print(f"[DB] Erreur regle '{r.get('metric','?')}': {e}")
                    _safe_rollback(conn)

            print(f"[DB] {n_sauvegardees}/{len(regles)} règles sauvegardées")
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
# Recommendations — persistance de l'espace de travail actionnable
# ══════════════════════════════════════════════════════════════════════════════
# ← AJOUT : la table existait déjà (schéma créé) mais aucune fonction ne
# l'utilisait -- suggestions ne vivait qu'en mémoire React (App.jsx),
# perdue à chaque rechargement de page. Ne persiste QUE les recommendations
# OUVERTES par défaut (get_recommendations_ouvertes) -- volontairement
# borné, pas un historique qui grossit indéfiniment (l'historique complet,
# lui, reste sur la page Incidents).
def sauvegarder_recommendation(donnees: dict) -> int:
    """Sauvegarde une recommendation -- MET À JOUR une recommendation OUVERTE
    existante pour la même cible si elle existe déjà, sinon en crée une
    nouvelle. Retourne l'id (existant ou nouveau), -1 si échec total.

    ← CORRIGÉ (root cause de l'accumulation massive observée en Active --
    130 recommendations, la plupart pour les 2-3 mêmes problèmes
    persistants répétés) : avant, chaque cycle de surveillance qui
    détectait encore le même problème (ex: RAM haute sur pve2, toujours
    vraie 10 minutes plus tard) créait une TOUTE NOUVELLE ligne, jamais
    liée aux précédentes -- un unique problème qui persiste pendant des
    heures pouvait ainsi produire des dizaines de recommendations quasi
    identiques ("RAM high: 82.3%", puis "84.1%", puis "86.0%"...).
    Recherche maintenant une recommendation OUVERTE existante pour la
    MÊME cible (target + target_vmid) avant d'insérer -- si trouvée, ses
    données sont rafraîchies (nouveau contenu, nouvelle date) au lieu de
    créer un doublon. Recherche par cible plutôt que par titre exact : le
    titre varie légèrement d'un cycle à l'autre selon le pourcentage
    exact, mais c'est le même problème sous-jacent.

    ← CORRIGÉ (bug précédent, toujours vrai) : si l'écriture DB échoue
    alors que DB_OK=True globalement (panne réseau ponctuelle vers
    PostgreSQL), la fonction retournait -1 SANS jamais utiliser le repli
    mémoire déjà existant plus bas. Retombe maintenant sur le même repli
    mémoire dans les deux cas. IDs négatifs pour les entrées de repli --
    jamais en collision avec un id réel de la séquence PostgreSQL
    (toujours positive), et immédiatement reconnaissable en debug."""
    titre    = donnees.get("title", "Infrastructure Issue")
    severite = donnees.get("severity", "IMPORTANT")
    cible    = donnees.get("target")
    # ← MODIFIÉ (root cause vue en pratique) : dédoublonnage désormais par
    # "target" (le nœud) SEUL, plus par target+target_vmid. Deux
    # recommendations réelles observées à 20 minutes d'écart : l'une
    # ciblant "pve2 + VM103" (le texte met en avant la VM comme
    # contributrice), l'autre ciblant "pve2 seul" (le texte reste
    # node-level) -- même remède exact dans les deux cas (KSM +
    # ballooning sur pve2), donc le MÊME problème sous-jacent, jamais
    # fusionné avant parce que target_vmid différait (103 vs absent).
    # Une pression RAM sur un nœud reste UN SEUL problème à résoudre côté
    # nœud, quelle que soit la VM que le LLM met en avant d'un cycle à
    # l'autre pour l'expliquer -- fusionner par nœud seul est donc plus
    # juste que par (nœud, VM). target_vmid reste dans "donnees" (transmis
    # tel quel), seule la recherche de doublon change.

    if DB_OK:
        conn = get_conn()
        if conn:
            try:
                cur = conn.cursor()

                existante = None
                if cible:
                    cur.execute("""
                        SELECT id FROM recommendations
                        WHERE statut='OPEN' AND donnees->>'target' = %s
                        ORDER BY created_at DESC LIMIT 1
                    """, (cible,))
                    existante = cur.fetchone()

                if existante:
                    rec_id = existante[0]
                    cur.execute("""
                        UPDATE recommendations SET titre=%s, severite=%s, donnees=%s, created_at=NOW()
                        WHERE id=%s
                    """, (titre, severite, psycopg2.extras.Json(donnees), rec_id))
                    conn.commit()
                    cur.close()
                    return rec_id

                cur.execute("""
                INSERT INTO recommendations (titre, severite, donnees)
                VALUES (%s, %s, %s) RETURNING id
                """, (titre, severite, psycopg2.extras.Json(donnees)))
                rec_id = cur.fetchone()[0]
                conn.commit()
                cur.close()
                return rec_id
            except Exception as e:
                print(f"[DB] Erreur recommendation: {e} -- repli memoire pour ne pas la perdre")
                _safe_rollback(conn)
            finally:
                release_conn(conn)
        else:
            print("[DB] Connexion indisponible pour sauvegarder_recommendation -- repli memoire")

    with _mem_lock:
        # ← même dédoublonnage par nœud seul en mode mémoire, cohérent
        # avec le chemin PostgreSQL ci-dessus.
        if cible:
            for r in _mem_recommendations:
                if r["statut"] != "OPEN":
                    continue
                d = r.get("donnees", {})
                if d.get("target") == cible:
                    r["titre"]      = titre
                    r["severite"]   = severite
                    r["donnees"]    = donnees
                    r["created_at"] = datetime.now().isoformat()
                    return r["id"]
        rec_id = -(len(_mem_recommendations) + 1)
        _mem_recommendations.append({
            "id": rec_id, "created_at": datetime.now().isoformat(),
            "titre": titre, "severite": severite, "statut": "OPEN",
            "donnees": donnees,
        })
        return rec_id


def marquer_recommendation_resolue(rec_id: int):
    """Marque une recommendation comme résolue -- déclenché soit par un
    clic explicite sur la carte, soit automatiquement quand une action
    proposée s'exécute avec succès (voir routes.py, PageRecommendations.jsx).

    ← MODIFIÉ : un id négatif désigne une recommendation créée en repli
    mémoire (voir sauvegarder_recommendation) -- routé directement vers
    la mise à jour mémoire, jamais vers une requête DB inutile sur un id
    qui n'y existe pas. Corrige aussi un manque préexistant : resolu_at
    n'était jamais posé côté mémoire, ce qui aurait cassé le tri de
    l'onglet History (déjà trié sur ce champ)."""
    if DB_OK and rec_id > 0:
        conn = get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            cur.execute("""
            UPDATE recommendations SET statut='RESOLVED', resolu_at=NOW() WHERE id=%s
            """, (rec_id,))
            conn.commit()
            cur.close()
        except Exception as e:
            print(f"[DB] Erreur resolution recommendation: {e}")
            _safe_rollback(conn)
        finally:
            release_conn(conn)
    else:
        with _mem_lock:
            for r in _mem_recommendations:
                if r["id"] == rec_id:
                    r["statut"]    = "RESOLVED"
                    r["resolu_at"] = datetime.now().isoformat()


# ← AJOUT : cherche la recommendation liée à un rapport précis, tous
# statuts confondus (OPEN et RESOLVED) -- répond à "depuis un incident,
# savoir quelle recommendation en a résulté". "rapport" est déjà stocké
# dans donnees (JSONB) pour chaque recommendation depuis sa création
# (voir sauvegarder_recommendation, appelée depuis surveillance.py avec
# "rapport": nom_rapport) -- jamais interrogé jusqu'ici, la donnée
# existait déjà. Requête directe sur le champ JSONB (donnees->>'rapport'),
# pas de scan de toute la table côté Python.
def trouver_recommendation_par_rapport(nom_rapport: str) -> dict | None:
    if DB_OK:
        conn = get_conn()
        if conn:
            try:
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(
                    "SELECT id, statut, donnees, resolu_at FROM recommendations WHERE donnees->>'rapport' = %s LIMIT 1",
                    (nom_rapport,)
                )
                row = cur.fetchone()
                cur.close()
                return dict(row) if row else None
            except Exception as e:
                print(f"[DB] Erreur recherche recommendation par rapport: {e}")
                return None
            finally:
                release_conn(conn)
        return None
    else:
        with _mem_lock:
            for r in _mem_recommendations:
                if r.get("donnees", {}).get("rapport") == nom_rapport:
                    return r
        return None


def _date_depuis_iso(timestamp_iso):
    """Convertit un timestamp ISO (ou None) en date, sans jamais lever
    d'exception -- utilisé par le repli mémoire de get_recommendations_
    ouvertes/resolues pour comparer à la date du jour. Retourne None si
    le timestamp est absent ou malformé (ne matchera alors jamais
    CURRENT_DATE, traité comme "pas aujourd'hui" par défaut, jamais
    comme une erreur qui casse l'appelant)."""
    if not timestamp_iso:
        return None
    try:
        return datetime.fromisoformat(timestamp_iso).date()
    except (ValueError, TypeError):
        return None


def get_recommendations_ouvertes() -> list:
    """Récupère les recommendations ACTIVES : ouvertes ET datant
    d'aujourd'hui uniquement -- même principe que Today/History pour les
    Incidents, sur demande explicite. Une recommendation ouverte mais
    datant d'un jour précédent bascule automatiquement vers l'historique
    (get_recommendations_resolues), sans action manuelle nécessaire.

    ← MODIFIÉ (root cause du "130 recommendations coincées dans Active,
    certaines vieilles de plusieurs jours") : avant, Active = tout ce qui
    n'était jamais résolu, sans notion de date -- une recommendation
    ouverte le 17/08 et jamais cliquée "Resolve" restait indéfiniment
    dans Active, même des jours plus tard. Ajoute created_at::date =
    CURRENT_DATE au filtre.

    ← MODIFIÉ (inchangé) : fusionne les éventuelles recommendations en
    repli mémoire (ids négatifs, voir sauvegarder_recommendation) même
    quand DB_OK=True."""
    if DB_OK:
        resultats = []
        conn = get_conn()
        if conn:
            try:
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute("""
                    SELECT id, donnees FROM recommendations
                    WHERE statut='OPEN' AND created_at::date = CURRENT_DATE
                    ORDER BY created_at ASC
                """)
                rows = cur.fetchall()
                cur.close()
                resultats = [dict(r) for r in rows]
            except Exception as e:
                print(f"[DB] Erreur get recommendations: {e}")
            finally:
                release_conn(conn)
        with _mem_lock:
            aujourdhui = datetime.now().date()
            resultats += [
                {"id": r["id"], "donnees": r["donnees"]}
                for r in _mem_recommendations
                if r["statut"] == "OPEN" and _date_depuis_iso(r.get("created_at")) == aujourdhui
            ]
        return resultats
    else:
        with _mem_lock:
            aujourdhui = datetime.now().date()
            return [
                r for r in _mem_recommendations
                if r["statut"] == "OPEN" and _date_depuis_iso(r.get("created_at")) == aujourdhui
            ]


# ← AJOUT : historique + suppression définitive -- même principe déjà
# appliqué à la page Incidents (Today vs History, paginé) : Resolve garde
# une trace consultable (déplace vers l'historique), Delete la retire
# complètement, sans trace. Pattern standard (PagerDuty, Datadog,
# ServiceNow) -- les deux actions ont un sens différent, jamais confondues.
def get_recommendations_resolues(limit: int = 50, offset: int = 0) -> list:
    """Récupère l'HISTORIQUE : tout ce qui n'est PAS dans Active -- les
    recommendations RÉSOLUES (n'importe quelle date) PLUS les
    recommendations encore OUVERTES mais datant d'avant aujourd'hui.
    Triées par la date la plus pertinente (résolution si résolue, sinon
    création) la plus récente d'abord, paginé.

    ← MODIFIÉ (miroir du changement dans get_recommendations_ouvertes,
    même raisonnement) : avant, ne contenait QUE les résolues -- une
    recommendation ouverte depuis plusieurs jours n'apparaissait ni ici
    ni ne quittait jamais Active. Le statut ("statut" dans donnees déjà
    présent) reste inchangé (toujours OPEN si jamais résolue) -- c'est au
    frontend de distinguer visuellement "ancien mais toujours ouvert" de
    "résolu", pas à cette fonction de mentir sur le statut réel."""
    if DB_OK:
        resultats = []
        conn = get_conn()
        if conn:
            try:
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute("""
                    SELECT id, donnees, resolu_at, statut FROM recommendations
                    WHERE statut='RESOLVED' OR (statut='OPEN' AND created_at::date < CURRENT_DATE)
                    ORDER BY COALESCE(resolu_at, created_at) DESC
                    LIMIT %s OFFSET %s
                """, (limit, offset))
                rows = cur.fetchall()
                cur.close()
                resultats = [dict(r) for r in rows]
            except Exception as e:
                print(f"[DB] Erreur get recommendations resolues: {e}")
            finally:
                release_conn(conn)
        with _mem_lock:
            aujourdhui = datetime.now().date()
            historique_mem = [
                r for r in _mem_recommendations
                if r["statut"] == "RESOLVED"
                or (r["statut"] == "OPEN" and (_date_depuis_iso(r.get("created_at")) or aujourdhui) < aujourdhui)
            ]
        if historique_mem:
            resultats += historique_mem
            resultats.sort(key=lambda r: r.get("resolu_at") or r.get("created_at") or "", reverse=True)
            resultats = resultats[:limit]
        return resultats
    else:
        with _mem_lock:
            aujourdhui = datetime.now().date()
            historique = [
                r for r in _mem_recommendations
                if r["statut"] == "RESOLVED"
                or (r["statut"] == "OPEN" and (_date_depuis_iso(r.get("created_at")) or aujourdhui) < aujourdhui)
            ]
            historique.sort(key=lambda r: r.get("resolu_at") or r.get("created_at") or "", reverse=True)
            return historique[offset:offset + limit]


def compter_recommendations_resolues() -> int:
    """Total de l'historique (résolues + ouvertes-mais-anciennes) -- pour
    que le frontend sache s'il reste des pages à charger.

    ← MODIFIÉ : compte maintenant le même ensemble élargi que
    get_recommendations_resolues ci-dessus (même raisonnement)."""
    total_mem = 0
    with _mem_lock:
        aujourdhui = datetime.now().date()
        total_mem = sum(
            1 for r in _mem_recommendations
            if r["statut"] == "RESOLVED"
            or (r["statut"] == "OPEN" and (_date_depuis_iso(r.get("created_at")) or aujourdhui) < aujourdhui)
        )
    if DB_OK:
        conn = get_conn()
        if not conn:
            return total_mem
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT COUNT(*) FROM recommendations
                WHERE statut='RESOLVED' OR (statut='OPEN' AND created_at::date < CURRENT_DATE)
            """)
            total = cur.fetchone()[0]
            cur.close()
            return total + total_mem
        except Exception as e:
            print(f"[DB] Erreur count recommendations resolues: {e}")
            return total_mem
        finally:
            release_conn(conn)
    else:
        return total_mem


def supprimer_recommendation(rec_id: int):
    """Suppression DÉFINITIVE -- distincte de marquer_recommendation_resolue.
    Résoudre garde une trace (déplace vers l'historique) ; supprimer retire
    complètement, sans trace, pour un faux positif ou du bruit.

    ← MODIFIÉ : id négatif -> repli mémoire, même principe que
    marquer_recommendation_resolue ci-dessus."""
    if DB_OK and rec_id > 0:
        conn = get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM recommendations WHERE id=%s", (rec_id,))
            conn.commit()
            cur.close()
        except Exception as e:
            print(f"[DB] Erreur suppression recommendation: {e}")
            _safe_rollback(conn)
        finally:
            release_conn(conn)
    else:
        with _mem_lock:
            _mem_recommendations[:] = [r for r in _mem_recommendations if r["id"] != rec_id]


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
            _safe_rollback(conn)
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
# Échantillons ML — ré-entraînement LSTM sur données réelles du cluster
# ══════════════════════════════════════════════════════════════════════════════
# ← AJOUT : avant, le LSTM ne se ré-entraînait que sur self.historique (les
# ~500 derniers points EN MÉMOIRE, perdus à chaque redémarrage de l'agent).
# Ces deux fonctions donnent accès à l'historique RÉEL persisté en base --
# potentiellement des mois de données, plutôt que quelques heures glissantes.
def sauvegarder_echantillon_ml(vecteur: dict, contamine: bool = False):
    """Sauvegarde le vecteur de features tel que vu par le LSTM ce cycle.
    'contamine' = une anomalie a été détectée ce même cycle -- utilisé pour
    exclure cette période du ré-entraînement futur (voir fonction suivante)."""
    if DB_OK:
        conn = get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO ml_training_samples (vecteur, contamine) VALUES (%s, %s)",
                (psycopg2.extras.Json(vecteur), bool(contamine))
            )
            conn.commit()
            cur.close()
        except Exception as e:
            print(f"[DB] Erreur echantillon ML: {e}")
            _safe_rollback(conn)
        finally:
            release_conn(conn)
    else:
        with _mem_lock:
            _mem_ml_samples.append({
                "time": datetime.now().isoformat(),
                "vecteur": vecteur,
                "contamine": bool(contamine),
            })
            if len(_mem_ml_samples) > 5000:
                _mem_ml_samples.pop(0)


def get_echantillons_ml_propres(limit: int = 5000, marge_min: int = 15) -> list:
    """
    Récupère les échantillons NON contaminés, dans l'ordre chronologique.
    Exclut aussi tout échantillon dans une fenêtre de +/- marge_min minutes
    autour d'un échantillon marqué contaminé -- un incident a souvent des
    signes avant-coureurs et une période de retour à la normale qui ne
    sont pas non plus représentatifs d'un fonctionnement "propre".
    """
    if DB_OK:
        conn = get_conn()
        if not conn:
            return []
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT s.time, s.vecteur
                FROM ml_training_samples s
                WHERE NOT EXISTS (
                    SELECT 1 FROM ml_training_samples c
                    WHERE c.contamine = TRUE
                    AND s.time BETWEEN c.time - (%s || ' minutes')::interval
                                   AND c.time + (%s || ' minutes')::interval
                )
                ORDER BY s.time ASC
                LIMIT %s
            """, (marge_min, marge_min, limit))
            rows = cur.fetchall()
            cur.close()
            return [dict(r) for r in rows]
        except Exception as e:
            print(f"[DB] Erreur get echantillons ML: {e}")
            return []
        finally:
            release_conn(conn)
    else:
        with _mem_lock:
            # Repli mémoire simplifié -- pas de fenêtre temporelle précise,
            # exclut seulement les points marqués contaminés eux-mêmes.
            return [s for s in _mem_ml_samples if not s["contamine"]][-limit:]


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


# ══════════════════════════════════════════════════════════════════════════════
# Authentification — comptes, sessions, réinitialisation de mot de passe
# ══════════════════════════════════════════════════════════════════════════════
# ← AJOUT : toute la logique de sécurité (hashage, validation de force,
# tokens, limitation des tentatives) vit dans auth.py -- ce fichier ne
# fait que stocker/lire, jamais de décision de sécurité ici.
_mem_users:    list = []
_mem_sessions: list = []
_mem_reset_tokens: list = []


def creer_utilisateur(email: str, password_hash: str, nom: str):
    """Crée un compte. Retourne l'id créé, ou None si l'email existe déjà
    (contrainte UNIQUE) -- ne lève jamais d'exception vers l'appelant,
    la route traduit None en message d'erreur adapté."""
    email = email.strip().lower()
    if DB_OK:
        conn = get_conn()
        if not conn:
            return None
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO users (email, password_hash, nom) VALUES (%s, %s, %s) RETURNING id",
                (email, password_hash, nom),
            )
            user_id = cur.fetchone()[0]
            conn.commit()
            cur.close()
            return user_id
        except psycopg2.errors.UniqueViolation:
            _safe_rollback(conn)
            return None
        except Exception as e:
            print(f"[DB] Erreur creation utilisateur: {e}")
            _safe_rollback(conn)
            return None
        finally:
            release_conn(conn)
    else:
        with _mem_lock:
            if any(u["email"] == email for u in _mem_users):
                return None
            user_id = len(_mem_users) + 1
            _mem_users.append({
                "id": user_id, "email": email, "password_hash": password_hash,
                "nom": nom, "is_active": True,
            })
            return user_id


def get_utilisateur_par_email(email: str):
    """
    ← MODIFIÉ : lève BaseDeDonneesIndisponible si la connexion échoue
    réellement, au lieu de retourner None silencieusement comme avant --
    voir la classe pour le raisonnement complet. None continue de
    signifier UNIQUEMENT "requête réussie, aucun utilisateur avec cet
    email", exactement comme avant -- aucun appelant existant qui
    dépendait de ce contrat n'est affecté par ce changement.
    """
    email = email.strip().lower()
    if DB_OK:
        conn = get_conn()
        if not conn:
            raise BaseDeDonneesIndisponible("connexion indisponible")
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM users WHERE email=%s", (email,))
            row = cur.fetchone()
            cur.close()
            return dict(row) if row else None
        except Exception as e:
            print(f"[DB] Erreur get utilisateur: {e}")
            raise BaseDeDonneesIndisponible(str(e))
        finally:
            release_conn(conn)
    else:
        with _mem_lock:
            return next((dict(u) for u in _mem_users if u["email"] == email), None)


def get_utilisateur_par_id(user_id: int):
    if DB_OK:
        conn = get_conn()
        if not conn:
            return None
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM users WHERE id=%s", (user_id,))
            row = cur.fetchone()
            cur.close()
            return dict(row) if row else None
        except Exception as e:
            print(f"[DB] Erreur get utilisateur par id: {e}")
            return None
        finally:
            release_conn(conn)
    else:
        with _mem_lock:
            return next((dict(u) for u in _mem_users if u["id"] == user_id), None)


def mettre_a_jour_mot_de_passe(user_id: int, nouveau_hash: str):
    if DB_OK:
        conn = get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            cur.execute("UPDATE users SET password_hash=%s WHERE id=%s", (nouveau_hash, user_id))
            conn.commit()
            cur.close()
        except Exception as e:
            print(f"[DB] Erreur maj mot de passe: {e}")
            _safe_rollback(conn)
        finally:
            release_conn(conn)
    else:
        with _mem_lock:
            for u in _mem_users:
                if u["id"] == user_id:
                    u["password_hash"] = nouveau_hash


def creer_session(token: str, user_id: int, duree_s: int):
    if DB_OK:
        conn = get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO sessions (token, user_id, expires_at) VALUES (%s, %s, NOW() + make_interval(secs => %s))",
                (token, user_id, duree_s),
            )
            conn.commit()
            cur.close()
        except Exception as e:
            print(f"[DB] Erreur creation session: {e}")
            _safe_rollback(conn)
        finally:
            release_conn(conn)
    else:
        with _mem_lock:
            _mem_sessions.append({
                "token": token, "user_id": user_id,
                "expires_at": time.time() + duree_s,
            })


def get_session(token: str):
    """Retourne {token, user_id, expires_at} si le token existe ET n'a pas
    expiré, sinon None -- l'appelant (auth middleware) n'a pas à vérifier
    l'expiration lui-même, une session expirée est traitée comme absente."""
    if DB_OK:
        conn = get_conn()
        if not conn:
            return None
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM sessions WHERE token=%s AND expires_at > NOW()", (token,))
            row = cur.fetchone()
            cur.close()
            return dict(row) if row else None
        except Exception as e:
            print(f"[DB] Erreur get session: {e}")
            return None
        finally:
            release_conn(conn)
    else:
        with _mem_lock:
            s = next((s for s in _mem_sessions if s["token"] == token), None)
            if s and s["expires_at"] > time.time():
                return s
            return None


def supprimer_session(token: str):
    """Déconnexion -- invalide immédiatement cette session précise."""
    if DB_OK:
        conn = get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM sessions WHERE token=%s", (token,))
            conn.commit()
            cur.close()
        except Exception as e:
            print(f"[DB] Erreur suppression session: {e}")
            _safe_rollback(conn)
        finally:
            release_conn(conn)
    else:
        with _mem_lock:
            _mem_sessions[:] = [s for s in _mem_sessions if s["token"] != token]


def supprimer_sessions_utilisateur(user_id: int):
    """Déconnecte CE compte de partout -- appelé après un changement de
    mot de passe, pratique standard : si le mot de passe a fuité, changer
    le mot de passe doit aussi couper l'accès à toute session déjà
    ouverte avec l'ancien, pas seulement empêcher une future connexion."""
    if DB_OK:
        conn = get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM sessions WHERE user_id=%s", (user_id,))
            conn.commit()
            cur.close()
        except Exception as e:
            print(f"[DB] Erreur suppression sessions utilisateur: {e}")
            _safe_rollback(conn)
        finally:
            release_conn(conn)
    else:
        with _mem_lock:
            _mem_sessions[:] = [s for s in _mem_sessions if s["user_id"] != user_id]


def creer_token_reset(token: str, user_id: int, duree_s: int):
    if DB_OK:
        conn = get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO password_reset_tokens (token, user_id, expires_at) VALUES (%s, %s, NOW() + make_interval(secs => %s))",
                (token, user_id, duree_s),
            )
            conn.commit()
            cur.close()
        except Exception as e:
            print(f"[DB] Erreur creation token reset: {e}")
            _safe_rollback(conn)
        finally:
            release_conn(conn)
    else:
        with _mem_lock:
            _mem_reset_tokens.append({
                "token": token, "user_id": user_id,
                "expires_at": time.time() + duree_s, "used": False,
            })


def get_token_reset(token: str):
    """Retourne {token, user_id, used} si le token existe et n'a PAS
    expiré, sinon None -- ne vérifie PAS "used" ici (la route appelante
    décide explicitement quoi faire d'un token déjà utilisé, pour pouvoir
    donner un message d'erreur distinct de "token invalide/expiré")."""
    if DB_OK:
        conn = get_conn()
        if not conn:
            return None
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM password_reset_tokens WHERE token=%s AND expires_at > NOW()", (token,))
            row = cur.fetchone()
            cur.close()
            return dict(row) if row else None
        except Exception as e:
            print(f"[DB] Erreur get token reset: {e}")
            return None
        finally:
            release_conn(conn)
    else:
        with _mem_lock:
            t = next((t for t in _mem_reset_tokens if t["token"] == token), None)
            if t and t["expires_at"] > time.time():
                return t
            return None


def marquer_token_reset_utilise(token: str):
    if DB_OK:
        conn = get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            cur.execute("UPDATE password_reset_tokens SET used=TRUE WHERE token=%s", (token,))
            conn.commit()
            cur.close()
        except Exception as e:
            print(f"[DB] Erreur maj token reset: {e}")
            _safe_rollback(conn)
        finally:
            release_conn(conn)
    else:
        with _mem_lock:
            for t in _mem_reset_tokens:
                if t["token"] == token:
                    t["used"] = True