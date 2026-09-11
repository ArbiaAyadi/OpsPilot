import asyncio
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "proxmox"))

from agent.config        import (SURVEILLANCE_INTERVAL, SCORE_MIN_LLM,
                                  COOLDOWN_RAPPORT_S, SCORE_PLANCHER_LSTM,
                                  MIN_NOEUDS_ONLINE_POUR_ALERTE)
from agent.groq_client   import appeler_groq, rate_limiter, budget_journalier_restant
from agent.prompts       import system_prompt_surveillance
from agent.anomaly_detector import detecter_anomalies
from agent.rules_engine  import generer_regles_ia, regles_necessitent_regeneration, marquer_etat_accessibilite
from agent.report_writer import sauvegarder_rapport
from agent.etat_normalizer import normaliser_etat
from agent.incident_prompt import classifier_anomalies, construire_prompt_specifique, parser_reponse_llm, obtenir_doc_url, _bucket_depuis_metric

_normaliser_etat = normaliser_etat

dernier_etat  = {}
dernier_lstm  = {"score": 0.0, "seuil": 0.5, "drift": False,
                 "score_if": 0.0, "score_lstm": 0.0, "lstm_ready": False}
dernier_rapport_ts = 0.0
ws_queue: asyncio.Queue = None

# ← AJOUT (root cause du quota Groq journalier épuisé, 199162/200000
# tokens confirmé par erreur Groq reçue) : un problème qui PERSISTE (ex:
# RAM pve2 critique pendant des heures) se re-signale toutes les 30 min
# via la ré-escalade d'anomaly_detector.py (INTERVALLE_REESCALADE_S) --
# chaque re-signalement déclenchait jusqu'ici un appel LLM COMPLET et
# COÛTEUX (2000-3000 tokens), pour ré-analyser une situation qui n'a
# souvent pas fondamentalement changé depuis la dernière analyse.
# Plusieurs métriques persistantes en parallèle (RAM + CPU + latence
# disque, chacune avec son propre chronomètre de ré-escalade) épuisent
# ainsi le quota journalier en accumulant des ré-analyses largement
# redondantes.
#
# ← CORRIGÉ (après un vrai cas observé) : la première version ne stockait
# que l'horodatage, pas la sévérité -- un problème passant de HIGH à
# CRITICAL pour la MÊME cible (ex: VM CPU 85.8% -> 109.0% en 12 min)
# était traité comme "même problème, encore là" et bloqué comme les
# autres, alors qu'une vraie aggravation mérite une nouvelle analyse --
# exactement le même principe déjà établi dans anomaly_detector.py
# (_palier() : un changement de palier redéclenche toujours une
# anomalie, indépendamment du chronomètre de ré-escalade). Stocke
# maintenant (horodatage, niveau) par cible -- le frein ne s'applique que
# si la nouvelle sévérité n'est PAS pire que celle déjà analysée.
_dernier_appel_llm_par_cible: dict = {}  # (cible, categorie) -> (timestamp, niveau, nb_reanalyses)

# ← MODIFIÉ (cas réel observé) : clé désormais (cible, categorie) plutôt
# que cible seule. Avant, une cible n'avait qu'UNE catégorie enregistrée
# à la fois -- si "linux-vm2" était analysée pour RAM à 98%, puis qu'une
# action corrective (KSM+ballooning) faisait redescendre la RAM sous le
# seuil, un NOUVEAU problème CPU sur la même VM était bloqué par le
# backoff de RAM (jamais vu comme sa propre catégorie), tombant dans le
# message dégradé alors qu'aucune analyse CPU n'avait jamais eu lieu.
# Chaque (cible, categorie) a maintenant son propre chronomètre et son
# propre cache -- un problème RAM résolu n'empêche plus jamais un
# problème CPU sur la même cible d'être analysé pour la première fois.
_derniere_analyse_par_cible: dict = {}  # (cible, categorie) -> {"structured": ..., "markdown": ..., "ts": ...}

# ← AJOUT (amélioration du rapport d'incident) : instant de PREMIÈRE
# détection d'une condition par cible, et nombre total de signalements.
# Un rapport professionnel distingue "quand le problème a commencé" de
# "quand ce rapport a été écrit", et indique si c'est un pic isolé ou une
# condition récurrente -- deux lectures très différentes pour celui qui
# reçoit l'alerte. Réinitialisé quand la condition disparaît assez
# longtemps (même seuil que le plafond d'espacement).
_premiere_detection_par_cible: dict = {}  # cible -> {"ts": float, "occurrences": int}
INTERVALLE_MIN_REANALYSE_LLM_S = int(os.getenv("INTERVALLE_MIN_REANALYSE_LLM_S", "3600"))  # 1h par défaut -- ajustable par .env sans toucher au code
INTERVALLE_MAX_REANALYSE_LLM_S = int(os.getenv("INTERVALLE_MAX_REANALYSE_LLM_S", "14400"))  # 4h -- plafond de l'espacement progressif
_RANG_SEVERITE = {"CRITIQUE": 2, "IMPORTANT": 1, "INFO": 0}


def _severite_pire_ou_egale(nouvelle: str, precedente: str) -> bool:
    """Vrai si `nouvelle` n'est PAS pire que `precedente` -- une escalade
    réelle (IMPORTANT -> CRITIQUE) retourne toujours False, même si le
    chronomètre n'est pas encore écoulé."""
    return _RANG_SEVERITE.get(nouvelle, 0) <= _RANG_SEVERITE.get(precedente, 0)


def _intervalle_pour(nb_reanalyses: int) -> int:
    """← AJOUT : espacement PROGRESSIF pour les conditions chroniques.

    Un frein fixe (1h) traite de la même façon un problème vu pour la
    2e fois et un problème vu pour la 20e fois -- alors que le second
    n'apprend plus rien de nouveau. Cas réel sur cette infrastructure :
    la RAM de pve1/pve2 reste à 92-95% en PERMANENCE (nœuds à 1.9 GB,
    Proxmox en consomme déjà l'essentiel) -- ce n'est pas une anomalie
    ponctuelle, c'est une condition chronique de capacité. La ré-analyser
    24 fois par jour consomme le quota sans jamais produire une
    recommandation différente de la précédente.

    Espacement doublé à chaque ré-analyse consécutive de la MÊME
    condition (1h -> 2h -> 4h, plafonné), même principe que le
    back-off exponentiel des outils professionnels. Un problème NOUVEAU
    (jamais vu, compteur à 0) garde son analyse immédiate, et une
    aggravation réelle de sévérité traverse toujours le frein quel que
    soit le compteur (voir _severite_pire_ou_egale)."""
    return min(INTERVALLE_MIN_REANALYSE_LLM_S * (2 ** max(0, nb_reanalyses - 1)),
               INTERVALLE_MAX_REANALYSE_LLM_S)

_dernier_ai_niveau = None
_derniere_alerte_ai_ts = 0.0
INTERVALLE_REESCALADE_AI_S = 1800

_dernier_reentrainement_ts = 0.0
INTERVALLE_MIN_ENTRE_REENTRAINEMENTS_S = 3600
INTERVALLE_REENTRAINEMENT_PERIODIQUE_S = 86400

# ← AJOUT : confirmation multi-cycles avant de considerer un changement
# d'infrastructure comme reel -- voir _infra_a_change_de_maniere_stable()
# plus bas. Sans ca, un simple timeout Proxmox ponctuel (le repli
# multi-cluster pve1<->pve2 en cas de coupure reseau, deja en place)
# pouvait faire disparaitre puis reapparaitre une VM/un noeud d'un cycle a
# l'autre, redeclenchant une regeneration complete des regles (10 appels
# Tavily + 1 appel Groq) pour un aleas reseau, pas un vrai changement.
_derniere_infra_generee: tuple | None = None
_cycles_infra_differente = 0
CYCLES_CONFIRMATION_CHANGEMENT_INFRA = 3  # ~3 min a 60s/cycle -- filtre un aleas d'1 cycle, laisse passer un vrai changement qui persiste


def _infra_a_change_de_maniere_stable(vms_actuelles: frozenset, noeuds_actuels: frozenset) -> bool:
    """
    Retourne True seulement si l'infrastructure a réellement changé, de
    manière stable sur CYCLES_CONFIRMATION_CHANGEMENT_INFRA cycles
    consécutifs -- filtre une fluctuation ponctuelle (coupure Proxmox d'un
    seul cycle) sans jamais bloquer indéfiniment un vrai changement (VM
    réellement ajoutée/retirée), qui persiste et finit par être confirmé.

    Fonction pure testable séparément de la boucle asyncio qui l'entoure --
    toute la logique de décision vit ici, _boucle_surveillance() se
    contente d'appeler et d'agir sur le résultat.
    """
    global _derniere_infra_generee, _cycles_infra_differente
    infra_actuelle = (vms_actuelles, noeuds_actuels)

    if _derniere_infra_generee is None:
        _derniere_infra_generee = infra_actuelle
        return False

    if infra_actuelle == _derniere_infra_generee:
        _cycles_infra_differente = 0
        return False

    _cycles_infra_differente += 1
    if _cycles_infra_differente >= CYCLES_CONFIRMATION_CHANGEMENT_INFRA:
        _derniere_infra_generee = infra_actuelle
        _cycles_infra_differente = 0
        return True
    return False


def set_ws_queue(q: asyncio.Queue):
    global ws_queue
    ws_queue = q


def _proxmox_accessible(etat: dict) -> bool:
    noeuds = etat.get("noeuds", [])
    if not noeuds:
        return False
    online = sum(
        1 for n in noeuds
        if str(n.get("statut", "")).lower() in ("online", "en ligne", "up")
    )
    return online >= MIN_NOEUDS_ONLINE_POUR_ALERTE


def _cle_dedup_anomalie(a: dict) -> tuple:
    if a.get("type") == "service_down":
        cible   = a.get("cible", "")
        service = cible.split("/")[-1].strip().lower() if cible else a.get("message", "").lower()
        return ("service_down", service)
    return (a.get("cible", ""), a.get("message", "").lower().strip())


def _rendre_markdown(donnees: dict) -> str:
    if donnees.get("_parse_failed"):
        return donnees.get("_raw", "Erreur: réponse LLM illisible.")

    lignes = [
        f"**Severity:** {donnees.get('severity','?')}",
        f"**Summary:** {donnees.get('summary','')}",
        "",
        "**Causes:**",
    ]
    for c in donnees.get("causes", []) or []:
        lignes.append(f"- {c}")

    lignes += ["", f"### {donnees.get('fix_title') or 'Recommended Action'}", ""]
    if donnees.get("warning"):
        lignes.append(f"⚠️ **{donnees['warning']}**")
        lignes.append("")

    for step in donnees.get("steps", []) or []:
        phase = (step.get("phase") or "").upper().replace("_", " ")
        lignes.append(f"**[{phase}]** {step.get('action','')}")
        if step.get("command"):
            lignes.append(f"```bash\n{step['command']}\n```")
        lignes.append("")

    return "\n".join(lignes)


async def analyser_anomalie_llm(anomalies: list, etat: dict) -> dict:
    lstm = dernier_lstm
    prompt, dominant = construire_prompt_specifique(anomalies, etat, lstm)
    loop = asyncio.get_event_loop()
    reponse_brute = await loop.run_in_executor(
        None,
        # ← MODIFIÉ : 1400 → 2000. Une réponse avec plusieurs causes ET
        # plusieurs étapes de remédiation (commandes bash incluses)
        # dépassait parfois 1400 tokens avant d'atteindre la fermeture
        # du JSON -- reproduit exactement le symptôme observé (JSON
        # syntaxiquement valide au début, coupé net avant la fin,
        # échouant au parsing malgré le filet ast.literal_eval, puisque
        # aucun des deux ne peut analyser une structure réellement
        # incomplète). Pas une solution parfaite si un incident a
        # vraiment beaucoup de causes/étapes, mais couvre nettement plus
        # de cas qu'avant sans faire exploser la pression sur le plafond
        # tokens/minute Groq (voir agent/rules_engine.py, déjà réduit
        # côté génération de règles pour la même raison).
        # ← MODIFIÉ (2e ajustement) : 2000 -> 3000. Le premier ajustement
        # (1400 -> 2000, voir raisonnement ci-dessus) restait insuffisant
        # pour les incidents les plus riches -- preuve directe reçue :
        # une réponse à 3 causes + plusieurs étapes coupée NET en plein
        # milieu de la valeur du champ "command" ('"command": "pve-ksm
        # on", "' -- littéralement tronquée à ce caractère précis, pas un
        # JSON malformé autrement). Toujours un compromis avec la
        # pression tokens/minute Groq, mais une réponse tronquée est de
        # toute façon totalement perdue (échoue même avec le filet
        # ast.literal_eval, qui ne peut pas réparer une structure
        # réellement incomplète) -- gaspiller plus de tokens sur un appel
        # qui aboutit vaut mieux que d'en gaspiller moins sur un appel
        # voué à l'échec.
        # format_json=True : contraint un fournisseur de secours a produire du
        # JSON valide (voir groq_client.py). Cet appel attend strictement du
        # JSON structure -- sans effet sur Groq, dont le formatage est deja fiable.
        # ← MODIFIÉ (3e ajustement) : 3000 -> 5000. Preuve directe reçue :
        # un lot d'anomalies couvrant DEUX cibles à la fois (linux-vm1 RAM
        # + pve2/VM103 RAM+CPU dans le même cycle) produisait un JSON
        # encore tronqué à 3000 -- coupé en plein milieu de "physical host
        # has negative RAM...". Un lot multi-cibles double naturellement
        # le contenu nécessaire (causes et étapes pour CHAQUE cible), pas
        # seulement pour une. Alignement sur 5000, déjà utilisé et éprouvé
        # sans excès pour la génération de règles (agent/rules_engine.py,
        # REGLES_MAX_TOKENS) -- cohérence entre les deux plafonds plutôt
        # qu'une valeur inventée séparément ici.
        lambda: appeler_groq(system_prompt_surveillance(etat, lstm), [], prompt, 5000, format_json=True),
    )
    donnees = parser_reponse_llm(reponse_brute)
    if not donnees.get("_parse_failed"):
        try:
            donnees["doc_url"] = obtenir_doc_url(dominant)
        except Exception:
            donnees["doc_url"] = None
    return {
        "markdown":   _rendre_markdown(donnees),
        "structured": donnees,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Analyse de secours déterministe — filet de sécurité universel
# ══════════════════════════════════════════════════════════════════════════════
# ← AJOUT (solution de fond, après plusieurs correctifs ponctuels) :
# jusqu'ici, chaque fois que la sortie du LLM ne pouvait pas être exploitée
# (troncature, réponse inattendue, quota, panne réseau, format différent
# d'un fournisseur de secours...), la carte affichait le drapeau
# "_parse_failed" et le texte brut -- ce qui se présente à l'utilisateur
# comme une panne du produit, alors que l'infrastructure, elle, a bien un
# problème réel à traiter.
#
# Chaque cause individuelle a été corrigée tour à tour (max_tokens 1400 ->
# 2000 -> 3000 -> 5000, balises <think>, format JSON strict, cache par
# catégorie...) mais une nouvelle finissait toujours par apparaître. Ce
# n'est pas une bataille qu'on gagne cause par cause : un service externe
# PEUT toujours renvoyer quelque chose d'inattendu.
#
# Approche définitive : ne plus jamais dépendre du LLM pour produire une
# carte exploitable. Cette fonction construit une analyse structurée
# COMPLÈTE (mêmes champs que le LLM : severity, summary, causes, warning,
# steps) uniquement à partir des anomalies détectées et de l'état réel du
# cluster -- aucun appel réseau, aucun parsing, donc elle ne peut pas
# échouer. Les actions proposées suivent exactement l'ordre établi dans ce
# projet (migration -> KSM -> ballooning -> RAM physique -> nœud
# supplémentaire) et n'incluent JAMAIS de réduction de RAM ou de vCPU
# d'une VM en production.
#
# Marquée "_source": "regles" pour rester honnête : ce n'est pas une
# analyse IA, et l'interface peut l'indiquer plutôt que de laisser croire
# le contraire.
_PLAYBOOK_SECOURS = {
    "ram": {
        "titre": "Reduce memory pressure",
        "causes": [
            "Node RAM usage is above the configured threshold, forcing the kernel to swap.",
            "Sustained swap usage degrades every VM sharing this node's storage.",
        ],
        "warning": "Never reduce an existing VM's RAM in production — this can crash running applications.",
        "steps": [
            ("immediate",  "Enable Kernel Same-page Merging to deduplicate identical memory pages across VMs", "echo 1 > /sys/kernel/mm/ksm/run", "enable_ksm", ["node"]),
            ("short_term", "Enable ballooning so idle guest memory can be reclaimed by the hypervisor", "qm set {vmid} --balloon {balloon}", "enable_balloon", ["node", "vmid", "min_mb"]),
            ("long_term",  "Add physical RAM to the host, or migrate a VM to another node to spread the load", "qm migrate {vmid} {autre_noeud} --online", "migrate_vm", ["node", "vmid", "target_node"]),
        ],
    },
    "cpu": {
        "titre": "Relieve CPU saturation",
        "causes": [
            "CPU usage is above the configured threshold on this target.",
            "Sustained saturation causes scheduling delays for every workload on the node.",
        ],
        "warning": "Never reduce an existing VM's vCPU count in production — this can destabilise running services.",
        "steps": [
            ("immediate",  "Identify the top CPU consumers before changing any allocation", "ps aux --sort=-%cpu | head -15", None, []),
            ("short_term", "Migrate a VM to a less loaded node to spread CPU demand", "qm migrate {vmid} {autre_noeud} --online", "migrate_vm", ["node", "vmid", "target_node"]),
            ("long_term",  "Add vCPUs only if the physical host has real spare cores available", "qm set {vmid} --cores <n>", None, []),
        ],
    },
    "iowait": {
        "titre": "Investigate storage saturation",
        "causes": [
            "Disk latency or I/O wait is above threshold — processes are blocked waiting on storage.",
            "Concurrent I/O from several guests on a shared physical disk multiplies latency.",
        ],
        "warning": "Do not move workloads onto the same physical disk while it is already saturated.",
        "steps": [
            ("immediate",  "Measure per-device latency to confirm which disk is saturated", "iostat -x 1 5", None, []),
            ("short_term", "Identify the processes generating the I/O", "iotop -o -b -n 3", None, []),
            ("long_term",  "Spread guests across separate physical disks, or move to faster storage", "", None, []),
        ],
    },
    "disk": {
        "titre": "Free storage space",
        "causes": [
            "Disk usage is above the configured threshold.",
            "On LVM-thin, a full pool causes writes to fail silently and can corrupt guests.",
        ],
        "warning": "Never delete guest disk images to free space — remove backups and logs first.",
        "steps": [
            ("immediate",  "Check what is consuming space on the storage pool", "pvesm status && df -h", None, []),
            ("short_term", "Reclaim space from journal logs and the package cache", "journalctl --vacuum-size=200M && apt-get clean -y", "clean_logs", ["node"]),
            ("long_term",  "Review old backup archives before deleting any (needs a human decision)", "du -sh /var/lib/vz/dump/* | sort -rh | head -10", None, []),
        ],
    },
    "swap": {
        "titre": "RAM saturated — swap in use",
        "causes": [
            "Swap usage is high, which means physical RAM is exhausted.",
            "Swapping to disk is orders of magnitude slower than RAM and degrades every workload.",
        ],
        "warning": "Never reduce an existing VM's RAM in production.",
        "steps": [
            ("immediate",  "Enable KSM to reclaim duplicated memory pages", "echo 1 > /sys/kernel/mm/ksm/run", "enable_ksm", ["node"]),
            ("short_term", "Reduce swap aggressiveness so the kernel prefers RAM", "sysctl vm.swappiness=10", None, []),
            ("long_term",  "Add physical RAM to the host — this is the only durable fix", "", None, []),
        ],
    },
    "service": {
        "titre": "Restore the failed service",
        "causes": [
            "A monitored application service is no longer responding on its host VM.",
            "A service outage is usually caused by the process having stopped, by memory pressure "
            "(OOM kill), or by a full disk preventing it from writing.",
        ],
        "warning": "Investigate WHY the service stopped before restarting it — an immediate restart on an "
                   "unresolved cause (OOM, full disk) will simply fail again.",
        "steps": [
            ("immediate",  "Check the service state and its most recent log lines on the host VM", "systemctl status {service} --no-pager -l", None, []),
            ("short_term", "Look for an out-of-memory kill or a disk-full error around the time it stopped", "journalctl -u {service} --since '30 min ago' --no-pager | tail -40", None, []),
            ("long_term",  "Once the cause is understood and fixed, restart the service", "systemctl restart {service}", None, []),
        ],
    },
    "quorum": {
        "titre": "Cluster communication at risk",
        "causes": [
            "Corosync reports a degraded state or lost quorum.",
            "Without quorum, Proxmox fences nodes and stops guests to prevent split-brain corruption.",
        ],
        "warning": "Do not restart corosync on several nodes at once — this can worsen the split.",
        "steps": [
            ("immediate",  "Check the current cluster state", "pvecm status", None, []),
            ("short_term", "Inspect the corosync rings for errors", "corosync-cfgtool -s", None, []),
            ("long_term",  "Review the cluster network path between nodes", "journalctl -u corosync -n 50", None, []),
        ],
    },
}

_PLAYBOOK_GENERIQUE = {
    "titre": "Investigate threshold breach",
    "causes": ["A monitored metric has crossed its configured threshold on this target."],
    "warning": "Verify the current value before applying any change.",
    "steps": [
        ("immediate", "Review this target's live metrics on the Infrastructure page", "", None, []),
    ],
}


def _analyse_de_secours(anomalies: list, etat: dict, dominant_type: str,
                         anomalie_dominante: dict, raison: str) -> dict:
    """Construit une analyse structurée COMPLÈTE sans aucun appel externe.
    Ne peut pas échouer : pas de réseau, pas de parsing, uniquement les
    données déjà en mémoire. Voir le commentaire au-dessus pour le
    raisonnement."""
    playbook = _PLAYBOOK_SECOURS.get(dominant_type, _PLAYBOOK_GENERIQUE)
    cible    = anomalie_dominante.get("cible", "cluster")
    niveau   = anomalie_dominante.get("niveau", "IMPORTANT")

    # Contexte réel pour rendre les commandes exécutables telles quelles,
    # plutôt que de laisser des placeholders que l'utilisateur devrait
    # remplir à la main.
    # ← AJOUT : une cible de service arrive sous la forme "vm/service"
    # (ex: "linux-vm1/postgresql") -- il faut isoler les deux parties pour
    # retrouver la VM d'accueil ET nommer le service dans les commandes.
    nom_service = None
    cible_vm = cible
    if "/" in str(cible):
        cible_vm, nom_service = str(cible).split("/", 1)

    vms = etat.get("vms", []) or []
    vm_cible = next((v for v in vms if str(v.get("nom", "")).lower() == str(cible_vm).lower()), None)
    if vm_cible is None:
        vm_cible = next((v for v in vms if str(v.get("noeud", "")).lower() == str(cible_vm).lower()), None)
    vmid = str(vm_cible.get("vmid")) if vm_cible else "<vmid>"

    noeuds = etat.get("noeuds", []) or []
    # ← Le nœud à éviter est celui qui HÉBERGE la cible, pas la cible
    # elle-même : quand la cible est une VM (ex: "linux-vm1"), comparer
    # les noms de nœuds à "linux-vm1" ne correspond à rien, et le premier
    # nœud en ligne — souvent celui qui l'héberge déjà — était proposé
    # comme destination. La commande de migration devenait alors sans
    # effet ("qm migrate 101 pve1" alors que 101 est déjà sur pve1).
    noeud_actuel = (vm_cible.get("noeud") if vm_cible else None) or cible_vm
    autre_noeud = next(
        (n.get("nom") for n in noeuds
         if str(n.get("nom", "")).lower() != str(noeud_actuel).lower()
         and str(n.get("statut", "")).lower() in ("online", "up", "en ligne")),
        "<autre-noeud>",
    )
    balloon = "256"
    if vm_cible and vm_cible.get("ram_total_gb"):
        # Moitié de l'allocation, jamais en dessous de 128 Mo -- le
        # ballooning réclame de la mémoire INACTIVE, il ne force jamais une
        # VM sous ce dont elle a réellement besoin.
        balloon = str(max(128, int(float(vm_cible["ram_total_gb"]) * 1024 / 2)))

    steps = []
    for phase, action, commande, action_id, params_requis in playbook["steps"]:
        cmd = commande.format(vmid=vmid, autre_noeud=autre_noeud, balloon=balloon,
                              service=(nom_service or "<service>")) if commande else ""
        step = {"phase": phase, "action": action, "command": cmd,
                "action_id": None, "action_params": None}
        # ← AJOUT : renseigne action_id + action_params avec de VRAIES
        # valeurs, pour que les étapes de secours obtiennent les boutons
        # "Accepter & Exécuter" exactement comme celles produites par le
        # LLM. Sans ça, une recommandation de secours restait purement
        # informative -- l'utilisateur voyait la bonne commande mais devait
        # la copier à la main, alors que l'action est déjà dans le
        # catalogue pré-approuvé.
        if action_id:
            valeurs = {
                "node":        noeud_actuel,
                "vmid":        vmid,
                "target_node": autre_noeud,
                "min_mb":      balloon,
            }
            # Une action n'est proposée en un clic que si TOUS ses
            # paramètres ont une valeur réelle -- jamais avec un
            # placeholder non résolu, qui produirait une commande
            # invalide au moment de l'exécution.
            fournis = {k: valeurs.get(k) for k in params_requis}
            if all(v and not str(v).startswith("<") for v in fournis.values()):
                step["action_id"]     = action_id
                step["action_params"] = fournis
        steps.append(step)

    # ← Passe par la MÊME validation que les étapes issues du LLM
    # (agent/incident_prompt.py, _valider_step) : vérifie que l'action_id
    # existe réellement dans le catalogue exécutable de cet environnement,
    # que tous les paramètres requis sont présents, et régénère la commande
    # affichée depuis ces paramètres. Réutiliser cette fonction plutôt que
    # d'en écrire une seconde garantit que les deux chemins ne peuvent pas
    # diverger avec le temps.
    try:
        from agent.incident_prompt import _valider_step
        steps = [_valider_step(s) for s in steps]
    except Exception:
        # Si la validation n'est pas disponible, on retombe sur des étapes
        # purement informatives -- jamais sur une action non validée.
        for s in steps:
            s["action_id"] = None
            s["action_params"] = None

    messages = [a.get("message", "") for a in anomalies if a.get("message")]
    resume = (f"{anomalie_dominante.get('message', 'Threshold breach detected')} "
              f"— rule-based analysis (AI analysis unavailable: {raison}).")

    return {
        "severity":    "CRITICAL" if str(niveau).upper().startswith("CRIT") else "HIGH",
        "target_node": cible,
        "target_vmid": vmid if vmid != "<vmid>" else None,
        "summary":     resume,
        "causes":      list(playbook["causes"]) + ([f"Also detected this cycle: {m}" for m in messages[1:4]] if len(messages) > 1 else []),
        "fix_title":   playbook["titre"] + f" on {cible}",
        "warning":     playbook["warning"],
        "steps":       steps,
        # ← Honnêteté : cette analyse n'est PAS produite par l'IA.
        # L'interface peut s'appuyer sur ce champ pour l'indiquer, plutôt
        # que de laisser croire à une analyse IA qui n'a pas eu lieu.
        "_source":     "regles",
        "_raison_secours": raison,
    }


def _boucle_surveillance(loop: asyncio.AbstractEventLoop):

    global dernier_etat, dernier_lstm, dernier_rapport_ts, _dernier_ai_niveau, _derniere_alerte_ai_ts, _dernier_reentrainement_ts

    try:
        from proxmox_api import get_etat_cluster, get_etat_tous_clusters
        PROXMOX_OK = True
    except Exception:
        PROXMOX_OK = False
        def get_etat_cluster(): return {}
        def get_etat_tous_clusters(): return {}

    try:
        from metriques_proxmox import collecter_metriques_cluster
        PROMETHEUS_OK = True
    except Exception:
        PROMETHEUS_OK = False
        def collecter_metriques_cluster(): return {}

    try:
        from hypervisor_detect import get_hypervisor_context
        HYPERVISOR_OK = True
    except Exception as e:
        HYPERVISOR_OK = False
        print(f"[Hypervisor] Module non disponible: {e}")
        def get_hypervisor_context():
            return {"type": "unknown", "recommandation_ram": "Check systemd-detect-virt on the node."}

    try:
        from metriques_pc_hote import collecter_ressources_pc_hote
        HOTE_PC_OK = True
    except Exception as e:
        HOTE_PC_OK = False
        print(f"[PC Host] Module non disponible: {e}")
        def collecter_ressources_pc_hote(): return {"disponible": False}

    try:
        from hypervisor_metrics import collecter_metriques_hyperviseur
        HYPERVISOR_METRICS_OK = True
    except Exception as e:
        HYPERVISOR_METRICS_OK = False
        print(f"[Hypervisor Metrics] Module non disponible: {e}")
        def collecter_metriques_hyperviseur(type_hyperviseur): return {"disponible": False}

    _analyser = None

    try:
        from vm_app_monitor import collecter_metriques_apps
        VM_MONITOR_OK = True
    except Exception as e:
        VM_MONITOR_OK = False
        print(f"[VM Monitor] Non disponible: {e}")
        def collecter_metriques_apps(): return {"alertes_apps": []}

    try:
        from ml_analyser import MLAnalyseur
        _analyser = MLAnalyseur()
        print("[OK] AI detection engine - IF actif, LSTM en apprentissage")
    except Exception as e:
        print(f"[WARN] AI engine: {e}")

    etat_prec = {}
    print(f"[Monitoring] Demarre -- toutes les {SURVEILLANCE_INTERVAL}s")

    while True:
        try:
            if PROXMOX_OK:
                etat_raw     = get_etat_tous_clusters()
                etat         = normaliser_etat(etat_raw)
                etat["hyperviseur"] = get_hypervisor_context()

                if HYPERVISOR_METRICS_OK:
                    try:
                        etat["hyperviseur_metriques"] = collecter_metriques_hyperviseur(
                            etat["hyperviseur"].get("type", "unknown")
                        )
                        if etat["hyperviseur_metriques"].get("disponible"):
                            noeuds_liste = etat.get("noeuds", [])
                            etat["hyperviseur_metriques"]["vms_ram_allouee_gb"] = round(
                                sum(n.get("ram_total_gb", 0) for n in noeuds_liste), 2
                            )
                            etat["hyperviseur_metriques"]["vms_disk_allouee_gb"] = round(
                                sum(n.get("disk_total_gb", 0) for n in noeuds_liste), 2
                            )
                    except Exception as e:
                        print(f"[Hypervisor Metrics] Erreur collecte: {e}")
                        etat["hyperviseur_metriques"] = {"disponible": False}

                if HOTE_PC_OK:
                    try:
                        etat["hote_physique"] = collecter_ressources_pc_hote()
                    except Exception as e:
                        print(f"[PC Host] Erreur collecte: {e}")
                        etat["hote_physique"] = {"disponible": False}

                hote = etat.get("hote_physique", {})
                hyp  = etat.get("hyperviseur_metriques", {})
                if hote.get("disponible") and hyp.get("disponible"):
                    reserve_windows = hote.get("vmware_allocation", {}) \
                                          .get("windows_reserve", {}) \
                                          .get("ram_gb", 0)
                    vms_allouee = hyp.get("vms_ram_allouee_gb", 0)
                    hyp["marge_reelle_ram_gb"] = round(
                        hote.get("ram_total_gb", 0) - reserve_windows - vms_allouee, 2
                    )

                    host_ram_total  = hote.get("ram_total_gb", 0)
                    host_disk_total = hote.get("disk_total_gb", 0)
                    if host_ram_total > 0:
                        hyp["vms_ram_allouee_pct_hote"] = round(
                            vms_allouee / host_ram_total * 100, 1
                        )
                    if host_disk_total > 0:
                        hyp["vms_disk_allouee_pct_hote"] = round(
                            hyp.get("vms_disk_allouee_gb", 0) / host_disk_total * 100, 1
                        )

                if VM_MONITOR_OK:
                    try:
                        apps_data = collecter_metriques_apps()
                        etat["apps"] = apps_data
                        from vm_app_monitor import (detecter_services_vm, get_metriques_services_vm,
                                                     generer_alertes_services)
                        alertes_services = []
                        for v in etat.get("vms", []):
                            vmid = v.get("vmid")
                            v["services_detectes"]  = detecter_services_vm(vmid)
                            v["metriques_services"] = get_metriques_services_vm(
                                vmid, v["services_detectes"], vcpus=v.get("vcpus")
                            )
                            alertes_services.extend(
                                generer_alertes_services(vmid, v["services_detectes"], v["metriques_services"])
                            )
                        etat.setdefault("alertes", []).extend(
                            apps_data.get("alertes_apps", [])
                        )
                        etat["alertes"].extend(alertes_services)
                        if apps_data.get("alertes_apps"):
                            print(f"[VM Monitor] {len(apps_data['alertes_apps'])} alertes applicatives")
                        if alertes_services:
                            print(f"[VM Monitor] {len(alertes_services)} alerte(s) service generique(s) (DOWN/derive)")
                    except Exception as e:
                        print(f"[VM Monitor] Erreur collecte: {e}")
                dernier_etat = etat
            else:
                etat = dernier_etat

            if not _proxmox_accessible(etat):
                print("[Monitoring] Proxmox non accessible -- alertes et rapports suspendus")
                marquer_etat_accessibilite(False)
                if ws_queue and etat:
                    asyncio.run_coroutine_threadsafe(ws_queue.put({
                        "type":      "etat_cluster",
                        "etat":      etat,
                        "lstm":      dernier_lstm,
                        "timestamp": datetime.now().isoformat(),
                    }), loop)
                time.sleep(SURVEILLANCE_INTERVAL)
                continue

            if etat:
                marquer_etat_accessibilite(True)
                try:
                    from database import sauvegarder_metriques
                    sauvegarder_metriques(etat, dernier_lstm.get("score", 0.0))
                except Exception:
                    pass

            if etat and regles_necessitent_regeneration() and rate_limiter.slots() >= 5:
                print("[AI Rules] Generation automatique des regles (24h)...")
                asyncio.run_coroutine_threadsafe(generer_regles_ia(etat), loop).result(timeout=120)
            elif etat and rate_limiter.slots() >= 5:
                # ← MODIFIÉ : compare maintenant à la DERNIÈRE INFRASTRUCTURE
                # AYANT DÉCLENCHÉ UNE GÉNÉRATION (via _infra_a_change_de_
                # maniere_stable), plus seulement au cycle précédent
                # (etat_prec) -- celui-ci pouvait légitimement différer d'un
                # seul cycle à l'autre à cause du repli multi-cluster
                # pve1<->pve2 (déjà en place) lors d'une simple coupure
                # réseau ponctuelle vers l'un des deux hôtes, sans qu'aucune
                # VM n'ait réellement changé. Le changement doit maintenant
                # persister sur plusieurs cycles consécutifs pour être
                # considéré réel -- un vrai ajout/retrait de VM finit
                # toujours par être confirmé, un aléa réseau d'un seul cycle
                # ne déclenche plus rien.
                vms_actuelles  = frozenset(v["vmid"] for v in etat.get("vms", []))
                noeuds_actuels = frozenset(n["nom"] for n in etat.get("noeuds", []))
                if _infra_a_change_de_maniere_stable(vms_actuelles, noeuds_actuels):
                    print("[AI Rules] Changement infrastructure confirme sur plusieurs cycles -- regeneration")
                    asyncio.run_coroutine_threadsafe(generer_regles_ia(etat), loop).result(timeout=120)

            metriques_prom = {}
            if PROMETHEUS_OK:
                try:
                    metriques_prom = collecter_metriques_cluster()
                except Exception as e:
                    print(f"[Prometheus] {e}")

            net_keys   = ("net_in_mbps", "net_out_mbps")
            io_keys    = ("cpu_iowait_pct", "disk_read_iops", "disk_write_iops")
            hw_keys    = ("cpu_temp_max_c",)
            smart_keys = ("smart_disks_monitored",)

            if etat and metriques_prom and isinstance(metriques_prom, dict):
                prom_noeuds = metriques_prom.get("noeuds", [])
                for n in etat.get("noeuds", []):
                    nom = (n.get("nom") or "").lower()
                    match = next(
                        (pn for pn in prom_noeuds
                         if str(pn.get("node", "")).lower() == nom
                         or nom in str(pn.get("node", "")).lower()),
                        None
                    )
                    if match:
                        for key in (
                            "swap_pct","swap_used_gb","swap_total_gb",
                            "cpu_iowait_pct","cpu_steal_pct","load_avg_1m","load_avg_5m","load_avg_15m",
                            "disk_read_iops","disk_write_iops","disk_read_mbps","disk_write_mbps",
                            "disk_read_latency_ms","disk_write_latency_ms",
                            "net_in_mbps","net_out_mbps","net_errors_in","net_errors_out",
                            "net_drop_in","net_drop_out","cpu_temp_max_c","cpu_temp_avg_c",
                            "disk_temp_max_c","smart_ok","smart_reallocated_sectors",
                            "smart_pending_sectors","smart_uncorrectable","smart_disks_monitored",
                            "zfs_arc_hit_rate","zfs_arc_size_gb","zfs_available",
                            "corosync_ok","corosync_quorum_ok","fd_used_pct","procs_running","procs_blocked",
                        ):
                            if key in match:
                                n[key] = match[key]
                        if match.get("uptime_h", 0) > 0:
                            n["uptime_h"] = match["uptime_h"]
                        n["net_available"]   = any(k in match for k in net_keys)
                        n["io_available"]    = any(k in match for k in io_keys)
                        n["hw_available"]    = any(k in match for k in hw_keys)
                        n["smart_available"] = any(k in match for k in smart_keys)
                    else:
                        n["net_available"] = n["io_available"] = n["hw_available"] = n["smart_available"] = False
                if "cluster" in metriques_prom:
                    etat["cluster"] = {**etat.get("cluster", {}), **metriques_prom["cluster"]}

                prom_vms = metriques_prom.get("vms", [])
                for v in etat.get("vms", []):
                    match = next(
                        (pv for pv in prom_vms if str(pv.get("vmid","")) == str(v.get("vmid",""))),
                        None
                    )
                    if match:
                        for key in ("disk_used_gb", "disk_total_gb", "disk_pct",
                                    "disk_read_mbps", "disk_write_mbps",
                                    "net_in_mbps", "net_out_mbps"):
                            if key in match and match[key]:
                                v[key] = match[key]

                dernier_etat = etat

            metriques_ml_courant = None

            if _analyser and etat:
                try:
                    noeuds   = etat.get("noeuds", [])
                    n_noeuds = max(len(noeuds), 1)
                    avg      = lambda k: sum(x.get(k, 0) for x in noeuds) / n_noeuds
                    total    = lambda k: sum(x.get(k, 0) for x in noeuds)
                    metriques_ml = {
                        "cpu_pct": avg("cpu_pct"), "ram_pct": avg("ram_pct"),
                        "disk_pct": avg("disk_pct"), "swap_pct": avg("swap_pct"),
                        "cpu_iowait_pct": avg("cpu_iowait_pct"),
                        "disk_read_iops": total("disk_read_iops"), "disk_write_iops": total("disk_write_iops"),
                        "disk_read_latency_ms": avg("disk_read_latency_ms"),
                        "disk_write_latency_ms": avg("disk_write_latency_ms"),
                        "net_in_mbps": total("net_in_mbps"), "net_out_mbps": total("net_out_mbps"),
                        "net_errors_in": total("net_errors_in"), "net_errors_out": total("net_errors_out"),
                        "net_drop_in": total("net_drop_in"), "net_drop_out": total("net_drop_out"),
                        "vms_running": etat.get("vms_running", 0),
                        "load_avg_1m": avg("load_avg_1m"),
                        "zfs_arc_hit_rate": avg("zfs_arc_hit_rate") or 95.0,
                        "cpu_temp_max_c": max((n.get("cpu_temp_max_c", 0) for n in noeuds), default=0),
                        "fd_used_pct": avg("fd_used_pct"),
                    }
                    if metriques_prom and isinstance(metriques_prom, dict):
                        for k in metriques_ml:
                            if k in metriques_prom:
                                metriques_ml[k] = metriques_prom[k]
                    score, seuil = _analyser.analyser(metriques_ml)
                    seuil = max(float(seuil), SCORE_PLANCHER_LSTM)
                    ml_stats = _analyser.get_stats() if hasattr(_analyser, "get_stats") else {}
                    dernier_lstm = {
                        "score":      float(score),
                        "seuil":      seuil,
                        "drift":      ml_stats.get("lstm", {}).get("drift_detecte", False),
                        "score_if":   ml_stats.get("score_if", 0.0),
                        "score_lstm": ml_stats.get("score_lstm", 0.0),
                        "lstm_ready": ml_stats.get("lstm_ready", False),
                    }
                    metriques_ml_courant = metriques_ml
                except Exception as e:
                    print(f"[AI Engine] {e}")

            nouvelles = detecter_anomalies(etat, etat_prec)

            if dernier_lstm["score"] >= SCORE_MIN_LLM:
                ai_score  = dernier_lstm["score"]
                ai_niveau = "CRITIQUE" if ai_score >= 0.8 else "IMPORTANT"
                nouveau_palier = ai_niveau != _dernier_ai_niveau
                reescalade = (ai_niveau == "CRITIQUE"
                              and (time.time() - _derniere_alerte_ai_ts) >= INTERVALLE_REESCALADE_AI_S)
                if nouveau_palier or reescalade:
                    nouvelles.append({
                        "niveau": ai_niveau, "cible": "cluster",
                        "message": f"AI score {ai_score:.4f} > threshold {dernier_lstm['seuil']:.4f}",
                        "type": "ai_score",
                    })
                    _derniere_alerte_ai_ts = time.time()
                _dernier_ai_niveau = ai_niveau
            else:
                _dernier_ai_niveau = None

            seen_keys = set()
            nouvelles_dedup = []
            for a in nouvelles:
                key = _cle_dedup_anomalie(a)
                if key not in seen_keys:
                    seen_keys.add(key)
                    nouvelles_dedup.append(a)
            nouvelles = nouvelles_dedup

            if metriques_ml_courant is not None:
                try:
                    from database import sauvegarder_echantillon_ml
                    sauvegarder_echantillon_ml(metriques_ml_courant, contamine=bool(nouvelles))
                except Exception:
                    pass

            now = time.time()
            if nouvelles and (now - dernier_rapport_ts) >= COOLDOWN_RAPPORT_S:
                slots = rate_limiter.slots()
                print(f"[Monitoring] {len(nouvelles)} anomalie(s) -- slots: {slots}")

                # ← AJOUT : cible ET sévérité dominantes calculées une
                # seule fois ici, réutilisées plus bas pour la
                # notification (évite un appel redondant à
                # classifier_anomalies) -- voir _dernier_appel_llm_par_cible
                # en tête de fichier pour le raisonnement complet (root
                # cause du quota journalier épuisé : un problème
                # persistant redéclenchait un appel LLM complet à chaque
                # ré-escalade de 30 min).
                types_dict, dominant_type = classifier_anomalies(nouvelles)
                # ← CORRIGÉ (titre/contenu incohérents, observés en
                # conditions réelles) : types_dict[dominant_type][0]
                # prenait la PREMIÈRE anomalie ajoutée au groupe dominant,
                # peu importe sa sévérité au sein de ce groupe. Deux
                # anomalies RAM sur des cibles différentes (linux-vm1
                # HIGH, pve2 CRITIQUE) tombent toutes les deux dans le
                # même groupe "ram" -- l'index [0] choisissait alors
                # linux-vm1 simplement parce qu'elle avait été détectée
                # en premier ce cycle-là, alors que pve2 (CRITIQUE) est
                # objectivement la plus grave. La carte de recommendation
                # se titrait "linux-vm1 RAM 85.1%" tout en contenant une
                # analyse portant sur pve2/VM103 -- le LLM, lui, priorise
                # correctement par sévérité dans son propre raisonnement,
                # seul le choix du titre affiché ne le faisait pas.
                # Choisit maintenant la PIRE sévérité au sein du groupe
                # dominant, avec l'ordre d'origine comme départage seulement
                # entre anomalies de MÊME sévérité (pas de choix arbitraire
                # supplémentaire au-delà de ce cas).
                groupe_dominant    = types_dict.get(dominant_type) or nouvelles
                anomalie_dominante = groupe_dominant[0]
                for a in groupe_dominant[1:]:
                    if not _severite_pire_ou_egale(a.get("niveau", "IMPORTANT"), anomalie_dominante.get("niveau", "IMPORTANT")):
                        anomalie_dominante = a
                cible_dominante      = anomalie_dominante.get("cible", "cluster")
                niveau_dominant      = anomalie_dominante.get("niveau", "IMPORTANT")
                # ← MODIFIÉ : clé (cible, categorie) au lieu de cible seule
                # -- voir le raisonnement complet en tête de fichier
                # (_dernier_appel_llm_par_cible). dominant_type vient déjà
                # de classifier_anomalies() plus haut, réutilisé tel quel.
                cle_suivi = (cible_dominante, dominant_type)
                dernier_ts, dernier_niveau, nb_reanalyses = _dernier_appel_llm_par_cible.get(
                    cle_suivi, (0, "INFO", 0)
                )
                # ← AJOUT : si la condition a été SILENCIEUSE plus longtemps
                # que le plafond d'espacement, elle a probablement été
                # résolue entre-temps -- son retour est traité comme un
                # problème NEUF (compteur remis à zéro, analyse immédiate),
                # pas comme la suite d'une chronique déjà espacée au maximum.
                if dernier_ts and (now - dernier_ts) > INTERVALLE_MAX_REANALYSE_LLM_S:
                    nb_reanalyses = 0
                intervalle_requis    = _intervalle_pour(nb_reanalyses)
                dans_fenetre_recente = (now - dernier_ts) < intervalle_requis
                # ← Le frein ne s'applique que si en plus la sévérité n'a
                # PAS empiré depuis la dernière analyse -- une aggravation
                # réelle (ex: HIGH -> CRITICAL) déclenche toujours une
                # nouvelle analyse, même dans la fenêtre. Cas observé
                # concrètement : VM CPU 85.8% (HIGH) -> 109.0% (CRITICAL)
                # en 12 min, bloqué à tort par la première version de ce
                # correctif, qui ne regardait que le délai.
                reanalyse_trop_recente = dans_fenetre_recente and _severite_pire_ou_egale(niveau_dominant, dernier_niveau)

                # ← AJOUT : réserve de budget pour les incidents CRITIQUE
                # -- sous RESERVE_BUDGET_CRITIQUE_SEULEMENT_TOKENS, seul un
                # niveau CRITIQUE tente encore un appel Groq ; IMPORTANT
                # bascule directement sur le message léger, sans même
                # essayer (groq_client.py évite déjà l'appel individuel
                # sous sa propre marge de sécurité, mais ici on décide
                # SELON LA SÉVÉRITÉ avant d'y arriver -- pour ne jamais
                # laisser un incident IMPORTANT consommer les derniers
                # tokens qu'un vrai CRITIQUE pourrait avoir besoin dans la
                # même journée).
                budget_restant = budget_journalier_restant()
                RESERVE_BUDGET_CRITIQUE_SEULEMENT_TOKENS = 20000
                budget_insuffisant_pour_ce_niveau = (
                    budget_restant < RESERVE_BUDGET_CRITIQUE_SEULEMENT_TOKENS
                    and niveau_dominant != "CRITIQUE"
                )

                if reanalyse_trop_recente:
                    minutes_restantes = int((intervalle_requis - (now - dernier_ts)) / 60)
                    print(f"[Monitoring] {cible_dominante} : condition chronique deja analysee "
                          f"{nb_reanalyses}x, espacement actuel {intervalle_requis//60}min, sans aggravation -- "
                          f"pas de nouvel appel Groq (encore ~{minutes_restantes}min)")
                    lignes    = "\n".join(f"- [{a['niveau']}] {a['message']}" for a in nouvelles)
                    # ← MODIFIÉ : clé (cible, categorie) -- la vérification
                    # de catégorie explicite qui existait ici avant devient
                    # inutile : la clé elle-même garantit qu'on ne retrouve
                    # QUE l'analyse de cette catégorie précise pour cette
                    # cible précise, jamais une autre.
                    precedente = _derniere_analyse_par_cible.get(cle_suivi)
                    reutilisable = (
                        precedente
                        and isinstance(precedente.get("structured"), dict)
                        and not precedente["structured"].get("_parse_failed")
                    )
                    if reutilisable:
                        # ← On réutilise l'analyse IA complète produite pour
                        # cette même cible ET cette même catégorie : causes,
                        # avertissements et actions restent valables tant que
                        # la condition persiste -- c'est justement pour ça
                        # qu'on ne rappelle pas le LLM. La carte affiche donc
                        # une vraie recommandation actionnable, au lieu d'un
                        # message d'erreur trompeur.
                        structured = dict(precedente["structured"])
                        structured["_analyse_reutilisee"] = True
                        heure_origine = datetime.fromtimestamp(precedente["ts"]).strftime("%H:%M")
                        structured["_analysee_a"] = heure_origine
                        prefixe = (f"[Analyse de {heure_origine} — condition toujours active, "
                                   f"valeurs ci-dessous datées de cette analyse] ")
                        if structured.get("summary"):
                            structured["summary"] = prefixe + structured["summary"]
                        analyse = f"_{prefixe.strip()}_\n\n" + precedente["markdown"]
                    else:
                        # ← CORRIGÉ (texte trompeur signalé) : "pour préserver
                        # le quota" laissait croire que le budget Groq était
                        # en cause, ce qui n'est PAS ce que cette branche
                        # vérifie -- elle se déclenche uniquement sur le
                        # délai et la sévérité (reanalyse_trop_recente),
                        # totalement indépendamment du budget restant. La
                        # branche budget_insuffisant_pour_ce_niveau, plus
                        # bas, est la SEULE qui parle réellement de quota --
                        # jamais celle-ci. Reformulé pour dire la vraie
                        # raison : cette catégorie précise, pour cette
                        # cible précise, vient d'être analysée sans
                        # s'aggraver, donc pas encore ré-analysée -- avec la
                        # première analyse ratée (aucun cache exploitable
                        # encore), ce qui reste rare mais possible juste
                        # après un redémarrage.
                        structured = _analyse_de_secours(nouvelles, etat, dominant_type,
                                                          anomalie_dominante, "recently analysed, awaiting re-analysis")
                        analyse    = _rendre_markdown(structured)
                elif budget_insuffisant_pour_ce_niveau:
                    print(f"[Monitoring] Budget journalier bas ({budget_restant} tokens restants) -- "
                          f"reserve aux incidents CRITIQUE uniquement, {niveau_dominant} mis en attente sans appel Groq")
                    structured = _analyse_de_secours(nouvelles, etat, dominant_type,
                                                      anomalie_dominante, "daily budget reserved for critical incidents")
                    analyse    = _rendre_markdown(structured)
                elif slots >= 5:
                    try:
                        resultat_llm = asyncio.run_coroutine_threadsafe(
                            analyser_anomalie_llm(nouvelles, etat), loop
                        # ← MODIFIÉ : 60 → 120s. La cascade de repli dans
                        # appeler_groq() (3 modèles, chacun avec ses pauses
                        # de rate-limit de 5-8s en cas de 429, plus une
                        # dernière tentative après 30s si les 3 échouent)
                        # peut légitimement dépasser 60s en période de
                        # forte limitation Groq -- l'appel abandonnait
                        # alors AVANT que Groq ait fini de répondre,
                        # produisant le message "delai depasse" observé,
                        # alors que la réponse serait potentiellement
                        # arrivée quelques secondes plus tard.
                        ).result(timeout=120)
                        analyse    = resultat_llm["markdown"]
                        structured = resultat_llm["structured"]
                        # ← AJOUT (filet universel) : le LLM a répondu mais
                        # sa sortie n'a pas pu être exploitée. Plutôt que
                        # d'afficher une erreur interne à l'utilisateur, on
                        # produit une analyse déterministe complète (voir
                        # _analyse_de_secours). La carte reste exploitable,
                        # avec de vraies actions -- l'infrastructure a un
                        # problème réel, il faut y répondre même quand le
                        # service externe déçoit.
                        if isinstance(structured, dict) and structured.get("_parse_failed"):
                            print(f"[Monitoring] Sortie LLM inexploitable -- bascule sur l'analyse deterministe "
                                  f"({dominant_type}, cible {cible_dominante})")
                            structured = _analyse_de_secours(nouvelles, etat, dominant_type,
                                                              anomalie_dominante, "unreadable model output")
                            analyse    = _rendre_markdown(structured)
                        # ← AJOUT : trace du fournisseur qui a réellement
                        # produit CETTE analyse (groq, mistral, ollama...).
                        # Sans ça, impossible de savoir a posteriori si une
                        # recommandation vient du modèle principal ou d'un
                        # secours -- information nécessaire pour juger de sa
                        # fiabilité, et pour diagnostiquer si un fournisseur
                        # donné produit des sorties mal formées.
                        if isinstance(structured, dict):
                            try:
                                from agent.groq_client import DERNIER_FOURNISSEUR as _f
                                structured["_fournisseur"] = _f
                            except Exception:
                                pass
                        # ← CORRIGÉ : marque TOUTES les cibles uniques
                        # présentes dans ce lot (avec leur pire sévérité
                        # chacune), pas seulement cible_dominante -- un lot
                        # mixte (ex: pve1 disk latency + linux-vm2 CPU
                        # ensemble) ne protégeait auparavant QUE la cible
                        # dominante ; l'autre restait sans protection pour
                        # son propre cycle suivant, exactement le cas
                        # observé (linux-vm2 re-analysée 12 min après,
                        # alors que la cible dominante du lot précédent
                        # était différente).
                        # ← MODIFIÉ : clé (cible, categorie) au lieu de
                        # cible seule -- voir le raisonnement complet en
                        # tête de fichier. Chaque anomalie du lot garde SA
                        # PROPRE catégorie (déduite de son "metric" via
                        # _bucket_depuis_metric), pas la seule catégorie
                        # dominante du lot entier -- un lot mixte
                        # (ex: pve2 RAM + linux-vm2 CPU) marque désormais
                        # deux entrées distinctes, chacune avec son propre
                        # chronomètre.
                        pires_par_cle = {}
                        for a in nouvelles:
                            c = a.get("cible", "cluster")
                            n = a.get("niveau", "IMPORTANT")
                            metric = a.get("metric")
                            cat = _bucket_depuis_metric(metric) if metric else dominant_type
                            cle = (c, cat)
                            if cle not in pires_par_cle or not _severite_pire_ou_egale(n, pires_par_cle[cle]):
                                pires_par_cle[cle] = n
                        for cle, n in pires_par_cle.items():
                            # ← Compteur incrémenté par (cible, categorie) :
                            # pilote l'espacement progressif (_intervalle_pour)
                            # au cycle suivant. Remis à 0 quand la sévérité
                            # empire -- le problème a changé de nature, il
                            # redevient "neuf" et mérite un suivi rapproché.
                            _, ancien_niveau, ancien_compteur = _dernier_appel_llm_par_cible.get(cle, (0, "INFO", 0))
                            compteur = 0 if not _severite_pire_ou_egale(n, ancien_niveau) else ancien_compteur + 1
                            _dernier_appel_llm_par_cible[cle] = (now, n, compteur)
                            # ← Mémorise l'analyse complète pour CETTE
                            # combinaison (cible, categorie) précise : elle
                            # sera réaffichée telle quelle si LE MÊME
                            # problème (même cible, même catégorie) se
                            # re-signale pendant la fenêtre d'espacement --
                            # jamais réutilisée pour une catégorie
                            # différente sur la même cible, qui obtient sa
                            # propre analyse fraîche. Uniquement si le
                            # parsing a réussi -- réutiliser une analyse
                            # ratée n'aurait aucun intérêt.
                            if isinstance(structured, dict) and not structured.get("_parse_failed"):
                                _derniere_analyse_par_cible[cle] = {
                                    "structured": structured,
                                    "markdown":   analyse,
                                    "ts":         now,
                                    # ← Catégorie du problème réellement
                                    # analysé (ram/cpu/disk...) : conditionne
                                    # la réutilisation, pour ne jamais
                                    # réafficher une analyse CPU face à une
                                    # anomalie RAM.
                                    # categorie retirée : redondante, la clé
                                    # (cible, categorie) l'encode déjà --
                                    # la stocker à nouveau ici aurait risqué
                                    # d'y mettre dominant_type (celui du lot
                                    # entier) au lieu de "cat" (celui de
                                    # cette entrée précise), une source
                                    # d'incohérence silencieuse.
                                }
                    except TimeoutError:
                        print(f"[Monitoring] Analyse LLM trop lente (>120s) -- bascule sur l'analyse deterministe")
                        structured = _analyse_de_secours(nouvelles, etat, dominant_type,
                                                          anomalie_dominante, "analysis timed out")
                        analyse    = _rendre_markdown(structured)
                else:
                    print("[Monitoring] Debit par minute epuise -- bascule sur l'analyse deterministe")
                    structured = _analyse_de_secours(nouvelles, etat, dominant_type,
                                                      anomalie_dominante, "rate limit reached")
                    analyse    = _rendre_markdown(structured)

                # ← Suivi de la première détection et du nombre de
                # signalements pour cette cible. Réinitialisé si la
                # condition a disparu plus longtemps que le plafond
                # d'espacement (elle est alors considérée comme un
                # nouvel incident, pas la suite du précédent).
                suivi = _premiere_detection_par_cible.get(cible_dominante)
                if not suivi or (now - suivi.get("dernier_vu", 0)) > INTERVALLE_MAX_REANALYSE_LLM_S:
                    suivi = {"ts": now, "occurrences": 0}
                suivi["occurrences"] += 1
                suivi["dernier_vu"] = now
                _premiere_detection_par_cible[cible_dominante] = suivi

                etat_rapport       = etat if etat and etat.get("noeuds") else dernier_etat
                nom_rapport        = sauvegarder_rapport(
                    nouvelles, analyse, etat_rapport, dernier_lstm["score"], structured,
                    recurrence={"premiere_detection": suivi["ts"], "occurrence": suivi["occurrences"]},
                )
                dernier_rapport_ts = now

                rec_id = -1
                try:
                    from database import sauvegarder_recommendation
                    rec_id = sauvegarder_recommendation({
                        "structured":  structured,
                        # ← CORRIGÉ (incohérence titre/contenu observée) :
                        # nouvelles[0] -> anomalie_dominante. nouvelles[0]
                        # est simplement la première anomalie dans l'ordre
                        # de détection (arbitraire, dépend de l'ordre
                        # d'itération des nœuds/VMs) -- sans rapport avec
                        # ce que le LLM a réellement jugé le plus sévère.
                        # Cas observé : carte titrée "linux-vm1 RAM 85.1%"
                        # contenant une analyse portant sur "pve2/VM103,
                        # RAM 95%, CPU 105.8%" -- linux-vm1 se trouvait
                        # juste être détectée en premier ce cycle-là.
                        # anomalie_dominante (déjà calculée plus haut via
                        # classifier_anomalies, utilisée pour la
                        # notification) est la MÊME anomalie que le
                        # prompt envoyé au LLM présente comme prioritaire
                        # -- le titre affiché correspond désormais à ce
                        # que l'analyse traite réellement, même quand le
                        # parsing échoue et qu'on retombe sur ce repli.
                        "title":       structured.get("fix_title") or anomalie_dominante.get("message", "Infrastructure Issue"),
                        "severity":    structured.get("severity") or anomalie_dominante.get("niveau", "HIGH"),
                        "status":      "OPEN",
                        "timestamp":   datetime.now().isoformat(),
                        # ← CORRIGÉ (cause des cartes dupliquées) : la clé
                        # de déduplication venait de structured["target_node"],
                        # un champ écrit LIBREMENT par le LLM -- il pouvait
                        # valoir "pve2" à un cycle et être absent au suivant
                        # (le code retombait alors sur la cible de la
                        # première anomalie, souvent la VM au lieu du nœud).
                        # Clé instable = aucune fusion : constaté en réel
                        # avec 6 cartes pour un seul problème sur pve2 entre
                        # 00:26 et 01:22. On utilise désormais
                        # cible_dominante, calculée de façon déterministe
                        # par classifier_anomalies() à partir du champ
                        # "metric" canonique des anomalies -- même entrée,
                        # même sortie, toujours. Le target_node du LLM reste
                        # conservé pour l'affichage, mais ne pilote plus
                        # rien.
                        "target":      cible_dominante,
                        "target_affiche": structured.get("target_node") or cible_dominante,
                        "target_vmid": structured.get("target_vmid"),
                        "rapport":     nom_rapport,
                    })
                    # ← AJOUT : un id négatif signale un repli mémoire
                    # (PostgreSQL injoignable au moment de l'écriture).
                    # Sans ce journal, la divergence entre ce qui s'affiche
                    # en direct et ce qui subsiste après rafraîchissement
                    # restait totalement silencieuse -- impossible à
                    # diagnostiquer autrement qu'en comptant les cartes.
                    if rec_id < 0:
                        print(f"[DB] Recommendation {cible_dominante} sauvegardee en MEMOIRE seulement "
                              f"(id {rec_id}) -- PostgreSQL indisponible, elle ne survivra pas a un redemarrage")
                except Exception as e:
                    print(f"[DB] Erreur sauvegarde recommendation: {e}")

                if ws_queue:
                    asyncio.run_coroutine_threadsafe(ws_queue.put({
                        "type": "alerte", "role": "assistant",
                        "content": analyse,
                        "structured": structured,
                        "anomalies": nouvelles,
                        "timestamp": datetime.now().isoformat(),
                        "rapport": nom_rapport, "lstm": dernier_lstm,
                        # ← CORRIGÉ (cause du compteur qui chute au
                        # rafraîchissement, ex: 5 -> 4) : un rec_id NÉGATIF
                        # désigne une recommendation créée en repli mémoire
                        # (voir database.py, sauvegarder_recommendation --
                        # PostgreSQL momentanément injoignable). Le
                        # convertir en None faisait apparaître la carte en
                        # direct sans identifiant : impossible à dédupliquer
                        # côté frontend, absente de la base, donc disparue
                        # au premier rafraîchissement -- exactement l'écart
                        # observé entre l'affichage direct et le rechargement.
                        # Un id négatif est pourtant parfaitement utilisable :
                        # marquer_recommendation_resolue() et
                        # supprimer_recommendation() savent déjà le router
                        # vers la mémoire plutôt que vers SQL. Seul 0 (ou
                        # absence) reste sans identifiant.
                        "recommendation_id": rec_id if rec_id != 0 else None,
                    }), loop)

                try:
                    from notifications import envoyer_alerte
                    anomalies_metriques = [a for a in nouvelles if a.get("type") != "ai_score"]
                    anomalies_ai        = [a for a in nouvelles if a.get("type") == "ai_score"]
                    if any(a.get("niveau") == "CRITIQUE" for a in anomalies_metriques):
                        niv_max = "CRITIQUE"
                    elif anomalies_metriques:
                        niv_max = "IMPORTANT"
                    elif any(a.get("niveau") == "CRITIQUE" for a in anomalies_ai):
                        niv_max = "CRITIQUE"
                    else:
                        niv_max = "IMPORTANT"

                    # ← types_dict/anomalie_dominante déjà calculés plus
                    # haut (avant la décision d'appeler ou non le LLM) --
                    # réutilisés ici, plus recalculés une seconde fois.
                    titre_notif = anomalie_dominante.get("message", "Anomalie")[:60]

                    asyncio.run_coroutine_threadsafe(
                        envoyer_alerte(titre=titre_notif, message=analyse, severite=niv_max,
                                       anomalies=nouvelles, etat=etat), loop
                    )
                except Exception as e:
                    print(f"[Notif] {e}")

                try:
                    from database import sauvegarder_anomalie
                    for a in nouvelles:
                        sauvegarder_anomalie(niveau=a.get("niveau","INFO"), message=a.get("message",""),
                                             noeud=a.get("cible"), score=dernier_lstm["score"], rapport=nom_rapport)
                except Exception:
                    pass

            elif nouvelles:
                print(f"[Monitoring] Cooldown ({int(COOLDOWN_RAPPORT_S - (now - dernier_rapport_ts))}s)")

            temps_depuis_reentrainement = time.time() - _dernier_reentrainement_ts
            doit_reentrainer = (
                _analyser and _analyser.lstm_analyseur
                and temps_depuis_reentrainement >= INTERVALLE_MIN_ENTRE_REENTRAINEMENTS_S
                and (dernier_lstm.get("drift") or temps_depuis_reentrainement >= INTERVALLE_REENTRAINEMENT_PERIODIQUE_S)
            )
            if doit_reentrainer:
                _dernier_reentrainement_ts = time.time()
                try:
                    from database import get_echantillons_ml_propres
                    echantillons = get_echantillons_ml_propres(limit=5000)
                    raison_decl = "drift" if dernier_lstm.get("drift") else "periodique (24h)"
                    print(f"[LSTM] Reentrainement declenche ({raison_decl}) -- {len(echantillons)} echantillons propres disponibles")
                    threading.Thread(
                        target=_analyser.declencher_reentrainement_reel,
                        args=(echantillons,), daemon=True
                    ).start()
                except Exception as e:
                    print(f"[LSTM] Erreur declenchement reentrainement: {e}")

            if ws_queue:
                asyncio.run_coroutine_threadsafe(ws_queue.put({
                    "type": "etat_cluster", "etat": etat,
                    "lstm": dernier_lstm, "timestamp": datetime.now().isoformat(),
                }), loop)

            etat_prec = etat

        except Exception as e:
            print(f"[Monitoring] Erreur: {e}")
            import traceback; traceback.print_exc()

        time.sleep(SURVEILLANCE_INTERVAL)


def demarrer(loop: asyncio.AbstractEventLoop):
    threading.Thread(target=_boucle_surveillance, args=(loop,), daemon=True).start()