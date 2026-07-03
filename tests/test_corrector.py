"""
test_corrector.py — Tests du correcteur post-LLM.

Le LLM llama-3.1-8b-instant génère systématiquement des erreurs
de syntaxe Proxmox. Ces tests vérifient que _corriger_reponse_llm()
les corrige toutes avant d'envoyer la réponse au frontend.

Couverture : 10 corrections (VMID, pct create, pct start, --disk,
--cpu, --net0, --template, qm set, pveam, --rootfs)
"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def get_corrector():
    """Importer la fonction correctrice depuis websocket_handler."""
    try:
        from agent.websocket_handler import _corriger_reponse_llm
        return _corriger_reponse_llm
    except ImportError:
        pytest.skip("websocket_handler non disponible")


# ─── Correction 1 : VMID plancher 104 ─────────────────────────────────────────

@pytest.mark.unit
class TestVMIDFloor:
    """Le correcteur force next_vmid >= 104 car pve2 OFFLINE cache VMID 103."""

    def test_vmid_100_remplace_par_104(self):
        fn = get_corrector()
        result = fn("pct create 100 local:vztmpl/debian.tar.zst", 100)
        assert "pct create 104" in result, "VMID 100 doit être remplacé par 104"

    def test_vmid_101_remplace_par_104(self):
        fn = get_corrector()
        result = fn("pct create 101 local:vztmpl/debian.tar.zst", 101)
        assert "pct create 104" in result

    def test_vmid_102_remplace_par_104(self):
        fn = get_corrector()
        result = fn("pct create 102 local:vztmpl/debian.tar.zst", 102)
        assert "pct create 104" in result

    def test_vmid_103_remplace_par_104(self):
        fn = get_corrector()
        result = fn("pct create 103 local:vztmpl/debian.tar.zst", 103)
        assert "pct create 104" in result

    def test_vmid_104_inchange(self):
        fn = get_corrector()
        result = fn("pct create 104 local:vztmpl/debian.tar.zst", 104)
        assert "pct create 104" in result

    def test_vmid_105_reste_105(self):
        """next_vmid=105 → VMID 105 valide, pas de remplacement."""
        fn = get_corrector()
        result = fn("pct create 105 local:vztmpl/debian.tar.zst", 105)
        assert "pct create 105" in result

    def test_next_vmid_104_quand_inferieur(self):
        """Si next_vmid calculé ≤ 103, le plancher force 104."""
        fn = get_corrector()
        # next_vmid=102 (seul VMID connu = 101) → plancher → 104
        result = fn("pct create 102 local:vztmpl/debian.tar.zst", 102)
        assert "pct create 104" in result


# ─── Correction 2 : pct create VMID cohérent ──────────────────────────────────

@pytest.mark.unit
class TestPctCreate:
    """pct create <wrong_vmid> → pct create <next_vmid>."""

    def test_pct_create_vmid_corrige(self):
        fn = get_corrector()
        result = fn(
            "pct create 99 local:vztmpl/debian-12.tar.zst "
            "--hostname test --memory 256 --rootfs local-lvm:2 --cores 1",
            104
        )
        assert "pct create 104" in result

    def test_pct_create_preserve_autres_args(self):
        """Les autres arguments ne doivent pas être supprimés."""
        fn = get_corrector()
        result = fn(
            "pct create 100 local:vztmpl/debian-12.tar.zst "
            "--hostname test-lxc --memory 256 --cores 1",
            104
        )
        assert "--memory 256" in result
        assert "--cores 1" in result
        assert "--hostname test-lxc" in result


# ─── Correction 3 : pct start cohérent ────────────────────────────────────────

@pytest.mark.unit
class TestPctStart:
    """pct start doit utiliser le même VMID que pct create."""

    def test_pct_start_corrige(self):
        fn = get_corrector()
        result = fn("pct start 100", 104)
        assert "pct start 104" in result

    def test_pct_start_incoherent_corrige(self):
        """Le LLM crée VMID 102 mais démarre VMID 100 — les deux corrigés."""
        fn = get_corrector()
        response = "pct create 102 ...\npct start 100"
        result = fn(response, 104)
        assert "pct create 104" in result
        assert "pct start 104" in result


# ─── Correction 4 : --disk invalide supprimé ──────────────────────────────────

@pytest.mark.unit
class TestDiskParam:
    """--disk n'existe pas pour pct create (c'est --rootfs)."""

    def test_disk_param_supprime(self):
        fn = get_corrector()
        result = fn("pct create 104 debian.tar.zst --disk 5 --memory 256", 104)
        assert "--disk" not in result

    def test_disk_param_supprime_avec_valeur_differente(self):
        fn = get_corrector()
        result = fn("pct create 104 debian.tar.zst --disk 10 --cores 1", 104)
        assert "--disk" not in result

    def test_rootfs_preservee(self):
        """--rootfs doit rester intact quand --disk est supprimé."""
        fn = get_corrector()
        result = fn("pct create 104 debian.tar.zst --disk 5 --rootfs local-lvm:2", 104)
        assert "--disk" not in result
        assert "--rootfs local-lvm:2" in result


# ─── Correction 5 : --cpu → --cores ──────────────────────────────────────────

@pytest.mark.unit
class TestCpuParam:
    """pct create utilise --cores, pas --cpu."""

    def test_cpu_remplace_par_cores(self):
        fn = get_corrector()
        result = fn("pct create 104 debian.tar.zst --cpu 1", 104)
        assert "--cores 1" in result
        assert "--cpu" not in result

    def test_cpu_2_remplace_par_cores_2(self):
        fn = get_corrector()
        result = fn("pct create 104 debian.tar.zst --cpu 2", 104)
        assert "--cores 2" in result

    def test_cores_deja_correct_inchange(self):
        fn = get_corrector()
        result = fn("pct create 104 debian.tar.zst --cores 1", 104)
        assert "--cores 1" in result


# ─── Correction 6 : --net0 format complet ─────────────────────────────────────

@pytest.mark.unit
class TestNet0Param:
    """--net0 doit avoir le format complet avec name=eth0,bridge=,ip=dhcp."""

    def test_net0_vmbr0_seul_corrige(self):
        fn = get_corrector()
        result = fn("pct create 104 debian.tar.zst --net0 vmbr0", 104)
        assert "name=eth0,bridge=vmbr0,ip=dhcp" in result

    def test_net0_format_complet_inchange(self):
        fn = get_corrector()
        cmd = "pct create 104 debian.tar.zst --net0 name=eth0,bridge=vmbr0,ip=dhcp"
        result = fn(cmd, 104)
        assert "name=eth0,bridge=vmbr0,ip=dhcp" in result


# ─── Correction 7 : --template invalide supprimé ─────────────────────────────

@pytest.mark.unit
class TestTemplateParam:
    """--template n'est pas un paramètre pct create valide."""

    def test_template_supprime(self):
        fn = get_corrector()
        result = fn(
            "pct create 104 local:vztmpl/debian.tar.zst --template debian.tar.gz",
            104
        )
        assert "--template" not in result

    def test_template_ubuntu_supprime(self):
        fn = get_corrector()
        result = fn("pct create 104 debian.tar.zst --template ubuntu-20.04.tar.gz", 104)
        assert "--template" not in result


# ─── Correction 8 : qm set VMID invalide ─────────────────────────────────────

@pytest.mark.unit
class TestQmSet:
    """qm set ne doit référencer que linux-vm1 (VMID 101) — le seul existant."""

    def test_qm_set_102_corrige_vers_101(self):
        fn = get_corrector()
        result = fn("qm set 102 --balloon 512", 104)
        assert "qm set 101" in result

    def test_qm_set_100_corrige_vers_101(self):
        fn = get_corrector()
        result = fn("qm set 100 --balloon 512", 104)
        assert "qm set 101" in result

    def test_qm_set_101_inchange(self):
        """VMID 101 = linux-vm1 existant — ne pas modifier."""
        fn = get_corrector()
        result = fn("qm set 101 --balloon 512", 104)
        assert "qm set 101 --balloon 512" in result

    def test_qm_set_balloon_valeur_preservee(self):
        """La valeur du balloon doit être préservée."""
        fn = get_corrector()
        result = fn("qm set 102 --balloon 1024", 104)
        assert "--balloon 1024" in result


# ─── Correction 9 : pveam download template valide ───────────────────────────

@pytest.mark.unit
class TestPveamDownload:
    """Le LLM invente des noms de templates. On force Debian 12."""

    def test_lubuntu_remplace_par_debian12(self):
        fn = get_corrector()
        result = fn("pveam download local lubuntu", 104)
        assert "debian-12-standard_12.7-1_amd64.tar.zst" in result

    def test_ubuntu_remplace_par_debian12(self):
        fn = get_corrector()
        result = fn("pveam download local ubuntu-20.04", 104)
        assert "debian-12" in result

    def test_debian12_inchange(self):
        """Template Debian 12 correct → ne pas modifier."""
        fn = get_corrector()
        result = fn("pveam download local debian-12-standard_12.7-1_amd64.tar.zst", 104)
        assert "debian-12-standard_12.7-1_amd64.tar.zst" in result


# ─── Correction 10 : --rootfs minimum 2GB ────────────────────────────────────

@pytest.mark.unit
class TestRootfsMinimum:
    """Debian 12 minimal nécessite au moins 2GB."""

    def test_rootfs_1gb_corrige_vers_2gb(self):
        fn = get_corrector()
        result = fn("pct create 104 debian.tar.zst --rootfs local-lvm:1", 104)
        assert "local-lvm:2" in result
        assert "local-lvm:1" not in result

    def test_rootfs_0gb_corrige_vers_2gb(self):
        fn = get_corrector()
        result = fn("pct create 104 debian.tar.zst --rootfs local-lvm:0", 104)
        assert "local-lvm:2" in result

    def test_rootfs_2gb_inchange(self):
        fn = get_corrector()
        result = fn("pct create 104 debian.tar.zst --rootfs local-lvm:2", 104)
        assert "local-lvm:2" in result

    def test_rootfs_5gb_inchange(self):
        fn = get_corrector()
        result = fn("pct create 104 debian.tar.zst --rootfs local-lvm:5", 104)
        assert "local-lvm:5" in result


# ─── Test scénario complet ────────────────────────────────────────────────────

@pytest.mark.unit
class TestScenarioComplet:
    """Test avec une réponse LLM réaliste contenant plusieurs erreurs."""

    def test_reponse_lxc_complete(self, sample_response_lxc):
        """
        Réponse LLM avec 7 erreurs simultanées.
        Après correction : commande pct create 100% valide.
        """
        fn = get_corrector()
        result = fn(sample_response_lxc, 104)

        # VMID correct
        assert "pct create 104" in result
        assert "pct start 104" in result

        # Paramètres invalides supprimés
        assert "--disk" not in result
        assert "--cpu" not in result
        assert "--template" not in result

        # Paramètres valides présents
        assert "--cores" in result or "cores" in result
        assert "name=eth0,bridge=vmbr0,ip=dhcp" in result

        # Template Debian 12
        assert "debian-12" in result

        # rootfs minimum
        assert "local-lvm:1" not in result

    def test_reponse_sans_erreur_inchangee(self):
        """Une réponse déjà correcte ne doit pas être altérée."""
        fn = get_corrector()
        reponse_correcte = (
            "pct create 104 local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst "
            "--hostname test-104 --memory 256 --swap 128 "
            "--rootfs local-lvm:2 --net0 name=eth0,bridge=vmbr0,ip=dhcp "
            "--cores 1 --unprivileged 1"
        )
        result = fn(reponse_correcte, 104)
        assert "pct create 104" in result
        assert "--rootfs local-lvm:2" in result
        assert "--cores 1" in result