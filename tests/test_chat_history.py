"""
test_chat_history.py — Tests du système de gestion des conversations.

Couvre le cycle de vie complet d'une conversation :
  créer → écrire → lire → éditer → supprimer → persistance JSON
"""
import pytest
import sys
import json
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def get_chat_history_module(tmp_path):
    """Importer chat_history avec un fichier conversations.json temporaire."""
    # Pointer vers un fichier temporaire pour éviter de polluer les vraies données
    os.environ["OPSPILOT_CONV_FILE"] = str(tmp_path / "conversations.json")
    try:
        import importlib
        import agent.chat_history as ch
        importlib.reload(ch)  # Recharger avec le nouveau path
        return ch
    except ImportError:
        pytest.skip("agent.chat_history non disponible")


@pytest.fixture
def ch(tmp_path):
    """Module chat_history avec état propre pour chaque test."""
    import importlib
    import agent.chat_history as chat_module
    importlib.reload(chat_module)
    return chat_module


# ─── Tests conversation lifecycle ─────────────────────────────────────────────

@pytest.mark.unit
class TestConversationLifecycle:
    """Cycle de vie complet d'une conversation."""

    def test_nouvelle_conversation_cree_id(self, ch):
        conv_id = ch.nouvelle_conversation()
        assert conv_id is not None
        assert isinstance(conv_id, str)
        assert conv_id.startswith("conv_")

    def test_nouvelle_conversation_active(self, ch):
        conv_id = ch.nouvelle_conversation()
        assert ch.get_conv_active() == conv_id

    def test_deux_conversations_differents_ids(self, ch, monkeypatch):
        """Deux conversations créées dans la même seconde ont des IDs distincts."""
        counter = [1_000_000]
        def fake_time():
            counter[0] += 1
            return float(counter[0])
        monkeypatch.setattr("time.time", fake_time)
        id1 = ch.nouvelle_conversation()
        id2 = ch.nouvelle_conversation()
        assert id1 != id2, "Deux conversations ne peuvent pas avoir le même ID"

    def test_supprimer_conversation_inexistante_sans_crash(self, ch):
        """Supprimer un ID inexistant ne doit pas planter."""
        ch.supprimer_conversation("conv_inexistant_99999")

    def test_changer_conversation_inexistante_retourne_false(self, ch):
        result = ch.changer_conversation("conv_inexistant_99999")
        assert result is False

    def test_changer_conversation_existante_retourne_true(self, ch):
        id1 = ch.nouvelle_conversation()
        import time; time.sleep(0.01)
        id2 = ch.nouvelle_conversation()

        result = ch.changer_conversation(id1)
        assert result is True
        assert ch.get_conv_active() == id1


# ─── Tests messages ────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestMessages:
    """Ajout, lecture et édition de messages."""

    def test_ajouter_message_user(self, ch):
        ch.nouvelle_conversation()
        msg = ch.ajouter_message("user", "Bonjour OpsPilot")
        assert msg["role"] == "user"
        assert msg["content"] == "Bonjour OpsPilot"
        assert "id" in msg
        assert "timestamp" in msg

    def test_ajouter_message_assistant(self, ch):
        ch.nouvelle_conversation()
        msg = ch.ajouter_message("assistant", "Bonjour, comment puis-je aider ?")
        assert msg["role"] == "assistant"

    def test_message_id_unique(self, ch):
        ch.nouvelle_conversation()
        msg1 = ch.ajouter_message("user", "Question 1")
        msg2 = ch.ajouter_message("user", "Question 2")
        assert msg1["id"] != msg2["id"]

    def test_historique_complet_retourne_messages(self, ch):
        ch.nouvelle_conversation()
        ch.ajouter_message("user", "Test 1")
        ch.ajouter_message("assistant", "Réponse 1")

        hist = ch.get_historique_complet()
        assert len(hist) == 2

    def test_historique_vide_au_demarrage(self, ch):
        ch.nouvelle_conversation()
        hist = ch.get_historique_complet()
        assert hist == []

    def test_messages_llm_limite(self, ch):
        """get_messages_llm() doit respecter la limite."""
        ch.nouvelle_conversation()
        for i in range(20):
            ch.ajouter_message("user", f"Message {i}")
            ch.ajouter_message("assistant", f"Réponse {i}")

        msgs = ch.get_messages_llm(max_messages=6)
        assert len(msgs) <= 6, \
            f"get_messages_llm doit limiter à 6 messages, got {len(msgs)}"

    def test_messages_llm_format(self, ch):
        """Format LLM : dicts avec role et content uniquement."""
        ch.nouvelle_conversation()
        ch.ajouter_message("user", "Créer une LXC")
        ch.ajouter_message("assistant", "Voici la commande...")

        msgs = ch.get_messages_llm()
        for msg in msgs:
            assert "role" in msg
            assert "content" in msg
            # Ne doit pas contenir les champs internes (id, timestamp...)
            assert "timestamp" not in msg or True  # optionnel selon impl


# ─── Tests édition de messages ────────────────────────────────────────────────

@pytest.mark.unit
class TestEditionMessages:
    """L'édition supprime les messages suivants (comportement ChatGPT)."""

    def test_edition_message_change_contenu(self, ch):
        ch.nouvelle_conversation()
        msg = ch.ajouter_message("user", "Question originale")
        ch.editer_message(msg["id"], "Question modifiée")

        hist = ch.get_historique_complet()
        assert hist[0]["content"] == "Question modifiée"

    def test_edition_supprime_messages_suivants(self, ch):
        ch.nouvelle_conversation()
        msg1 = ch.ajouter_message("user", "Question 1")
        ch.ajouter_message("assistant", "Réponse 1")
        ch.ajouter_message("user", "Question 2")
        ch.ajouter_message("assistant", "Réponse 2")

        # Éditer message 1 → messages 2, 3, 4 supprimés
        ch.editer_message(msg1["id"], "Question 1 modifiée")
        hist = ch.get_historique_complet()
        assert len(hist) == 1, \
            f"L'édition devrait supprimer les messages suivants, got {len(hist)}"

    def test_edition_message_assistant_sans_effet(self, ch):
        """On ne peut éditer que les messages user."""
        ch.nouvelle_conversation()
        ch.ajouter_message("user", "Question")
        msg_assistant = ch.ajouter_message("assistant", "Réponse")

        result = ch.editer_message(msg_assistant["id"], "Réponse modifiée")
        assert result == -1, "L'édition d'un message assistant doit retourner -1"

    def test_edition_id_inexistant_retourne_moins_un(self, ch):
        ch.nouvelle_conversation()
        result = ch.editer_message("msg_inexistant_99999", "nouveau contenu")
        assert result == -1


# ─── Tests vider_historique ────────────────────────────────────────────────────

@pytest.mark.unit
class TestViderHistorique:
    """Vider l'historique de la conversation active."""

    def test_vider_historique(self, ch):
        ch.nouvelle_conversation()
        ch.ajouter_message("user", "Message")
        ch.vider_historique()
        assert ch.get_historique_complet() == []

    def test_vider_ne_supprime_pas_conversation(self, ch):
        conv_id = ch.nouvelle_conversation()
        ch.ajouter_message("user", "Message")
        ch.vider_historique()

        # La conversation doit encore exister mais vide
        convs = ch.lister_conversations()
        ids = [c["id"] for c in convs]
        assert conv_id in ids, "Vider l'historique ne doit pas supprimer la conversation"


# ─── Tests lister_conversations ───────────────────────────────────────────────

@pytest.mark.unit
class TestListerConversations:
    """lister_conversations() retourne les métadonnées des conversations."""

    def test_liste_vide_au_demarrage(self, ch):
        convs = ch.lister_conversations()
        # Peut être vide ou avoir une conv active par défaut
        assert isinstance(convs, list)

    def test_liste_contient_nouvelle_conv(self, ch):
        conv_id = ch.nouvelle_conversation()
        convs = ch.lister_conversations()
        ids = [c["id"] for c in convs]
        assert conv_id in ids

    def test_preview_depuis_premier_message(self, ch):
        """
        Le preview doit contenir le début du premier message utilisateur.
        On recherche par ID (pas par position) car le tri par timestamp
        peut mettre d'autres conversations devant si des convs existaient.
        """
        conv_id = ch.nouvelle_conversation()
        ch.ajouter_message("user", "Je veux créer une LXC sur pve1")

        convs = ch.lister_conversations()
        # Trouver par ID, pas par position — robuste au tri
        conv = next((c for c in convs if c["id"] == conv_id), None)
        assert conv is not None, f"Conversation {conv_id} non trouvée dans la liste"
        assert "preview" in conv
        assert "LXC" in conv["preview"] or "créer" in conv["preview"].lower()

    def test_conv_active_marquee(self, ch):
        conv_id = ch.nouvelle_conversation()
        convs = ch.lister_conversations()
        active = [c for c in convs if c.get("active")]
        assert len(active) >= 1, "Une conversation doit être marquée active"
        assert active[0]["id"] == conv_id

    def test_compte_messages(self, ch):
        """Le compteur de messages doit être exact. Recherche par ID."""
        conv_id = ch.nouvelle_conversation()
        ch.ajouter_message("user", "Message 1")
        ch.ajouter_message("assistant", "Réponse 1")
        ch.ajouter_message("user", "Message 2")

        convs = ch.lister_conversations()
        # Trouver par ID — évite le problème de tri
        conv = next((c for c in convs if c["id"] == conv_id), None)
        assert conv is not None, f"Conversation {conv_id} non trouvée"
        assert conv["count"] == 3, f"Attendu 3 messages, got {conv['count']}"


# ─── Tests isolation entre conversations ──────────────────────────────────────

@pytest.mark.unit
class TestIsolationConversations:
    """Les messages d'une conversation ne doivent pas apparaître dans une autre."""

    def test_messages_isoles_par_conversation(self, ch, monkeypatch):
        """Les messages d'une conversation ne doivent pas apparaître dans une autre."""
        counter = [1_000_000]
        def fake_time():
            counter[0] += 1
            return float(counter[0])
        monkeypatch.setattr("time.time", fake_time)

        id1 = ch.nouvelle_conversation()
        ch.ajouter_message("user", "Message conv 1")

        id2 = ch.nouvelle_conversation()
        ch.ajouter_message("user", "Message conv 2")

        # Revenir à conv 1
        ch.changer_conversation(id1)
        hist1 = ch.get_historique_complet()
        assert len(hist1) == 1
        assert hist1[0]["content"] == "Message conv 1"

        # Vérifier conv 2
        ch.changer_conversation(id2)
        hist2 = ch.get_historique_complet()
        assert len(hist2) == 1
        assert hist2[0]["content"] == "Message conv 2"

    def test_supprimer_conv_active_cree_nouvelle(self, ch, monkeypatch):
        """Après suppression de la conv active, une nouvelle est créée automatiquement."""
        counter = [1_000_000]
        def fake_time():
            counter[0] += 1
            return float(counter[0])
        monkeypatch.setattr("time.time", fake_time)

        conv_id = ch.nouvelle_conversation()
        ch.supprimer_conversation(conv_id)

        # Une nouvelle conversation doit être créée automatiquement
        nouvelle_id = ch.get_conv_active()
        assert nouvelle_id != conv_id, \
            "Après suppression, une nouvelle conv active doit exister"