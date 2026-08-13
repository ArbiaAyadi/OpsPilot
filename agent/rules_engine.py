import asyncio
import json
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
    "server.cpu_pct":           {"CRITIQUE": 80,   "IMPORTANT": 65},
    "server.ram_pct":           {"CRITIQUE": 85,   "IMPORTANT": 75},
    "server.disk_pct":          {"CRITIQUE": 90,   "IMPORTANT": 80},
    "server.swap_pct":          {"CRITIQUE": 80,   "IMPORTANT": 50},
    "server.io_wait_pct":       {"CRITIQUE": 30,   "IMPORTANT": 15},
    "server.latency":           {"CRITIQUE": 50,   "IMPORTANT": 10},
    "server.temperature":       {"CRITIQUE": 85,   "IMPORTANT": 75},
    "server.net_errors":        {"CRITIQUE": 20,   "IMPORTANT": 5},
    "server.load_avg":          {"CRITIQUE": 8,    "IMPORTANT": 4},
    "server.fd_used_pct":       {"CRITIQUE": 90,   "IMPORTANT": 70},
    "vm.cpu_pct":               {"CRITIQUE": 90,   "IMPORTANT": 75},
    "vm.ram_pct":               {"CRITIQUE": 90,   "IMPORTANT": 80},
    "vm.disk_pct":              {"CRITIQUE": 90,   "IMPORTANT": 80},
    "vm.status":                {},  # booleen, pas de plancher numerique
    "cluster.quorum":           {},
    "ai.score":                 {"CRITIQUE": 0.8,  "IMPORTANT": 0.5},
    "zfs.arc_hit_rate":         {},  # inverse : plancher = minimum acceptable
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
_CATEGORIES_RECHERCHE = ["cpu", "ram", "disk", "swap", "iowait", "temperature", "network", "quorum", "vm"]


async def _rassembler_recherche_doc() -> tuple:
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

    Retourne (texte_assemblé, recherche_reussie) -- recherche_reussie sert
    ensuite à écrire un champ "source" honnête sur les règles générées,
    plutôt que le LLM affirmant "official documentation" sans condition.
    """
    if not WEB_SEARCH_OK:
        return "", False

    loop = asyncio.get_event_loop()

    async def _rechercher_une_categorie(categorie: str):
        try:
            doc = await loop.run_in_executor(None, rechercher_doc_proxmox, categorie)
            return categorie, doc
        except Exception as e:
            print(f"[AI Rules] Recherche '{categorie}' echouee: {e}")
            return categorie, None

    resultats = await asyncio.gather(*[_rechercher_une_categorie(c) for c in _CATEGORIES_RECHERCHE])

    morceaux = []
    au_moins_un_succes = False
    for categorie, doc in resultats:
        if doc:
            morceaux.append(f"[{categorie.upper()}]\n{doc[:600]}")
            au_moins_un_succes = True

    if au_moins_un_succes:
        print(f"[AI Rules] Recherche documentaire: {len(morceaux)}/{len(_CATEGORIES_RECHERCHE)} categories trouvees")
    else:
        print("[AI Rules] Aucune recherche documentaire disponible -- repli sur les planchers seuls")

    return "\n\n---\n\n".join(morceaux), au_moins_un_succes


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

### Virtual Machines
VM CPU: floor HIGH >= 75%
VM RAM: floor HIGH >= 80%

### Cluster
Quorum:   CRITICAL = lost (not negotiable, no floor concept applies)
AI Score: floor CRITICAL >= 0.8 | floor HIGH >= 0.5

These floors will be enforced programmatically after you respond, regardless of what you write here --
but use the research above to propose real, reasoned values at or above them, not just the floor number
copied verbatim for every single rule.
"""


async def generer_regles_ia(etat: dict) -> list:
    """
    Genere 15 regles d'alerte professionnelles.

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
    global _regles_ia, _derniere_generation_regles

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

    doc_recherche, recherche_ok = await _rassembler_recherche_doc()
    doc_bloc = (
        f"## REAL-TIME RESEARCH (fetched now from pve.proxmox.com / forum.proxmox.com)\n{doc_recherche}\n"
        if doc_recherche else
        "## REAL-TIME RESEARCH\n(unavailable this cycle -- rely on the professional floors below only)\n"
    )

    prompt = f"""You are a senior Proxmox VE infrastructure engineer.
Generate exactly 15 professional alert rules for this specific cluster, reasoning from the real
research and the safety floors below -- not a fixed template to copy.

CLUSTER:
{noeuds_ctx}
VMs: {vms_ctx if vms_ctx else "None"}
Node names: {node_names}

{doc_bloc}
{GARDE_FOUS_SEUILS}

RULES TO GENERATE (one per metric, in this exact order -- the STRUCTURE is fixed, the exact threshold
value and description are yours to reason out from the research and floors above):
1.  server.cpu_pct       CRITICAL   hypervisor CPU
2.  server.ram_pct       CRITICAL   hypervisor RAM
3.  server.disk_pct      CRITICAL   hypervisor disk
4.  server.swap_pct      CRITICAL   hypervisor swap
5.  server.io_wait_pct   CRITICAL   CPU I/O wait
6.  server.latency       CRITICAL   disk latency (ms)
7.  server.temperature   CRITICAL   CPU temperature (C)
8.  server.cpu_pct       HIGH       early CPU warning
9.  server.ram_pct       HIGH       early RAM warning
10. server.net_errors    HIGH       network interface errors/s
11. vm.status            CRITICAL = stopped  VM unexpected shutdown
12. vm.cpu_pct           HIGH       VM CPU high
13. vm.ram_pct           HIGH       VM RAM high
14. cluster.quorum       CRITICAL = lost  quorum lost
15. ai.score             CRITICAL   AI anomaly score

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
            max_tokens=2800,
        ),
    )

    try:
        json_match = re.search(r'\[.*?\]', reponse, re.DOTALL)
        if json_match:
            regles = json.loads(json_match.group())
            if isinstance(regles, list) and len(regles) > 0:
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

    return _regles_ia


def get_regles_ia() -> list:
    return _regles_ia


def regles_necessitent_regeneration() -> bool:
    return time.time() - _derniere_generation_regles > INTERVALLE_REGENERATION_REGLES


def get_derniere_generation() -> float:
    return _derniere_generation_regles