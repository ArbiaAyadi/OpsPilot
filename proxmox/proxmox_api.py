import os
import requests
import urllib3
from dotenv import load_dotenv

load_dotenv()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PVE1_IP = os.getenv("PVE1_IP", "192.168.138.100")
PVE2_IP = os.getenv("PVE2_IP", "192.168.138.101")

HOST         = os.getenv("PROXMOX_HOST", "192.168.138.100")
TOKEN_ID     = os.getenv("PROXMOX_TOKEN_ID", "root@pam!opspilot")
TOKEN_SECRET = os.getenv("PROXMOX_TOKEN_SECRET", "")
VERIFY_SSL   = os.getenv("PROXMOX_VERIFY_SSL", "false").lower() == "true"

HEADERS  = {"Authorization": f"PVEAPIToken={TOKEN_ID}={TOKEN_SECRET}"}

# ── Correspondance nom de nœud -> IP directe ──────────────────────────────────
# Utilisée par get_vms() pour interroger chaque nœud SUR SA PROPRE IP plutôt
# que de toujours passer par HOST (qui devrait alors relayer la requête en
# interne si ce n'est pas le même nœud -- lent si le nœud cible est chargé).
# Un nœud absent de cette liste (ex: nouveau nœud ajouté au cluster) retombe
# automatiquement sur l'ancien comportement de relais -- toujours détecté,
# juste un peu moins vite tant que son IP n'est pas ajoutée ici.
NODE_IP_MAP = {
    "pve1": PVE1_IP,
    "pve2": PVE2_IP,
}


# ══════════════════════════════════════════════════════════════════════════════
# Base
# ══════════════════════════════════════════════════════════════════════════════

def _get(endpoint: str, timeout: int = 5, host_override: str = None):
    # BASE_URL recalculé à chaque appel pour que le fallback fonctionne.
    # host_override : interroger une IP précise sans changer le HOST global
    # (utilisé par get_vms() pour parler à chaque nœud directement).
    base_url = f"https://{host_override or HOST}:8006/api2/json"
    try:
        r = requests.get(f"{base_url}{endpoint}", headers=HEADERS, verify=VERIFY_SSL, timeout=timeout)
        r.raise_for_status()
        return r.json().get("data")
    except Exception as e:
        print(f"[Proxmox API] {endpoint}: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Noeuds
# ══════════════════════════════════════════════════════════════════════════════

def get_noeuds() -> list[dict]:
    noeuds = _get("/nodes") or []
    result = []
    for n in noeuds:
        maxmem  = max(n.get("maxmem", 1), 1)
        maxdisk = max(n.get("maxdisk", 1), 1)
        result.append({
            "nom":          n.get("node"),
            "statut":       n.get("status"),
            "cpu_pct":      round(n.get("cpu", 0) * 100, 1),
            "cpu_cores":    n.get("maxcpu", 0),
            "ram_used_gb":  round(n.get("mem", 0) / 1024**3, 1),
            "ram_total_gb": round(maxmem / 1024**3, 1),
            "ram_pct":      round(n.get("mem", 0) / maxmem * 100, 1),
            "disk_used_gb": round(n.get("disk", 0) / 1024**3, 1),
            "disk_total_gb":round(maxdisk / 1024**3, 1),
            "disk_pct":     round(n.get("disk", 0) / maxdisk * 100, 1),
            "uptime_h":     round(n.get("uptime", 0) / 3600, 1),
        })
    return result


# ══════════════════════════════════════════════════════════════════════════════
# VMs
# ══════════════════════════════════════════════════════════════════════════════

def get_vms(noeud: str = None) -> list[dict]:
    noeuds_list = [noeud] if noeud else [n["nom"] for n in get_noeuds()]
    vms = []
    for node in noeuds_list:
        # ← Interroger directement l'IP du nœud si on la connaît (pve1/pve2)
        # -- évite le relais interne via HOST, donc insensible à sa charge.
        # Nœud inconnu (nouveau nœud jamais vu) -> relais habituel, toujours
        # détecté, juste potentiellement plus lent.
        ip_directe = NODE_IP_MAP.get(node)
        data = _get(f"/nodes/{node}/qemu", timeout=10, host_override=ip_directe) or []
        for vm in data:
            maxmem = max(vm.get("maxmem", 1), 1)
            vms.append({
                "vmid":         vm.get("vmid"),
                "nom":          vm.get("name", f"vm-{vm.get('vmid')}"),
                "noeud":        node,
                "statut":       vm.get("status"),
                "cpu_pct":      round(vm.get("cpu", 0) * 100, 1),
                "ram_used_gb":  round(vm.get("mem", 0) / 1024**3, 1),
                "ram_total_gb": round(maxmem / 1024**3, 1),
                "ram_pct":      round(vm.get("mem", 0) / maxmem * 100, 1),
                "disk_gb":      round(vm.get("disk", 0) / 1024**3, 1),
                "uptime_h":     round(vm.get("uptime", 0) / 3600, 1),
                "cpus":         vm.get("cpus", 1),
                "tags":         vm.get("tags", ""),
            })
    return vms


def get_vm_config(noeud: str, vmid: int) -> dict:
    ip_directe = NODE_IP_MAP.get(noeud)
    config = _get(f"/nodes/{noeud}/qemu/{vmid}/config", host_override=ip_directe) or {}
    return {
        "vmid":    vmid,
        "noeud":   noeud,
        "cores":   config.get("cores", 1),
        "sockets": config.get("sockets", 1),
        "memory":  config.get("memory", 512),
        "os":      config.get("ostype", "unknown"),
        "tags":    config.get("tags", ""),
        "description": config.get("description", ""),
        "gpu_passthrough": any(k.startswith("hostpci") for k in config),
        "hostpci_devices": [config[k] for k in config if k.startswith("hostpci")],
    }


def get_vm_status(noeud: str, vmid: int) -> dict:
    ip_directe = NODE_IP_MAP.get(noeud)
    s = _get(f"/nodes/{noeud}/qemu/{vmid}/status/current", host_override=ip_directe) or {}
    maxmem = max(s.get("maxmem", 1), 1)
    return {
        "vmid":        vmid,
        "statut":      s.get("status"),
        "cpu_pct":     round(s.get("cpu", 0) * 100, 1),
        "ram_used_gb": round(s.get("mem", 0) / 1024**3, 1),
        "ram_total_gb":round(maxmem / 1024**3, 1),
        "ram_pct":     round(s.get("mem", 0) / maxmem * 100, 1),
        "uptime_h":    round(s.get("uptime", 0) / 3600, 1),
    }


# ══════════════════════════════════════════════════════════════════════════════
# NIVEAU 3 — Allocation vs Usage
# ══════════════════════════════════════════════════════════════════════════════

def get_allocation_vs_usage() -> list[dict]:
    """
    Compare ce que chaque VM a ALLOUÉ vs ce qu'elle CONSOMME réellement.

    Résultat : identifie le gaspillage de ressources.
    Exemple : VM avec 8 GB alloués mais 1 GB utilisé = 87.5% gaspillé.

    Seuils :
      - RAM gaspillée > 70% → recommandation de réduction
      - CPU gaspillé  > 80% → recommandation de réduction
    """
    noeuds = get_noeuds()
    rapport = []

    for noeud in noeuds:
        node_name = noeud["nom"]
        ip_directe = NODE_IP_MAP.get(node_name)
        vms = _get(f"/nodes/{node_name}/qemu", host_override=ip_directe) or []

        for vm in vms:
            if vm.get("status") != "running":
                continue

            vmid   = vm.get("vmid")
            nom    = vm.get("name", f"vm-{vmid}")
            maxmem = max(vm.get("maxmem", 1), 1)
            cpus_alloues = vm.get("cpus", 1)

            ram_utilisee_gb   = round(vm.get("mem", 0) / 1024**3, 2)
            ram_allouee_gb    = round(maxmem / 1024**3, 2)
            ram_utilisee_pct  = round(vm.get("mem", 0) / maxmem * 100, 1)
            ram_gaspillee_pct = round((1 - vm.get("mem", 0) / maxmem) * 100, 1)

            cpu_utilise_pct  = round(vm.get("cpu", 0) * 100, 1)
            cpu_gaspille_pct = round((1 - vm.get("cpu", 0)) * 100, 1) if cpus_alloues > 0 else 0

            ram_recommandee_gb = round(ram_utilisee_gb * 1.3, 1)
            economie_ram_gb    = round(max(0, ram_allouee_gb - ram_recommandee_gb), 1)

            score_gaspillage = round((ram_gaspillee_pct * 0.6 + cpu_gaspille_pct * 0.4), 1)

            if score_gaspillage > 70:
                niveau = "HIGH_WASTE"
            elif score_gaspillage > 50:
                niveau = "MEDIUM_WASTE"
            else:
                niveau = "OPTIMAL"

            rapport.append({
                "vmid":               vmid,
                "nom":                nom,
                "noeud":              node_name,
                "ram_allouee_gb":     ram_allouee_gb,
                "ram_utilisee_gb":    ram_utilisee_gb,
                "ram_utilisee_pct":   ram_utilisee_pct,
                "ram_gaspillee_pct":  ram_gaspillee_pct,
                "ram_recommandee_gb": ram_recommandee_gb,
                "economie_ram_gb":    economie_ram_gb,
                "cpus_alloues":       cpus_alloues,
                "cpu_utilise_pct":    cpu_utilise_pct,
                "cpu_gaspille_pct":   cpu_gaspille_pct,
                "score_gaspillage":   score_gaspillage,
                "niveau":             niveau,
                "recommandation": (
                    f"Reduce RAM from {ram_allouee_gb} GB to {ram_recommandee_gb} GB "
                    f"(save {economie_ram_gb} GB)"
                    if economie_ram_gb > 0.5 else "Allocation is optimal"
                ),
            })

    return sorted(rapport, key=lambda x: x["score_gaspillage"], reverse=True)


# ══════════════════════════════════════════════════════════════════════════════
# NIVEAU 3 — GPU
# ══════════════════════════════════════════════════════════════════════════════

def get_gpu_info() -> list[dict]:
    """
    Détecte les GPUs sur chaque noeud Proxmox.
    """
    noeuds = get_noeuds()
    gpus = []

    for noeud in noeuds:
        node_name = noeud["nom"]
        ip_directe = NODE_IP_MAP.get(node_name)
        pci_devices = _get(f"/nodes/{node_name}/hardware/pci", host_override=ip_directe) or []

        for pci in pci_devices:
            device_class = str(pci.get("class", "")).lower()
            subsystem    = str(pci.get("subsystem_device_name", "")).lower()
            vendor       = str(pci.get("vendor_name", "")).lower()
            device_name  = str(pci.get("device_name", ""))

            is_gpu = (
                "0x03" in device_class or
                "vga" in device_class or
                "nvidia" in vendor or
                "amd" in vendor or
                "radeon" in device_name.lower() or
                "geforce" in device_name.lower() or
                "quadro" in device_name.lower()
            )

            if is_gpu:
                vms_using = _gpu_passthrough_vms(node_name, pci.get("id", ""))

                gpus.append({
                    "noeud":             node_name,
                    "pci_id":            pci.get("id"),
                    "nom":               device_name or "GPU inconnu",
                    "vendor":            pci.get("vendor_name", "Unknown"),
                    "class":             pci.get("class"),
                    "en_passthrough":    len(vms_using) > 0,
                    "vms_utilisant_gpu": vms_using,
                    "nb_vms":            len(vms_using),
                    "statut":            "IN_USE" if vms_using else "AVAILABLE",
                    "utilisation_pct":   None,
                    "note": "GPU utilization requires nvidia-smi on the host or VM",
                })

    return gpus


def _gpu_passthrough_vms(noeud: str, pci_id: str) -> list[str]:
    """Retourne les noms des VMs qui utilisent ce GPU en passthrough."""
    ip_directe = NODE_IP_MAP.get(noeud)
    vms = _get(f"/nodes/{noeud}/qemu", host_override=ip_directe) or []
    vms_avec_gpu = []

    for vm in vms:
        if vm.get("status") != "running":
            continue
        vmid   = vm.get("vmid")
        config = _get(f"/nodes/{noeud}/qemu/{vmid}/config", host_override=ip_directe) or {}

        for key, val in config.items():
            if key.startswith("hostpci") and pci_id in str(val):
                vms_avec_gpu.append(vm.get("name", f"vm-{vmid}"))
                break

    return vms_avec_gpu


def get_gpu_utilisation_ssh(noeud_ip: str) -> dict:
    """
    Optionnel : récupère l'utilisation GPU via nvidia-smi en SSH.
    Nécessite : pip install paramiko
    """
    try:
        import paramiko
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            noeud_ip, port=22,
            username=os.getenv("SSH_USER", "root"),
            password=os.getenv("SSH_PASSWORD", ""),
            timeout=5
        )
        _, stdout, _ = ssh.exec_command(
            "nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu "
            "--format=csv,noheader,nounits 2>/dev/null | head -1"
        )
        line = stdout.read().decode().strip()
        ssh.close()

        if line:
            parts = [p.strip() for p in line.split(",")]
            return {
                "utilisation_pct":  float(parts[0]) if parts[0] else None,
                "vram_used_mb":     float(parts[1]) if len(parts) > 1 else None,
                "vram_total_mb":    float(parts[2]) if len(parts) > 2 else None,
                "temperature_c":    float(parts[3]) if len(parts) > 3 else None,
                "disponible":       True,
            }
    except ImportError:
        return {"disponible": False, "note": "pip install paramiko pour activer"}
    except Exception as e:
        return {"disponible": False, "note": str(e)}

    return {"disponible": False}


# ══════════════════════════════════════════════════════════════════════════════
# Stockage
# ══════════════════════════════════════════════════════════════════════════════

def get_stockage(noeud: str) -> list[dict]:
    ip_directe = NODE_IP_MAP.get(noeud)
    data = _get(f"/nodes/{noeud}/storage", host_override=ip_directe) or []
    result = []
    for s in data:
        total = max(s.get("total", 1), 1)
        result.append({
            "nom":      s.get("storage"),
            "type":     s.get("type"),
            "used_gb":  round(s.get("used", 0) / 1024**3, 1),
            "total_gb": round(total / 1024**3, 1),
            "used_pct": round(s.get("used", 0) / total * 100, 1),
            "actif":    s.get("active", 0) == 1,
        })
    return result


# ══════════════════════════════════════════════════════════════════════════════
# État complet du cluster — appelé par chat_agent.py toutes les 30s
# ══════════════════════════════════════════════════════════════════════════════

def get_etat_cluster() -> dict:
    """
    Retourne l'état complet du cluster avec toutes les ressources.
    Niveau 1 (Critique) + Niveau 2 (Important) + Niveau 3 (Optimisation)
    """
    # ── FALLBACK AUTOMATIQUE pve1 → pve2 ─────────────────────────────────────
    global HOST
    _hosts = [
        os.getenv("PVE1_IP", "192.168.138.100"),
        os.getenv("PVE2_IP", "192.168.138.101"),
    ]
    noeuds = []
    for _host in _hosts:
        try:
            HOST = _host
            os.environ["PROXMOX_HOST"] = _host
            noeuds = get_noeuds()
            if noeuds:
                print(f"[Proxmox API] Connecté via {_host}")
                break
        except Exception as _e:
            print(f"[Proxmox API] {_host} inaccessible: {_e}")
            if _host == _hosts[-1]:
                return {"noeuds": [], "vms": [], "alertes": []}
    # ── FIN FALLBACK ──────────────────────────────────────────────────────────

    vms    = get_vms()

    vms_running = [v for v in vms if v["statut"] == "running"]
    vms_stopped = [v for v in vms if v["statut"] == "stopped"]

    # ── Alertes Niveau 1 — Critique ───────────────────────────────────────────
    alertes = []

    for n in noeuds:
        if n["ram_pct"] > 85:
            alertes.append({
                "niveau":  "CRITIQUE",
                "cible":   n["nom"],
                "message": f"Node {n['nom']} — Memory critical: {n['ram_pct']}% ({n['ram_used_gb']}/{n['ram_total_gb']} GB)",
                "type":    "ram",
            })
        if n["disk_pct"] > 90:
            alertes.append({
                "niveau":  "CRITIQUE",
                "cible":   n["nom"],
                "message": f"Node {n['nom']} — Disk critical: {n['disk_pct']}% ({n['disk_used_gb']}/{n['disk_total_gb']} GB)",
                "type":    "disk",
            })
        if n["cpu_pct"] > 80:
            alertes.append({
                "niveau":  "CRITIQUE",
                "cible":   n["nom"],
                "message": f"Node {n['nom']} — CPU critical: {n['cpu_pct']}%",
                "type":    "cpu",
            })

    for v in vms_running:
        if v["cpu_pct"] > 80:
            alertes.append({
                "niveau":  "IMPORTANT",
                "cible":   v["nom"],
                "message": f"VM {v['nom']} — CPU high: {v['cpu_pct']}%",
                "type":    "cpu",
            })
        if v["ram_pct"] > 85:
            alertes.append({
                "niveau":  "IMPORTANT",
                "cible":   v["nom"],
                "message": f"VM {v['nom']} — Memory high: {v['ram_pct']}% ({v['ram_used_gb']}/{v['ram_total_gb']} GB)",
                "type":    "ram",
            })

    # ── Niveau 3 — Allocation vs Usage ────────────────────────────────────────
    allocation_rapport = []
    try:
        allocation_rapport = get_allocation_vs_usage()
        for vm_alloc in allocation_rapport:
            if vm_alloc["niveau"] == "HIGH_WASTE":
                alertes.append({
                    "niveau":  "SURVEILLANCE",
                    "cible":   vm_alloc["nom"],
                    "message": f"VM {vm_alloc['nom']} — Over-provisioned: {vm_alloc['ram_gaspillee_pct']}% RAM unused. {vm_alloc['recommandation']}",
                    "type":    "allocation",
                })
    except Exception as e:
        print(f"[Proxmox API] Allocation check: {e}")

    # ── Niveau 3 — GPU ────────────────────────────────────────────────────────
    gpu_info = []
    try:
        gpu_info = get_gpu_info()
        ADAPTATEURS_VIRTUELS = ("svga", "vmware", "virtualbox", "vbox", "qemu", "bochs", "cirrus", "virtio-vga")
        for gpu in gpu_info:
            nom_lower = gpu.get("nom","").lower()
            is_virtual = any(v in nom_lower for v in ADAPTATEURS_VIRTUELS)
            if not gpu["en_passthrough"] and not is_virtual:
                alertes.append({
                    "niveau":  "SURVEILLANCE",
                    "cible":   gpu["noeud"],
                    "message": f"GPU {gpu['nom']} on {gpu['noeud']} is available but not assigned to any VM",
                    "type":    "gpu",
                })
    except Exception as e:
        print(f"[Proxmox API] GPU check: {e}")

    return {
        "noeuds":              noeuds,
        "vms":                 vms,
        "vms_running":         len(vms_running),
        "vms_stopped":         len(vms_stopped),
        "alertes":             alertes,
        "nb_alertes":          len(alertes),
        "allocation_rapport":  allocation_rapport,
        "gpu_info":            gpu_info,
        "nb_vms_over_provisioned": len([a for a in allocation_rapport if a["niveau"] == "HIGH_WASTE"]),
        "nb_gpus":             len(gpu_info),
    }


# ── Test standalone ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json

    print("=== NODES ===")
    for n in get_noeuds():
        print(f"  {n['nom']} — CPU:{n['cpu_pct']}% RAM:{n['ram_pct']}% Disk:{n['disk_pct']}%")

    print("\n=== VMs ===")
    for v in get_vms():
        print(f"  [{v['statut']:7s}] {v['nom']:20s} CPU:{v['cpu_pct']}% RAM:{v['ram_pct']}%")

    print("\n=== ALLOCATION vs USAGE ===")
    for a in get_allocation_vs_usage():
        print(f"  {a['nom']:20s} RAM: {a['ram_utilisee_gb']}/{a['ram_allouee_gb']} GB "
              f"({a['ram_gaspillee_pct']}% wasted) — {a['niveau']}")
        print(f"    → {a['recommandation']}")

    print("\n=== GPU ===")
    gpus = get_gpu_info()
    if gpus:
        for g in gpus:
            print(f"  [{g['noeud']}] {g['nom']} — {g['statut']} — Passthrough: {g['en_passthrough']}")
    else:
        print("  No GPU detected on cluster nodes")

    print("\n=== ALERTS ===")
    etat = get_etat_cluster()
    if etat["alertes"]:
        for a in etat["alertes"]:
            print(f"  [{a['niveau']}] {a['message']}")
    else:
        print("  All systems healthy")

# ══════════════════════════════════════════════════════════════════════════════
# SUPPORT MULTI-CLUSTER
# ══════════════════════════════════════════════════════════════════════════════

def _charger_clusters() -> list:
    """
    Charge tous les clusters depuis .env.
    Si aucun CLUSTER_N_HOST défini → utilise la config existante (1 cluster).
    Compatible avec le mode actuel — rien ne casse.
    """
    clusters = []
    i = 1
    while True:
        host = os.getenv(f"CLUSTER_{i}_HOST")
        if not host:
            break
        clusters.append({
            "nom":          os.getenv(f"CLUSTER_{i}_NAME", f"Cluster-{i}"),
            "host":         host,
            "cluster_host": host,
            "token_id":     os.getenv(f"CLUSTER_{i}_TOKEN_ID", TOKEN_ID),
            "token_secret": os.getenv(f"CLUSTER_{i}_TOKEN_SECRET", TOKEN_SECRET),
        })
        i += 1

    # Fallback : mode actuel (1 seul cluster)
    if not clusters:
        clusters.append({
            "nom":          "MonCluster",
            "host":         HOST,
            "cluster_host": HOST,
            "token_id":     TOKEN_ID,
            "token_secret": TOKEN_SECRET,
        })
    return clusters


def get_etat_tous_clusters() -> dict:
    """
    Interroge TOUS les clusters configurés et agrège les résultats.
    Utilisé par surveillance.py à la place de get_etat_cluster().

    En mode simple (un seul cluster "MonCluster", le cas homelab par défaut),
    on tente PVE1_IP puis PVE2_IP avant d'abandonner. En mode multi-cluster
    explicite (CLUSTER_N_HOST défini), chaque cluster garde son hôte fixe.
    """
    global HOST, HEADERS
    clusters    = _charger_clusters()
    tous_noeuds = []
    toutes_vms  = []

    for cluster in clusters:
        hosts_a_essayer = [cluster["host"]]
        if cluster["nom"] == "MonCluster":
            hosts_a_essayer = [
                os.getenv("PVE1_IP", "192.168.138.100"),
                os.getenv("PVE2_IP", "192.168.138.101"),
            ]

        noeuds, vms = [], []
        hote_connecte = None
        for _host in hosts_a_essayer:
            HOST    = _host
            HEADERS = {
                "Authorization": f"PVEAPIToken={cluster['token_id']}={cluster['token_secret']}"
            }
            try:
                noeuds = get_noeuds()
                if noeuds:
                    vms = get_vms()
                    hote_connecte = _host
                    print(f"[Multi-Cluster] {cluster['nom']} connecté via {_host}: "
                          f"{len(noeuds)} nœuds, {len(vms)} VMs")
                    break
            except Exception as e:
                print(f"[Multi-Cluster] {cluster['nom']} via {_host} inaccessible: {e}")

        if noeuds:
            for n in noeuds:
                n["cluster"]      = cluster["nom"]
                n["cluster_host"] = hote_connecte
            for v in vms:
                v["cluster"]      = cluster["nom"]
                v["cluster_host"] = hote_connecte
            tous_noeuds.extend(noeuds)
            toutes_vms.extend(vms)
        else:
            print(f"[Multi-Cluster] {cluster['nom']} injoignable sur tous les hôtes testés: {hosts_a_essayer}")
            tous_noeuds.append({
                "nom":          f"{cluster['nom']}",
                "statut":       "offline",
                "cluster":      cluster["nom"],
                "cluster_host": cluster["host"],
                "ram_pct": 0, "cpu_pct": 0, "disk_pct": 0,
                "ram_used_gb": 0, "ram_total_gb": 0,
                "disk_used_gb": 0, "disk_total_gb": 0,
                "uptime_h": 0, "cpu_cores": 0,
            })

    # Réinitialiser HOST au cluster principal après la boucle
    HOST    = os.getenv("PROXMOX_HOST", "192.168.138.100")
    HEADERS = {"Authorization": f"PVEAPIToken={TOKEN_ID}={TOKEN_SECRET}"}

    vms_running = [v for v in toutes_vms if v["statut"] == "running"]
    vms_stopped = [v for v in toutes_vms if v["statut"] == "stopped"]

    return {
        "noeuds":      tous_noeuds,
        "vms":         toutes_vms,
        "vms_running": len(vms_running),
        "vms_stopped": len(vms_stopped),
        "alertes":     [],
        "nb_clusters": len(clusters),
        "clusters":    [c["nom"] for c in clusters],
    }