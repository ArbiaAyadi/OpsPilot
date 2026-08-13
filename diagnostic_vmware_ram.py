"""
diagnostic_vmware_attribution.py — Script autonome, PAS à intégrer au projet.
But : trouver un moyen fiable d'associer chaque processus vmware-vmx.exe à
son nom de VM (pve1/pve2) -- CommandLine vide via WMI/psutil (déjà testé,
confirmé vide). Deux pistes différentes testées ici en une fois :

  1. vmrun.exe list -- liste les VMs en cours avec leur chemin .vmx complet
     (dont le nom de dossier révèle le nom de la VM). Chemin de vmrun.exe
     déduit de celui de vmware.exe, déjà confirmé dans la conversation :
     "C:\\Program Files (x86)\\VMware\\VMware Workstation\\vmware.exe"

  2. psutil .open_files() par PID -- les fichiers qu'un processus
     vmware-vmx.exe garde ouverts pendant qu'il tourne (.vmdk, .nvram,
     .log...) devraient se trouver dans SON PROPRE dossier de VM, donc
     révéler indirectement à quelle VM appartient ce PID précis.

Si l'une des deux pistes donne un résultat exploitable, ça débloque à la
fois l'attribution CPU par VM ET la localisation des fichiers .vmdk pour
mesurer leur taille réelle sur le disque physique.

Lance simplement :
    python diagnostic_vmware_attribution.py
"""
import subprocess
import psutil

print("═══ Piste 1 : vmrun list (VMs en cours, avec leur chemin .vmx) ═══\n")
chemins_vmrun_possibles = [
    r"C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe",
    r"C:\Program Files\VMware\VMware Workstation\vmrun.exe",
]
vmrun_trouve = False
for chemin in chemins_vmrun_possibles:
    try:
        resultat = subprocess.run([chemin, "list"], capture_output=True, text=True, timeout=10)
        print(f"(vmrun trouvé : {chemin})")
        print(resultat.stdout or "(sortie vide)")
        if resultat.stderr:
            print(f"stderr: {resultat.stderr}")
        vmrun_trouve = True
        break
    except FileNotFoundError:
        continue
if not vmrun_trouve:
    print("vmrun.exe introuvable aux emplacements habituels. Cherche-le manuellement :")
    print(r'  Get-ChildItem "C:\Program Files*\VMware" -Recurse -Filter vmrun.exe')

print("\n═══ Piste 2 : fichiers ouverts par chaque vmware-vmx.exe ═══\n")
trouve = False
for proc in psutil.process_iter(["pid", "name"]):
    if (proc.info.get("name") or "").lower() != "vmware-vmx.exe":
        continue
    trouve = True
    pid = proc.info["pid"]
    print(f"PID {pid}:")
    try:
        p = psutil.Process(pid)
        fichiers = p.open_files()
        if not fichiers:
            print("  (aucun fichier ouvert visible)")
        for f in fichiers[:20]:
            print(f"  {f.path}")
    except Exception as e:
        print(f"  Erreur: {e}")
    print()

if not trouve:
    print("Aucun processus vmware-vmx.exe trouvé.")