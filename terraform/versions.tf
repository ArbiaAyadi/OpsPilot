# ---------------------------------------------------------------------------
# Terraform - versions et fournisseurs
# ---------------------------------------------------------------------------
# Terraform PROVISIONNE (cree, modifie, detruit des VMs) et tient un
# FICHIER D'ETAT qui memorise ce qu'il a cree.
# Ansible CONFIGURE des machines qui existent deja, et ne memorise rien.
# Enchainement habituel : Terraform cree les VMs, Ansible les configure.

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    proxmox = {
      # bpg/proxmox plutot que telmate/proxmox : maintenu activement,
      # supporte l'API Proxmox 8.x et les jetons API. Le fournisseur
      # telmate n'est plus suivi depuis 2023.
      source  = "bpg/proxmox"
      version = "~> 0.66"
    }
  }

  # BACKEND -- ou est stocke le fichier d'etat. Local par defaut :
  # acceptable en solo, inadapte en equipe (deux apply simultanes
  # corrompent l'etat, et le fichier contient des secrets en clair).
  #
  # En equipe :
  #   backend "s3" {
  #     bucket         = "opspilot-tfstate"
  #     key            = "proxmox/terraform.tfstate"
  #     region         = "eu-west-3"
  #     dynamodb_table = "terraform-locks"
  #     encrypt        = true
  #   }
}

provider "proxmox" {
  endpoint = var.proxmox_endpoint

  # Jeton API, jamais mot de passe : un jeton se revoque sans changer le
  # mot de passe root, et ses droits se limitent au necessaire.
  api_token = "${var.proxmox_token_id}=${var.proxmox_token_secret}"

  # Certificats Proxmox auto-signes par defaut. A passer a false des
  # qu'un certificat valide est en place.
  insecure = var.proxmox_insecure

  ssh {
    # Certaines operations (redimensionnement, clonage entre noeuds) ne
    # passent pas par l'API REST et exigent SSH.
    agent    = false
    username = var.ssh_user
    password = var.ssh_password
  }
}