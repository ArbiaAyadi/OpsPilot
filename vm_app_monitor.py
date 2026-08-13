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
# hypervisor_detect.py a déjà SSH_USER/SSH_PASSWORD, mais c'est le compte
# root de pve1/pve2 -- une VM Ubuntu classique a son propre compte
# utilisateur, presque jamais le même. Variables séparées pour ne jamais
# mélanger les deux.
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

# ── Catalogue de services reconnus (badge de détection uniquement) ─────────
# Nom du service systemd (sans ".service") -> nom affiché. Un service actif
# absent de cette liste reste invisible pour l'instant -- ajouter une ligne
# suffit à l'inclure, jamais besoin de toucher à detecter_services_vm().
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

# ── Mesure de consommation réelle — process-exporter (port 9256) ───────────
# ← CORRECTION IMPORTANTE : process-exporter tourne avec sa config générique
# par défaut (name: "{{.Comm}}", cmdline: ['.+']) -- JAMAIS régénérée,
# jamais éditée. Ça veut dire qu'il suit déjà TOUT processus automatiquement,
# y compris un service qui n'a jamais été ajouté à CATALOGUE_SERVICES.
# La table ci-dessous ne sert PLUS à configurer quoi que ce soit -- juste à
# retrouver la bonne donnée quand le nom du service systemd diffère du nom
# réel du processus (postgresql.service lance un binaire "postgres";
# docker.service lance "dockerd"). Un service absent d'ici est cherché sous
# son propre nom directement -- fonctionne déjà pour la majorité des cas
# (nginx, redis-server, mongod... le nom du service EST le nom du processus).
PROCESS_EXPORTER_PORT = 9256
COMM_REEL = {
    "postgresql": "postgres",
    "docker":     "dockerd",
    "mysql":      "mysqld",
    "mariadb":    "mariadbd",
}


def get_process_metrics(vmid, service_key: str, vcpus: int = None) -> dict:
    """
    Retourne la consommation réelle du processus correspondant à service_key
    (ex: "postgresql", "redis-server", ou n'importe quel autre nom de
    service systemd détecté) -- RAM, CPU et I/O disque, tout ce que
    process-exporter expose de directement utile pour juger la santé d'un
    service. Générique : fonctionne pour n'importe quel processus déjà
    suivi automatiquement, pas seulement ceux listés dans COMM_REEL.

    vcpus (optionnel, rétrocompatible -- omis = comportement identique à
    avant) : cpu_pct ci-dessous reste le % d'UN SEUL cœur (ce que
    process-exporter mesure nativement) -- pas comparable tel quel au
    cpu_pct affiché au niveau VM, qui lui est déjà normalisé sur l'ensemble
    des vCPU alloués. cpu_pct_vm est ajouté EN PLUS (cpu_pct reste inchangé)
    quand vcpus est fourni -- seul chiffre réellement comparable au CPU% de
    la VM. Générique : vcpus vient de la VM, pas du service -- s'applique
    pareil à PostgreSQL, Docker, Redis ou n'importe quel autre.

    Absent volontairement : le réseau (process-exporter ne l'expose pas par
    processus, aucun contournement fiable) et toute donnée interne au
    service (connexions, requêtes lentes...) -- voir ENRICHISSEURS_SERVICE
    pour les services qui ont un exportateur dédié capable de fournir ça.
    """
    ip = VM_IP_MAP.get(str(vmid))
    if not ip:
        return {}
    comm = COMM_REEL.get(service_key, service_key)
    base = f'instance="{ip}:{PROCESS_EXPORTER_PORT}",groupname="{comm}"'

    ram_bytes = _query_prometheus(f'namedprocess_namegroup_memory_bytes{{{base},memtype="resident"}}')
    num_procs = _query_prometheus(f'namedprocess_namegroup_num_procs{{{base}}}')
    if not ram_bytes and not num_procs:
        return {}  # groupe introuvable -- service pas suivi ou pas actif actuellement

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
    via Prometheus -- pas une déclaration statique. Si un service tombe, il
    disparaît de cette liste au prochain cycle (60s), automatiquement.

    Générique : n'importe quel service du catalogue est détecté de la même
    façon, sans code spécifique par service. Ajouter un nouveau service à
    surveiller = une ligne dans CATALOGUE_SERVICES.
    """
    ip = VM_IP_MAP.get(str(vmid))
    if not ip:
        return []

    services = []

    # ── Détection générique (n'importe quel service actif du catalogue) ────
    resultats = _query_prometheus_series(
        f'node_systemd_unit_state{{instance="{ip}:9100",state="active"}} == 1'
    )
    for r in resultats:
        nom_unit = r.get("metric", {}).get("name", "")
        if nom_unit.endswith(".service"):
            nom_unit = nom_unit[:-len(".service")]
        if nom_unit in CATALOGUE_SERVICES:
            services.append(CATALOGUE_SERVICES[nom_unit])

    # ── Exception documentée : conteneurs Docker (Prometheus/Alertmanager),
    # jamais visibles comme service systemd de l'hôte -- vérifiés via leur
    # propre endpoint, comme avant ce correctif.
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
# Un service avec un exportateur dédié, une auto-exposition native (comme
# Prometheus), ou un accès SSH direct (comme Docker) peut apporter des
# métriques que process-exporter ne voit jamais. Ajouter le support d'un
# nouveau service = écrire une fonction d'enrichissement + une ligne dans
# ENRICHISSEURS_SERVICE ci-dessous. Rien d'autre à toucher : ni
# detecter_services_vm(), ni get_metriques_services_vm(), ni la détection
# DOWN générique, ni le suivi num_procs. Un service SANS entrée ici garde
# simplement RAM/CPU/disk/num_procs génériques -- c'est le comportement par
# défaut, pas une exception à gérer.
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
    /metrics natif (il s'auto-scrape) -- pas besoin d'exportateur séparé,
    contrairement à PostgreSQL. prometheus_tsdb_head_series explique
    directement pourquoi sa RAM grandit dans le temps.

    ← AJOUT : container_restarts, via _restart_pour_service() -- voir la
    section "Redémarrages de conteneurs Docker" plus bas dans ce fichier.
    Absent du dict si aucun conteneur nommé "prometheus" n'est trouvé
    (silencieux, pas d'erreur).
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


# ── Docker : espace disque images/volumes/cache, via SSH ────────────────────
DOCKER_DISK_CACHE_TTL_S = float(os.getenv("DOCKER_DISK_CACHE_TTL_S", "300"))
_docker_disk_cache = None
_docker_disk_ts    = 0.0


def _parser_taille_docker(texte: str) -> float:
    """
    Convertit une taille Docker human-readable ("1.2GB", "450MB (37%)",
    "0B") en GB (float). Ne garde que le premier "mot" (avant un éventuel
    "(37%)" de réutilisable) puis extrait nombre + unité.
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
    build) -- invisible pour process-exporter, qui ne voit que des
    processus, jamais la comptabilité interne du moteur Docker. Nécessite
    docker system df en SSH sur la VM qui héberge Docker (voir docstring du
    fichier). Mis en cache DOCKER_DISK_CACHE_TTL_S (5 min par défaut) --
    cette donnée ne change pas seconde par seconde, inutile de rouvrir une
    connexion SSH à chaque cycle de surveillance (60s).
    """
    global _docker_disk_cache, _docker_disk_ts
    if _docker_disk_cache is not None and (time.time() - _docker_disk_ts) < DOCKER_DISK_CACHE_TTL_S:
        return _docker_disk_cache

    ip = VM_IP_MAP.get("103")  # linux-vm2, seule VM avec Docker actuellement
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


# ── Redémarrages de conteneurs Docker (Prometheus, Alertmanager) ────────────
# ← AJOUT : angle mort identifié -- si un conteneur plante et redémarre
# automatiquement (politique "restart: always", très courante), process-
# exporter voit juste "le processus existe" en continu, le plantage est
# invisible. Docker tient lui-même à jour un compteur de redémarrages par
# conteneur (RestartCount) -- ce bloc le lit en SSH une fois pour tous les
# conteneurs (docker inspect), le range par nom de conteneur, et
# _restart_pour_service() retrouve le bon conteneur par sous-chaîne dans son
# nom ("prometheus" matche "docker-prometheus-1", "monitoring_prometheus_1",
# peu importe le préfixe posé par docker-compose). Cache plus court que
# l'espace disque (60s, pas 5min) -- un redémarrage est un événement qu'on
# veut détecter rapidement, pas une donnée qui évolue lentement.
DOCKER_RESTART_CACHE_TTL_S = float(os.getenv("DOCKER_RESTART_CACHE_TTL_S", "60"))
_docker_restart_cache = None
_docker_restart_ts    = 0.0
_restart_precedent    = {}  # (vmid(str), service_key) -> dernier restart_count vu

_DOCKER_INSPECT_CMD = r"""docker inspect --format '{"name":"{{.Name}}","restart_count":{{.RestartCount}},"started_at":"{{.State.StartedAt}}"}' $(docker ps -q)"""


def _obtenir_conteneurs_docker() -> dict:
    """
    Interroge docker inspect UNE fois pour tous les conteneurs en cours,
    retourne {nom_conteneur_en_minuscules: {"restart_count": int,
    "started_at": str}}. $(docker ps -q) est résolu par le SHELL DISTANT
    (dans la commande SSH elle-même), pas par Python -- un seul aller-retour
    SSH pour tous les conteneurs, pas un par conteneur.
    """
    global _docker_restart_cache, _docker_restart_ts
    if _docker_restart_cache is not None and (time.time() - _docker_restart_ts) < DOCKER_RESTART_CACHE_TTL_S:
        return _docker_restart_cache

    ip = VM_IP_MAP.get("103")  # linux-vm2, seule VM avec des conteneurs actuellement
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
    cle_service -- pas une correspondance exacte, pour rester indépendant du
    préfixe de projet docker-compose. Retourne {} si aucun conteneur ne
    correspond (pas d'erreur, juste rien à ajouter pour ce service).
    """
    for nom, infos in _obtenir_conteneurs_docker().items():
        if cle_service in nom:
            return infos
    return {}


def detecter_redemarrage_conteneur(vmid, service_key: str, restart_count) -> dict:
    """
    Compare le restart_count actuel au dernier connu pour CE service sur
    CETTE VM -- une hausse indique qu'un conteneur vient de redémarrer
    (plantage + relance automatique par Docker, ou redémarrage manuel),
    invisible pour process-exporter qui ne voit que "le processus existe",
    jamais son historique de plantages. Premier cycle : rien à comparer,
    juste enregistre la valeur de départ (même principe que
    detecter_changements_services).
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
    ← AJOUT : Alertmanager reste SANS enrichisseur pour ses métriques
    internes (alertes actives, silences...) -- toujours vrai, ça reste
    hors-sujet pour le dimensionnement (voir note ci-dessous). Celui-ci
    existe UNIQUEMENT pour le comptage de redémarrages de conteneur -- un
    signal de fiabilité, pas de ressource, invisible pour process-exporter.
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
    # Alertmanager n'a PAS d'enrichisseur pour ses métriques internes
    # (alertes actives, silences...) -- ça concerne la santé de son propre
    # pipeline, pas le dimensionnement de la VM -- process-exporter
    # (RAM/CPU/disk) suffit déjà pour que la recommandation reste correcte
    # sur ce point précis. Son entrée ci-dessus sert uniquement au comptage
    # de redémarrages, une préoccupation différente (fiabilité).
    # Prochain service avec exportateur dédié : une fonction + une ligne
    # ici, rien d'autre à modifier.
}


def get_metriques_services_vm(vmid, services_detectes: list, vcpus: int = None) -> dict:
    """
    Retourne {nom_service_affiché: {ram_mb, cpu_pct, cpu_pct_vm?, disk_read_mbps,
    disk_write_mbps, num_procs, ...métriques internes si exportateur dédié}}
    pour chaque service déjà détecté.

    vcpus (optionnel, rétrocompatible) : transmis à get_process_metrics()
    pour calculer cpu_pct_vm sur CHAQUE service, générique -- pas seulement
    PostgreSQL.

    Retrouve la clé systemd depuis le nom affiché via CATALOGUE_SERVICES
    (inversé) -- Prometheus/Alertmanager, spéciaux car détectés par leur
    endpoint Docker plutôt que par systemd, sont ajoutés explicitement ici
    puisqu'absents de ce catalogue. Un service sans clé connue est cherché
    directement sous son nom en minuscules -- fonctionne pour la majorité
    des cas, générique par défaut plutôt que par exception.
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
                pass  # exportateur dédié indisponible -- garde au moins RAM/CPU/disk génériques
        resultat[label] = m
    return resultat


# ══════════════════════════════════════════════════════════════════════════════
# Détection générique de service DOWN + dérive du nombre de processus
# ══════════════════════════════════════════════════════════════════════════════
_etat_precedent_services = {}   # vmid(str) -> set(labels actifs au cycle précédent)
_historique_num_procs    = {}   # (vmid(str), service_key) -> deque des derniers num_procs


def detecter_changements_services(vmid, services_actuels: list) -> list:
    """
    Compare la liste de services actifs de ce cycle à celle du cycle
    précédent pour CETTE VM. Un service qui disparaît (crash, arrêt manuel,
    unité systemd qui bascule à "failed") génère une alerte générique,
    immédiatement, sans code spécifique par service.
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
    Suit num_procs dans le temps pour CE service sur CETTE VM -- générique,
    s'applique à n'importe quel service du catalogue. Une fuite de
    processus/connexions fait grimper ce nombre en continu.
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


def generer_alertes_services(vmid, services_detectes: list, metriques_services: dict) -> list:
    """
    Point d'entrée unique à appeler UNE FOIS PAR CYCLE PAR VM, juste après
    detecter_services_vm() et get_metriques_services_vm().

    ← AJOUT : vérifie aussi la dérive de container_restarts, quand présent
    (Prometheus, Alertmanager) -- même boucle que num_procs, pas une passe
    supplémentaire. Générique : tout futur service dont l'enrichisseur
    ajoute un jour un champ "container_restarts" est couvert automatiquement,
    sans toucher à cette fonction.
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

    return alertes


# ══════════════════════════════════════════════════════════════════════════════
# NIVEAU 2 — PostgreSQL (linux-vm1, 192.168.138.133)
# ══════════════════════════════════════════════════════════════════════════════

# Base de données réellement monitorée, alignée sur DB_NAME dans .env (la
# base qu'OpsPilot utilise pour son propre stockage) -- une seule source de
# vérité pour ce nom, pas deux valeurs distinctes à garder en synchro.
DB_NAME_MONITOREE = os.getenv("DB_NAME", "opspilot")


def get_postgres_metrics(instance: str = "192.168.138.133:9187", datname: str = None) -> dict:
    """
    Collecte les métriques PostgreSQL depuis postgres_exporter.

    ← CORRECTION MAJEURE : "pg_up" interrogeait up{job="postgres_exporter"}
    (santé du SCRAPE Prometheus) au lieu de la vraie métrique "pg_up" que
    postgres_exporter expose lui-même (santé de SA connexion à PostgreSQL).
    Confirmé par curl direct : pg_up=0 alors que le scrape réussit toujours
    -- collision de nom entre notre champ et la métrique native de
    l'exportateur, on lisait la mauvaise depuis le début. pg_scrape_ok
    ajouté séparément pour garder les deux signaux distincts.

    pg_stat_activity_count, pg_locks_count et pg_stat_database_blks_hit/
    blks_read restent filtrés sur datname (DB_NAME dans .env) -- inchangé,
    mais tant que pg_up=0 (exportateur qui n'arrive pas à joindre
    PostgreSQL), ces métriques ne seront de toute façon jamais exposées du
    tout, peu importe le filtre.
    """
    if datname is None:
        datname = DB_NAME_MONITOREE
    try:
        return {
            # Santé RÉELLE de la connexion exportateur -> PostgreSQL
            "pg_up": _query_prometheus(f'pg_up{{instance="{instance}"}}'),
            # Santé du SCRAPE Prometheus -> exportateur (différent de pg_up)
            "pg_scrape_ok": _query_prometheus(
                f'up{{instance="{instance}",job="postgres_exporter"}}'
            ),
            # Connexions actives
            "pg_active_connections": _query_prometheus(
                f'pg_stat_activity_count{{instance="{instance}",datname="{datname}",state="active"}}'
            ),
            # Connexions max utilisées (%)
            # ← CORRECTION : "ignoring(datname)" ajouté -- pg_stat_activity_count
            # a le label datname, pg_settings_max_connections non (volontairement,
            # c'est un réglage serveur). Sans ce indiquer explicitement à Prometheus
            # d'ignorer ce label pour la division, les deux séries ne matchent pas
            # (labels différents de chaque côté) et la division retourne vide -- 0%
            # silencieux, alors que "Connections Active" (une seule métrique, pas de
            # division) affichait la vraie valeur juste au-dessus.
            "pg_connections_pct": _query_prometheus(
                f'pg_stat_activity_count{{instance="{instance}",datname="{datname}"}} / ignoring(datname) '
                f'pg_settings_max_connections{{instance="{instance}"}} * 100'
            ),
            # Locks en attente
            "pg_waiting_locks": _query_prometheus(
                f'pg_locks_count{{instance="{instance}",datname="{datname}",granted="false"}}'
            ),
            # Taux de hits cache
            "pg_cache_hit_pct": _query_prometheus(
                f'pg_stat_database_blks_hit{{instance="{instance}",datname="{datname}"}} / '
                f'(pg_stat_database_blks_hit{{instance="{instance}",datname="{datname}"}} + '
                f'pg_stat_database_blks_read{{instance="{instance}",datname="{datname}"}}) * 100'
            ),
            # Taux de rollbacks (signe de problème)
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
    Appelée dans la boucle de surveillance toutes les 60s.
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
    uniquement). ← Cette condition n'a pas changé, mais elle lit maintenant
    la VRAIE santé de connexion (voir correction pg_up ci-dessus) -- elle
    va probablement se déclencher au prochain cycle, ce qui est correct :
    la panne était réelle, juste invisible jusqu'ici.
    """
    alertes = []

    # PostgreSQL DOWN
    if pg.get("pg_up", 1) == 0 or not pg_health.get("healthy", True):
        alertes.append({
            "niveau":  "CRITIQUE",
            "cible":   "linux-vm1/postgresql",
            "message": "PostgreSQL is DOWN on linux-vm1 — database unavailable",
            "type":    "service_down",
        })

    # PostgreSQL connexions saturées
    conn_pct = pg.get("pg_connections_pct", 0)
    if conn_pct > 80:
        alertes.append({
            "niveau":  "IMPORTANT",
            "cible":   "linux-vm1/postgresql",
            "message": f"PostgreSQL connections at {conn_pct:.0f}% of max (threshold: 80%)",
            "type":    "connections",
        })

    # PostgreSQL locks
    locks = pg.get("pg_waiting_locks", 0)
    if locks > 5:
        alertes.append({
            "niveau":  "IMPORTANT",
            "cible":   "linux-vm1/postgresql",
            "message": f"PostgreSQL: {int(locks)} waiting locks detected — possible deadlock",
            "type":    "locks",
        })

    return alertes