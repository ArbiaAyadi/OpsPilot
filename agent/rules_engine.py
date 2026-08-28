import asyncio
import json
import os
import re
import time
from agent.groq_client import appeler_groq, rate_limiter
from agent.config import INTERVALLE_REGENERATION_REGLES

try:
    from web_search import rechercher_doc_proxmox
    WEB_SEARCH_OK = True
except ImportError:
    WEB_SEARCH_OK = False

_regles_ia:                  list  = []
_derniere_generation_regles: float = 0.0
# ← AJOUT : verrou distinct pour les tentatives ÉCHOUÉES (rate limit Groq,
# JSON invalide...) -- voir le correctif dans generer_regles_ia() plus bas.
# Sans lui, une tentative échouée ne fait jamais avancer
# _derniere_generation_regles, donc regles_necessitent_regeneration()
# continue de retourner True à CHAQUE cycle de surveillance (60s) au lieu
# d'une fois par 24h -- une panne Groq transitoire devenait une boucle de
# nouvelles tentatives toutes les 60s, qui aggravait elle-même le
# rate-limiting Groq et affamait les vraies analyses d'incidents.
_dernier_echec_generation_regles:            float = 0.0
INTERVALLE_MIN_ENTRE_TENTATIVES_ECHOUEES_S = 1800  # 30 min (etait 10 min -- allonge car les nouveaux modeles Groq ont un plafond tokens/minute plus serre que les anciens, meme 10 min restait court si un autre appel Groq tombe dans la meme fenetre)

# ── Fraîcheur des règles ────────────────────────────────────────────────────
_proxmox_accessible_derniere_maj: bool  = True
_derniere_verification_ts:        float = 0.0

def marquer_etat_accessibilite(accessible: bool):
    """Appelé par surveillance.py à chaque cycle de la boucle principale."""
    global _proxmox_accessible_derniere_maj, _derniere_verification_ts
    _proxmox_accessible_derniere_maj = accessible
    _derniere_verification_ts        = time.time()


def get_etat_fraicheur() -> dict:
    """
    Indique si les règles actuellement en cache reflètent un cluster
    joignable en ce moment, ou si elles datent d'avant une coupure.
    """
    return {
        "proxmox_accessible":   _proxmox_accessible_derniere_maj,
        "derniere_verification": _derniere_verification_ts,
        "regles_perimees":      not _proxmox_accessible_derniere_maj,
    }


# ← RENOMMÉ conceptuellement (le nom reste, le RÔLE change) : ce n'est plus
# "la réponse que le LLM doit recopier" -- avant ce correctif,
# PROXMOX_DOCS_CONTEXT (bloc de texte figé plus bas) jouait ce rôle, avec
# l'étiquette "SOURCE: Proxmox VE official documentation" collée dessus
# sans qu'aucune recherche n'ait jamais eu lieu. Vérifié : Proxmox ne
# publie nulle part une table officielle de seuils d'alerte -- ces
# nombres restent une bonne base professionnelle (recoupée avec des
# sources réelles au moment d'écrire ce correctif), mais ne sont plus
# présentés comme LA documentation officielle. Rôle actuel : PLANCHER
# minimum, jamais franchi vers le bas, quoi que la vraie recherche
# ci-dessous (ou le LLM) propose -- un filet de sécurité, pas une réponse
# imposée. Le LLM peut proposer plus strict si la recherche le justifie.
SEUILS_PLANCHER = {
    # ← CORRIGÉ (régression de la découverte dynamique) : les clés doivent
    # correspondre EXACTEMENT aux noms de champs réels produits par
    # _decouvrir_metriques() -- c'est sur ces noms-là que le LLM génère
    # désormais ses règles. Les anciennes clés abrégées ("server.latency",
    # "server.temperature", "server.io_wait_pct"...) venaient de la liste
    # figée d'avant et ne matchaient plus rien depuis le passage à la
    # découverte : 6 planchers étaient devenus silencieusement orphelins,
    # laissant le LLM décider seul sur des métriques pourtant protégées
    # auparavant. Les anciens noms sont CONSERVÉS en alias plus bas, au cas
    # où le modèle produirait encore une forme abrégée.
    "server.cpu_pct":              {"CRITIQUE": 80,   "IMPORTANT": 65},
    "server.ram_pct":              {"CRITIQUE": 85,   "IMPORTANT": 75},
    "server.disk_pct":             {"CRITIQUE": 90,   "IMPORTANT": 80},
    "server.swap_pct":             {"CRITIQUE": 80,   "IMPORTANT": 50},
    "server.cpu_iowait_pct":       {"CRITIQUE": 30,   "IMPORTANT": 15},
    "server.disk_read_latency_ms": {"CRITIQUE": 50,   "IMPORTANT": 10},
    "server.disk_write_latency_ms":{"CRITIQUE": 50,   "IMPORTANT": 10},
    "server.cpu_temp_max_c":       {"CRITIQUE": 85,   "IMPORTANT": 75},
    "server.net_errors_in":        {"CRITIQUE": 20,   "IMPORTANT": 5},
    "server.net_errors_out":       {"CRITIQUE": 20,   "IMPORTANT": 5},
    "server.net_drop_in":          {"CRITIQUE": 20,   "IMPORTANT": 5},
    "server.net_drop_out":         {"CRITIQUE": 20,   "IMPORTANT": 5},
    "server.load_avg_1m":          {"CRITIQUE": 8,    "IMPORTANT": 4},
    "server.fd_used_pct":          {"CRITIQUE": 90,   "IMPORTANT": 70},
    "server.cpu_steal_pct":        {"CRITIQUE": 25,   "IMPORTANT": 10},
    "server.procs_blocked":        {"CRITIQUE": 5,    "IMPORTANT": 2},
    "server.disk_temp_max_c":      {"CRITIQUE": 60,   "IMPORTANT": 50},
    "server.corosync_ok":          {},   # booléen
    "server.corosync_quorum_ok":   {},   # booléen
    "server.smart_ok":             {},   # booléen
    "server.power_ok":             {},   # booléen
    # ← RÉINTÉGRÉES : ces compteurs déclenchent de vraies alertes dans
    # anomaly_detector.py mais étaient exclus de la découverte, donc
    # aucune règle n'était générée ni affichée pour eux -- incohérence
    # entre ce qui alerte et ce qui est documenté comme règle. Ce sont des
    # compteurs d'erreurs matérielles : toute valeur non nulle est déjà
    # anormale, d'où des planchers très bas (0 = avertissement dès la
    # première occurrence, contrairement à un pourcentage d'utilisation).
    "server.smart_uncorrectable":       {"CRITIQUE": 0, "IMPORTANT": 0},
    "server.smart_reallocated_sectors": {"CRITIQUE": 0, "IMPORTANT": 0},
    "server.smart_pending_sectors":     {"CRITIQUE": 0, "IMPORTANT": 0},
    "server.corosync_ring_errors":      {"CRITIQUE": 0, "IMPORTANT": 0},
    "server.zfs_arc_hit_rate":     {},   # inverse : plus BAS = pire, pas de plancher minimum applicable tel quel
    "vm.cpu_pct":                  {"CRITIQUE": 90,   "IMPORTANT": 75},
    "vm.ram_pct":                  {"CRITIQUE": 90,   "IMPORTANT": 80},
    "vm.disk_pct":                 {"CRITIQUE": 90,   "IMPORTANT": 80},
    "vm.status":                   {},   # booléen, pas de plancher numérique
    "cluster.quorum":              {},
    "ai.score":                    {"CRITIQUE": 0.8,  "IMPORTANT": 0.5},

    # ── Alias des anciens noms abrégés ────────────────────────────────────
    # Conservés pour que le plancher s'applique quand même si le modèle
    # produit une forme abrégée plutôt que le nom de champ exact.
    "server.io_wait_pct":          {"CRITIQUE": 30,   "IMPORTANT": 15},
    "server.latency":              {"CRITIQUE": 50,   "IMPORTANT": 10},
    "server.temperature":          {"CRITIQUE": 85,   "IMPORTANT": 75},
    "server.net_errors":           {"CRITIQUE": 20,   "IMPORTANT": 5},
    "server.load_avg":             {"CRITIQUE": 8,    "IMPORTANT": 4},
    "zfs.arc_hit_rate":            {},
}

def _normaliser_severite(severite: str) -> str:
    """Normalise la severite generee par le LLM vers CRITIQUE/IMPORTANT/SURVEILLANCE."""
    MAP = {
        "critique":    "CRITIQUE",
        "critical":    "CRITIQUE",
        "important":   "IMPORTANT",
        "high":        "IMPORTANT",
        "surveillance":"SURVEILLANCE",
        "monitoring":  "SURVEILLANCE",
        "low":         "SURVEILLANCE",
    }
    return MAP.get(str(severite or "").lower(), "SURVEILLANCE")


def _valider_seuil(metric: str, seuil, severite: str):
    """
    Valide que le seuil genere par le LLM est >= au minimum professionnel
    (SEUILS_PLANCHER). Retourne le seuil corrige si trop bas, le seuil
    original sinon -- toujours actif, indépendamment de si une vraie
    recherche a alimenté cette règle ou non (voir _rassembler_recherche_doc
    ci-dessous : si la recherche échoue pour une catégorie, le plancher
    reste la seule garantie de seuils raisonnables).
    """
    plancher = SEUILS_PLANCHER.get(metric, {})
    if not plancher or not isinstance(seuil, (int, float)):
        return seuil
    sev_key = "CRITIQUE" if "CRIT" in severite.upper() else "IMPORTANT"
    minimum = plancher.get(sev_key)
    if minimum is not None and float(seuil) < minimum:
        print(f"[AI Rules] Seuil corrige: {metric} {seuil} → {minimum} ({sev_key})")
        return minimum
    return seuil


# ══════════════════════════════════════════════════════════════════════════════
# Vraie recherche documentaire — remplace l'ancien PROXMOX_DOCS_CONTEXT figé
# ══════════════════════════════════════════════════════════════════════════════
# Catégories couvrant les 15 règles générées plus bas. Une seule catégorie
# n'a volontairement pas d'appel dédié ("ai" -- l'AI score n'est pas un
# seuil documenté quelque part, c'est un score statistique interne, la
# recherche n'apporterait rien de pertinent pour celui-là spécifiquement).
# Sortie maximale pour la generation de regles -- ajustable par .env
# si l'infrastructure grandit beaucoup (voir le commentaire au point
# d'appel pour le calcul detaille).
REGLES_MAX_TOKENS = int(os.getenv("REGLES_MAX_TOKENS", "5000"))

_CATEGORIES_RECHERCHE = ["cpu", "ram", "disk", "swap", "iowait", "temperature", "network", "quorum", "vm"]

# ← AJOUT : mappe le premier segment d'un nom de métrique découverte vers
# une catégorie de recherche EXISTANTE et déjà soigneusement rédigée dans
# _rechercher_tavily() (web_search.py) quand une correspondance évidente
# existe -- sinon, le premier segment lui-même sert de catégorie, et
# _rechercher_tavily() construit alors une requête générique à partir de
# ce nom (déjà supporté nativement de son côté, voir son repli
# "queries.get(probleme.lower(), f'Proxmox VE {probleme} solution best
# practices')"). "temp" cherché n'importe où dans le nom (pas seulement en
# premier segment) car plusieurs métriques de température ne commencent
# pas par ce mot (disk_temp_max_c, cpu_temp_max_c).
_SYNONYMES_CATEGORIE = {
    "cpu": "cpu", "ram": "ram", "disk": "disk", "swap": "swap",
    "net": "network", "corosync": "quorum", "zfs": "zfs", "smart": "smart",
}


def _categorie_depuis_metrique(nom_complet: str) -> str:
    """Déduit une catégorie de recherche à partir d'un nom de métrique
    découvert (ex: 'server.cpu_temp_max_c' -> 'temperature',
    'server.gpu_util_pct' -> 'gpu' -- aucune correspondance connue, le nom
    lui-même sert de catégorie de recherche générique)."""
    champ = nom_complet.split(".", 1)[-1]
    if "temp" in champ:
        return "temperature"
    premier_mot = champ.split("_")[0]
    return _SYNONYMES_CATEGORIE.get(premier_mot, premier_mot)


async def _rassembler_recherche_doc(hyperviseur_type: str = None, categories: list = None) -> tuple:
    """
    Interroge rechercher_doc_proxmox() pour chaque catégorie pertinente --
    avant ce correctif, cette fonction n'existait pas du tout : le prompt
    utilisait uniquement un bloc de texte fixe qui PRÉTENDAIT être de la
    documentation officielle sans jamais faire une seule recherche.

    ← CORRIGÉ (TimeoutError) : rechercher_doc_proxmox() est une fonction
    bloquante (I/O réseau synchrone, Tavily/requests) -- l'appeler 9 fois
    de suite (une par catégorie) dans une boucle for prenait facilement
    plus de 60 secondes au total, ce qui dépassait le timeout fixé côté
    surveillance._boucle_surveillance() (asyncio.run_coroutine_threadsafe
    (...).result(timeout=60)), provoquant un TimeoutError qui interrompait
    la génération de règles en plein milieu. Les 9 recherches tournent
    maintenant CONCURREMMENT via un thread pool (run_in_executor +
    gather) -- le temps total redevient celui de la recherche la PLUS
    LENTE, pas la somme des 9, ramenant ça de 60+s à quelques secondes en
    pratique.

    ← AJOUT (recherche hyperviseur) : hyperviseur_type vient de
    etat["hyperviseur"]["type"] (voir hypervisor_detect.py, déjà détecté à
    chaque cycle -- "vmware" ici, mais jamais codé en dur : whatever est
    réellement détecté). Ajoutée comme 10e catégorie SEULEMENT quand connue
    ("unknown" ou absente = pas de recherche inutile) -- même mécanisme
    exact que les 9 autres, juste une catégorie de plus. Utile
    spécifiquement pour cette infrastructure : Proxmox tournant imbriqué
    sous VMware Workstation a des considérations propres (virtualisation
    imbriquée, allocation CPU/RAM) que les 9 catégories de métriques pures
    ne couvrent pas.

    Retourne (texte_assemblé, recherche_reussie) -- recherche_reussie sert
    ensuite à écrire un champ "source" honnête sur les règles générées,
    plutôt que le LLM affirmant "official documentation" sans condition.
    """
    if not WEB_SEARCH_OK:
        return "", False

    # ← MODIFIÉ : utilise la liste dynamique fournie par l'appelant si
    # présente (généer_regles_ia lui passe maintenant les catégories
    # déduites des métriques réellement découvertes) -- sinon, ancien
    # comportement inchangé (liste fixe + hyperviseur), pour tout autre
    # appelant éventuel qui n'aurait pas encore de liste dynamique à
    # fournir.
    if categories is None:
        categories = list(_CATEGORIES_RECHERCHE)
        if hyperviseur_type and hyperviseur_type != "unknown":
            categories.append(hyperviseur_type)

    loop = asyncio.get_event_loop()

    async def _rechercher_une_categorie(categorie: str):
        try:
            doc = await loop.run_in_executor(None, rechercher_doc_proxmox, categorie)
            return categorie, doc
        except Exception as e:
            print(f"[AI Rules] Recherche '{categorie}' echouee: {e}")
            return categorie, None

    resultats = await asyncio.gather(*[_rechercher_une_categorie(c) for c in categories])

    morceaux = []
    au_moins_un_succes = False
    for categorie, doc in resultats:
        if doc:
            # ← RÉDUIT : 600 → 300 caractères par catégorie (10 catégories
            # au total). Les nouveaux modèles Groq (openai/gpt-oss-20b/120b,
            # qwen3.6-27b) ont un plafond tokens/minute nettement plus
            # serré que les anciens llama-3.1-8b-instant/llama-3.3-70b --
            # ce prompt (documentation + spécification des 22 règles) est
            # le plus lourd de tout le système, celui qui sature le plus
            # facilement ce plafond. Ce correctif retire ~750 tokens
            # d'entrée sans changer la structure du prompt -- les planchers
            # professionnels (GARDE_FOUS_SEUILS) restent la contrainte
            # dure de toute façon, cette doc n'est qu'un contexte
            # d'appoint pour la justification/description des règles.
            morceaux.append(f"[{categorie.upper()}]\n{doc[:300]}")
            au_moins_un_succes = True

    if au_moins_un_succes:
        print(f"[AI Rules] Recherche documentaire: {len(morceaux)}/{len(categories)} categories trouvees")
    else:
        print("[AI Rules] Aucune recherche documentaire disponible -- repli sur les planchers seuls")

    return "\n\n---\n\n".join(morceaux), au_moins_un_succes


# ══════════════════════════════════════════════════════════════════════════════
# Découverte automatique des métriques alertables -- remplace la liste fixe
# ══════════════════════════════════════════════════════════════════════════════
# ← AJOUT (refonte complète, sur demande explicite) : avant, cette fonction
# demandait un nombre FIXE de règles pour une liste de métriques tapée à la
# main dans le prompt (1. server.cpu_pct, 2. server.ram_pct...) -- ajouter
# une nouvelle métrique voulait dire éditer ce fichier à la main à chaque
# fois. Ce n'est PAS ce que le projet vise : l'agent doit détecter LUI-MÊME
# quelles métriques existent réellement dans les données collectées et
# générer une règle pour chacune, sans intervention humaine à chaque
# évolution de l'infrastructure (nouvelle VM, nouveau service, nouveau
# matériel). Construit en lisant metriques_proxmox.py champ par champ.
#
# Un champ est exclu de la découverte s'il est : un identifiant (node,
# vmid, name...), une valeur brute redondante avec son équivalent normalisé
# déjà présent (ram_used_gb à côté de ram_pct -- le % est la métrique
# alertable, le brut n'est que du contexte), un compte purement
# informationnel (cpu_cores, smart_disks_monitored), un drapeau de
# disponibilité (zfs_available, ipmi_available), ou confirmé structurellement
# toujours à zéro par le fichier source lui-même (corosync_ring_latency_ms
# -- l'exportateur ne fournit tout simplement pas cette donnée, une règle
# dessus ne déclencherait jamais, juste du bruit dans le prompt).
#
# Les métriques conditionnelles à une capacité matérielle absente
# aujourd'hui (power_watts/power_ok -- nécessitent un vrai contrôleur IPMI,
# absent sur des VMs VMware) sont volontairement GARDÉES dans la
# découverte, pas filtrées : la règle générée est inoffensive tant que la
# valeur reste à 0 (elle ne déclenche jamais), mais elle existe déjà le
# jour où l'infrastructure passe sur du matériel physique réel -- aucune
# intervention nécessaire à ce moment-là, exactement l'objectif de
# généralisation visé.
_CHAMPS_EXCLUS_DECOUVERTE = {
    "node", "timestamp", "name", "type", "vmid", "statut", "noeud",
    "cpu_cores", "load_avg_5m", "load_avg_15m",
    "ram_used_gb", "ram_total_gb", "swap_total_gb", "swap_used_gb",
    "disk_used_gb", "disk_total_gb", "maxmem_gb", "maxdisk_gb",
    "procs_total", "procs_running", "uptime_h", "vms_running", "vcpus",
    "cpu_temp_avg_c", "cpu_temp_cores", "cpu_temp_critical_c",
    "smart_power_on_hours_max", "smart_disks_monitored",
    "zfs_arc_size_gb", "zfs_arc_miss_rate", "zfs_arc_max_gb", "zfs_available",
    "corosync_members", "corosync_members_ok",
    "corosync_ring_latency_ms", "corosync_available", "ipmi_available",
    "net_in_mbps", "net_out_mbps",  # débit brut nœud -- convention existante : pas seuillé directement, contrairement aux erreurs/drops
    "tags", "services_detectes", "metriques_services", "ai_score",
}


def _decouvrir_metriques(objets: list, prefixe: str) -> list:
    """Découvre les métriques numériques/booléennes réellement présentes dans
    une liste de dicts (noeuds ou VMs) -- UNION sur tous les objets, pas
    seulement le premier, pour ne rater aucun champ qui ne serait présent
    que sur certains (ex: zfs_arc_hit_rate absent tant que ZFS n'est pas
    utilisé sur CE nœud précis). Retourne une liste de tuples
    (nom_complet, valeur_exemple, unite_devinee) triée par nom pour un
    prompt stable d'un cycle à l'autre. La valeur exemple n'est PAS un
    seuil -- juste un repère de contexte, le prompt l'indique explicitement
    au LLM."""
    vus: dict = {}
    for obj in (objets or []):
        for champ, valeur in (obj or {}).items():
            if champ in _CHAMPS_EXCLUS_DECOUVERTE or champ in vus:
                continue
            if isinstance(valeur, bool):
                vus[champ] = (valeur, "bool")
            elif isinstance(valeur, (int, float)):
                if champ.endswith("_pct") or champ.endswith("_hit_rate"):
                    unite = "%"
                elif champ.endswith("_ms"):    unite = "ms"
                elif champ.endswith("_gb"):    unite = "GB"
                elif champ.endswith("_mbps"):  unite = "MB/s"
                elif champ.endswith("_c"):     unite = "°C"
                elif champ.endswith("_watts"): unite = "W"
                elif "iops" in champ:          unite = "ops/s"
                elif "rate" in champ:          unite = "/s"
                else:                          unite = ""
                vus[champ] = (valeur, unite)
    return sorted(
        [(f"{prefixe}.{champ}", val, unite) for champ, (val, unite) in vus.items()],
        key=lambda t: t[0]
    )


GARDE_FOUS_SEUILS = """
## MINIMUM PROFESSIONAL FLOORS — a safety net, NOT the definitive answer
These are the LOWEST acceptable values for CRITICAL/HIGH thresholds. You may propose an EQUAL or HIGHER
(stricter) value if the research above supports it for this specific metric -- but never lower than the
floor. If the research above is silent on a specific metric, use the floor value directly.

### Hypervisor Nodes
CPU Usage:       floor CRITICAL >= 80%  | floor HIGH >= 65%
RAM Usage:       floor CRITICAL >= 85%  | floor HIGH >= 75%
Disk Usage:      floor CRITICAL >= 90%  | floor HIGH >= 80%
Swap Usage:      floor CRITICAL >= 80%  | floor HIGH >= 50%
CPU I/O Wait:    floor CRITICAL >= 30%  | floor HIGH >= 15%
Disk Latency:    floor CRITICAL >= 50ms | floor HIGH >= 10ms
CPU Temperature: floor CRITICAL >= 85C  | floor HIGH >= 75C
Net Errors:      floor CRITICAL >= 20/s | floor HIGH >= 5/s
CPU Steal:       floor CRITICAL >= 25%  | floor HIGH >= 10% (VMware Workstation host-level contention)
Blocked Processes: floor CRITICAL >= 5  | floor HIGH >= 2 (uninterruptible I/O wait, storage bottleneck signal)
Disk Temperature: floor CRITICAL >= 60C | floor HIGH >= 50C
File Descriptors: floor CRITICAL >= 90% | floor HIGH >= 70%
Load Average:    floor CRITICAL >= 8    | floor HIGH >= 4
Corosync Daemon: CRITICAL = degraded (distinct from quorum loss -- daemon health itself)

### Virtual Machines
VM CPU: floor HIGH >= 75%
VM RAM: floor HIGH >= 80%
VM Disk: floor HIGH >= 80%

### Cluster
Quorum:   CRITICAL = lost (not negotiable, no floor concept applies)
AI Score: floor CRITICAL >= 0.8 | floor HIGH >= 0.5

These floors will be enforced programmatically after you respond, regardless of what you write here --
but use the research above to propose real, reasoned values at or above them, not just the floor number
copied verbatim for every single rule.
"""


async def generer_regles_ia(etat: dict) -> list:
    """
    Genere une regle par metrique DECOUVERTE automatiquement au niveau noeud/VM (plus 3 regles d'etat speciales), plus
    une regle additionnelle par service applicatif reellement detecte ce
    cycle-ci et disposant de metriques mesurables (voir services_ctx
    ci-dessous) -- jamais une liste de services figee, exactement ce que
    detecter_services_vm() trouve en direct.

    ← REFONTE : rechercher_doc_proxmox() est maintenant réellement appelée
    (voir _rassembler_recherche_doc ci-dessus), une fois par catégorie
    pertinente, AVANT de construire le prompt -- son résultat est injecté
    tel quel. Auparavant, le prompt contenait un bloc de texte fixe
    (PROXMOX_DOCS_CONTEXT) présenté comme "official documentation" sans
    qu'aucune recherche n'ait jamais eu lieu -- le LLM ne faisait que
    reformater ce texte en JSON. Le LLM raisonne maintenant à partir d'une
    vraie recherche + un plancher de sécurité minimum (SEUILS_PLANCHER,
    inchangé, toujours appliqué après coup via _valider_seuil) -- pas une
    réponse à recopier.

    Le champ "source" de chaque règle est réécrit en Python après
    génération (pas laissé au LLM) pour refléter honnêtement si une vraie
    recherche a alimenté cette règle ou non ce cycle-ci.
    """
    global _regles_ia, _derniere_generation_regles, _dernier_echec_generation_regles

    if not etat or not etat.get("noeuds"):
        return _regles_ia

    noeuds_online = [n for n in etat.get("noeuds", []) if n.get("statut") == "online"]
    noeuds_ctx    = "\n".join([
        f"  {n['nom']}: CPU={n['cpu_pct']}% RAM={n['ram_pct']}% "
        f"({n.get('ram_used_gb',0)}/{n.get('ram_total_gb',0)}GB) "
        f"Disk={n['disk_pct']}% Swap={n.get('swap_pct',0)}%"
        for n in etat.get("noeuds", [])
    ])
    vms_ctx = "\n".join([
        f"  {v['nom']} (VMID:{v['vmid']}) on {v['noeud']}: {v['statut']}"
        for v in etat.get("vms", [])
    ])
    node_names = ",".join(n["nom"] for n in etat.get("noeuds", []))

    # ← AJOUT : services applicatifs réellement détectés ce cycle-ci
    # (jamais une liste figée -- exactement ce que detecter_services_vm()
    # trouve en direct via Prometheus, voir vm_app_monitor.py). Répond au
    # constat que les 22 règles ci-dessous ne couvraient QUE le niveau
    # nœud/VM, jamais un service applicatif (PostgreSQL, Docker...) --
    # même principe de généralisation que le reste du fichier : le
    # contenu s'adapte à CE qui tourne réellement, pas à une liste
    # écrite une fois pour toutes. Dédupliqué par nom de service (un
    # service tournant sur plusieurs VMs n'apparaît qu'une fois, avec la
    # première VM trouvée comme référence).
    CLES_METRIQUES_GENERIQUES = {"ram_mb", "cpu_pct", "cpu_pct_vm", "disk_read_mbps", "disk_write_mbps", "num_procs"}
    services_detectes: dict = {}
    for v in etat.get("vms", []):
        for s in v.get("services_detectes", []) or []:
            if s in services_detectes:
                continue
            m = (v.get("metriques_services") or {}).get(s)
            extras = {k: val for k, val in (m or {}).items() if k not in CLES_METRIQUES_GENERIQUES and val is not None}
            services_detectes[s] = {"vm": v.get("nom"), "vmid": v.get("vmid"), "extras": extras}

    services_ctx_lignes = []
    for nom_service, info in services_detectes.items():
        if info["extras"]:
            details = ", ".join(f"{k}={val}" for k, val in info["extras"].items())
            services_ctx_lignes.append(f"  {nom_service} on {info['vm']} (VMID {info['vmid']}) -- measurable metrics: {details}")
        else:
            services_ctx_lignes.append(f"  {nom_service} on {info['vm']} (VMID {info['vmid']}) -- no service-specific metric collected yet, only generic VM RAM/CPU available")
    services_ctx = "\n".join(services_ctx_lignes) if services_ctx_lignes else "  No application service currently detected on any VM."

    # ← AJOUT : liste de métriques construite par découverte, plus par
    # énumération figée -- voir _decouvrir_metriques() plus haut pour le
    # raisonnement complet. server.* depuis les nœuds, vm.* depuis les VMs.
    # ← DÉPLACÉ avant la recherche documentaire (au lieu d'après) : les
    # catégories de recherche EN DÉPENDENT maintenant (voir juste en
    # dessous) -- il faut savoir ce qui est découvert avant de savoir quoi
    # rechercher.
    metriques_serveur  = _decouvrir_metriques(etat.get("noeuds", []), "server")
    metriques_vm       = _decouvrir_metriques(etat.get("vms", []),    "vm")
    lignes_decouvertes = "\n".join(
        f"- {nom} (currently {val}{unite}, {'boolean -- alert when False unless noted otherwise' if unite=='bool' else 'numeric'})"
        for nom, val, unite in metriques_serveur + metriques_vm
    )
    nb_metriques_decouvertes = len(metriques_serveur) + len(metriques_vm)

    # ← AJOUT : type d'hyperviseur réellement détecté ce cycle (voir
    # hypervisor_detect.py, déjà stocké dans etat["hyperviseur"]["type"]
    # avant l'appel à cette fonction dans surveillance.py) -- jamais codé
    # en dur, s'adapte automatiquement si l'infrastructure change.
    hyperviseur_type = etat.get("hyperviseur", {}).get("type")

    # ← AJOUT : catégories de recherche déduites des métriques réellement
    # DÉCOUVERTES ci-dessus, plus la liste fixe historique en repli (sur
    # demande explicite : la recherche documentaire ne doit plus dépendre
    # d'une liste figée non plus, sinon une métrique GPU découverte
    # n'aurait jamais de vraie recherche "GPU" derrière -- juste un
    # raisonnement LLM sans aucun ancrage documentaire, l'exact problème
    # signalé). _rechercher_tavily() (web_search.py) gère déjà nativement
    # une catégorie inconnue via une requête générique construite sur son
    # nom -- rien à changer de ce côté, seulement lui envoyer la bonne
    # liste.
    categories_deduites = {
        _categorie_depuis_metrique(nom) for nom, _, _ in metriques_serveur + metriques_vm
    }
    categories = set(categories_deduites) | set(_CATEGORIES_RECHERCHE)
    if hyperviseur_type and hyperviseur_type != "unknown":
        categories.add(hyperviseur_type)
    categories = sorted(categories)
    doc_recherche, recherche_ok = await _rassembler_recherche_doc(hyperviseur_type, categories)
    doc_bloc = (
        f"## REAL-TIME RESEARCH (fetched now from pve.proxmox.com / forum.proxmox.com)\n{doc_recherche}\n"
        if doc_recherche else
        "## REAL-TIME RESEARCH\n(unavailable this cycle -- rely on the professional floors below only)\n"
    )

    prompt = f"""You are a senior Proxmox VE infrastructure engineer.
Generate ONE professional alert rule for EACH metric listed below under "METRICS DISCOVERED THIS
CYCLE" (skip a metric only if it is genuinely not operationally meaningful to alert on -- this should
be rare), plus the 3 special state-based rules listed separately, plus one additional rule per
application service with measurable metrics (detailed further below). Reason the threshold from the
real research and the safety floors below -- not a fixed template to copy. This list is generated
automatically from what this cluster's actual data contains -- it will differ as the infrastructure
changes (new VMs, new hardware capabilities, new services), and that is intentional: reason about
whatever is listed below, not a memorized set of Proxmox metrics.

CLUSTER:
{noeuds_ctx}
VMs: {vms_ctx if vms_ctx else "None"}
Node names: {node_names}

{doc_bloc}
{GARDE_FOUS_SEUILS}

METRICS DISCOVERED THIS CYCLE ({nb_metriques_decouvertes} total, "server."=hypervisor node level, "vm."=virtual machine level)
-- generate exactly one rule per metric below, choosing CRITICAL or HIGH severity yourself based on
operational impact (some of the most important ones may warrant both an early HIGH warning and a
CRITICAL rule -- use your judgment, this is not required for every metric):
{lignes_decouvertes}

SPECIAL STATE-BASED RULES (not simple numeric thresholds -- always include these 3 regardless of the discovered list above):
- vm.status            CRITICAL = stopped  VM unexpected shutdown
- cluster.quorum       CRITICAL = lost     quorum lost
- ai.score             CRITICAL            AI anomaly score

APPLICATION SERVICES DETECTED THIS CYCLE (real-time, via Prometheus process detection):
{services_ctx}

ADDITIONAL RULE PER SERVICE WITH MEASURABLE METRICS: for each service listed above that has
"measurable metrics" (skip any listed as having none -- those are already covered by the discovered
vm.cpu_pct/vm.ram_pct/vm.disk_pct rules above), generate ONE additional rule using its single most
operationally important metric (e.g. for a database: connection pool saturation or cache hit ratio;
for a queue: backlog size; for a cache: eviction/hit rate). Use metric format
"service.<lowercase_service_name>.<metric_name>" (e.g. "service.postgresql.connections_pct"), cible =
the VM name hosting it, and reason the threshold from established best practices for that type of
service -- not from the single snapshot value shown above. Add these AFTER the rules above, keeping
the same JSON structure.

For each rule, respond ONLY with this JSON array (no text, no markdown):

[
  {{
    "id": "rule_001",
    "metric": "server.cpu_pct",
    "operateur": ">",
    "seuil": 80,
    "duree_min": 5,
    "severite": "CRITIQUE",
    "cible": "{node_names}",
    "titre": "Hypervisor CPU Critical",
    "description": "CPU usage above this threshold sustained for 5 minutes causes VM scheduling contention and increased latency for all running VMs on this node.",
    "action": "pvesh get /nodes/pve1/status | grep cpu\\nqm list"
  }}
]

STRICT RULES:
- seuil must respect the floors in MINIMUM PROFESSIONAL FLOORS above -- never lower
- Do NOT use observed metric values from CLUSTER above as thresholds (e.g. do NOT use 1.5 for CPU just because that's the current reading)
- Write descriptions in English, professional tone, explain the operational impact -- ground them in the research above where it applies to this metric
- action field: real Proxmox/Linux commands separated by \\n
- Do NOT include a "source" field -- it will be added automatically"""

    loop = asyncio.get_event_loop()
    reponse = await loop.run_in_executor(
        None,
        lambda: appeler_groq(
            "You are a Proxmox VE expert. Respond ONLY with a valid JSON array. No markdown, no explanation.",
            [],
            prompt,
            # ← CORRIGÉ (panne réelle constatée : "[AI Rules] Generation
            # echouee (aucun JSON trouve dans la reponse LLM)") : 2800 ->
            # 5000. Cette valeur datait de l'époque où le prompt demandait
            # 22 règles d'une liste figée. Depuis le passage à la
            # découverte dynamique, il en demande ~43 sur cette
            # infrastructure (36 métriques découvertes + 3 règles d'état +
            # une par service détecté) -- et j'ai augmenté le nombre de
            # règles sans jamais toucher à cette limite de sortie.
            # Mesuré : une règle JSON de ce projet pèse ~82 tokens, donc
            # 43 règles réclament ~3500 tokens. La réponse était coupée en
            # plein JSON, d'où l'échec de parsing. 5000 laisse une marge
            # confortable si l'infrastructure grandit encore (nouvelles
            # VMs, nouveaux services) sans repasser par ici.
            max_tokens=REGLES_MAX_TOKENS,
            # format_json=True : contraint un fournisseur de secours à
            # produire du JSON valide (voir groq_client.py). Cet appel
            # attend strictement un tableau JSON -- sans effet sur Groq.
            format_json=True,
        ),
    )

    def _marquer_echec(raison: str):
        global _dernier_echec_generation_regles
        _dernier_echec_generation_regles = time.time()
        print(f"[AI Rules] Generation echouee ({raison}) -- nouvelle tentative dans au moins "
              f"{INTERVALLE_MIN_ENTRE_TENTATIVES_ECHOUEES_S // 60} min, pas au prochain cycle")

    # ← AJOUT : même filet de sécurité que parser_reponse_llm() dans
    # incident_prompt.py -- voir ce fichier pour le détail complet du
    # raisonnement. Ce prompt utilise déjà max_tokens=2800 (le plus gros
    # du système, voir commentaire plus haut) -- une trace de
    # raisonnement non filtrée y a d'autant plus de place pour se glisser
    # avant la troncature.
    #
    # ← AJOUT (2e cas) : balise <think> ouverte mais jamais refermée
    # (max_tokens a coupé en plein raisonnement) -- voir incident_prompt.py
    # pour le détail complet. Marqué comme échec explicite ici aussi,
    # avant toute tentative de retrait ou de parsing.
    if '<think>' in reponse and '</think>' not in reponse:
        _marquer_echec("reponse coupee en plein raisonnement (balise <think> non refermee)")
        return _regles_ia
    reponse = re.sub(r'<think>.*?</think>', '', reponse, flags=re.DOTALL).strip()

    try:
        json_match = re.search(r'\[.*?\]', reponse, re.DOTALL)
        if not json_match:
            _marquer_echec("aucun JSON trouve dans la reponse LLM")
            return _regles_ia

        regles = json.loads(json_match.group())
        if not (isinstance(regles, list) and len(regles) > 0):
            _marquer_echec("JSON parse mais vide ou de mauvais type")
            return _regles_ia

        # ← MODIFIÉ : "source" écrasé en Python, honnête sur si une
        # vraie recherche a alimenté cette génération -- jamais
        # laissé au LLM de l'affirmer sans condition.
        source_finale = (
            "Proxmox VE community & official sources (real-time research)"
            if recherche_ok else
            "Professional baseline floor (no live documentation reachable this cycle)"
        )
        regles_validees = []
        for r in regles:
            r["severite"] = _normaliser_severite(r.get("severite",""))
            r["seuil"]    = _valider_seuil(r.get("metric",""), r.get("seuil"), r.get("severite",""))
            r["source"]   = source_finale
            regles_validees.append(r)
        _regles_ia = regles_validees
        _derniere_generation_regles = time.time()
        print(f"[AI Rules] {len(regles_validees)} regles generees et validees (recherche: {'ok' if recherche_ok else 'indisponible'})")
        try:
            from database import sauvegarder_regles
            sauvegarder_regles(regles_validees)
        except Exception:
            pass
        return regles_validees

    except Exception as e:
        print(f"[AI Rules] Erreur parsing JSON: {e}")
        print(f"[AI Rules] Reponse brute: {reponse[:300]}")
        _marquer_echec(f"exception: {e}")
        return _regles_ia


def get_regles_ia() -> list:
    return _regles_ia


# ══════════════════════════════════════════════════════════════════════════════
# Règles de service -- rend VISIBLES des seuils qui existaient déjà et
# étaient déjà réellement appliqués (vm_app_monitor.py:
# SEUILS_METRIQUES_SERVICE, CHAMPS_BOOLEENS_CRITIQUES, appelés depuis
# generer_alertes_services() à chaque cycle de surveillance.py) mais
# n'apparaissaient nulle part sur la page Monitoring Rules -- qui ne
# montrait que les 22 règles nœud/VM. Ce n'est PAS une nouvelle détection,
# juste la même détection existante, enfin affichée. Import local (pas en
# haut du fichier) pour éviter tout risque d'import circulaire entre
# agent/rules_engine.py et vm_app_monitor.py (racine du projet).
# ══════════════════════════════════════════════════════════════════════════════
def regles_services_actives(etat: dict) -> list:
    """
    Construit, sur le même format que les 22 règles nœud/VM (mêmes clés :
    id, metric, operateur, seuil, duree_min, severite, cible, titre,
    description, action, source), la liste des seuils de service
    RÉELLEMENT actifs pour les services RÉELLEMENT détectés dans cet état
    -- jamais une liste figée : un service absent du cluster n'apparaît
    pas ici, un nouveau service détecté apparaît automatiquement au
    prochain cycle, sans toucher au code.
    """
    try:
        from vm_app_monitor import SEUILS_METRIQUES_SERVICE, CHAMPS_BOOLEENS_CRITIQUES, CATALOGUE_SERVICES
    except Exception:
        return []

    services_detectes = set()
    for v in (etat or {}).get("vms", []):
        services_detectes.update(v.get("services_detectes", []))
    if not services_detectes:
        return []

    label_vers_cle = {v: k for k, v in CATALOGUE_SERVICES.items()}
    label_vers_cle.update({"Prometheus": "prometheus", "Alertmanager": "alertmanager"})
    cle_vers_label = {v: k for k, v in label_vers_cle.items()}

    # ← CORRIGÉ (bug silencieux : AUCUNE règle de service n'a jamais été
    # générée depuis l'ajout de cette fonction) : les clés de
    # SEUILS_METRIQUES_SERVICE sont en minuscules ('postgresql', 'docker'),
    # alors que services_detectes contient les noms d'affichage tels que
    # produits par la détection ('PostgreSQL', 'Docker'). Le test
    # `if svc not in services_detectes` ne correspondait donc JAMAIS, et
    # la fonction retournait systématiquement une liste vide -- sans
    # aucune erreur, d'où le fait que ça n'ait pas été vu. Vérifié en
    # réel avec les 4 services de ce cluster : 0 règle générée avant ce
    # correctif, 8 après. On normalise en minuscules des deux côtés pour
    # comparer, plutôt que de dépendre d'une correspondance exacte de
    # casse entre deux sources différentes.
    services_normalises = {str(s).lower() for s in services_detectes}

    regles = []
    for (svc, champ), (warn, crit, inverse) in SEUILS_METRIQUES_SERVICE.items():
        if str(svc).lower() not in services_normalises:
            continue
        nom_affiche = cle_vers_label.get(svc, svc.capitalize())
        unite = "GB" if champ.endswith("_gb") else ("%" if champ.endswith("_pct") else "/s")
        regles.append({
            "id": f"svc_{svc}_{champ}", "metric": f"service.{svc}.{champ}",
            "operateur": "<" if inverse else ">", "seuil": crit,
            "duree_min": 0, "severite": "CRITIQUE", "cible": svc,
            "titre": f"{nom_affiche} {champ.replace('_', ' ')}",
            "description": f"{'Below' if inverse else 'Above'} {crit}{unite} indicates a real operational problem for {nom_affiche} -- see vm_app_monitor.py SEUILS_METRIQUES_SERVICE for the reasoning.",
            "action": f"# voir vm_app_monitor.py -- seuil déjà appliqué en direct, pas une suggestion",
            "source": "Existing service threshold (vm_app_monitor.py) -- surfaced here, not newly invented",
        })

    for (svc, champ), description in CHAMPS_BOOLEENS_CRITIQUES.items():
        if str(svc).lower() not in services_normalises:
            continue
        nom_affiche = cle_vers_label.get(svc, svc.capitalize())
        regles.append({
            "id": f"svc_{svc}_{champ}", "metric": f"service.{svc}.{champ}",
            "operateur": "==", "seuil": "false",
            "duree_min": 0, "severite": "CRITIQUE", "cible": svc,
            "titre": f"{nom_affiche} {champ.replace('_', ' ')}",
            "description": description,
            "action": f"# voir vm_app_monitor.py -- seuil déjà appliqué en direct, pas une suggestion",
            "source": "Existing service threshold (vm_app_monitor.py) -- surfaced here, not newly invented",
        })

    return regles


def regles_necessitent_regeneration() -> bool:
    maintenant = time.time()
    if maintenant - _derniere_generation_regles <= INTERVALLE_REGENERATION_REGLES:
        return False
    # ← AJOUT : le délai normal (24h) est dépassé, mais si la DERNIÈRE
    # tentative a échoué récemment (rate limit Groq, JSON invalide...),
    # attendre au moins INTERVALLE_MIN_ENTRE_TENTATIVES_ECHOUEES_S avant
    # de retenter -- pas au prochain cycle de surveillance (60s), qui
    # aggraverait une situation de rate-limit déjà en cours.
    return maintenant - _dernier_echec_generation_regles > INTERVALLE_MIN_ENTRE_TENTATIVES_ECHOUEES_S


def get_derniere_generation() -> float:
    return _derniere_generation_regles