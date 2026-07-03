"""
conftest.py — Fixtures partagées pour tous les tests OpsPilot.

Stratégie de mocking :
- Proxmox API → remplacée par un état cluster factice réaliste
- Groq API    → remplacée par une réponse LLM factice
- Database    → remplacée par des no-ops (aucun accès disque)
- Prometheus  → remplacé par des métriques factices

Cela permet d'exécuter tous les tests sans Proxmox, Groq ni PostgreSQL.
"""
import sys
import os
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from pathlib import Path

# ── Ajouter la racine du projet au path Python ─────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ── Enregistrer proxmox_api dans sys.modules pour le mocking ──────────────
# patch("proxmox_api.get_etat_cluster") requiert que le module soit déjà
# importé ou enregistré dans sys.modules. Sans cela : ModuleNotFoundError.
try:
    import proxmox_api          # noqa — enregistre le vrai module
except Exception:
    # Si proxmox_api n'est pas importable (CI sans Proxmox), créer un mock
    _proxmox_mock = MagicMock()
    _proxmox_mock.get_etat_cluster = MagicMock(return_value={})
    sys.modules['proxmox_api'] = _proxmox_mock

# ── Protection de conversations.json pendant toute la session de tests ────────
# Sans cela, les tests écrivent dans le vrai fichier et cassent l'UI du chat.

@pytest.fixture(scope="session", autouse=True)
def proteger_conversations_json():
    """
    Fixture session automatique — s'exécute UNE FOIS pour toute la session pytest.

    Sauvegarde conversations.json AVANT les tests et le restaure APRÈS.
    Garantit que les tests n'affectent jamais l'UI du chat en production.
    """
    import shutil
    import os

    try:
        import agent.chat_history as ch_mod
        conv_file = ch_mod.CONVERSATIONS_FILE
    except Exception:
        conv_file = str(ROOT / "conversations.json")

    backup_file = conv_file + ".pytest_backup"

    # Sauvegarder le fichier original
    if os.path.exists(conv_file):
        shutil.copy2(conv_file, backup_file)
    
    yield  # ← Tous les tests s'exécutent ici
    
    # Restaurer le fichier original après TOUS les tests
    if os.path.exists(backup_file):
        shutil.copy2(backup_file, conv_file)
        os.remove(backup_file)
    elif os.path.exists(conv_file):
        # Si pas de backup (fichier n'existait pas avant), remettre vide
        import json
        with open(conv_file, 'w') as f:
            json.dump({"conversations": {}, "active": None}, f)



# ─── État cluster factice réaliste ────────────────────────────────────────────
ETAT_CLUSTER_NORMAL = {
    "noeuds": [
        {
            "nom": "pve1", "statut": "online",
            "cpu_pct": 15.0, "cpu_cores": 2,
            "ram_pct": 45.0, "ram_used_gb": 0.85, "ram_total_gb": 1.9,
            "disk_pct": 44.8, "disk_used_gb": 9.0, "disk_total_gb": 20.0,
            "net_in_mbps": 5.0, "net_out_mbps": 2.0,
            "swap_pct": 15.5, "swap_used_gb": 0.3, "swap_total_gb": 2.0,
            "cpu_iowait_pct": 0.01,
            "disk_read_iops": 50.0, "disk_write_iops": 30.0,
            "disk_read_latency_ms": 0.5, "disk_write_latency_ms": 1.0,
            "net_errors_in": 0.0, "net_errors_out": 0.0,
            "net_drop_in": 0.0, "net_drop_out": 0.0,
            "cpu_temp_max_c": 0.0, "smart_ok": True,
            "smart_reallocated_sectors": 0, "smart_disks_monitored": 0,
            "zfs_arc_hit_rate": 0.0, "zfs_arc_size_gb": 0.0, "zfs_available": False,
            "corosync_ok": True, "corosync_quorum_ok": True,
            "uptime_h": 2.5, "vms_running": 0,
            "load_avg_1m": 0.2, "fd_used_pct": 5.0,
            "net_available": True, "io_available": True,
            "hw_available": False, "smart_available": False,
        },
        {
            "nom": "pve2", "statut": "offline",
            "cpu_pct": 0.0, "cpu_cores": 0,
            "ram_pct": 0.0, "ram_used_gb": 0.0, "ram_total_gb": 0.0,
            "disk_pct": 0.0, "disk_used_gb": 0.0, "disk_total_gb": 0.0,
            "net_in_mbps": 0.0, "net_out_mbps": 0.0,
            "swap_pct": 0.0, "cpu_iowait_pct": 0.0,
            "disk_read_iops": 0.0, "disk_write_iops": 0.0,
            "disk_read_latency_ms": 0.0, "disk_write_latency_ms": 0.0,
            "net_errors_in": 0.0, "net_errors_out": 0.0,
            "net_drop_in": 0.0, "net_drop_out": 0.0,
            "cpu_temp_max_c": 0.0, "smart_ok": True,
            "smart_reallocated_sectors": 0, "smart_disks_monitored": 0,
            "zfs_arc_hit_rate": 0.0, "zfs_arc_size_gb": 0.0, "zfs_available": False,
            "corosync_ok": True, "corosync_quorum_ok": True,
            "uptime_h": 0.0, "vms_running": 0,
            "load_avg_1m": 0.0, "fd_used_pct": 0.0,
            "net_available": False, "io_available": False,
            "hw_available": False, "smart_available": False,
        },
    ],
    "vms": [
        {
            "vmid": "101", "nom": "linux-vm1", "statut": "stopped",
            "noeud": "pve1", "vcpus": 1, "maxmem_gb": 1.0, "maxdisk_gb": 10.0,
            "cpu_pct": 0.0, "ram_pct": 0.0,
            "net_in_mbps": 0.0, "net_out_mbps": 0.0, "uptime_h": 0.0,
        }
    ],
    "alertes": [],
    "vms_running": 0,
    "net_in_mbps": 5.0, "net_out_mbps": 2.0,
}

ETAT_CLUSTER_RAM_CRITIQUE = {
    **ETAT_CLUSTER_NORMAL,
    "noeuds": [
        {**ETAT_CLUSTER_NORMAL["noeuds"][0], "ram_pct": 87.0, "ram_used_gb": 1.65},
        ETAT_CLUSTER_NORMAL["noeuds"][1],
    ],
    "alertes": [{"niveau": "CRITIQUE", "message": "RAM high: 87%", "cible": "pve1"}],
}

# ─── Métriques ML ─────────────────────────────────────────────────────────────
METRIQUES_NORMALES = {
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


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def etat_normal():
    """État cluster normal — pve1 online, pve2 offline."""
    return ETAT_CLUSTER_NORMAL.copy()


@pytest.fixture(scope="session")
def etat_ram_critique():
    """État cluster avec RAM critique sur pve1."""
    return ETAT_CLUSTER_RAM_CRITIQUE.copy()


@pytest.fixture(scope="session")
def metriques_normales():
    """Métriques ML pour un cluster stable."""
    return METRIQUES_NORMALES.copy()


@pytest.fixture(scope="session")
def ml_analyser():
    """Instance MLAnalyseur chargée une seule fois pour toute la session."""
    try:
        from ml_analyser import MLAnalyseur
        return MLAnalyseur()
    except Exception as e:
        pytest.skip(f"MLAnalyseur non disponible: {e}")


@pytest.fixture
def client(tmp_path):
    """
    Client HTTP FastAPI pour les tests d'intégration.
    Mocke Proxmox, Groq et la DB pour ne pas avoir besoin de services externes.
    """
    # Mock DB
    db_mock = MagicMock()
    db_mock.return_value = None

    # Mock Groq
    groq_mock = MagicMock(return_value="Test LLM response")

    # Mock Proxmox
    proxmox_mock = MagicMock(return_value=ETAT_CLUSTER_NORMAL)

    # Mock conversations.json path pour éviter les conflits
    os.environ["OPSPILOT_CONV_FILE"] = str(tmp_path / "conversations.json")

    with patch("proxmox_api.get_etat_cluster", proxmox_mock), \
         patch("agent.groq_client.appeler_groq", groq_mock), \
         patch("database.sauvegarder_metriques", db_mock), \
         patch("database.sauvegarder_anomalie", db_mock):

        try:
            from fastapi.testclient import TestClient
            from agent.main import app
            yield TestClient(app)
        except Exception as e:
            pytest.skip(f"App FastAPI non disponible: {e}")


@pytest.fixture
def sample_response_lxc():
    """Réponse LLM typique avec erreurs de syntaxe pct create."""
    return """Pour créer un conteneur LXC :

```bash
pveam download local lubuntu

pct create 100 local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst \\
  --hostname test-100 \\
  --memory 256 \\
  --disk 5 \\
  --cpu 1 \\
  --net0 vmbr0 \\
  --rootfs local-lvm:1 \\
  --template ubuntu.tar.gz

pct start 100
qm set 102 --balloon 512
```
"""


@pytest.fixture
def ch(tmp_path):
    """
    Fixture chat_history — état propre + restauration complète après le test.

    PROBLÈME RÉSOLU : sans restauration, sauvegarder_conversations() écrit
    les conversations de test dans le vrai conversations.json, qui apparaissent
    ensuite dans l'UI du chat OpsPilot comme conversations fantômes.
    """
    import agent.chat_history as chat_module

    # Sauvegarder état original
    orig_file   = chat_module.CONVERSATIONS_FILE
    orig_convs  = dict(chat_module._conversations)
    orig_active = chat_module._conv_active

    # Isoler : état vide + fichier temporaire dédié au test
    chat_module._conversations.clear()
    chat_module._conv_active = None
    chat_module.CONVERSATIONS_FILE = str(tmp_path / "conversations_test.json")

    yield chat_module

    # RESTAURER l'état original après le test — empêche la pollution de l'UI
    chat_module._conversations.clear()
    chat_module._conversations.update(orig_convs)
    chat_module._conv_active = orig_active
    chat_module.CONVERSATIONS_FILE = orig_file