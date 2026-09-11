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

← AJOUT (voie SSH) : DEUX VOIES D'EXÉCUTION coexistent désormais.
  - API Proxmox (jeton) : enable_balloon, migrate_vm, set_cpu_limit
  - SSH                 : enable_ksm, disable_ksm, clean_logs
Raison : l'endpoint /nodes/{node}/execute de Proxmox renvoie
systématiquement "403 Permission check failed (user != root@pam)" avec
un jeton API, quels que soient ses privilèges. Ce n'est pas un réglage de
permission mais une contrainte de conception de Proxmox, destinée à
empêcher l'exécution de commandes shell arbitraires via un jeton qui
pourrait fuiter. Les actions qui ont besoin d'un shell passent donc par
SSH ; celles qui manipulent la configuration d'une VM (/config,
/migrate) continuent d'utiliser le jeton, qui y a bien accès.
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
# Exécution par SSH — pour les actions qui ont besoin d'un vrai shell
# ══════════════════════════════════════════════════════════════════════════════
# ← AJOUT (erreur réelle constatée) : enable_ksm et clean_node_logs
# passaient par _api_post("/nodes/{node}/execute") et échouaient
# systématiquement avec :
#     403 Permission check failed (user != root@pam)
#
# Voir la docstring du fichier pour le raisonnement complet. En résumé :
# aucun jeton API ne peut utiliser /execute, c'est voulu par Proxmox.
#
# SÉCURITÉ : les commandes exécutées par cette voie sont écrites EN DUR
# dans les fonctions d'action de ce fichier. Aucune valeur fournie par
# l'utilisateur ou produite par le LLM n'est jamais interpolée dans une
# commande shell -- c'est la propriété qui rend ce chemin sûr, et elle
# doit être préservée si de nouvelles actions sont ajoutées.
try:
    import paramiko
    SSH_OK = True
except ImportError:
    SSH_OK = False
    print("[Action] paramiko non installe -- actions SSH indisponibles")
    print("[Action] Installe : pip install paramiko")

SSH_USER     = os.getenv("SSH_USER", "root")
SSH_PASSWORD = os.getenv("SSH_PASSWORD", "")
SSH_TIMEOUT  = int(os.getenv("SSH_TIMEOUT_S", "20"))


def _ip_du_noeud(node: str) -> str:
    """Résout l'IP d'une cible depuis .env. Retourne une chaîne vide si la
    cible est inconnue -- l'appelant échoue alors proprement plutôt que de
    tenter une connexion au hasard.

    ← ÉTENDU : accepte désormais aussi bien un nœud Proxmox (pve1 ->
    PVE1_IP) qu'une VM (linux-vm1 -> LINUX_VM1_IP). Nécessaire pour
    restart_exporter, dont les cibles sont les VMs qui hébergent les
    exportateurs, pas les nœuds. La normalisation (majuscules, tirets ->
    underscores) suit la convention déjà utilisée dans .env, plutôt que
    d'introduire une table de correspondance codée en dur qui devrait
    être maintenue à chaque ajout de VM."""
    cle = node.strip().upper().replace("-", "_").replace(".", "_")
    return os.getenv(f"{cle}_IP", "").strip()


def _ssh_execute(node: str, command: str) -> dict:
    """Exécute une commande shell sur un nœud Proxmox via SSH.

    Retourne le même format que _api_post/_api_put ({"ok": bool, ...})
    pour que les fonctions d'action restent interchangeables entre les
    deux voies d'exécution."""
    if not SSH_OK:
        return {"ok": False, "error": "paramiko non installe (pip install paramiko)"}

    ip = _ip_du_noeud(node)
    if not ip:
        return {"ok": False,
                "error": f"IP inconnue pour le noeud '{node}' -- ajouter {node.upper()}_IP dans .env"}
    if not SSH_PASSWORD:
        return {"ok": False, "error": "SSH_PASSWORD absent de .env"}

    client = paramiko.SSHClient()
    # AutoAddPolicy : accepte la clé d'hôte au premier contact. Acceptable
    # sur un réseau d'infrastructure privé et maîtrisé ; à remplacer par
    # un known_hosts pré-rempli pour un déploiement en production, où une
    # attaque par interception est un risque réel.
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(hostname=ip, username=SSH_USER, password=SSH_PASSWORD,
                       timeout=SSH_TIMEOUT, allow_agent=False, look_for_keys=False)
        _, stdout, stderr = client.exec_command(command, timeout=SSH_TIMEOUT)
        code   = stdout.channel.recv_exit_status()
        sortie = stdout.read().decode("utf-8", "replace").strip()
        erreur = stderr.read().decode("utf-8", "replace").strip()
        if code == 0:
            return {"ok": True, "data": sortie}
        return {"ok": False, "error": f"code {code}: {erreur or sortie or 'aucune sortie'}"}
    except Exception as e:
        return {"ok": False, "error": f"SSH {ip}: {e}"}
    finally:
        try:
            client.close()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# Actions disponibles
# ══════════════════════════════════════════════════════════════════════════════

def enable_ksm(node: str) -> dict:
    """
    Activer KSM (Kernel Samepage Merging) sur un nœud.
    KSM partage les pages mémoire identiques entre les VMs → libère RAM.
    Sûr : zero downtime, réversible, recommandé par Proxmox VE.
    Réversible : echo 0 > /sys/kernel/mm/ksm/run

    ← MODIFIÉ : passait par /nodes/{node}/execute (403 avec un jeton API).
    Passe désormais par SSH -- voir _ssh_execute() plus haut.
    """
    result = _ssh_execute(node, "echo 1 > /sys/kernel/mm/ksm/run")
    if result["ok"]:
        # ksmtuned est le démon Proxmox qui pilote KSM. L'activer rend le
        # changement persistant : sans lui, KSM repart à zéro au prochain
        # redémarrage du nœud. Son échec n'est pas bloquant -- l'activation
        # immédiate, elle, a bien eu lieu, et on le dit plutôt que de le
        # taire.
        persist   = _ssh_execute(node, "systemctl enable --now ksmtuned 2>/dev/null || true")
        partagees = _ssh_execute(node, "cat /sys/kernel/mm/ksm/pages_sharing 2>/dev/null || echo 0")
        pages     = partagees.get("data", "0") if partagees.get("ok") else "0"
        result["message"] = (f"✓ KSM activé sur {node} — partage de pages mémoire en cours "
                             f"({pages} pages déjà partagées)")
        if not persist.get("ok"):
            result["message"] += " — ksmtuned non activé, KSM ne survivra pas à un redémarrage du nœud"
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
    Supprime les logs journald > 200MB et les anciennes archives apt.
    Sûr : ne supprime pas les données critiques.

    ← MODIFIÉ : passait par /nodes/{node}/execute (403 avec un jeton API).
    Passe désormais par SSH. Les trois commandes sont enchaînées en un
    seul appel plutôt qu'en trois connexions successives -- même résultat,
    trois fois moins de latence.
    """
    commandes = " ; ".join([
        "journalctl --vacuum-size=200M",
        "apt-get clean -y",
        "find /var/log -name '*.gz' -mtime +30 -delete",
    ])
    result = _ssh_execute(node, commandes)
    if result["ok"]:
        libre  = _ssh_execute(node, "df -h / | awk 'NR==2 {print $4}'")
        espace = libre.get("data", "?") if libre.get("ok") else "?"
        result["message"] = f"✓ Logs nettoyés sur {node} — {espace} disponibles sur /"
    else:
        result["message"] = f"⚠ Nettoyage échoué sur {node} : {result.get('error', '')}"
    _log_action("clean_logs", {"node": node}, result)
    return result


def disable_ksm(node: str) -> dict:
    """
    Désactiver KSM sur un nœud — annule enable_ksm().

    ← MODIFIÉ : même correction que enable_ksm. Cette action garantit la
    réversibilité annoncée dans ACTION_META ; laissée sur /execute, cette
    réversibilité aurait été fausse -- on aurait pu activer KSM sans
    jamais pouvoir le désactiver depuis l'interface.
    """
    result = _ssh_execute(node, "echo 0 > /sys/kernel/mm/ksm/run")
    if result["ok"]:
        result["message"] = f"✓ KSM désactivé sur {node}"
    _log_action("disable_ksm", {"node": node}, result)
    return result


def set_swappiness(node: str, valeur: int = 10) -> dict:
    """
    Réduire l'agressivité du swap sur un nœud.

    vm.swappiness va de 0 à 100 : plus la valeur est basse, plus le noyau
    préfère garder les pages en RAM plutôt que de les écrire sur disque.
    La valeur par défaut de Debian/Proxmox est 60, adaptée à un serveur
    disposant de RAM confortable -- pas à un nœud sous pression, où elle
    provoque un va-et-vient disque coûteux (thrashing).

    Sûr : zero downtime, effet immédiat, aucune donnée touchée.
    Réversible : sysctl -w vm.swappiness=60 (valeur par défaut).

    ← AJOUT : le LLM proposait déjà cette commande dans ses analyses, mais
    sans action_id elle s'affichait en texte à copier. Elle vise
    directement le problème mesuré sur ce cluster (52 à 70% de swap sur
    les deux nœuds), et son effet est immédiatement observable.
    """
    if not isinstance(valeur, int) or not 0 <= valeur <= 100:
        return {"ok": False, "error": f"swappiness invalide: {valeur} (attendu 0-100)"}

    avant  = _ssh_execute(node, "cat /proc/sys/vm/swappiness")
    result = _ssh_execute(node, f"sysctl -w vm.swappiness={valeur}")
    if result["ok"]:
        # Rendre le changement persistant : sysctl -w seul est perdu au
        # redémarrage du nœud. On le dit honnêtement si l'écriture échoue,
        # plutôt que d'annoncer une correction qui ne survivra pas.
        persist = _ssh_execute(
            node,
            "grep -q '^vm.swappiness' /etc/sysctl.conf "
            f"&& sed -i 's/^vm.swappiness.*/vm.swappiness={valeur}/' /etc/sysctl.conf "
            f"|| echo 'vm.swappiness={valeur}' >> /etc/sysctl.conf"
        )
        ancienne = avant.get("data", "?") if avant.get("ok") else "?"
        result["message"] = (f"✓ Swappiness sur {node} : {ancienne} → {valeur} "
                             f"— le noyau privilégiera la RAM au disque")
        if not persist.get("ok"):
            result["message"] += " — non persisté, la valeur reviendra à 60 au redémarrage du nœud"
    _log_action("set_swappiness", {"node": node, "valeur": valeur}, result)
    return result


def drop_caches(node: str) -> dict:
    """
    Libérer le cache page du noyau sur un nœud.

    Le noyau garde en RAM les fichiers lus récemment. Ce cache est utile,
    mais sous forte pression mémoire il peut être rendu immédiatement.
    'sync' est exécuté d'abord : il écrit sur disque toutes les données en
    attente, ce qui garantit qu'aucune écriture n'est perdue.

    Sûr : aucune donnée perdue grâce à sync. Zero downtime.
    Réversible : sans objet -- le cache se reconstruit naturellement à
    l'usage. Effet temporaire par nature.

    ← AJOUT : soulagement immédiat quand un nœud est au bord de la
    saturation, le temps qu'une correction durable soit appliquée. Ne
    remplace JAMAIS l'ajout de RAM physique -- l'effet se dissipe à mesure
    que le cache se reconstruit.
    """
    avant  = _ssh_execute(node, "free -m | awk 'NR==2 {print $7}'")
    result = _ssh_execute(node, "sync && echo 1 > /proc/sys/vm/drop_caches")
    if result["ok"]:
        apres = _ssh_execute(node, "free -m | awk 'NR==2 {print $7}'")
        try:
            libere = int(apres.get("data", 0)) - int(avant.get("data", 0))
            detail = f"{libere} MB libérés" if libere > 0 else "effet marginal"
        except (ValueError, TypeError):
            detail = "cache vidé"
        result["message"] = (f"✓ Cache page libéré sur {node} — {detail} "
                             f"(effet temporaire, le cache se reconstruira)")
    _log_action("drop_caches", {"node": node}, result)
    return result


# Exportateurs autorisés au redémarrage. Liste FERMÉE délibérément : le
# nom du service est interpolé dans une commande shell, donc n'accepter
# qu'une valeur de cette liste est ce qui empêche une injection de
# commande si le LLM produisait un nom inattendu.
_EXPORTEURS_AUTORISES = {
    "node_exporter",
    "postgres_exporter",
    "process-exporter",
    "pve_exporter",
}


def restart_exporter(node: str, exporter: str) -> dict:
    """
    Redémarrer un exportateur Prometheus sur une VM ou un nœud.

    Un exportateur muet fait croire à une panne du service qu'il mesure --
    cas réel constaté sur ce cluster : postgres_exporter arrêté produisait
    une alerte "PostgreSQL is DOWN" alors que la base acceptait les
    connexions. Redémarrer l'exportateur est sûr : il ne fait que LIRE des
    métriques, il ne touche jamais au service surveillé.

    Sûr : aucun impact sur le service mesuré, zero downtime applicatif.
    Réversible : sans objet -- un redémarrage d'exportateur n'a pas d'état
    à restaurer.
    """
    if exporter not in _EXPORTEURS_AUTORISES:
        return {"ok": False,
                "error": f"exportateur non autorisé: '{exporter}' "
                         f"(attendus: {', '.join(sorted(_EXPORTEURS_AUTORISES))})"}

    # ← AJOUT (échec réel : "Unit postgres_exporter.service not found" sur
    # pve1) : la description du catalogue indique désormais au modèle où
    # chaque exportateur tourne, mais une description GUIDE sans
    # CONTRAINDRE -- rien n'empêche une prochaine génération de se
    # tromper à nouveau. Ici on le sait de façon déterministe : un
    # exportateur applicatif tourne sur la VM qu'il mesure, jamais sur
    # l'hyperviseur. Plutôt que d'échouer alors qu'on connaît la bonne
    # cible, on redirige et on le dit dans le message -- l'utilisateur
    # voit ce qui a réellement été fait, sans correction silencieuse.
    _EXPORTEURS_INVITE = {"postgres_exporter", "process-exporter", "node_exporter"}
    redirige = None
    if exporter in _EXPORTEURS_INVITE and node.lower().startswith("pve"):
        from_node = node
        candidats = sorted(v[:-3].replace("_", "-").lower()
                           for v in os.environ if v.endswith("_IP") and v.startswith("LINUX_VM"))
        # ← AMÉLIORÉ : la version précédente refusait dès qu'il y avait
        # PLUSIEURS VMs candidates, ce qui bloquait l'action alors que la
        # bonne cible est parfaitement déterminable -- il suffit de
        # demander à chaque machine si elle connaît ce service. On
        # interroge donc systemd plutôt que de deviner ou d'abandonner.
        # Un refus prudent qui bloque une action déterminable est un
        # mauvais compromis : il donne l'apparence d'une panne là où le
        # système avait toute l'information nécessaire.
        hotes = [h for h in candidats
                 if _ssh_execute(h, f"systemctl list-unit-files | grep -q '^{exporter}.service'").get("ok")]
        if len(hotes) == 1:
            node = hotes[0]
            redirige = f" (redirigé depuis {from_node} : ce service est installé sur {node}, pas sur l'hyperviseur)"
        elif len(hotes) > 1:
            return {"ok": False,
                    "error": (f"'{exporter}' est présent sur plusieurs machines ({', '.join(hotes)}). "
                              f"Préciser laquelle redémarrer (ex: node='{hotes[0]}').")}
        else:
            return {"ok": False,
                    "error": (f"'{exporter}' introuvable sur les VMs déclarées "
                              f"({', '.join(candidats) or 'aucune'}) ni sur '{from_node}'. "
                              f"Vérifier son nom exact : systemctl list-units --type=service | grep export")}

    result = _ssh_execute(node, f"systemctl restart {exporter}")
    if result["ok"]:
        # Vérifier qu'il est réellement reparti : systemctl restart peut
        # rendre la main sans que le service tienne. Annoncer un succès
        # sans le vérifier serait pire que de signaler l'échec.
        etat = _ssh_execute(node, f"systemctl is-active {exporter}")
        actif = etat.get("ok") and etat.get("data", "").strip() == "active"
        if actif:
            result["message"] = (f"✓ {exporter} redémarré sur {node} — métriques à nouveau collectées"
                                 + (redirige or ""))
        else:
            result["ok"] = False
            result["message"] = (f"⚠ {exporter} redémarré sur {node} mais n'est pas actif "
                                 f"— consulter: journalctl -u {exporter} -n 30")
    _log_action("restart_exporter", {"node": node, "exporter": exporter}, result)
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
    # ← AJOUT : trois actions supplémentaires, sélectionnées selon les
    # mêmes critères que les précédentes -- sûres, réversibles, et
    # répondant à un problème réellement mesuré sur ce cluster.
    "set_swappiness":   set_swappiness,
    "drop_caches":      drop_caches,
    "restart_exporter": restart_exporter,
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
    "set_swappiness": {
        "label":       "Reduce swap aggressiveness",
        "description": ("Lowers vm.swappiness so the kernel keeps pages in RAM instead of "
                        "writing them to disk — directly reduces disk write pressure"),
        "risk":        "low",
        "reversible":  True,
        "params":      ["node", "valeur"],
    },
    "drop_caches": {
        "label":       "Free kernel page cache",
        "description": ("Flushes pending writes then releases the page cache — immediate "
                        "memory relief, temporary by nature"),
        "risk":        "low",
        "reversible":  True,
        "params":      ["node"],
    },
    "restart_exporter": {
        "label":       "Restart Prometheus exporter",
        # ← PRÉCISÉ (échec réel : "Unit postgres_exporter.service not
        # found") : le paramètre s'appelle "node" comme pour les autres
        # actions, ce qui conduisait naturellement le modèle à y mettre un
        # nœud Proxmox. Or un exportateur applicatif tourne sur la VM
        # qu'il mesure, pas sur l'hyperviseur -- la commande partait donc
        # sur pve1 où le service n'existe pas. La description dit
        # désormais explicitement où chaque exportateur se trouve, plutôt
        # que de laisser le modèle deviner à partir du nom du paramètre.
        "description": ("Restarts a metrics exporter that stopped responding — the exporter "
                        "only reads metrics, the monitored service is never touched. "
                        "IMPORTANT: 'node' must be the machine where the exporter RUNS. "
                        "postgres_exporter and process-exporter run INSIDE the guest VMs "
                        "(e.g. node='linux-vm1'), while pve_exporter runs on the Proxmox "
                        "node itself (e.g. node='pve1'). Using the hypervisor name for a "
                        "guest-side exporter fails with 'Unit not found'."),
        "risk":        "low",
        "reversible":  True,
        "params":      ["node", "exporter"],
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