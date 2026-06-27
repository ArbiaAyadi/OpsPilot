
import asyncio
import json
import re
import time
from agent.groq_client import appeler_groq, rate_limiter
from agent.config import INTERVALLE_REGENERATION_REGLES

_regles_ia:                  list  = []
_derniere_generation_regles: float = 0.0

# Seuils plancher professionnels — le LLM ne peut jamais descendre en dessous.
# Ceci evite le bug "server.cpu_pct > 1.5" ou le LLM adapte a la baseline
# observee (CPU a 1.5%) au lieu des seuils Proxmox officiels.
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
    Valide que le seuil genere par le LLM est >= au minimum professionnel.
    Retourne le seuil corrige si trop bas, le seuil original sinon.
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


PROXMOX_DOCS_CONTEXT = """
## OFFICIAL PROXMOX VE THRESHOLDS — MANDATORY, DO NOT LOWER

### Hypervisor Nodes (pve1, pve2)
CPU Usage:      CRITICAL > 80%  | HIGH > 65%   (contention between VMs, scheduler impact)
RAM Usage:      CRITICAL > 85%  | HIGH > 75%   (OOM killer risk, excessive swap)
Disk Usage:     CRITICAL > 90%  | HIGH > 80%   (LVM-thin writes fail, VM corruption)
Swap Usage:     CRITICAL > 80%  | HIGH > 50%   (RAM saturated, disk used as memory)
CPU I/O Wait:   CRITICAL > 30%  | HIGH > 15%   (CPU blocked by storage I/O)
Disk Latency:   CRITICAL > 50ms | HIGH > 10ms  (storage degradation or failure)
CPU Temperature:CRITICAL > 85C  | HIGH > 75C   (thermal throttling engaged)
Net Errors:     CRITICAL > 20/s | HIGH > 5/s   (NIC or cable failure)
Load Average:   CRITICAL > 8    | HIGH > 4     (system overloaded, queue buildup)
File Desc:      CRITICAL > 90%  | HIGH > 70%   (too-many-open-files risk)

### Virtual Machines
VM CPU:         CRITICAL > 90%  | HIGH > 75%   (vCPU saturation, guest latency)
VM RAM:         CRITICAL > 90%  | HIGH > 80%   (guest swap, application slowdown)
VM Disk:        CRITICAL > 90%  | HIGH > 80%   (guest filesystem full)
VM Down:        CRITICAL = stopped unexpectedly (HA event, OOM kill, hardware fault)

### Cluster Health
Quorum:         CRITICAL = lost (VMs shut down automatically to prevent split-brain)
ZFS ARC hit:    HIGH < 70%      (I/O going to disk, add RAM to improve cache)
AI Score:       CRITICAL > 0.8  | HIGH > 0.5   (statistical anomaly confirmed)

IMPORTANT: These thresholds are the MINIMUM professional standards.
You MUST use these exact values or higher — NEVER lower them based on observed metrics.
The observed metrics (e.g. CPU at 1.5%) describe the current load, NOT the alert threshold.
"""


async def generer_regles_ia(etat: dict) -> list:
    """
    Genere 15 regles d'alerte professionnelles basees sur la doc Proxmox officielle.
    Le LLM adapte uniquement les descriptions et commandes au cluster specifique,
    pas les seuils (ceux-ci sont valides par _valider_seuil apres generation).
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

    prompt = f"""You are a senior Proxmox VE infrastructure engineer.
Generate exactly 15 professional alert rules for this specific cluster.

CLUSTER:
{noeuds_ctx}
VMs: {vms_ctx if vms_ctx else "None"}
Node names: {node_names}

{PROXMOX_DOCS_CONTEXT}

RULES TO GENERATE (one per metric, in this order):
1.  server.cpu_pct       CRITICAL > 80    hypervisor CPU
2.  server.ram_pct       CRITICAL > 85    hypervisor RAM
3.  server.disk_pct      CRITICAL > 90    hypervisor disk
4.  server.swap_pct      CRITICAL > 80    hypervisor swap
5.  server.io_wait_pct   CRITICAL > 30    CPU I/O wait
6.  server.latency       CRITICAL > 50    disk latency (ms)
7.  server.temperature   CRITICAL > 85    CPU temperature (C)
8.  server.cpu_pct       HIGH     > 65    early CPU warning
9.  server.ram_pct       HIGH     > 75    early RAM warning
10. server.net_errors    HIGH     > 5     network interface errors/s
11. vm.status            CRITICAL = stopped  VM unexpected shutdown
12. vm.cpu_pct           HIGH     > 75    VM CPU high
13. vm.ram_pct           HIGH     > 80    VM RAM high
14. cluster.quorum       CRITICAL = lost  quorum lost
15. ai.score             CRITICAL > 0.8   AI anomaly score

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
    "description": "CPU usage above 80% sustained for 5 minutes causes VM scheduling contention and increased latency for all running VMs on this node.",
    "action": "pvesh get /nodes/pve1/status | grep cpu\\nqm list",
    "source": "Proxmox VE official documentation"
  }}
]

STRICT RULES:
- seuil values MUST match the thresholds listed above EXACTLY (80, 85, 90, etc.)
- Do NOT use observed metric values as thresholds (e.g. do NOT use 1.5 for CPU)
- Write descriptions in English, professional tone, explain the operational impact
- action field: real Proxmox/Linux commands separated by \\n"""

    loop = asyncio.get_event_loop()
    reponse = await loop.run_in_executor(
        None,
        lambda: appeler_groq(
            "You are a Proxmox VE expert. Respond ONLY with a valid JSON array. No markdown, no explanation.",
            [],
            prompt,
            max_tokens=2500,
        ),
    )

    try:
        json_match = re.search(r'\[.*?\]', reponse, re.DOTALL)
        if json_match:
            regles = json.loads(json_match.group())
            if isinstance(regles, list) and len(regles) > 0:
                # Valider et corriger les seuils trop bas
                regles_validees = []
                for r in regles:
                    r["severite"] = _normaliser_severite(r.get("severite",""))
                    r["seuil"]    = _valider_seuil(r.get("metric",""), r.get("seuil"), r.get("severite",""))
                    regles_validees.append(r)
                _regles_ia = regles_validees
                _derniere_generation_regles = time.time()
                print(f"[AI Rules] {len(regles_validees)} regles generees et validees")
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