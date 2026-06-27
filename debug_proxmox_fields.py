"""
debug_proxmox_fields.py — Diagnostic ponctuel
Affiche les cles EXACTES retournees par get_etat_cluster() pour
identifier pourquoi cpu_cores/uptime_h n'arrivent pas jusqu'au frontend.

Usage : python debug_proxmox_fields.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "proxmox"))

from proxmox_api import get_etat_cluster

etat = get_etat_cluster()
print("=" * 70)
print("CLES au niveau racine :", list(etat.keys()))
print("=" * 70)

noeuds = etat.get("noeuds") or etat.get("nodes") or []
if not noeuds:
    print("AUCUN noeud trouve sous 'noeuds' ou 'nodes' — verifier le nom de cle racine ci-dessus.")
else:
    for n in noeuds:
        print(f"\nNoeud: {n.get('nom') or n.get('name') or n.get('node')}")
        print("  Cles disponibles:", list(n.keys()))
        for candidat in ("cpu_cores", "maxcpu", "uptime", "uptime_h"):
            if candidat in n:
                print(f"  TROUVE '{candidat}' = {n[candidat]}")
print("=" * 70)