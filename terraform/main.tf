
  for_each = var.vms

  name        = each.key
  vm_id       = each.value.vmid
  node_name   = each.value.node
  description = each.value.description

  tags = ["opspilot", "terraform"]

  clone {
    vm_id = try(data.proxmox_virtual_environment_vms.template.vms[0].vm_id, 9000)
    full  = true
  }

  agent {
    enabled = true
  }

  cpu {
    cores = each.value.cores
    type = "host"
  }

  memory {
    dedicated = each.value.memory_mb
    floating = floor(each.value.memory_mb / 2)
  }

  disk {
    datastore_id = var.storage_pool
    interface    = "scsi0"
    size         = each.value.disk_gb
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
      initialization[0].user_account,
    ]
  }
}

data "proxmox_virtual_environment_vms" "template" {
  filter {
    name   = "name"
    values = [var.template_name]
  }
}
