"""
web_search.py — Recherche intelligente dans la documentation Proxmox VE.

Architecture à 3 niveaux :
  Niveau 1 : Tavily API (recherche intelligente sur TOUT le web Proxmox)
  Niveau 2 : Scraping direct des pages wiki Proxmox officielles (fallback)
  Niveau 3 : Texte statique intégré (si pas de réseau)

Couverture Tavily :
  - pve.proxmox.com  (documentation officielle)
  - forum.proxmox.com (solutions de la communauté)
  - proxmox.com/blog  (annonces et bonnes pratiques)
  - github.com/proxmox (code source et issues)
"""
import os
import json
import time
import requests
from pathlib import Path
from bs4 import BeautifulSoup
import re

# ── Configuration ─────────────────────────────────────────────────────────────
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# Domaines officiels Proxmox couverts par Tavily
PROXMOX_DOMAINS = [
    "pve.proxmox.com",
    "forum.proxmox.com",
    "proxmox.com",
    "github.com/proxmox",
]

# URLs wiki Proxmox par type de problème (fallback si pas Tavily)
PROXMOX_DOCS_URLS = {
    "ram": [
        "https://pve.proxmox.com/wiki/Dynamic_Memory_Management",
        "https://pve.proxmox.com/wiki/KSM",
    ],
    "cpu": [
        "https://pve.proxmox.com/wiki/Qemu/KVM_Virtual_Machines",
        "https://pve.proxmox.com/wiki/CPU_Models",
    ],
    "disk": [
        "https://pve.proxmox.com/wiki/Storage",
        "https://pve.proxmox.com/wiki/ZFS_on_Linux",
    ],
    "swap": [
        "https://pve.proxmox.com/wiki/Dynamic_Memory_Management",
    ],
    "iowait": [
        "https://pve.proxmox.com/wiki/Storage",
        "https://pve.proxmox.com/wiki/Performance_Tweaks",
    ],
    "cluster": [
        "https://pve.proxmox.com/wiki/Cluster_Manager",
        "https://pve.proxmox.com/wiki/High_Availability",
    ],
    "quorum": [
        "https://pve.proxmox.com/wiki/Cluster_Manager",
        "https://pve.proxmox.com/wiki/High_Availability",
    ],
    "smart": [
        "https://pve.proxmox.com/wiki/S.M.A.R.T",
    ],
    "zfs": [
        "https://pve.proxmox.com/wiki/ZFS_on_Linux",
    ],
    "migration": [
        "https://pve.proxmox.com/wiki/Live_Migration",
    ],
    "temperature": [
        "https://pve.proxmox.com/wiki/Performance_Tweaks",
    ],
    "network": [
        "https://pve.proxmox.com/wiki/Network_Configuration",
    ],
    "backup": [
        "https://pve.proxmox.com/wiki/Backup_and_Restore",
    ],
    "vm": [
        "https://pve.proxmox.com/wiki/Qemu/KVM_Virtual_Machines",
        "https://pve.proxmox.com/wiki/High_Availability",
    ],
    "lxc": [
        "https://pve.proxmox.com/wiki/Linux_Container",
    ],
    "ai": [
        "https://pve.proxmox.com/wiki/Performance_Tweaks",
    ],
}

# ── Cache persistant sur disque (7 jours par défaut) ───────────────────────────
# ← CORRIGÉ : remplace l'ancien dict purement en mémoire (_cache = {}) --
# celui-ci était intégralement perdu à chaque redémarrage du serveur, très
# fréquent en développement actif. Résultat concret observé : saturation
# du quota mensuel Tavily (alerte à 80%) causée en grande partie par le
# re-téléchargement complet des mêmes 10 catégories de documentation
# Proxmox à répétition, alors que ce contenu (seuils officiels, bonnes
# pratiques KSM/ballooning/ZFS...) change en pratique sur des semaines,
# pas des heures. Persisté en JSON à côté de ce fichier -- survit aux
# redémarrages, chargé une fois à l'import, sauvegardé à chaque nouvelle
# entrée. Entrées expirées (au-delà de CACHE_TTL_S) filtrées au
# chargement, jamais servies même si encore présentes dans le fichier.
# ← MODIFIÉ : 7 → 30 jours par défaut. La documentation officielle
# Proxmox/PostgreSQL/Docker/etc. ne change pas à l'échelle de la semaine --
# aucune raison de retélécharger aussi souvent. Reste configurable via
# WEB_SEARCH_CACHE_TTL_S dans .env si besoin de raccourcir un jour (ex: en
# cas de changement de version majeure de Proxmox).
CACHE_TTL_S  = int(os.getenv("WEB_SEARCH_CACHE_TTL_S", str(30 * 86400)))  # 30 jours
_CACHE_FICHIER = Path(__file__).parent / "web_search_cache.json"


def _charger_cache_disque() -> dict:
    if not _CACHE_FICHIER.exists():
        return {}
    try:
        brut = json.loads(_CACHE_FICHIER.read_text(encoding="utf-8"))
        maintenant = time.time()
        vivantes = {
            cle: entree for cle, entree in brut.items()
            if isinstance(entree, dict) and "valeur" in entree
            and maintenant - entree.get("ts", 0) < CACHE_TTL_S
        }
        if vivantes:
            print(f"[WebSearch] Cache disque charge -- {len(vivantes)} entree(s) valide(s) "
                  f"(sur {len(brut)} au total, TTL {CACHE_TTL_S // 86400}j)")
        return vivantes
    except Exception as e:
        print(f"[WebSearch] Cache disque illisible ({e}) -- redemarrage a vide, sans casser le service")
        return {}


def _sauvegarder_cache_disque():
    try:
        _CACHE_FICHIER.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        # Non bloquant a dessein : une ecriture disque ratee ne doit jamais
        # faire echouer la recherche elle-meme, juste priver le PROCHAIN
        # redemarrage du benefice du cache pour cette entree precise.
        print(f"[WebSearch] Erreur sauvegarde cache disque (non bloquant): {e}")


def _cache_get(cle: str):
    entree = _cache.get(cle)
    if not entree:
        return None
    if time.time() - entree.get("ts", 0) >= CACHE_TTL_S:
        del _cache[cle]
        return None
    return entree["valeur"]


def _cache_set(cle: str, valeur):
    _cache[cle] = {"valeur": valeur, "ts": time.time()}
    _sauvegarder_cache_disque()


_cache: dict = _charger_cache_disque()


# ══════════════════════════════════════════════════════════════════════════════
# NIVEAU 1 — Tavily API (recherche intelligente complète)
# ══════════════════════════════════════════════════════════════════════════════

def _rechercher_tavily(probleme: str, max_chars: int = 1500) -> str:
    """
    Recherche via Tavily API — couvre TOUTE la documentation Proxmox.

    Tavily cherche sur :
    - pve.proxmox.com (wiki officiel complet)
    - forum.proxmox.com (solutions communauté)
    - proxmox.com/blog (bonnes pratiques)
    - github.com/proxmox (issues, changelogs)

    Retourne le contenu pertinent nettoyé, prêt pour le LLM.
    """
    if not TAVILY_API_KEY:
        return ""

    cache_key = f"tavily_{probleme}"
    hit = _cache_get(cache_key)
    if hit is not None:
        return hit

    # Requêtes spécialisées par type de problème
    queries = {
        "ram":         f"Proxmox VE high memory usage solution KSM ballooning live migration enterprise",
        "cpu":         f"Proxmox VE high CPU usage solution live migration VM optimization",
        "disk":        f"Proxmox VE disk full solution storage expansion ZFS LVM",
        "swap":        f"Proxmox VE swap usage high memory exhausted solution",
        "iowait":      f"Proxmox VE high iowait CPU blocked disk IO solution",
        "cluster":     f"Proxmox VE cluster issue HA high availability solution",
        "quorum":      f"Proxmox VE quorum lost corosync solution two_node",
        "smart":       f"Proxmox VE SMART disk failure warning solution",
        "zfs":         f"Proxmox VE ZFS pool issue ARC performance solution",
        "migration":   f"Proxmox VE live migration qm migrate online zero downtime",
        "temperature": f"Proxmox VE CPU temperature high thermal throttling solution",
        "network":     f"Proxmox VE network errors drops interface solution",
        "vm":          f"Proxmox VE VM stopped unexpected crash recovery HA",
        "ai":          f"Proxmox VE performance anomaly detection optimization",
    }

    query = queries.get(probleme.lower(),
                        f"Proxmox VE {probleme} solution best practices")

    try:
        response = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key":        TAVILY_API_KEY,
                "query":          query,
                "search_depth":   "advanced",
                "include_domains": PROXMOX_DOMAINS,
                "max_results":    5,
                "include_answer": True,
            },
            timeout=8,
        )
        response.raise_for_status()
        data = response.json()

        extraits = []

        # Réponse synthétisée par Tavily (le meilleur)
        if data.get("answer"):
            extraits.append(f"PROXMOX OFFICIAL ANSWER:\n{data['answer']}")

        # Résultats détaillés avec sources
        for result in data.get("results", [])[:3]:
            url     = result.get("url", "")
            content = result.get("content", "")
            if content and url:
                extraits.append(f"[SOURCE: {url}]\n{content[:400]}")

        if not extraits:
            return ""

        resultat = "\n\n---\n\n".join(extraits)[:max_chars]
        _cache_set(cache_key, resultat)
        print(f"[Tavily] '{probleme}' → {len(resultat)} chars depuis {len(data.get('results',[]))} sources")
        return resultat

    except Exception as e:
        print(f"[Tavily] Erreur pour '{probleme}': {e}")
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# NIVEAU 2 — Scraping direct wiki Proxmox (fallback si pas Tavily)
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_proxmox_page(url: str, max_chars: int = 1200) -> str:
    """Fetche une page wiki Proxmox et extrait le texte utile."""
    hit = _cache_get(url)
    if hit is not None:
        return hit

    try:
        headers = {
            "User-Agent": "OpsPilot-Agent/2.0 (infrastructure monitoring)"
        }
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code != 200:
            return ""

        soup = BeautifulSoup(r.text, "html.parser")
        content_div = (
            soup.find("div", {"id": "mw-content-text"}) or
            soup.find("div", {"class": "mw-parser-output"}) or
            soup.find("main")
        )
        if not content_div:
            return ""

        for tag in content_div.find_all(["nav", "aside", "footer", "script", "style"]):
            tag.decompose()

        texte = content_div.get_text(separator=" ", strip=True)
        texte = re.sub(r'\s+', ' ', texte).strip()

        resultat = texte[:max_chars]
        _cache_set(url, resultat)
        return resultat

    except Exception as e:
        print(f"[WebSearch] Erreur fetch {url}: {e}")
        return ""


def _rechercher_wiki(probleme: str) -> str:
    """Scraping direct des pages wiki Proxmox officielles."""
    urls = PROXMOX_DOCS_URLS.get(probleme.lower(), [])
    if not urls:
        return ""

    extraits = []
    for url in urls[:2]:
        contenu = _fetch_proxmox_page(url)
        if contenu:
            extraits.append(f"[SOURCE: {url}]\n{contenu}")

    return "\n\n---\n\n".join(extraits) if extraits else ""


# ══════════════════════════════════════════════════════════════════════════════
# Recherche documentaire par SERVICE (Redis, PostgreSQL, Docker...) — distincte
# de la recherche Proxmox ci-dessus
# ══════════════════════════════════════════════════════════════════════════════
# ← AJOUT : avant, un service détecté dans une VM (Redis, MySQL...) n'avait
# le choix qu'entre une mesure générique (RAM/CPU/disk via process-exporter,
# voir vm_app_monitor.py) et, à défaut de mesure, une phrase statique écrite
# une fois pour toutes côté incident_prompt.py (EXIGENCES_SERVICES) --
# exactement le même problème que résolvait déjà _rechercher_tavily()
# ci-dessus pour Proxmox (remplacer un texte figé par une vraie recherche).
# Domaines restreints au site OFFICIEL de chaque service -- jamais
# PROXMOX_DOMAINS, qui n'a évidemment aucune raison de documenter Redis ou
# PostgreSQL. Même cache _cache que le reste du fichier (préfixe "service_"
# pour ne jamais entrer en collision avec les clés "tavily_" existantes),
# même style d'appel Tavily (search_depth advanced, include_answer), même
# contrat de retour (chaîne vide si rien trouvé, jamais None) -- pour rester
# cohérent avec rechercher_doc_proxmox() ci-dessus.
SERVICE_DOMAINS = {
    "postgresql":      ["postgresql.org"],
    "docker":          ["docs.docker.com"],
    "nginx":           ["nginx.org"],
    "apache2":         ["httpd.apache.org"],
    "redis-server":    ["redis.io"],
    "redis":           ["redis.io"],
    "mysql":           ["dev.mysql.com"],
    "mariadb":         ["mariadb.org", "mariadb.com"],
    "mongod":          ["mongodb.com"],
    "rabbitmq-server": ["rabbitmq.com"],
    "grafana-server":  ["grafana.com"],
    "minio":           ["min.io"],
    "prometheus":      ["prometheus.io"],
    "alertmanager":    ["prometheus.io"],
}

# Requêtes spécialisées par service -- même principe que "queries" dans
# _rechercher_tavily() ci-dessus (une requête écrite pour ce service précis
# donne de meilleurs résultats qu'un gabarit générique). Un service absent
# d'ici retombe sur un gabarit générique construit à la volée, pas une
# erreur -- voir _rechercher_tavily_service() plus bas.
_QUERIES_SERVICE = {
    "postgresql":      "PostgreSQL memory shared_buffers connections monitoring best practices threshold",
    "docker":          "Docker container memory CPU limits monitoring best practices",
    "nginx":           "Nginx worker_connections memory CPU tuning monitoring best practices",
    "apache2":         "Apache httpd MaxRequestWorkers memory tuning monitoring best practices",
    "redis-server":    "Redis maxmemory maxmemory-policy memory fragmentation monitoring best practices",
    "redis":           "Redis maxmemory maxmemory-policy memory fragmentation monitoring best practices",
    "mysql":           "MySQL InnoDB buffer pool memory connections monitoring best practices threshold",
    "mariadb":         "MariaDB InnoDB buffer pool memory connections monitoring best practices threshold",
    "mongod":          "MongoDB WiredTiger cache memory connections monitoring best practices",
    "rabbitmq-server": "RabbitMQ memory watermark queue monitoring best practices",
    "grafana-server":  "Grafana memory CPU dashboard performance monitoring best practices",
    "minio":           "MinIO memory disk performance monitoring best practices",
    "prometheus":      "Prometheus memory retention head series scaling best practices",
    "alertmanager":    "Alertmanager memory clustering monitoring best practices",
}


def _rechercher_tavily_service(nom_service: str, max_chars: int = 1500) -> str:
    """Même structure que _rechercher_tavily() ci-dessus, appliquée à un
    service applicatif plutôt qu'à une catégorie Proxmox. Voir cette
    fonction pour le détail des paramètres Tavily -- inchangés ici."""
    if not TAVILY_API_KEY:
        return ""

    cache_key = f"service_{nom_service}"
    hit = _cache_get(cache_key)
    if hit is not None:
        return hit

    query = _QUERIES_SERVICE.get(
        nom_service.lower(),
        f"{nom_service} memory CPU metrics monitoring threshold best practices",
    )
    domaines = SERVICE_DOMAINS.get(nom_service.lower())

    body = {
        "api_key":        TAVILY_API_KEY,
        "query":          query,
        "search_depth":   "advanced",
        "max_results":    5,
        "include_answer": True,
    }
    if domaines:
        body["include_domains"] = domaines

    try:
        response = requests.post("https://api.tavily.com/search", json=body, timeout=8)
        response.raise_for_status()
        data = response.json()

        extraits = []
        if data.get("answer"):
            extraits.append(f"{nom_service.upper()} OFFICIAL ANSWER:\n{data['answer']}")
        for result in data.get("results", [])[:3]:
            url     = result.get("url", "")
            content = result.get("content", "")
            if content and url:
                extraits.append(f"[SOURCE: {url}]\n{content[:400]}")

        if not extraits:
            return ""

        resultat = "\n\n---\n\n".join(extraits)[:max_chars]
        _cache_set(cache_key, resultat)
        print(f"[Tavily] '{nom_service}' → {len(resultat)} chars depuis {len(data.get('results',[]))} sources")
        return resultat

    except Exception as e:
        print(f"[Tavily] Erreur pour service '{nom_service}': {e}")
        return ""


def rechercher_doc_service(nom_service: str) -> str:
    """
    Recherche la documentation OFFICIELLE d'un service applicatif (Redis,
    PostgreSQL, Docker...) -- pas Proxmox. Pas de repli wiki ici,
    contrairement à rechercher_doc_proxmox() : le scraping wiki ci-dessus
    est câblé en dur sur pve.proxmox.com (PROXMOX_DOCS_URLS), qui n'a
    structurellement aucune page pour Redis ou PostgreSQL -- le dupliquer
    pour chaque service nécessiterait sa propre table d'URLs par service,
    déjà couvert plus simplement par SERVICE_DOMAINS + Tavily ci-dessus.
    Chaîne vide si rien trouvé ou si Tavily n'est pas configuré -- jamais
    d'exception vers l'appelant, même contrat que rechercher_doc_proxmox().
    """
    if TAVILY_API_KEY:
        resultat = _rechercher_tavily_service(nom_service)
        if resultat:
            return resultat

    print(f"[WebSearch] Aucune doc officielle trouvée pour '{nom_service}'")
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# Interface principale — utilisée par surveillance.py
# ══════════════════════════════════════════════════════════════════════════════

def rechercher_doc_proxmox(type_probleme: str) -> str:
    """
    Recherche la documentation Proxmox officielle pour un problème donné.

    Ordre de priorité :
    1. Tavily API  → TOUTE la documentation Proxmox (wiki + forum + blog + github)
    2. Wiki direct → Scraping des pages officielles (si Tavily non configuré)
    3. Chaîne vide → Si aucun accès réseau

    Args:
        type_probleme: 'ram', 'cpu', 'disk', 'swap', 'cluster', 'vm', etc.

    Returns:
        Documentation Proxmox pertinente à injecter dans le prompt LLM.
    """
    # Niveau 1 : Tavily (si clé configurée)
    if TAVILY_API_KEY:
        resultat = _rechercher_tavily(type_probleme)
        if resultat:
            return resultat
        print(f"[Tavily] Pas de résultat → fallback wiki direct")

    # Niveau 2 : Scraping wiki Proxmox direct
    resultat = _rechercher_wiki(type_probleme)
    if resultat:
        print(f"[WebSearch] Wiki Proxmox: {len(resultat)} chars pour '{type_probleme}'")
        return resultat

    print(f"[WebSearch] Aucune doc trouvée pour '{type_probleme}'")
    return ""


def get_doc_url(type_probleme: str) -> str:
    """Retourne l'URL principale de la doc Proxmox pour un type de problème."""
    urls = PROXMOX_DOCS_URLS.get(type_probleme.lower(), [])
    return urls[0] if urls else "https://pve.proxmox.com/wiki/Main_Page"


def get_tavily_status() -> dict:
    """Retourne le statut de la configuration Tavily pour l'API /api/status."""
    return {
        "tavily_configured": bool(TAVILY_API_KEY),
        "tavily_key_present": bool(TAVILY_API_KEY),
        "search_level": "tavily" if TAVILY_API_KEY else "wiki_scraping",
        "proxmox_domains": PROXMOX_DOMAINS,
    }