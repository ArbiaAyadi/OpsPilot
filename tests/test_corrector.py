"""
test_corrector.py — Tests du correcteur post-LLM.
"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def get_corrector():
    try:
        from agent.websocket_handler import _corriger_reponse_llm
        return _corriger_reponse_llm
    except ImportError:
        pytest.skip("websocket_handler non disponible")


@pytest.mark.unit
class TestVMIDFloor:
    def test_vmid_100_remplace_par_104(self):
        fn = get_corrector()
        result = fn("pct create 100 local:vztmpl/debian.tar.zst", 100)
        assert "pct create 104" in result

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
        fn = get_corrector()
        result = fn("pct create 105 local:vztmpl/debian.tar.zst", 105)
        assert "pct create 105" in result

    def test_next_vmid_104_quand_inferieur(self):
        fn = get_corrector()
        result = fn("pct create 102 local:vztmpl/debian.tar.zst", 102)
        assert "pct create 104" in result


@pytest.mark.unit
class TestPctCreate:
    def test_pct_create_vmid_corrige(self):
        fn = get_corrector()
        result = fn(
            "pct create 99 local:vztmpl/debian-12.tar.zst "
            "--hostname test --memory 256 --rootfs local-lvm:2 --cores 1",
            104
        )
        assert "pct create 104" in result

    def test_pct_create_preserve_autres_args(self):
        fn = get_corrector()
        result = fn(
            "pct create 100 local:vztmpl/debian-12.tar.zst "
            "--hostname test-lxc --memory 256 --cores 1",
            104
        )
        assert "--memory 256" in result
        assert "--cores 1" in result
        assert "--hostname test-lxc" in result


@pytest.mark.unit
class TestPctStart:
    def test_pct_start_corrige(self):
        fn = get_corrector()
        result = fn("pct start 100", 104)
        assert "pct start 104" in result

    def test_pct_start_incoherent_corrige(self):
        fn = get_corrector()
        response = "pct create 102 ...\npct start 100"
        result = fn(response, 104)
        assert "pct create 104" in result
        assert "pct start 104" in result


@pytest.mark.unit
class TestDiskParam:
    def test_disk_param_supprime(self):
        fn = get_corrector()
        result = fn("pct create 104 debian.tar.zst --disk 5 --memory 256", 104)
        assert "--disk" not in result

    def test_disk_param_supprime_avec_valeur_differente(self):
        fn = get_corrector()
        result = fn("pct create 104 debian.tar.zst --disk 10 --cores 1", 104)
        assert "--disk" not in result

    def test_rootfs_preservee(self):
        fn = get_corrector()
        result = fn("pct create 104 debian.tar.zst --disk 5 --rootfs local-lvm:2", 104)
        assert "--disk" not in result
        assert "--rootfs local-lvm:2" in result


@pytest.mark.unit
class TestCpuParam:
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


@pytest.mark.unit
class TestNet0Param:
    def test_net0_vmbr0_seul_corrige(self):
        fn = get_corrector()
        result = fn("pct create 104 debian.tar.zst --net0 vmbr0", 104)
        assert "name=eth0,bridge=vmbr0,ip=dhcp" in result

    def test_net0_format_complet_inchange(self):
        fn = get_corrector()
        cmd = "pct create 104 debian.tar.zst --net0 name=eth0,bridge=vmbr0,ip=dhcp"
        result = fn(cmd, 104)
        assert "name=eth0,bridge=vmbr0,ip=dhcp" in result


@pytest.mark.unit
class TestTemplateParam:
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


@pytest.mark.unit
class TestQmSet:
    """
    ← Mis à jour : _corriger_reponse_llm() a maintenant un 3e paramètre
    vmids_connus -- seul un VMID absent de cette liste (halluciné) est
    forcé vers 101. Un VMID réel et connu (ex: 103) n'est plus jamais
    réécrit (voir docstring de websocket_handler.py, correction #3).
    """
    def test_qm_set_102_corrige_vers_101_si_inconnu(self):
        fn = get_corrector()
        result = fn("qm set 102 --balloon 512", 104, vmids_connus=[101])
        assert "qm set 101" in result

    def test_qm_set_100_corrige_vers_101_si_inconnu(self):
        fn = get_corrector()
        result = fn("qm set 100 --balloon 512", 104, vmids_connus=[101])
        assert "qm set 101" in result

    def test_qm_set_101_inchange(self):
        fn = get_corrector()
        result = fn("qm set 101 --balloon 512", 104, vmids_connus=[101])
        assert "qm set 101 --balloon 512" in result

    def test_qm_set_balloon_valeur_preservee(self):
        fn = get_corrector()
        result = fn("qm set 102 --balloon 1024", 104, vmids_connus=[101])
        assert "--balloon 1024" in result

    def test_qm_set_vmid_reel_non_101_preserve(self):
        """LE VRAI CORRECTIF TESTÉ ICI : qm set 103 (linux-vm2, une VM
        réelle) ne doit PLUS être réécrit vers 101 -- c'était le bug
        corrigé cette session (toute VM différente de 101 était écrasée,
        y compris une VM réelle et valide comme 103)."""
        fn = get_corrector()
        result = fn("qm set 103 --balloon 512", 104, vmids_connus=[101, 103])
        assert "qm set 103 --balloon 512" in result, \
            "VMID 103 est une VM réellement connue -- ne doit jamais être réécrit vers 101"

    def test_qm_set_sans_vmids_connus_replie_sur_101(self):
        """Sans liste fournie (vmids_connus=None, comportement par défaut),
        tout VMID est traité comme halluciné -- comportement de repli
        sûr, cohérent avec l'ancien comportement pour un appelant qui ne
        fournit pas encore ce paramètre."""
        fn = get_corrector()
        result = fn("qm set 103 --balloon 512", 104)
        assert "qm set 101" in result


@pytest.mark.unit
class TestPveamDownload:
    def test_lubuntu_remplace_par_debian12(self):
        fn = get_corrector()
        result = fn("pveam download local lubuntu", 104)
        assert "debian-12-standard_12.7-1_amd64.tar.zst" in result

    def test_ubuntu_remplace_par_debian12(self):
        fn = get_corrector()
        result = fn("pveam download local ubuntu-20.04", 104)
        assert "debian-12" in result

    def test_debian12_inchange(self):
        fn = get_corrector()
        result = fn("pveam download local debian-12-standard_12.7-1_amd64.tar.zst", 104)
        assert "debian-12-standard_12.7-1_amd64.tar.zst" in result


@pytest.mark.unit
class TestRootfsMinimum:
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


@pytest.mark.unit
class TestScenarioComplet:
    def test_reponse_lxc_complete(self, sample_response_lxc):
        fn = get_corrector()
        result = fn(sample_response_lxc, 104, vmids_connus=[101])

        assert "pct create 104" in result
        assert "pct start 104" in result

        assert "--disk" not in result
        assert "--cpu" not in result
        assert "--template" not in result

        assert "--cores" in result or "cores" in result
        assert "name=eth0,bridge=vmbr0,ip=dhcp" in result

        assert "debian-12" in result

        assert "local-lvm:1" not in result

    def test_reponse_sans_erreur_inchangee(self):
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