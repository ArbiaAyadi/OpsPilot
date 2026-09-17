# ---------------------------------------------------------------------------
# Variables d'entree
# ---------------------------------------------------------------------------
# Aucune valeur sensible n'a de defaut : Terraform echoue alors avec un
# message clair. Un defaut sur un secret est le defaut corrige dans
# database.py, ou le mot de passe figurait dans le code source.

variable "proxmox_endpoint" {
  description = "URL de l'API Proxmox VE"
  type        = string
  default     = "https://192.168.138.100:8006/"
}

variable "proxmox_token_id" {
  description = "Identifiant du jeton API (utilisateur@realm!nom)"
  type        = string
}

variable "proxmox_token_secret" {
  description = "Secret du jeton API"
  type        = string
  # sensitive masque la valeur dans TOUTES les sorties : plan, apply,
  # output, et les logs de CI. Sans cela le secret apparait en clair
  # dans l'historique du pipeline.
  sensitive = true
}

variable "proxmox_insecure" {
  description = "Accepter un certificat TLS auto-signe"
  type        = bool
  default     = true
}

variable "ssh_user" {
  description = "Utilisateur SSH sur les noeuds Proxmox"
  type        = string
  default     = "root"
}

variable "ssh_password" {
  description = "Mot de passe SSH"
  type        = string
  sensitive   = true
}

variable "ssh_public_key" {
  description = "Cle publique injectee dans les VMs creees"
  type        = string
  default     = ""
}

# Une MAP plutot qu'une liste : ajouter une VM ne decale pas les autres.
# Avec une liste, inserer un element ferait croire a Terraform que toutes
# les suivantes ont change -- il les detruirait et recreerait.
variable "vms" {
  description = "VMs a provisionner sur le cluster Proxmox"
  type = map(object({
    vmid        = number
    node        = string
    cores       = number
    memory_mb   = number
    disk_gb     = number
    ip_address  = string
    gateway     = string
    description = string
  }))

  default = {
    "linux-vm1" = {
      vmid        = 101
      node        = "pve1"
      cores       = 1
      memory_mb   = 1024
      disk_gb     = 34
      ip_address  = "192.168.138.133/24"
      gateway     = "192.168.138.1"
      description = "Base PostgreSQL + exportateurs Prometheus"
    }
    "linux-vm2" = {
      vmid        = 103
      node        = "pve2"
      cores       = 1
      memory_mb   = 1024
      disk_gb     = 34
      ip_address  = "192.168.138.137/24"
      gateway     = "192.168.138.1"
      description = "Docker : Prometheus, Alertmanager"
    }
  }
}

variable "template_name" {
  description = "Modele de VM a cloner (image cloud Debian/Ubuntu)"
  type        = string
  default     = "debian-12-cloudinit"
}

variable "storage_pool" {
  description = "Pool de stockage Proxmox"
  type        = string
  default     = "local-lvm"
}