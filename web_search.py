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
import requests
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

# Cache simple pour éviter les appels répétitifs
_cache: dict = {}


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
    if cache_key in _cache:
        return _cache[cache_key]

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
        _cache[cache_key] = resultat
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
    if url in _cache:
        return _cache[url]

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
        _cache[url] = resultat
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