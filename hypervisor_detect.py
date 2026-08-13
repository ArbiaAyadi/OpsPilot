"""
hypervisor_detect.py — Détection de la couche hyperviseur sous Proxmox.

Permet à l'agent de donner des recommandations EXACTES selon le contexte :
  - Proxmox sur VMware Workstation → recommander d'augmenter la RAM VMware
  - Proxmox sur serveur physique → recommander d'ajouter des barrettes RAM
  - Proxmox sur Hyper-V / KVM / autre → recommandations adaptées

Méthode : SSH direct (paramiko) vers le nœud, pas l'API Proxmox /execute.
/execute est restreint en dur par Proxmox à une vraie session root@pam --
AUCUN token API, même avec le rôle Administrator, ne peut jamais l'utiliser
("Permission check failed (user != root@pam)"). Ce n'est pas un problème de
permissions à corriger, c'est une limite structurelle de Proxmox. Le SSH
direct (déjà utilisé ailleurs dans ce projet, voir get_gpu_utilisation_ssh
dans proxmox_api.py) contourne complètement cette restriction.
"""
import os
import requests
import urllib3
from dotenv import load_dotenv

load_dotenv()

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HOST         = os.getenv("PROXMOX_HOST", "192.168.138.100")
TOKEN_ID     = os.getenv("PROXMOX_TOKEN_ID", "root@pam!opspilot")
TOKEN_SECRET = os.getenv("PROXMOX_TOKEN_SECRET", "")
VERIFY_SSL   = os.getenv("PROXMOX_VERIFY_SSL", "false").lower() == "true"
HEADERS      = {"Authorization": f"PVEAPIToken={TOKEN_ID}={TOKEN_SECRET}"}

# IP directe de chaque nœud connu -- pour le SSH, pas pour l'API Proxmox.
NODE_IP_MAP = {
    "pve1": os.getenv("PVE1_IP", "192.168.138.100"),
    "pve2": os.getenv("PVE2_IP", "192.168.138.101"),
}

# Identifiants SSH -- même variables que get_gpu_utilisation_ssh() dans
# proxmox_api.py, pour rester cohérent avec le reste du projet.
SSH_USER     = os.getenv("SSH_USER", "root")
SSH_PASSWORD = os.getenv("SSH_PASSWORD", "")

# Override manuel optionnel dans .env -- utile uniquement si SSH_PASSWORD
# n'est pas configuré, ou pour un nœud isolé. Dans le cas normal, la
# détection SSH ci-dessous suffit et cette variable n'a pas besoin d'exister.
HYPERVISOR_TYPE_OVERRIDE = os.getenv("HYPERVISOR_TYPE", "")


def _ssh_command(host: str, command: str) -> str | None:
    """Exécute une commande en SSH sur un nœud. Retourne None si échec."""
    try:
        import paramiko
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            host, port=22,
            username=SSH_USER,
            password=SSH_PASSWORD,
            timeout=8,
        )
        _, stdout, _ = ssh.exec_command(command)
        result = stdout.read().decode().strip()
        ssh.close()
        return result
    except ImportError:
        print("[Hypervisor] paramiko non installe -- pip install paramiko --break-system-packages")
        return None
    except Exception as e:
        print(f"[Hypervisor] SSH {host} indisponible: {e}")
        return None


def detecter_hyperviseur(node: str = None) -> dict:
    """
    Détecte la couche hyperviseur sous Proxmox, via SSH direct sur le nœud.

    Returns:
        {
            "type":        "vmware" | "kvm" | "hyper-v" | "physical" | "unknown"
            "produit":     "VMware Workstation" | "VMware ESXi" | ...
            "description": texte lisible pour les recommandations
            "recommandation_ram": texte spécifique pour ce type d'hyperviseur
        }
    """
    # Override manuel dans .env
    if HYPERVISOR_TYPE_OVERRIDE:
        return _creer_contexte(HYPERVISOR_TYPE_OVERRIDE, "Configured via .env")

    if not node:
        node = _get_premier_noeud_online()
    if not node:
        return _contexte_inconnu()

    node_ip = NODE_IP_MAP.get(node, HOST)

    # Détecter via systemd-detect-virt (méthode la plus fiable)
    virt = _ssh_command(node_ip, "systemd-detect-virt")
    if virt is not None:
        virt_lower = virt.lower()
        if "vmware" in virt_lower:
            return _creer_contexte("vmware", "systemd-detect-virt (SSH): vmware")
        if "kvm" in virt_lower or "qemu" in virt_lower:
            return _creer_contexte("kvm", "systemd-detect-virt (SSH): kvm")
        if "microsoft" in virt_lower or "hyper-v" in virt_lower:
            return _creer_contexte("hyper-v", "systemd-detect-virt (SSH): hyper-v")
        if virt_lower in ("none", ""):
            return _creer_contexte("physical", "systemd-detect-virt (SSH): none")

    # Fallback : lire le produit DMI
    produit = _ssh_command(node_ip, "cat /sys/class/dmi/id/product_name")
    if produit:
        produit_lower = produit.lower()
        if "vmware" in produit_lower:
            return _creer_contexte("vmware", f"DMI (SSH): {produit_lower}")
        if "virtual" in produit_lower or "kvm" in produit_lower:
            return _creer_contexte("kvm", f"DMI (SSH): {produit_lower}")

    return _contexte_inconnu()


def _get_premier_noeud_online() -> str:
    """Récupère le premier nœud Proxmox accessible (via l'API, en lecture seule -- pas /execute)."""
    try:
        r = requests.get(
            f"https://{HOST}:8006/api2/json/nodes",
            headers=HEADERS,
            verify=VERIFY_SSL,
            timeout=5,
        )
        r.raise_for_status()
        nodes = r.json().get("data", [])
        online = [n["node"] for n in nodes if n.get("status") == "online"]
        return online[0] if online else None
    except Exception:
        return None


def _creer_contexte(type_virt: str, source: str) -> dict:
    """Crée le contexte hyperviseur avec recommandations spécifiques."""
    contextes = {
        "vmware": {
            "type":    "vmware",
            "produit": "VMware Workstation / ESXi",
            "source":  source,
            "description": (
                "Proxmox runs as a VM inside VMware. "
                "Physical RAM limit is set by VMware VM settings."
            ),
            "recommandation_ram": (
                "⚠ Proxmox is running inside VMware — the '1.9GB RAM' is the VMware allocation, "
                "not physical server RAM. To add RAM: "
                "Shut down the VMware VM → Edit Settings → Memory → Increase to 4GB or more → Start. "
                "This is faster than adding physical RAM."
            ),
            "recommandation_cpu": (
                "To add CPU: VMware VM Settings → Processors → increase cores. "
                "Make sure your physical PC has enough cores available."
            ),
            "recommandation_disk": (
                "To expand disk: VMware VM Settings → Hard Disk → Expand. "
                "Then resize inside Proxmox: sgdisk, pvresize, lvresize."
            ),
        },
        "kvm": {
            "type":    "kvm",
            "produit": "KVM / QEMU Hypervisor",
            "source":  source,
            "description": "Proxmox runs as a VM inside KVM/QEMU.",
            "recommandation_ram": (
                "Proxmox is a KVM guest. To add RAM: update the VM config in the host KVM "
                "and restart. If using libvirt: virsh setmaxmem <vm> <size>."
            ),
            "recommandation_cpu": "Update vCPU count in the KVM host configuration.",
            "recommandation_disk": "Add a new virtual disk via the KVM host.",
        },
        "hyper-v": {
            "type":    "hyper-v",
            "produit": "Microsoft Hyper-V",
            "source":  source,
            "description": "Proxmox runs as a VM inside Hyper-V.",
            "recommandation_ram": (
                "Proxmox is a Hyper-V guest. To add RAM: Hyper-V Manager → VM Settings → Memory → "
                "increase allocation → restart VM."
            ),
            "recommandation_cpu": "Hyper-V Manager → VM Settings → Processor → increase count.",
            "recommandation_disk": "Hyper-V Manager → VM Settings → SCSI Controller → add virtual disk.",
        },
        "physical": {
            "type":    "physical",
            "produit": "Physical Server (Bare Metal)",
            "source":  source,
            "description": "Proxmox runs directly on physical hardware.",
            "recommandation_ram": (
                "Add physical DDR4/DDR5 RAM modules to the server. "
                "Check current slots: dmidecode -t 17 | grep -E 'Size|Type|Speed'. "
                "After installation, no reboot needed — Proxmox uses memory hot-add."
            ),
            "recommandation_cpu": "Add CPU cores or upgrade to a higher-core processor (requires maintenance window).",
            "recommandation_disk": "Add physical disk and configure in Proxmox storage: pvesm add.",
        },
    }
    return contextes.get(type_virt, _contexte_inconnu())


def _contexte_inconnu() -> dict:
    return {
        "type":    "unknown",
        "produit": "Unknown hypervisor",
        "source":  "detection failed",
        "description": "Could not detect the underlying hypervisor.",
        "recommandation_ram": (
            "Unable to detect if Proxmox runs on physical hardware or a VM. "
            "Check manually: systemd-detect-virt. "
            "If physical: add RAM modules. If virtual: increase VM memory allocation."
        ),
        "recommandation_cpu": "Check hypervisor type first: systemd-detect-virt",
        "recommandation_disk": "Check hypervisor type first: systemd-detect-virt",
    }


# ── Cache intelligent ────────────────────────────────────────────────────────
_hypervisor_cache = None

def get_hypervisor_context() -> dict:
    """Retourne le contexte hyperviseur (avec cache -- ne retient que les succès)."""
    global _hypervisor_cache
    if _hypervisor_cache is not None and _hypervisor_cache.get("type") != "unknown":
        return _hypervisor_cache

    resultat = detecter_hyperviseur()
    if resultat.get("type") != "unknown":
        _hypervisor_cache = resultat
        print(f"[Hypervisor] Détecté: {resultat['type']} ({resultat['produit']})")
    else:
        print("[Hypervisor] Détection non concluante -- nouvel essai au prochain cycle")
    return resultat