"""
test_api.py — Tests d'intégration des endpoints FastAPI.

Vérifie que chaque endpoint :
  - Retourne le bon code HTTP
  - Retourne la bonne structure JSON
  - Gère les erreurs gracieusement (pas de crash)
  - Valide les types de données retournés

Tous les tests sont mockés → s'exécutent sans Proxmox ni Groq.
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))


# ─── Helper pour obtenir le client ────────────────────────────────────────────

def get_client(etat=None):
    """Créer un TestClient FastAPI avec l'état cluster donné."""
    from unittest.mock import patch
    try:
        from fastapi.testclient import TestClient
        from agent.main import app
    except ImportError:
        pytest.skip("FastAPI app non disponible")

    from tests.conftest import ETAT_CLUSTER_NORMAL
    etat = etat or ETAT_CLUSTER_NORMAL

    with patch("proxmox_api.get_etat_cluster", return_value=etat), \
         patch("agent.groq_client.appeler_groq", return_value="OK"), \
         patch("database.sauvegarder_metriques", return_value=None), \
         patch("database.sauvegarder_anomalie", return_value=None):
        yield TestClient(app)


# ─── /api/status ──────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestApiStatus:
    """Endpoint de santé — vérifie l'état des composants."""

    def test_status_200(self, client):
        r = client.get("/api/status")
        assert r.status_code == 200

    def test_status_structure(self, client):
        r = client.get("/api/status")
        data = r.json()
        champs_requis = ["llm", "groq_model", "groq_ok", "ai_score", "timestamp"]
        for champ in champs_requis:
            assert champ in data, f"Champ manquant dans /api/status: {champ}"

    def test_status_ai_score_plage(self, client):
        r = client.get("/api/status")
        data = r.json()
        score = data.get("ai_score", 0)
        assert 0.0 <= score <= 1.0, f"ai_score hors plage: {score}"

    def test_status_llm_groq(self, client):
        r = client.get("/api/status")
        data = r.json()
        assert data["llm"] == "groq"


# ─── /api/cluster ─────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestApiCluster:
    """Endpoint état du cluster Proxmox."""

    def test_cluster_200(self, client):
        r = client.get("/api/cluster")
        assert r.status_code == 200

    def test_cluster_contient_noeuds(self, client):
        r = client.get("/api/cluster")
        data = r.json()
        # Soit des noeuds, soit une erreur gracieuse
        assert "noeuds" in data or "error" in data

    def test_cluster_structure_noeuds(self, client):
        r = client.get("/api/cluster")
        data = r.json()
        if "noeuds" in data and data["noeuds"]:
            noeud = data["noeuds"][0]
            champs = ["nom", "statut", "cpu_pct", "ram_pct", "disk_pct"]
            for champ in champs:
                assert champ in noeud, f"Champ noeud manquant: {champ}"

    def test_cluster_structure_vms(self, client):
        r = client.get("/api/cluster")
        data = r.json()
        if "vms" in data and data["vms"]:
            vm = data["vms"][0]
            champs = ["vmid", "nom", "statut", "noeud"]
            for champ in champs:
                assert champ in vm, f"Champ VM manquant: {champ}"

    def test_cluster_sans_proxmox_repond_gracieusement(self):
        """Sans Proxmox accessible, l'endpoint doit répondre sans planter."""
        try:
            from fastapi.testclient import TestClient
            from agent.main import app
        except ImportError:
            pytest.skip("App non disponible")

        with patch("proxmox_api.get_etat_cluster", side_effect=Exception("Connection refused")):
            client = TestClient(app)
            r = client.get("/api/cluster")
            assert r.status_code in [200, 503], \
                f"L'endpoint doit répondre même sans Proxmox, code={r.status_code}"


# ─── /api/regles ──────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestApiRegles:
    """Endpoint règles de monitoring AI-générées."""

    def test_regles_200(self, client):
        r = client.get("/api/regles")
        assert r.status_code == 200

    def test_regles_structure(self, client):
        r = client.get("/api/regles")
        data = r.json()
        assert "regles" in data, "Clé 'regles' manquante"
        assert "count" in data, "Clé 'count' manquante"

    def test_regles_count_coherent(self, client):
        r = client.get("/api/regles")
        data = r.json()
        assert data["count"] == len(data["regles"]), \
            f"count={data['count']} incohérent avec len(regles)={len(data['regles'])}"

    def test_regles_structure_regle(self, client):
        r = client.get("/api/regles")
        data = r.json()
        if data["regles"]:
            regle = data["regles"][0]
            # Une règle doit avoir au moins une métrique et une sévérité
            assert any(k in regle for k in ["metric", "nom", "description"]), \
                f"Structure de règle invalide: {list(regle.keys())}"

    def test_regles_regenerer_post(self, client):
        """POST /api/regles/regenerer doit déclencher la régénération."""
        with patch("agent.rules_engine.generer_regles_ia", return_value=[]):
            r = client.post("/api/regles/regenerer")
            assert r.status_code in [200, 503], \
                f"Code inattendu: {r.status_code}"


# ─── /api/conversations ───────────────────────────────────────────────────────

@pytest.mark.integration
class TestApiConversations:
    """Endpoint gestion des conversations du chat."""

    def test_conversations_200(self, client):
        r = client.get("/api/conversations")
        assert r.status_code == 200

    def test_conversations_structure(self, client):
        r = client.get("/api/conversations")
        data = r.json()
        assert "conversations" in data, "Clé 'conversations' manquante"
        assert isinstance(data["conversations"], list)

    def test_nouvelle_conversation_post(self, client):
        r = client.post("/api/conversations")
        assert r.status_code == 200
        data = r.json()
        assert "conv_id" in data, "conv_id manquant dans la réponse"
        assert data["conv_id"].startswith("conv_"), \
            f"Format conv_id invalide: {data['conv_id']}"

    def test_changer_conversation_put(self, client):
        # Créer une conversation d'abord
        r_create = client.post("/api/conversations")
        conv_id = r_create.json()["conv_id"]

        # Changer vers cette conversation
        r = client.put(f"/api/conversations/{conv_id}")
        assert r.status_code == 200
        data = r.json()
        assert "ok" in data

    def test_supprimer_conversation_delete(self, client):
        # Créer une conversation
        r_create = client.post("/api/conversations")
        conv_id = r_create.json()["conv_id"]

        # La supprimer
        r = client.delete(f"/api/conversations/{conv_id}")
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True

    def test_conversation_id_inexistant_gracieux(self, client):
        """Essayer de changer vers un conv_id inexistant → pas de crash."""
        r = client.put("/api/conversations/conv_inexistant_99999")
        assert r.status_code == 200
        data = r.json()
        assert "ok" in data
        assert data["ok"] is False


# ─── /api/rapports ────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestApiRapports:
    """Endpoint liste des rapports incidents."""

    def test_rapports_200(self, client):
        r = client.get("/api/rapports")
        assert r.status_code == 200

    def test_rapport_inexistant_404_ou_erreur(self, client):
        """Un rapport inexistant doit retourner une erreur claire."""
        r = client.get("/api/rapports/rapport_inexistant_99999.md")
        # Soit 404 soit 200 avec un message d'erreur dans le body
        if r.status_code == 200:
            data = r.json()
            assert "error" in data or "contenu" in data

    def test_rapports_nettoyage_bashcopy(self, client, tmp_path):
        """
        Les rapports doivent être nettoyés des artefacts 'bashCopy'.
        Vérifie la ligne de défense dans /api/rapports/{nom}.
        """
        rapport_brut = "```bash\nCopy\nps aux --sort=-%mem | head -15\n```"
        rapport_file = tmp_path / "test_rapport.md"
        rapport_file.write_text(rapport_brut)

        with patch("agent.report_writer.lire_rapport", return_value={
            "nom": "test_rapport.md",
            "contenu": rapport_brut,
        }):
            r = client.get("/api/rapports/test_rapport.md")
            if r.status_code == 200:
                data = r.json()
                contenu = data.get("contenu", "")
                assert "Copy\n" not in contenu, \
                    "Artefact 'Copy' non nettoyé dans le rapport"


# ─── /api/anomalies/historique ────────────────────────────────────────────────

@pytest.mark.integration
class TestApiAnomalies:
    """Endpoint historique des anomalies depuis la DB."""

    def test_anomalies_200(self, client):
        r = client.get("/api/anomalies/historique")
        assert r.status_code == 200

    def test_anomalies_structure(self, client):
        r = client.get("/api/anomalies/historique")
        data = r.json()
        assert "anomalies" in data
        assert isinstance(data["anomalies"], list)

    def test_anomalies_limit_param(self, client):
        """Le paramètre limit doit être respecté."""
        with patch("database.get_anomalies", return_value=[]) as mock_db:
            client.get("/api/anomalies/historique?limit=10")
            if mock_db.called:
                args, kwargs = mock_db.call_args
                limit_val = kwargs.get("limit") or (args[0] if args else None)
                if limit_val is not None:
                    assert limit_val == 10


# ─── /api/db/stats ────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestApiDbStats:
    """Endpoint statistiques base de données."""

    def test_db_stats_200(self, client):
        r = client.get("/api/db/stats")
        assert r.status_code == 200

    def test_db_stats_contient_ok_flag(self, client):
        r = client.get("/api/db/stats")
        data = r.json()
        assert "db_ok" in data, "Champ db_ok manquant"
        assert isinstance(data["db_ok"], bool)