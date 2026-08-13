"""
surveillance.py — Boucle principale de l'agent.

← AJOUT (format JSON structuré, voir incident_prompt.py) : analyser_anomalie_llm()
retourne maintenant un dict {"markdown": str, "structured": dict} au lieu
d'un simple str. "markdown" est un rendu déterministe (Python, jamais une
2e requête LLM) du JSON structuré, pour ne RIEN casser côté
report_writer.py/notifications.py qui attendaient déjà du texte lisible.
"structured" est le nouveau JSON complet (causes, steps avec action_id
validés, target_node/vmid) transmis en plus au frontend via le websocket
-- PageRecommendations.jsx peut l'utiliser directement, sans plus jamais
deviner un nœud/VMID par regex sur du texte libre.

← AJOUT (ré-escalade du score IA) : la vérification "AI score >= seuil"
n'avait AUCUNE protection contre la répétition, contrairement au reste des
anomalies (voir anomaly_detector.py) -- si le score restait élevé sur
plusieurs cycles (ce qui arrive précisément quand un problème métrique
reste critique longtemps), "nouvelles" n'était jamais vide, redéclenchant
un nouveau rapport/alerte dès la fin de chaque cooldown, en continu. Même
principe palier+ré-escalade que anomaly_detector.py appliqué ici : un
changement de palier (nouveau/désescalade/escalade) déclenche toujours,
un palier CRITIQUE soutenu ré-déclenche après INTERVALLE_REESCALADE_AI_S,
rester dans le même palier IMPORTANT ne redéclenche plus rien entre-temps.

Le reste de ce fichier (fusion Prometheus, hyperviseur, PC hôte, marge
réelle, alertes services...) est inchangé -- voir les commentaires "← AJOUT"
existants ci-dessous pour l'historique de ces sections.
"""
import asyncio
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
from agent.groq_client   import appeler_groq, rate_limiter
from agent.prompts       import system_prompt_surveillance
from agent.anomaly_detector import detecter_anomalies
from agent.rules_engine  import generer_regles_ia, regles_necessitent_regeneration, marquer_etat_accessibilite
from agent.report_writer import sauvegarder_rapport
from agent.etat_normalizer import normaliser_etat
# ← AJOUT : parser_reponse_llm (voir incident_prompt.py -- extrait et
# valide le JSON structuré retourné par le LLM), obtenir_doc_url (rétablit
# le lien "docs" retiré par inadvertance côté frontend)
from agent.incident_prompt import classifier_anomalies, construire_prompt_specifique, parser_reponse_llm, obtenir_doc_url

# ← Alias de compatibilite : agent/routes.py fait
# "from agent.surveillance import _normaliser_etat" -- garde ce nom utilisable
# ici pour ne rien casser ailleurs dans le projet.
_normaliser_etat = normaliser_etat

dernier_etat  = {}
dernier_lstm  = {"score": 0.0, "seuil": 0.5, "drift": False,
                 "score_if": 0.0, "score_lstm": 0.0, "lstm_ready": False}
dernier_rapport_ts = 0.0
ws_queue: asyncio.Queue = None

# ← AJOUT : suivi du palier du score IA d'un cycle à l'autre, pour la
# ré-escalade -- même principe que anomaly_detector._derniere_alerte_par_cle,
# mais localisé ici car cette vérification vit directement dans la boucle
# de surveillance, pas dans detecter_anomalies().
_dernier_ai_niveau = None
_derniere_alerte_ai_ts = 0.0
INTERVALLE_REESCALADE_AI_S = 1800  # 30 min -- cohérent avec anomaly_detector.py


def set_ws_queue(q: asyncio.Queue):
    global ws_queue
    ws_queue = q


def _proxmox_accessible(etat: dict) -> bool:
    """
    Retourne True uniquement si au moins MIN_NOEUDS_ONLINE_POUR_ALERTE noeuds
    sont en ligne. Bloque toutes les alertes si Proxmox est eteint.
    """
    noeuds = etat.get("noeuds", [])
    if not noeuds:
        return False
    online = sum(
        1 for n in noeuds
        if str(n.get("statut", "")).lower() in ("online", "en ligne", "up")
    )
    return online >= MIN_NOEUDS_ONLINE_POUR_ALERTE


def _cle_dedup_anomalie(a: dict) -> tuple:
    """
    Cle de deduplication pour une anomalie du cycle courant. Cas particulier
    "service_down" : deux sources independantes peuvent detecter la MEME
    panne. Toute autre anomalie garde la cle d'origine (cible, message).
    """
    if a.get("type") == "service_down":
        cible   = a.get("cible", "")
        service = cible.split("/")[-1].strip().lower() if cible else a.get("message", "").lower()
        return ("service_down", service)
    return (a.get("cible", ""), a.get("message", "").lower().strip())


def _rendre_markdown(donnees: dict) -> str:
    """
    Convertit le JSON structuré (voir incident_prompt.parser_reponse_llm)
    en texte markdown lisible -- déterministe, en Python, JAMAIS une 2e
    requête au LLM. Sert à report_writer.py (rapports .md sur disque) et
    notifications.py (corps des emails/Ntfy), qui attendaient déjà du
    texte avant ce changement -- leur comportement reste identique, seule
    la SOURCE du texte change.
    """
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
    """
    ← MODIFIÉ : retourne maintenant {"markdown": str, "structured": dict}
    au lieu d'un simple str. Le prompt demande désormais un objet JSON --
    parser_reponse_llm() l'extrait et valide chaque action_id proposé
    contre action_executor.ACTION_META avant de le renvoyer.

    ← AJOUT : "doc_url" injecté dans les données structurées après coup, en
    Python (jamais par le LLM) -- via obtenir_doc_url(dominant), calculée
    de façon déterministe. Rétablit le lien "docs" retiré par inadvertance
    côté PageRecommendations.jsx quand l'ancienne bibliothèque SOLUTIONS
    (qui portait ce lien) a été retirée.
    """
    lstm = dernier_lstm
    # ← MODIFIÉ : construire_prompt_specifique retourne maintenant
    # (prompt, dominant), pas juste prompt.
    prompt, dominant = construire_prompt_specifique(anomalies, etat, lstm)
    loop = asyncio.get_event_loop()
    reponse_brute = await loop.run_in_executor(
        None,
        lambda: appeler_groq(system_prompt_surveillance(etat, lstm), [], prompt, 1400),
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


def _boucle_surveillance(loop: asyncio.AbstractEventLoop):
    global dernier_etat, dernier_lstm, dernier_rapport_ts, _dernier_ai_niveau, _derniere_alerte_ai_ts

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
            elif etat and etat_prec and rate_limiter.slots() >= 5:
                vms_avant    = {v["vmid"] for v in etat_prec.get("vms", [])}
                vms_apres    = {v["vmid"] for v in etat.get("vms", [])}
                noeuds_avant = {n["nom"] for n in etat_prec.get("noeuds", [])}
                noeuds_apres = {n["nom"] for n in etat.get("noeuds", [])}
                if (vms_avant != vms_apres) or (noeuds_avant != noeuds_apres):
                    print("[AI Rules] Changement infrastructure -- regeneration")
                    asyncio.run_coroutine_threadsafe(generer_regles_ia(etat), loop).result(timeout=60)

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
                except Exception as e:
                    print(f"[AI Engine] {e}")

            nouvelles = detecter_anomalies(etat, etat_prec)

            # ← MODIFIÉ (ré-escalade du score IA) : avant, cette vérification
            # ajoutait une entrée à CHAQUE cycle où le score restait au-dessus
            # du seuil, sans aucune protection contre la répétition -- si le
            # score restait élevé longtemps (ce qui arrive précisément quand
            # un problème métrique reste critique), "nouvelles" n'était
            # jamais vide, redéclenchant un nouveau rapport dès la fin de
            # chaque cooldown, en continu. Même principe que
            # anomaly_detector.py : un changement de palier déclenche
            # toujours, un palier CRITIQUE soutenu ré-déclenche après
            # INTERVALLE_REESCALADE_AI_S, rester dans le même palier
            # IMPORTANT entre-temps ne redéclenche plus rien.
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

            now = time.time()
            if nouvelles and (now - dernier_rapport_ts) >= COOLDOWN_RAPPORT_S:
                slots = rate_limiter.slots()
                print(f"[Monitoring] {len(nouvelles)} anomalie(s) -- slots: {slots}")
                if slots >= 5:
                    resultat_llm = asyncio.run_coroutine_threadsafe(
                        analyser_anomalie_llm(nouvelles, etat), loop
                    ).result(timeout=30)
                    analyse    = resultat_llm["markdown"]
                    structured = resultat_llm["structured"]
                else:
                    lignes     = "\n".join(f"- [{a['niveau']}] {a['message']}" for a in nouvelles)
                    analyse    = f"**Anomalies** (quota reserve)\n\n{lignes}"
                    structured = {"_parse_failed": True, "_raw": analyse}

                etat_rapport       = etat if etat and etat.get("noeuds") else dernier_etat
                # ← MODIFIÉ : structured transmis en plus -- report_writer.py
                # construit maintenant le rapport directement depuis le JSON,
                # plus par reparsing fragile du texte markdown.
                nom_rapport        = sauvegarder_rapport(nouvelles, analyse, etat_rapport, dernier_lstm["score"], structured)
                dernier_rapport_ts = now

                if ws_queue:
                    asyncio.run_coroutine_threadsafe(ws_queue.put({
                        "type": "alerte", "role": "assistant",
                        "content": analyse,
                        "structured": structured,
                        "anomalies": nouvelles,
                        "timestamp": datetime.now().isoformat(),
                        "rapport": nom_rapport, "lstm": dernier_lstm,
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

                    types_dict, dominant_type = classifier_anomalies(nouvelles)
                    anomalie_dominante = types_dict[dominant_type][0] if types_dict.get(dominant_type) else nouvelles[0]
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

            if dernier_lstm.get("drift") and _analyser:
                _analyser.reentrainer()

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