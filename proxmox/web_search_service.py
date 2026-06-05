"""
web_search_service.py — L'agent cherche sur le web comme un vrai AI

Comment ça marche (explication simple) :
  Avant chaque analyse, l'agent cherche sur le web :
    1. La documentation officielle Proxmox (seuils recommandés, bonnes pratiques)
    2. Les erreurs connues qui correspondent aux symptômes détectés
    3. Les solutions de la communauté (forum Proxmox, Reddit, StackOverflow)

  Résultat : l'agent ne se base pas uniquement sur les métriques brutes,
  mais sur une connaissance à jour de l'écosystème Proxmox.

  Ça utilise l'API Anthropic avec le tool "web_search" intégré à Claude,
  donc PAS besoin d'une API Google Search séparée.
"""

import os
import json
import anthropic
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL  = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")

# Cache simple en mémoire pour éviter de chercher la même chose 10x par heure
_cache: dict[str, dict] = {}
CACHE_DUREE_MINUTES = 60  # Renouvelle les recherches toutes les heures

# ══════════════════════════════════════════════════════════════════════════════
# Fonctions de recherche contextuelle
# ══════════════════════════════════════════════════════════════════════════════

def _cache_valide(cle: str) -> bool:
    if cle not in _cache:
        return False
    age = datetime.now() - _cache[cle]["timestamp"]
    return age < timedelta(minutes=CACHE_DUREE_MINUTES)

def _chercher_avec_claude(question: str, contexte_court: str) -> str:
    """
    Utilise Claude avec web_search pour chercher une information précise.
    Retourne un résumé textuel des trouvailles.
    """
    cle = f"{question[:80]}"
    if _cache_valide(cle):
        print(f"[WebSearch] Cache hit: {cle[:50]}...")
        return _cache[cle]["resultat"]

    print(f"[WebSearch] Recherche: {question[:80]}...")

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            system=(
                "Tu es un assistant technique spécialisé Proxmox. "
                "Cherche sur le web et retourne un résumé CONCIS et FACTUEL en français. "
                "Focus : chiffres, seuils, commandes concrètes. Maximum 300 mots. "
                "Cite les sources (forum Proxmox, docs officielles, wiki)."
            ),
            messages=[{"role": "user", "content": question}]
        )

        # Extraire le texte de la réponse
        texte = ""
        for bloc in response.content:
            if bloc.type == "text":
                texte += bloc.text

        resultat = texte.strip() or "Aucun résultat trouvé."

        # Mettre en cache
        _cache[cle] = {"timestamp": datetime.now(), "resultat": resultat}
        return resultat

    except Exception as e:
        print(f"[WebSearch] Erreur: {e}")
        return f"Recherche web indisponible: {e}"


def rechercher_bonnes_pratiques_proxmox() -> str:
    """
    Cherche les seuils recommandés pour un cluster Proxmox sain.
    Exemple : à partir de quel % de CPU faut-il migrer une VM ?
    """
    return _chercher_avec_claude(
        question=(
            "Proxmox VE cluster best practices 2024 2025 : "
            "quels sont les seuils recommandés pour CPU RAM disk par noeud ? "
            "Quand faut-il migrer une VM ? Quels paramètres kernel optimiser ? "
            "Source : documentation officielle Proxmox pve.proxmox.com"
        ),
        contexte_court="bonnes_pratiques"
    )


def rechercher_optimisation_memoire_proxmox() -> str:
    """
    Cherche les optimisations mémoire spécifiques à Proxmox (KSM, ballooning, hugepages).
    """
    return _chercher_avec_claude(
        question=(
            "Proxmox VE memory optimization KSM kernel same-page merging ballooning hugepages "
            "configuration 2024. Comment réduire la consommation RAM des VMs Proxmox ? "
            "Commandes et paramètres /etc/pve/"
        ),
        contexte_court="optim_memoire"
    )


def rechercher_solutions_cpu_eleve(cpu_pct: float, vm_name: str) -> str:
    """
    Si une VM a un CPU élevé, cherche les causes connues et solutions.
    """
    return _chercher_avec_claude(
        question=(
            f"Proxmox VM high CPU usage {cpu_pct:.0f}% causes and solutions. "
            f"CPU steal time Proxmox QEMU KVM. "
            "Comment identifier le processus responsable dans la VM ? "
            "vcpu pinning Proxmox configuration. "
            "Forum Proxmox reddit sysadmin solutions."
        ),
        contexte_court=f"cpu_eleve_{vm_name}"
    )


def rechercher_solutions_ram_elevee(ram_pct: float) -> str:
    """
    Si la RAM du noeud est élevée, cherche les optimisations.
    """
    return _chercher_avec_claude(
        question=(
            f"Proxmox node RAM {ram_pct:.0f}% high memory usage solutions. "
            "swappiness Proxmox hypervisor memory ballooning QEMU. "
            "Proxmox out of memory OOM killer. "
            "Comment libérer de la RAM sur un noeud Proxmox ? "
            "qm set memory balloon commands."
        ),
        contexte_court=f"ram_elevee_{int(ram_pct)}"
    )


def rechercher_solutions_disque_plein(disk_pct: float) -> str:
    """
    Si le disque est presque plein, cherche les solutions de nettoyage Proxmox.
    """
    return _chercher_avec_claude(
        question=(
            f"Proxmox disk {disk_pct:.0f}% full cleanup solutions. "
            "Proxmox clean old backups vzdump logs kernels. "
            "pveam purge unused templates. "
            "Proxmox storage thin provisioning. "
            "Commandes pour libérer de l'espace sur Proxmox VE."
        ),
        contexte_court=f"disk_plein_{int(disk_pct)}"
    )


def rechercher_migration_vm(vm_name: str, cpu_pct: float, ram_pct: float) -> str:
    """
    Cherche quand et comment migrer une VM vers un autre noeud.
    """
    return _chercher_avec_claude(
        question=(
            f"Proxmox live migration VM CPU {cpu_pct:.0f}% RAM {ram_pct:.0f}% "
            "when to migrate VM between nodes. "
            "qm migrate command Proxmox. "
            "Proxmox HA high availability automatic migration. "
            "Conditions et commandes pour migrer une VM Proxmox à chaud."
        ),
        contexte_court=f"migration_{vm_name}"
    )


def rechercher_securite_proxmox() -> str:
    """
    Cherche les bonnes pratiques de sécurité Proxmox récentes.
    """
    return _chercher_avec_claude(
        question=(
            "Proxmox VE security hardening 2024 2025 checklist. "
            "Proxmox firewall configuration. Two factor authentication 2FA. "
            "Proxmox API token permissions. Audit logs. "
            "CVE vulnerabilities Proxmox récentes."
        ),
        contexte_court="securite"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Fonction principale — assembler toutes les recherches pertinentes
# ══════════════════════════════════════════════════════════════════════════════

def enrichir_contexte_pour_analyse(metriques: dict) -> dict:
    """
    Regarde les métriques et décide quelles recherches web lancer.
    Retourne un dictionnaire de contexte documentaire pour le LLM.

    L'agent est intelligent : il ne cherche que ce qui est pertinent.
    Si le CPU est normal → pas de recherche CPU.
    Si le disque est à 90% → recherche solutions disque plein.
    """
    contexte = {}
    noeud = metriques.get("noeud", {})
    vms   = metriques.get("vms", [])

    print("[WebSearch] Analyse des métriques pour cibler les recherches...")

    # Toujours : bonnes pratiques générales (mise en cache 1h)
    contexte["bonnes_pratiques"] = rechercher_bonnes_pratiques_proxmox()

    # CPU noeud élevé ?
    cpu_noeud = noeud.get("cpu_pct", 0)
    if cpu_noeud > 70:
        print(f"[WebSearch] CPU noeud élevé ({cpu_noeud}%) → recherche solutions")
        contexte["cpu_noeud"] = rechercher_solutions_cpu_eleve(cpu_noeud, "proxmox-node")

    # RAM noeud élevée ?
    ram_noeud = noeud.get("ram_pct", 0)
    if ram_noeud > 75:
        print(f"[WebSearch] RAM noeud élevée ({ram_noeud}%) → recherche optimisation")
        contexte["ram_noeud"] = rechercher_solutions_ram_elevee(ram_noeud)
        contexte["optim_memoire"] = rechercher_optimisation_memoire_proxmox()

    # Disque presque plein ?
    disk_noeud = noeud.get("disk_pct", 0)
    if disk_noeud > 80:
        print(f"[WebSearch] Disque {disk_noeud}% → recherche nettoyage")
        contexte["disque_plein"] = rechercher_solutions_disque_plein(disk_noeud)

    # VMs avec CPU ou RAM élevés ?
    for vm in vms:
        if not vm.get("disponible"):
            continue
        cpu_vm = vm.get("cpu_pct", 0)
        ram_vm = vm.get("ram_pct", 0)
        vm_name = vm.get("vm_name", "vm")

        if cpu_vm > 80:
            print(f"[WebSearch] {vm_name} CPU {cpu_vm}% → recherche migration")
            contexte[f"migration_{vm_name}"] = rechercher_migration_vm(vm_name, cpu_vm, ram_vm)

        if cpu_vm > 90:
            contexte[f"cpu_{vm_name}"] = rechercher_solutions_cpu_eleve(cpu_vm, vm_name)

    print(f"[WebSearch] Contexte enrichi : {len(contexte)} recherches effectuées")
    return contexte


def formater_contexte_pour_prompt(contexte: dict) -> str:
    """
    Formate le contexte web en texte lisible pour l'injection dans le prompt LLM.
    """
    if not contexte:
        return "Aucun contexte web disponible."

    lignes = ["=== DOCUMENTATION ET BONNES PRATIQUES (sources web) ===\n"]
    for cle, contenu in contexte.items():
        titre = cle.replace("_", " ").title()
        lignes.append(f"--- {titre} ---")
        lignes.append(contenu)
        lignes.append("")

    return "\n".join(lignes)


# Test standalone
if __name__ == "__main__":
    print("[TEST] Recherche web service...")
    # Simuler des métriques avec RAM élevée
    metriques_test = {
        "noeud": {"cpu_pct": 45, "ram_pct": 88, "disk_pct": 72},
        "vms": [
            {"disponible": True, "vm_name": "vm-linux-1", "cpu_pct": 91, "ram_pct": 65},
            {"disponible": True, "vm_name": "vm-linux-2", "cpu_pct": 30, "ram_pct": 40},
        ]
    }
    contexte = enrichir_contexte_pour_analyse(metriques_test)
    print("\n" + formater_contexte_pour_prompt(contexte))