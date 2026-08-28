"""
vm_app_monitor.py — Monitoring niveau 2 et 3 : applications dans les VMs.

Niveau 2 : Métriques applicatives
  - PostgreSQL (linux-vm1, 192.168.138.133) : connexions, locks, cache hit, rollbacks

Niveau 3 : Health checks actifs
  - DB check : PostgreSQL accepte-t-il les connexions ?
  - Uptime et temps de réponse

Détection live des services par VM (detecter_services_vm) :
← RÉÉCRIT : générique, via node_exporter --collector.systemd, plutôt que 3
vérifications codées en dur (PostgreSQL/Prometheus/Alertmanager). Chaque
VM expose déjà node_systemd_unit_state{name="X.service",state="..."} --
une ligne par état possible pour chaque service, seule celle valant 1 est
la vraie. Ce fichier ne garde que les services présents dans
CATALOGUE_SERVICES (extensible en ajoutant une ligne, jamais un nouveau
bloc de code) -- sinon, chaque VM afficherait des dizaines de services
système sans intérêt (ModemManager, dbus, cron...) au lieu d'un signal
utile. Contrairement aux tags Proxmox (déclarés manuellement une fois via
qm set --tags), ceci interroge Prometheus à chaque cycle -- si un service
tombe, il disparaît de la liste automatiquement, sans intervention humaine.

Exception documentée : Prometheus et Alertmanager tournent dans des
conteneurs Docker sur linux-vm2, jamais comme service systemd de l'hôte --
node_systemd_unit_state ne peut structurellement pas les voir. Vérifiés
séparément via leur propre endpoint Prometheus, comme avant.

Volontairement absents de cette détection : node_exporter et pve_exporter.
Ce sont les agents de collecte eux-mêmes, présents partout sans exception --
les afficher comme "service détecté" serait du bruit, pas du signal.

Toutes les métriques sont collectées depuis Prometheus (192.168.138.137:9090,
hébergé en conteneur Docker sur linux-vm2).

── Architecture générique (IMPORTANT) ──────────────────────────────────────
Rien dans la détection, la normalisation CPU, la détection DOWN ou le suivi
num_procs n'est spécifique à un service. Ces mécanismes s'appliquent
identiquement à n'importe quelle entrée de CATALOGUE_SERVICES. Le SEUL
endroit spécifique à un service est ENRICHISSEURS_SERVICE (métriques
internes via exportateur dédié, auto-exposition, ou SSH direct) -- ajouter
le support d'un nouveau service = une fonction + une ligne dans ce
registre, rien d'autre à modifier dans ce fichier ni ailleurs. Un service
SANS entrée ici garde automatiquement le comportement générique complet
(RAM/CPU/disk/num_procs + DOWN + dérive).

← CORRECTION MAJEURE (pg_up) : le champ "pg_up" de get_postgres_metrics()
interrogeait up{job="postgres_exporter"} -- la santé du SCRAPE Prometheus
(est-ce que Prometheus a pu récupérer /metrics), pas la santé de la
CONNEXION de l'exportateur à PostgreSQL. postgres_exporter expose sa
propre métrique, elle aussi nommée "pg_up" (sans label job), qui répond à
la VRAIE question -- confirmée via curl direct sur l'exportateur :
pg_up=0 alors que up{job="postgres_exporter"}=1 en permanence. Collision
de nom malheureuse entre notre champ et la métrique native de
l'exportateur -- on lisait la mauvaise depuis le début, ce qui empêchait
_generer_alertes_apps() de jamais détecter cette panne (la condition
`pg.get("pg_up")==0` était juste, seule la donnée lue était fausse).
pg_scrape_ok ajouté séparément pour garder les deux signaux distincts et
utiles au diagnostic (scrape KO = exportateur injoignable ; pg_up KO =
exportateur injoignable à PostgreSQL, deux pannes différentes).

← AJOUT (enrichisseur Docker, espace disque images/volumes/cache) : ceci
est structurellement invisible pour process-exporter -- ce n'est pas un
processus, aucune métrique de process ne peut refléter la comptabilité
disque interne du moteur Docker. Nécessite un accès SSH direct à la VM,
comme hypervisor_detect.py le fait déjà pour les noeuds Proxmox -- même
bibliothèque (paramiko), mais identifiants SÉPARÉS
(LINUX_VM_SSH_USER/LINUX_VM_SSH_PASSWORD dans .env) : le compte root
utilisé pour pve1/pve2 n'est presque certainement pas celui de tes VMs
Ubuntu. Résultat mis en cache 5 minutes (DOCKER_DISK_CACHE_TTL_S) -- une
connexion SSH à chaque cycle de 60s serait un gaspillage pur pour une
donnée qui ne change pas seconde par seconde.

← AJOUT (redémarrages de conteneurs, Prometheus + Alertmanager) : angle
mort distinct de l'espace disque -- process-exporter voit "le processus
existe", jamais son historique de plantages. Un conteneur qui crash-loop
avec "restart: always" reste invisible sans ça : le processus semble
toujours présent, juste avec un PID qui change à chaque fois. Docker tient
lui-même à jour un compteur de redémarrages par conteneur (RestartCount,
via `docker inspect`) -- une hausse d'un cycle à l'autre génère une alerte
IMPORTANT, même mécanisme de comparaison que detecter_derive_num_procs.
Cache plus court que l'espace disque (60s, DOCKER_RESTART_CACHE_TTL_S) --
un redémarrage est un événement qu'on veut détecter rapidement, pas une
donnée qui évolue lentement. Alertmanager gagne ici son PREMIER
enrichisseur -- toujours aucune métrique interne (alertes/silences, hors
sujet pour le dimensionnement), seulement ce comptage de fiabilité.
"""
import os
import re
import json
import requests
import time
from collections import deque

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://192.168.138.137:9090")
TIMEOUT = 5


def _query_prometheus(query: str) -> float:
    """Exécute une requête PromQL et retourne la valeur scalaire (1er résultat)."""
    try:
        r = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": query},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        result = data.get("data", {}).get("result", [])
        if result:
            return float(result[0]["value"][1])
        return 0.0
    except Exception:
        return 0.0


def _query_prometheus_series(query: str) -> list:
    """
    Comme _query_prometheus, mais retourne la liste complète des séries avec
    leurs labels -- nécessaire quand une requête peut retourner plusieurs
    résultats (ex: tous les services actifs d'une VM), pas un seul scalaire.
    """
    try:
        r = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": query},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json().get("data", {}).get("result", [])
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════════════════
# SSH vers les VMs Linux (SÉPARÉ du SSH vers les noeuds Proxmox)
# ══════════════════════════════════════════════════════════════════════════════
LINUX_VM_SSH_USER     = os.getenv("LINUX_VM_SSH_USER", "")
LINUX_VM_SSH_PASSWORD = os.getenv("LINUX_VM_SSH_PASSWORD", "")


def _ssh_command_linux_vm(host: str, command: str) -> str | None:
    """
    Exécute une commande SSH sur une VM Linux (PAS un noeud Proxmox --
    hypervisor_detect.py._ssh_command s'en charge, avec des identifiants
    différents). Retourne None si échec ou si LINUX_VM_SSH_USER n'est pas
    configuré -- ne lève jamais d'exception vers l'appelant.
    """
    if not LINUX_VM_SSH_USER:
        return None
    try:
        import paramiko
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            host, port=22,
            username=LINUX_VM_SSH_USER,
            password=LINUX_VM_SSH_PASSWORD,
            timeout=8,
        )
        _, stdout, _ = ssh.exec_command(command)
        resultat = stdout.read().decode().strip()
        ssh.close()
        return resultat
    except ImportError:
        print("[VM Monitor] paramiko non installe -- pip install paramiko --break-system-packages")
        return None
    except Exception as e:
        print(f"[VM Monitor] SSH {host} indisponible: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Détection LIVE des services par VM
# ══════════════════════════════════════════════════════════════════════════════
VM_IP_MAP = {
    "101": "192.168.138.133",  # linux-vm1
    "103": "192.168.138.137",  # linux-vm2
}

CATALOGUE_SERVICES = {
    "postgresql":      "PostgreSQL",
    "docker":          "Docker",
    "nginx":           "Nginx",
    "apache2":         "Apache",
    "redis-server":    "Redis",
    "mysql":           "MySQL",
    "mariadb":         "MariaDB",
    "mongod":          "MongoDB",
    "rabbitmq-server": "RabbitMQ",
    "grafana-server":  "Grafana",
    "minio":           "MinIO",
}

PROCESS_EXPORTER_PORT = 9256
COMM_REEL = {
    "postgresql": "postgres",
    "docker":     "dockerd",
    "mysql":      "mysqld",
    "mariadb":    "mariadbd",
}


def get_process_metrics(vmid, service_key: str, vcpus: int = None) -> dict:
    """
    Retourne la consommation réelle du processus correspondant à service_key.
    """
    ip = VM_IP_MAP.get(str(vmid))
    if not ip:
        return {}
    comm = COMM_REEL.get(service_key, service_key)
    base = f'instance="{ip}:{PROCESS_EXPORTER_PORT}",groupname="{comm}"'

    ram_bytes = _query_prometheus(f'namedprocess_namegroup_memory_bytes{{{base},memtype="resident"}}')
    num_procs = _query_prometheus(f'namedprocess_namegroup_num_procs{{{base}}}')
    if not ram_bytes and not num_procs:
        return {}

    cpu_user = _query_prometheus(f'rate(namedprocess_namegroup_cpu_seconds_total{{{base},mode="user"}}[5m])')
    cpu_sys  = _query_prometheus(f'rate(namedprocess_namegroup_cpu_seconds_total{{{base},mode="system"}}[5m])')
    read_b   = _query_prometheus(f'rate(namedprocess_namegroup_read_bytes_total{{{base}}}[5m])')
    write_b  = _query_prometheus(f'rate(namedprocess_namegroup_write_bytes_total{{{base}}}[5m])')

    cpu_pct = round((cpu_user + cpu_sys) * 100, 1)
    resultat = {
        "ram_mb":          round(ram_bytes / (1024**2), 1),
        "cpu_pct":         cpu_pct,
        "disk_read_mbps":  round(read_b  / (1024**2), 3),
        "disk_write_mbps": round(write_b / (1024**2), 3),
        "num_procs":       int(num_procs),
    }
    if vcpus:
        resultat["cpu_pct_vm"] = round(cpu_pct / vcpus, 1)
    return resultat


def detecter_services_vm(vmid) -> list:
    """
    Retourne les services RÉELLEMENT actifs sur cette VM, vérifiés en direct
    via Prometheus.
    """
    ip = VM_IP_MAP.get(str(vmid))
    if not ip:
        return []

    services = []

    resultats = _query_prometheus_series(
        f'node_systemd_unit_state{{instance="{ip}:9100",state="active"}} == 1'
    )
    for r in resultats:
        nom_unit = r.get("metric", {}).get("name", "")
        if nom_unit.endswith(".service"):
            nom_unit = nom_unit[:-len(".service")]
        if nom_unit in CATALOGUE_SERVICES:
            services.append(CATALOGUE_SERVICES[nom_unit])

    prom_host = PROMETHEUS_URL.split("//")[-1].split(":")[0]
    if ip == prom_host:
        if _query_prometheus('up{job="prometheus"}') == 1.0:
            services.append("Prometheus")
        if _query_prometheus('up{job="alertmanager"}') == 1.0:
            services.append("Alertmanager")

    return services


# ══════════════════════════════════════════════════════════════════════════════
# Enrichissement optionnel par service — registre extensible
# ══════════════════════════════════════════════════════════════════════════════
def _enrichir_postgresql(vmid=None) -> dict:
    pg     = get_postgres_metrics()
    health = check_postgres_health()
    return {
        "connections_active": pg.get("pg_active_connections"),
        "connections_pct":    pg.get("pg_connections_pct"),
        "waiting_locks":      pg.get("pg_waiting_locks"),
        "cache_hit_pct":      pg.get("pg_cache_hit_pct"),
        "rollback_rate":      pg.get("pg_rollback_rate"),
        "up":                 pg.get("pg_up"),
        "scrape_ok":          pg.get("pg_scrape_ok"),
        "healthy":            health.get("healthy"),
        "latency_ms":         health.get("latency_ms"),
    }


def _enrichir_prometheus(vmid=None) -> dict:
    """
    Prometheus expose déjà ses propres statistiques internes sur son
    /metrics natif (il s'auto-scrape).
    """
    resultat = {
        "series_actives": _query_prometheus('prometheus_tsdb_head_series'),
        "chunks_memoire": _query_prometheus('prometheus_tsdb_head_chunks'),
        "config_ok":      _query_prometheus('prometheus_config_last_reload_successful') == 1.0,
    }
    conteneur = _restart_pour_service("prometheus")
    if conteneur:
        resultat["container_restarts"] = conteneur.get("restart_count")
    return resultat


DOCKER_DISK_CACHE_TTL_S = float(os.getenv("DOCKER_DISK_CACHE_TTL_S", "300"))
_docker_disk_cache = None
_docker_disk_ts    = 0.0


def _parser_taille_docker(texte: str) -> float:
    """
    Convertit une taille Docker human-readable en GB (float).
    """
    if not texte:
        return 0.0
    premier_mot = texte.split()[0]
    match = re.match(r'([\d.]+)\s*([KMGT]?B)', premier_mot, re.IGNORECASE)
    if not match:
        return 0.0
    valeur, unite = float(match.group(1)), match.group(2).upper()
    facteurs = {"B": 1 / (1024**3), "KB": 1 / (1024**2), "MB": 1 / 1024, "GB": 1.0, "TB": 1024.0}
    return round(valeur * facteurs.get(unite, 0.0), 3)


def _enrichir_docker(vmid=None) -> dict:
    """
    Espace disque réel pris par Docker (images, volumes locaux, cache de
    build).
    """
    global _docker_disk_cache, _docker_disk_ts
    if _docker_disk_cache is not None and (time.time() - _docker_disk_ts) < DOCKER_DISK_CACHE_TTL_S:
        return _docker_disk_cache

    ip = VM_IP_MAP.get("103")
    resultat = {}
    if ip:
        sortie = _ssh_command_linux_vm(ip, "docker system df --format '{{json .}}'")
        if sortie:
            images_gb, volumes_gb, cache_gb = 0.0, 0.0, 0.0
            for ligne in sortie.strip().split("\n"):
                try:
                    item = json.loads(ligne)
                except Exception:
                    continue
                type_  = item.get("Type", "").lower()
                taille = _parser_taille_docker(item.get("Size", ""))
                if type_ == "images":
                    images_gb = taille
                elif type_ == "local volumes":
                    volumes_gb = taille
                elif type_ == "build cache":
                    cache_gb = taille
            resultat = {
                "images_disk_gb":  images_gb,
                "volumes_disk_gb": volumes_gb,
                "build_cache_gb":  cache_gb,
                "total_disk_gb":   round(images_gb + volumes_gb + cache_gb, 3),
            }

    _docker_disk_cache = resultat
    _docker_disk_ts    = time.time()
    return resultat


DOCKER_RESTART_CACHE_TTL_S = float(os.getenv("DOCKER_RESTART_CACHE_TTL_S", "60"))
_docker_restart_cache = None
_docker_restart_ts    = 0.0
_restart_precedent    = {}

_DOCKER_INSPECT_CMD = r"""docker inspect --format '{"name":"{{.Name}}","restart_count":{{.RestartCount}},"started_at":"{{.State.StartedAt}}"}' $(docker ps -q)"""


def _obtenir_conteneurs_docker() -> dict:
    """
    Interroge docker inspect UNE fois pour tous les conteneurs en cours.
    """
    global _docker_restart_cache, _docker_restart_ts
    if _docker_restart_cache is not None and (time.time() - _docker_restart_ts) < DOCKER_RESTART_CACHE_TTL_S:
        return _docker_restart_cache

    ip = VM_IP_MAP.get("103")
    resultat = {}
    if ip:
        sortie = _ssh_command_linux_vm(ip, _DOCKER_INSPECT_CMD)
        if sortie:
            for ligne in sortie.strip().split("\n"):
                try:
                    item = json.loads(ligne)
                except Exception:
                    continue
                nom = item.get("name", "").lstrip("/").lower()
                if nom:
                    resultat[nom] = {
                        "restart_count": item.get("restart_count", 0),
                        "started_at":    item.get("started_at", ""),
                    }

    _docker_restart_cache = resultat
    _docker_restart_ts    = time.time()
    return resultat


def _restart_pour_service(cle_service: str) -> dict:
    """
    Retrouve les infos de redémarrage du conteneur dont le nom CONTIENT
    cle_service.
    """
    for nom, infos in _obtenir_conteneurs_docker().items():
        if cle_service in nom:
            return infos
    return {}


def detecter_redemarrage_conteneur(vmid, service_key: str, restart_count) -> dict:
    """
    Compare le restart_count actuel au dernier connu pour CE service sur
    CETTE VM.
    """
    cle = (str(vmid), service_key)
    precedent = _restart_precedent.get(cle)
    alerte = None
    if precedent is not None and restart_count is not None and restart_count > precedent:
        alerte = {
            "niveau":  "IMPORTANT",
            "cible":   f"vm-{vmid}/{service_key}",
            "message": f"{service_key}: container restarted ({precedent} → {restart_count} restarts) — check logs for crash cause",
            "type":    "container_restart",
        }
    _restart_precedent[cle] = restart_count
    return alerte


def _enrichir_alertmanager(vmid=None) -> dict:
    """
    Alertmanager reste SANS enrichisseur pour ses métriques internes.
    """
    conteneur = _restart_pour_service("alertmanager")
    if not conteneur:
        return {}
    return {"container_restarts": conteneur.get("restart_count")}


ENRICHISSEURS_SERVICE = {
    "postgresql":   _enrichir_postgresql,
    "prometheus":   _enrichir_prometheus,
    "docker":       _enrichir_docker,
    "alertmanager": _enrichir_alertmanager,
}


def get_metriques_services_vm(vmid, services_detectes: list, vcpus: int = None) -> dict:
    """
    Retourne {nom_service_affiché: {ram_mb, cpu_pct, cpu_pct_vm?, disk_read_mbps,
    disk_write_mbps, num_procs, ...métriques internes si exportateur dédié}}
    pour chaque service déjà détecté.
    """
    label_vers_cle = {v: k for k, v in CATALOGUE_SERVICES.items()}
    label_vers_cle.update({"Prometheus": "prometheus", "Alertmanager": "alertmanager"})

    resultat = {}
    for label in services_detectes:
        cle = label_vers_cle.get(label, label.lower())
        m = get_process_metrics(vmid, cle, vcpus=vcpus)
        if not m:
            continue
        enrichisseur = ENRICHISSEURS_SERVICE.get(cle)
        if enrichisseur:
            try:
                m.update(enrichisseur(vmid))
            except Exception:
                pass
        resultat[label] = m
    return resultat


# ══════════════════════════════════════════════════════════════════════════════
# Détection générique de service DOWN + dérive du nombre de processus
# ══════════════════════════════════════════════════════════════════════════════
_etat_precedent_services = {}
_historique_num_procs    = {}


def detecter_changements_services(vmid, services_actuels: list) -> list:
    """
    Compare la liste de services actifs de ce cycle à celle du cycle
    précédent pour CETTE VM.
    """
    cle = str(vmid)
    actuels = set(services_actuels)
    precedents = _etat_precedent_services.get(cle, actuels)
    disparus = precedents - actuels

    alertes = []
    for service in disparus:
        alertes.append({
            "niveau":  "CRITIQUE",
            "cible":   f"vm-{vmid}/{service.lower()}",
            "message": f"{service} is DOWN on vm-{vmid} — service was active, no longer detected",
            "type":    "service_down",
        })

    _etat_precedent_services[cle] = actuels
    return alertes


def detecter_derive_num_procs(vmid, service_key: str, num_procs: int,
                               fenetre: int = 10, facteur_alerte: float = 2.0):
    """
    Suit num_procs dans le temps pour CE service sur CETTE VM.
    """
    cle = (str(vmid), service_key)
    if cle not in _historique_num_procs:
        _historique_num_procs[cle] = deque(maxlen=fenetre)
    historique = _historique_num_procs[cle]

    alerte = None
    if len(historique) >= 3:
        base = sorted(historique)[len(historique) // 2]
        if base > 0 and num_procs >= base * facteur_alerte:
            alerte = {
                "niveau":  "IMPORTANT",
                "cible":   f"vm-{vmid}/{service_key}",
                "message": f"{service_key}: process count grew from ~{int(base)} to {num_procs} — possible leak",
                "type":    "num_procs_drift",
            }

    historique.append(num_procs)
    return alerte


# ══════════════════════════════════════════════════════════════════════════════
# Seuils sur les métriques ENRICHIES par service — registre extensible
# ══════════════════════════════════════════════════════════════════════════════
# ← AJOUT : avant, les métriques enrichies par service (ENRICHISSEURS_SERVICE
# ci-dessus) étaient MESURÉES et affichées dans l'UI, mais jamais comparées
# à un seuil -- seuls num_procs (dérive) et container_restarts (dérive)
# généraient une alerte. Le cas le plus concret : Docker total_disk_gb
# pouvait grossir indéfiniment sans jamais rien signaler, exactement le
# mécanisme qui a produit la crise VM103/pool LVM diagnostiquée plus tôt
# dans cette même session -- la donnée existait déjà, elle n'était
# simplement jamais vérifiée.
#
# Registre extensible, même principe que ENRICHISSEURS_SERVICE : ajouter un
# seuil pour un nouveau champ = une ligne ici, rien d'autre à modifier.
# Format (cle_service, champ) -> (avertissement, critique, inverse).
# inverse=True signifie "plus BAS = pire" (ex: cache_hit_pct) plutôt que
# "plus HAUT = pire" (comportement par défaut).
#
# Valeurs choisies comme point de départ raisonnable, pas mesurées sur TON
# infrastructure précise -- à ajuster si trop/pas assez sensibles en usage
# réel :
#   - docker.total_disk_gb : 15GB avertissement / 25GB critique. Ton pool
#     LVM fait actuellement 46.81GB (après l'agrandissement fait plus tôt
#     cette session) -- ces seuils laissent une marge confortable avant de
#     retoucher au pool, sans attendre qu'il soit de nouveau plein.
#   - postgresql.cache_hit_pct : en dessous de 90%/80%, le cache est trop
#     petit pour la charge réelle (le buffer pool PostgreSQL ne suffit
#     plus) -- guidance standard PostgreSQL, pas une valeur inventée pour
#     ce projet précis.
#   - postgresql.rollback_rate : un taux de rollback soutenu au-dessus de
#     1-5/s indique un problème applicatif (transactions qui échouent en
#     boucle), pas un pic ponctuel normal.
SEUILS_METRIQUES_SERVICE = {
    ("docker",     "total_disk_gb"):   (15,  25, False),
    ("postgresql", "cache_hit_pct"):   (90,  80, True),
    ("postgresql", "rollback_rate"):   (1,   5,  False),
}

# Champs booléens où False EST le problème (pas de notion de palier
# warning/critical -- soit c'est bon, soit non). Format (cle_service,
# champ) -> message si False.
CHAMPS_BOOLEENS_CRITIQUES = {
    ("prometheus", "config_ok"): "config reload failed -- check for a syntax error in the last edit to prometheus.yml",
}

INTERVALLE_REESCALADE_SEUIL_SERVICE_S = 1800  # 30 min -- cohérent avec anomaly_detector.py
_dernier_palier_seuil_service    = {}  # (vmid, service_key, champ) -> dernier palier connu (ou None)
_derniere_alerte_seuil_service   = {}  # (vmid, service_key, champ) -> timestamp du dernier signalement


def _palier_seuil_service(valeur: float, warn: float, crit: float, inverse: bool) -> str | None:
    if inverse:
        if valeur < crit: return "CRITIQUE"
        if valeur < warn: return "IMPORTANT"
        return None
    else:
        if valeur > crit: return "CRITIQUE"
        if valeur > warn: return "IMPORTANT"
        return None


def detecter_seuils_metriques_service(vmid, cle_service: str, metriques: dict) -> list:
    """
    Vérifie les métriques enrichies de CE service contre SEUILS_METRIQUES_SERVICE
    et CHAMPS_BOOLEENS_CRITIQUES -- même principe palier + ré-escalade que
    anomaly_detector.py (changement de palier déclenche toujours, un palier
    CRITIQUE soutenu se rappelle après 30 min), mais basé sur un état stocké
    en mémoire (dernier palier connu) plutôt qu'un etat_prec explicite -- ce
    fichier n'a jamais accès au cycle précédent complet, contrairement à
    anomaly_detector.detecter_anomalies(etat, etat_prec).
    """
    alertes = []
    cle_svc = cle_service.lower()

    for (svc, champ), (warn, crit, inverse) in SEUILS_METRIQUES_SERVICE.items():
        if svc != cle_svc:
            continue
        valeur = metriques.get(champ)
        if valeur is None:
            continue
        cle_etat = (str(vmid), cle_svc, champ)
        palier       = _palier_seuil_service(float(valeur), warn, crit, inverse)
        palier_avant = _dernier_palier_seuil_service.get(cle_etat)
        doit_reescalader = (
            palier == "CRITIQUE"
            and (time.time() - _derniere_alerte_seuil_service.get(cle_etat, 0)) >= INTERVALLE_REESCALADE_SEUIL_SERVICE_S
        )
        if (palier and palier != palier_avant) or doit_reescalader:
            mot   = "critical" if palier == "CRITIQUE" else "high"
            unite = "GB" if champ.endswith("_gb") else ("%" if champ.endswith("_pct") else "/s")
            alertes.append({
                "niveau":  palier,
                "cible":   f"vm-{vmid}/{cle_svc}",
                "message": f"{cle_service}: {champ.replace('_',' ')} {mot} ({valeur:.1f}{unite})",
                "type":    "service_threshold",
            })
            _derniere_alerte_seuil_service[cle_etat] = time.time()
        _dernier_palier_seuil_service[cle_etat] = palier

    for (svc, champ), description in CHAMPS_BOOLEENS_CRITIQUES.items():
        if svc != cle_svc:
            continue
        valeur = metriques.get(champ)
        if valeur is None:
            continue
        cle_etat = (str(vmid), cle_svc, champ)
        mauvais_maintenant = (valeur is False)
        etait_mauvais       = _dernier_palier_seuil_service.get(cle_etat) == "CRITIQUE"
        doit_reescalader = (
            mauvais_maintenant
            and (time.time() - _derniere_alerte_seuil_service.get(cle_etat, 0)) >= INTERVALLE_REESCALADE_SEUIL_SERVICE_S
        )
        if (mauvais_maintenant and not etait_mauvais) or doit_reescalader:
            alertes.append({
                "niveau":  "CRITIQUE",
                "cible":   f"vm-{vmid}/{cle_svc}",
                "message": f"{cle_service}: {description}",
                "type":    "service_threshold",
            })
            _derniere_alerte_seuil_service[cle_etat] = time.time()
        _dernier_palier_seuil_service[cle_etat] = "CRITIQUE" if mauvais_maintenant else None

    return alertes


def generer_alertes_services(vmid, services_detectes: list, metriques_services: dict) -> list:
    """
    Point d'entrée unique à appeler UNE FOIS PAR CYCLE PAR VM, juste après
    detecter_services_vm() et get_metriques_services_vm().
    """
    alertes = detecter_changements_services(vmid, services_detectes)

    label_vers_cle = {v: k for k, v in CATALOGUE_SERVICES.items()}
    label_vers_cle.update({"Prometheus": "prometheus", "Alertmanager": "alertmanager"})

    for label, m in metriques_services.items():
        cle = label_vers_cle.get(label, label.lower())
        if "num_procs" in m:
            alerte = detecter_derive_num_procs(vmid, cle, m["num_procs"])
            if alerte:
                alertes.append(alerte)
        if m.get("container_restarts") is not None:
            alerte = detecter_redemarrage_conteneur(vmid, cle, m["container_restarts"])
            if alerte:
                alertes.append(alerte)
        # ← AJOUT : seuils sur les metriques enrichies (Docker total_disk_gb,
        # PostgreSQL cache_hit_pct/rollback_rate, Prometheus config_ok...)
        alertes.extend(detecter_seuils_metriques_service(vmid, cle, m))

    return alertes


# ══════════════════════════════════════════════════════════════════════════════
# NIVEAU 2 — PostgreSQL (linux-vm1, 192.168.138.133)
# ══════════════════════════════════════════════════════════════════════════════
DB_NAME_MONITOREE = os.getenv("DB_NAME", "opspilot")


def get_postgres_metrics(instance: str = "192.168.138.133:9187", datname: str = None) -> dict:
    """
    Collecte les métriques PostgreSQL depuis postgres_exporter.
    """
    if datname is None:
        datname = DB_NAME_MONITOREE
    try:
        return {
            "pg_up": _query_prometheus(f'pg_up{{instance="{instance}"}}'),
            "pg_scrape_ok": _query_prometheus(
                f'up{{instance="{instance}",job="postgres_exporter"}}'
            ),
            "pg_active_connections": _query_prometheus(
                f'pg_stat_activity_count{{instance="{instance}",datname="{datname}",state="active"}}'
            ),
            "pg_connections_pct": _query_prometheus(
                f'pg_stat_activity_count{{instance="{instance}",datname="{datname}"}} / ignoring(datname) '
                f'pg_settings_max_connections{{instance="{instance}"}} * 100'
            ),
            "pg_waiting_locks": _query_prometheus(
                f'pg_locks_count{{instance="{instance}",datname="{datname}",granted="false"}}'
            ),
            "pg_cache_hit_pct": _query_prometheus(
                f'pg_stat_database_blks_hit{{instance="{instance}",datname="{datname}"}} / '
                f'(pg_stat_database_blks_hit{{instance="{instance}",datname="{datname}"}} + '
                f'pg_stat_database_blks_read{{instance="{instance}",datname="{datname}"}}) * 100'
            ),
            "pg_rollback_rate": _query_prometheus(
                f'rate(pg_stat_database_xact_rollback{{instance="{instance}",datname="{datname}"}}[5m])'
            ),
        }
    except Exception as e:
        print(f"[VM Monitor] PostgreSQL error: {e}")
        return {"pg_up": 0.0, "pg_scrape_ok": 0.0}


def check_postgres_health(host: str = "192.168.138.133", port: int = 5432) -> dict:
    """Check TCP actif : PostgreSQL accepte-t-il les connexions ?"""
    import socket
    start = time.time()
    try:
        sock = socket.create_connection((host, port), timeout=5)
        sock.close()
        return {
            "healthy":    True,
            "latency_ms": round((time.time() - start) * 1000, 1),
            "error":      None,
        }
    except Exception as e:
        return {
            "healthy":    False,
            "latency_ms": round((time.time() - start) * 1000, 1),
            "error":      str(e),
        }


# ══════════════════════════════════════════════════════════════════════════════
# Interface principale — appelée par surveillance.py
# ══════════════════════════════════════════════════════════════════════════════

def collecter_metriques_apps() -> dict:
    """
    Collecte toutes les métriques applicatives (niveaux 2 et 3).
    """
    pg_metrics = get_postgres_metrics()
    pg_health  = check_postgres_health()

    return {
        "postgresql": pg_metrics,
        "health": {
            "postgres_tcp": pg_health,
        },
        "alertes_apps": _generer_alertes_apps(pg_metrics, pg_health),
    }


def _generer_alertes_apps(pg, pg_health) -> list:
    """
    Génère des alertes sur les métriques applicatives (PostgreSQL
    uniquement).
    """
    alertes = []

    if pg.get("pg_up", 1) == 0 or not pg_health.get("healthy", True):
        alertes.append({
            "niveau":  "CRITIQUE",
            "cible":   "linux-vm1/postgresql",
            "message": "PostgreSQL is DOWN on linux-vm1 — database unavailable",
            "type":    "service_down",
        })

    conn_pct = pg.get("pg_connections_pct", 0)
    if conn_pct > 80:
        alertes.append({
            "niveau":  "IMPORTANT",
            "cible":   "linux-vm1/postgresql",
            "message": f"PostgreSQL connections at {conn_pct:.0f}% of max (threshold: 80%)",
            "type":    "connections",
        })

    locks = pg.get("pg_waiting_locks", 0)
    if locks > 5:
        alertes.append({
            "niveau":  "IMPORTANT",
            "cible":   "linux-vm1/postgresql",
            "message": f"PostgreSQL: {int(locks)} waiting locks detected — possible deadlock",
            "type":    "locks",
        })

    return alertes