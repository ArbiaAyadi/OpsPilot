# ---------------------------------------------------------------------------
# OpsPilot - point d'entree unique
# ---------------------------------------------------------------------------
# Un Makefile remplit deux roles, et le second est le plus important :
#
#   Il RACCOURCIT les commandes longues -- "make k8s-valider" au lieu de
#   trois commandes chainees avec leurs options.
#
#   Il DOCUMENTE ce qu'on peut faire sur le projet. "make help" repond a
#   la question qu'un nouvel arrivant se pose en premier : par ou
#   commencer ? Un depot sans point d'entree oblige a lire la CI pour
#   deviner les commandes.
#
# Usage : make help

.DEFAULT_GOAL := help
SHELL := /bin/bash

IMAGE      ?= opspilot
TAG        ?= local
NAMESPACE  ?= opspilot
OVERLAY    ?= minikube

# .PHONY declare les cibles qui ne produisent pas de fichier du meme nom.
# Sans cela, "make test" ne ferait rien si un fichier "test" existait --
# make croirait la cible deja a jour.
.PHONY: help install test test-cov lint format \
        docker-build docker-up docker-down docker-logs \
        k8s-valider k8s-deployer k8s-etat k8s-logs k8s-supprimer \
        helm-lint helm-rendre \
        tf-init tf-valider tf-plan \
        ansible-verifier ansible-appliquer \
        tout-valider nettoyer

help:  ## Afficher cette aide
	@echo "OpsPilot - commandes disponibles"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Variables : IMAGE=$(IMAGE) TAG=$(TAG) NAMESPACE=$(NAMESPACE) OVERLAY=$(OVERLAY)"

# --- Developpement ---------------------------------------------------------

install:  ## Installer les dependances Python et frontend
	pip install torch --index-url https://download.pytorch.org/whl/cpu
	pip install -r requirements.txt
	cd frontend && npm ci

test:  ## Lancer les 235 tests
	pytest tests/ -v

test-cov:  ## Tests avec rapport de couverture
	pytest tests/ --cov=. --cov-report=term-missing --cov-report=html
	@echo "Rapport HTML : htmlcov/index.html"

lint:  ## Analyse statique du code Python
	@echo "== Erreurs bloquantes =="
	ruff check . --select=E9,F63,F7,F82
	@echo "== Style (informatif) =="
	@ruff check . --statistics || true

format:  ## Reformater le code
	ruff format .
	terraform -chdir=terraform fmt

# --- Docker ----------------------------------------------------------------

docker-build:  ## Construire l'image
	docker build -t $(IMAGE):$(TAG) .

docker-up:  ## Demarrer la pile locale
	docker compose up -d
	@echo "Interface : https://localhost:8088"

docker-down:  ## Arreter la pile locale
	docker compose down

docker-logs:  ## Suivre les logs
	docker compose logs -f app

# --- Kubernetes ------------------------------------------------------------

k8s-valider:  ## Valider tous les manifestes et overlays
	@for env in base overlays/minikube overlays/dev overlays/prod; do \
		echo "== $$env =="; \
		kubectl kustomize k8s/$$env \
			| kubeconform -summary -strict -kubernetes-version 1.31.0 -ignore-missing-schemas -; \
	done

k8s-deployer:  ## Deployer sur le cluster (OVERLAY=minikube par defaut)
	kubectl apply -k k8s/overlays/$(OVERLAY)
	kubectl rollout status deployment/opspilot-app -n $(NAMESPACE) --timeout=5m

k8s-etat:  ## Etat des ressources deployees
	@kubectl get all,pvc,networkpolicy -n $(NAMESPACE)
	@echo ""
	@kubectl describe resourcequota -n $(NAMESPACE) 2>/dev/null || true

k8s-logs:  ## Suivre les logs de l'application
	kubectl logs -n $(NAMESPACE) -l app.kubernetes.io/component=backend -f --tail=100

k8s-supprimer:  ## Retirer l'application (les PVC survivent volontairement)
	kubectl delete -k k8s/overlays/$(OVERLAY)
	@echo "Les PersistentVolumeClaim sont conserves -- supprimer un objet"
	@echo "ne doit jamais detruire des donnees. Pour les retirer :"
	@echo "  kubectl delete pvc --all -n $(NAMESPACE)"

# --- Helm ------------------------------------------------------------------

helm-lint:  ## Valider le chart
	helm lint helm/opspilot

helm-rendre:  ## Rendre le chart et valider le YAML produit
	helm template opspilot helm/opspilot -f helm/opspilot/values-minikube.yaml \
		| kubeconform -summary -strict -kubernetes-version 1.31.0 -ignore-missing-schemas -

# --- Terraform -------------------------------------------------------------

tf-init:  ## Initialiser Terraform
	terraform -chdir=terraform init

tf-valider:  ## Valider la configuration
	terraform -chdir=terraform fmt -check
	terraform -chdir=terraform validate

tf-plan:  ## Simuler les changements (exige un Proxmox joignable)
	terraform -chdir=terraform plan

# --- Ansible ---------------------------------------------------------------

ansible-verifier:  ## Simuler le playbook sans rien modifier
	ansible-playbook -i ansible/inventory.ini ansible/site.yml --check

ansible-appliquer:  ## Appliquer la configuration aux noeuds
	ansible-playbook -i ansible/inventory.ini ansible/site.yml

# --- Tout ------------------------------------------------------------------

tout-valider: lint test k8s-valider helm-lint tf-valider  ## Toutes les validations
	@echo ""
	@echo "Toutes les validations sont passees."

nettoyer:  ## Supprimer les fichiers generes
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache htmlcov .coverage coverage.xml
	rm -rf frontend/node_modules dist