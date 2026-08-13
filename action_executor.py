"""
action_executor.py — Exécution des actions de remédiation sur Proxmox VE.

Pattern : Human-in-the-Loop Automation
L'utilisateur lit la recommandation → accepte ou rejette → l'agent exécute.

Actions disponibles :
  - enable_ksm       : Activer KSM deduplication (sûr, zero downtime)
  - enable_balloon   : Activer ballooning sur une VM (sûr, zero downtime)
  - migrate_vm       : Live-migrer une VM vers un autre nœud (zero downtime)
  - set_cpu_limit    : Limiter CPU d'une VM non-critique (réversible)
  - clean_logs       : Nettoyer les logs système (sûr)

Chaque action est :
  - Réversible : peut être annulée
  - Documentée : description claire avant exécution
  - Tracée : chaque exécution est loggée

← AJOUT (persistance) : l'historique des actions exécutées est maintenant
sauvegardé en base (database.sauvegarder_action(), nouvelle table
action_history) en plus de _action_history (liste Python en mémoire,
gardée comme accès rapide). Avant cet ajout, un redémarrage de l'agent
effaçait toute trace de "quelle action a été exécutée, quand, avec quel
résultat" -- pour un outil comparé à PagerDuty/Datadog, cette piste
d'audit devrait survivre à un redémarrage, comme les anomalies/rapports/
chat le font déjà. get_action_history() lit maintenant la base en
priorité (si disponible), retombe sur la liste mémoire de cette session
sinon -- même schéma que get_regles_ia()/get_anomalies() ailleurs dans
ce projet.
"""

import os
import requests
import urllib3
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HOST         = os.getenv("PROXMOX_HOST", "192.168.138.100")
TOKEN_ID     = os.getenv("PROXMOX_TOKEN_ID", "root@pam!opspilot")
TOKEN_SECRET = os.getenv("PROXMOX_TOKEN_SECRET", "")
VERIFY_SSL   = os.getenv("PROXMOX_VERIFY_SSL", "false").lower() == "true"
HEADERS      = {"Authorization": f"PVEAPIToken={TOKEN_ID}={TOKEN_SECRET}"}

# Log des actions exécutées (en mémoire) -- reste comme accès rapide et
# repli si la base est indisponible, voir _log_action() et
# get_action_history() plus bas pour la persistance en base.
_action_history: list = []


# ══════════════════════════════════════════════════════════════════════════════
# Exécution des commandes sur les nœuds Proxmox
# ══════════════════════════════════════════════════════════════════════════════

def _api_post(path: str, data: dict = None) -> dict:
    """POST vers l'API Proxmox."""
    url = f"https://{HOST}:8006/api2/json{path}"
    try:
        r = requests.post(url, headers=HEADERS, json=data or {},
                          verify=VERIFY_SSL, timeout=30)
        r.raise_for_status()
        return {"ok": True, "data": r.json().get("data")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _api_put(path: str, data: dict) -> dict:
    """PUT vers l'API Proxmox."""
    url = f"https://{HOST}:8006/api2/json{path}"
    try:
        r = requests.put(url, headers=HEADERS, json=data,
                         verify=VERIFY_SSL, timeout=30)
        r.raise_for_status()
        return {"ok": True, "data": r.json().get("data")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _log_action(action_id: str, params: dict, result: dict):
    """
    Enregistrer l'action dans l'historique -- en mémoire (accès rapide,
    session courante) ET en base (persiste après un redémarrage de
    l'agent, voir docstring du fichier).
    """
    message = result.get("message", result.get("error", ""))
    success = result.get("ok", False)

    _action_history.append({
        "timestamp": datetime.now().isoformat(),
        "action":    action_id,
        "params":    params,
        "success":   success,
        "message":   message,
    })
    print(f"[Action] {action_id} → {'✓ OK' if success else '✗ FAIL'}: {message}")

    # ← AJOUT : persistance en base, best-effort -- une erreur ici ne doit
    # jamais faire échouer l'action elle-même (déjà exécutée à ce stade),
    # seulement manquer sa trace persistante pour ce cas précis.
    try:
        from database import sauvegarder_action
        sauvegarder_action(action_id, params, success, message)
    except Exception as e:
        print(f"[Action] Persistance historique échouée (non bloquant): {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Actions disponibles
# ══════════════════════════════════════════════════════════════════════════════

def enable_ksm(node: str) -> dict:
    """
    Activer KSM (Kernel Samepage Merging) sur un nœud.
    KSM partage les pages mémoire identiques entre les VMs → libère RAM.

    Sûr : zero downtime, réversible, recommandé par Proxmox VE.
    Réversible : echo 0 > /sys/kernel/mm/ksm/run
    """
    result = _api_post(f"/nodes/{node}/execute",
                       {"command": "echo 1 > /sys/kernel/mm/ksm/run"})
    if result["ok"]:
        result["message"] = f"✓ KSM activé sur {node} — partage de pages mémoire en cours"
    _log_action("enable_ksm", {"node": node}, result)
    return result


def enable_vm_balloon(node: str, vmid: int, min_mb: int = 512) -> dict:
    """
    Activer le ballooning sur une VM.
    Permet à la VM de rendre la RAM inutilisée à l'hyperviseur.

    Sûr : zero downtime, la VM garde sa RAM si elle en a besoin.
    Réversible : qm set {vmid} --balloon 0 (désactive)
    """
    result = _api_put(f"/nodes/{node}/qemu/{vmid}/config",
                      {"balloon": min_mb})
    if result["ok"]:
        result["message"] = (f"✓ Ballooning activé sur VM {vmid} "
                             f"(minimum {min_mb} MB) — la VM peut rendre de la RAM")
    _log_action("enable_balloon", {"node": node, "vmid": vmid, "min_mb": min_mb}, result)
    return result


def migrate_vm_live(node: str, vmid: int, target_node: str) -> dict:
    """
    Live-migrer une VM vers un autre nœud (zero downtime).
    Libère des ressources sur le nœud source.

    Sûr : la VM continue de fonctionner pendant la migration.
    Réversible : migrer à nouveau vers le nœud original si besoin.

    Prérequis : stockage partagé ou cluster avec live migration activée.
    """
    result = _api_post(f"/nodes/{node}/qemu/{vmid}/migrate",
                       {"target": target_node, "online": 1})
    if result["ok"]:
        result["message"] = (f"✓ Migration live de VM {vmid} de {node} "
                             f"vers {target_node} démarrée — zero downtime")
    _log_action("migrate_vm", {"node": node, "vmid": vmid, "target": target_node}, result)
    return result


def set_cpu_limit(node: str, vmid: int, limit: float = 1.0) -> dict:
    """
    Limiter l'utilisation CPU d'une VM non-critique.
    1.0 = max 1 core, 0.5 = max 50% d'un core.

    ATTENTION : Utiliser uniquement sur des VMs non-critiques (dev, test).
    Réversible : qm set {vmid} --cpulimit 0 (retire la limite)
    """
    result = _api_put(f"/nodes/{node}/qemu/{vmid}/config",
                      {"cpulimit": limit})
    if result["ok"]:
        result["message"] = (f"✓ CPU limité à {limit} core(s) pour VM {vmid} "
                             f"— libère CPU pour les autres VMs")
    _log_action("set_cpu_limit", {"node": node, "vmid": vmid, "limit": limit}, result)
    return result


def clean_node_logs(node: str) -> dict:
    """
    Nettoyer les logs anciens sur un nœud pour libérer de l'espace disque.
    Supprime les logs journald > 500MB et les anciennes archives apt.

    Sûr : ne supprime pas les données critiques.
    """
    commands = [
        "journalctl --vacuum-size=200M",
        "apt-get clean -y",
        "find /var/log -name '*.gz' -mtime +30 -delete",
    ]
    results = []
    for cmd in commands:
        r = _api_post(f"/nodes/{node}/execute", {"command": cmd})
        results.append(r.get("ok", False))

    ok = all(results)
    result = {
        "ok": ok,
        "message": (f"✓ Logs nettoyés sur {node} — espace disque libéré"
                    if ok else f"⚠ Nettoyage partiel sur {node}")
    }
    _log_action("clean_logs", {"node": node}, result)
    return result


def disable_ksm(node: str) -> dict:
    """Désactiver KSM (action inverse de enable_ksm)."""
    result = _api_post(f"/nodes/{node}/execute",
                       {"command": "echo 0 > /sys/kernel/mm/ksm/run"})
    if result["ok"]:
        result["message"] = f"✓ KSM désactivé sur {node}"
    _log_action("disable_ksm", {"node": node}, result)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Dispatcher principal — appelé par routes.py
# ══════════════════════════════════════════════════════════════════════════════

ACTION_REGISTRY = {
    "enable_ksm":       enable_ksm,
    "enable_balloon":   enable_vm_balloon,
    "migrate_vm":       migrate_vm_live,
    "set_cpu_limit":    set_cpu_limit,
    "clean_logs":       clean_node_logs,
    "disable_ksm":      disable_ksm,
}

# Métadonnées pour l'UI (boutons Accept/Reject) -- aussi lu par
# incident_prompt._construire_menu_actions() pour le menu d'actions sûres
# proposé au LLM (voir incident_prompt.py).
ACTION_META = {
    "enable_ksm": {
        "label":       "Enable KSM deduplication",
        "description": "Activates memory page sharing between VMs — frees RAM with zero downtime",
        "risk":        "low",
        "reversible":  True,
        "params":      ["node"],
    },
    "enable_balloon": {
        "label":       "Enable VM ballooning",
        "description": "Allows idle VMs to return unused RAM to the hypervisor",
        "risk":        "low",
        "reversible":  True,
        "params":      ["node", "vmid", "min_mb"],
    },
    "migrate_vm": {
        "label":       "Live migrate VM",
        "description": "Moves a VM to another node with zero downtime — requires shared storage",
        "risk":        "medium",
        "reversible":  True,
        "params":      ["node", "vmid", "target_node"],
    },
    "set_cpu_limit": {
        "label":       "Set VM CPU limit",
        "description": "Limits CPU usage of a non-critical VM to free resources",
        "risk":        "medium",
        "reversible":  True,
        "params":      ["node", "vmid", "limit"],
        "warning":     "Only use on dev/test VMs — impacts application performance",
    },
    "clean_logs": {
        "label":       "Clean system logs",
        "description": "Removes old logs and apt cache to free disk space",
        "risk":        "low",
        "reversible":  False,
        "params":      ["node"],
    },
}


def executer_action(action_id: str, params: dict) -> dict:
    """
    Point d'entrée principal — dispatcher vers la bonne fonction.

    Args:
        action_id: Identifiant de l'action (ex: "enable_ksm")
        params:    Paramètres de l'action (ex: {"node": "pve1"})

    Returns:
        {"ok": True/False, "message": "...", "action_id": "..."}
    """
    if action_id not in ACTION_REGISTRY:
        return {
            "ok":       False,
            "error":    f"Action inconnue: {action_id}",
            "action_id": action_id,
        }

    fn = ACTION_REGISTRY[action_id]
    try:
        result = fn(**params)
        result["action_id"] = action_id
        return result
    except TypeError as e:
        return {
            "ok":       False,
            "error":    f"Paramètres manquants: {e}",
            "action_id": action_id,
        }
    except Exception as e:
        return {
            "ok":       False,
            "error":    f"Erreur exécution: {e}",
            "action_id": action_id,
        }


def get_action_history() -> list:
    """
    Retourner l'historique des actions exécutées.

    ← MODIFIÉ : lit maintenant la base en priorité (si disponible) --
    persiste après un redémarrage de l'agent, contrairement à
    _action_history seule. Retombe sur la liste en mémoire de cette
    session si la base est indisponible -- même principe que
    get_regles_ia()/get_anomalies() ailleurs dans ce projet.
    """
    try:
        from database import get_action_history_db, DB_OK
        if DB_OK:
            return get_action_history_db()
    except Exception:
        pass
    return list(reversed(_action_history))