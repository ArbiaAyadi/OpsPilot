# ---------------------------------------------------------------------------
# Provisionnement des VMs du cluster OpsPilot
# ---------------------------------------------------------------------------

# for_each plutot que count : avec count les ressources sont indexees par
# POSITION. Retirer la premiere VM decalerait toutes les suivantes, et
# Terraform detruirait puis recreerait tout. Avec for_each, chaque
# ressource est indexee par sa CLE -- erreur frequente et couteuse.
resource "proxmox_virtual_environment_vm" "opspilot" {
  for_each = var.vms

  name        = each.key
  vm_id       = each.value.vmid
  node_name   = each.value.node
  description = each.value.description

  tags = ["opspilot", "terraform"]

  # Clone d'un modele plutot qu'installation depuis ISO : quelques
  # secondes au lieu de minutes, et toutes les VMs partent d'une base
  # identique.
  clone {
    vm_id = try(data.proxmox_virtual_environment_vms.template.vms[0].vm_id, 9000)
    full  = true
  }

  agent {
    # L'agent QEMU permet a Proxmox de connaitre l'IP reelle et d'arreter
    # proprement. Sans lui, "qm shutdown" coupe brutalement.
    enabled = true
  }

  cpu {
    cores = each.value.cores
    # host : expose le jeu d'instructions reel. Meilleures performances
    # que kvm64, mais empeche la migration vers un processeur different.
    type = "host"
  }

  memory {
    dedicated = each.value.memory_mb
    # Ballooning a la moitie de l'allocation. Descendre plus bas risque
    # d'etrangler la VM -- constate sur ce cluster, ou un plancher a
    # 200 Mo avait rendu PostgreSQL injoignable.
    floating = floor(each.value.memory_mb / 2)
  }

  disk {
    datastore_id = var.storage_pool
    interface    = "scsi0"
    size         = each.value.disk_gb
    # discard : les blocs liberes sont rendus au stockage. Sans cela, un
    # disque a provisionnement fin ne fait que croitre.
    discard = "on"
    ssd     = true
  }

  network_device {
    bridge = "vmbr0"
    model  = "virtio"
  }

  initialization {
    datastore_id = var.storage_pool

    ip_config {
      ipv4 {
        address = each.value.ip_address
        gateway = each.value.gateway
      }
    }

    # cloud-init configure la VM au premier demarrage : reseau, cle SSH,
    # utilisateur. C'est ce qui evite toute intervention manuelle.
    user_account {
      username = var.ssh_user
      keys     = var.ssh_public_key != "" ? [var.ssh_public_key] : []
    }
  }

  operating_system {
    type = "l26"
  }

  lifecycle {
    ignore_changes = [
      # Le disque cloud-init est regenere a chaque demarrage. Sans cette
      # exception, Terraform proposerait de recreer la VM a chaque plan.
      initialization[0].user_account,
    ]
  }
}

# Recherche du modele par NOM plutot que par identifiant code en dur :
# reste valable si le modele est recree.
data "proxmox_virtual_environment_vms" "template" {
  filter {
    name   = "name"
    values = [var.template_name]
  }
}