# ---------------------------------------------------------------------------
# Sorties
# ---------------------------------------------------------------------------

output "vm_addresses" {
  description = "Adresses IP des VMs provisionnees"
  value = {
    for nom, vm in proxmox_virtual_environment_vm.opspilot :
    nom => split("/", var.vms[nom].ip_address)[0]
  }
}

output "vm_ids" {
  description = "Identifiants Proxmox des VMs"
  value = {
    for nom, vm in proxmox_virtual_environment_vm.opspilot :
    nom => vm.vm_id
  }
}

# Jonction entre les deux outils : Terraform cree les machines et sait ou
# elles sont, Ansible les configure. Generer l'inventaire evite de le
# tenir a jour a la main -- et donc de le laisser diverger.
#
#   terraform output -raw ansible_inventory > ../ansible/inventory-genere.ini
output "ansible_inventory" {
  description = "Inventaire Ansible au format INI"
  value       = <<-EOT
    # Genere par Terraform -- ne pas editer a la main
    [guest_vms]
    %{for nom, vm in var.vms~}
    ${nom} ansible_host=${split("/", vm.ip_address)[0]}
    %{endfor~}

    [guest_vms:vars]
    ansible_user=${var.ssh_user}
  EOT
}