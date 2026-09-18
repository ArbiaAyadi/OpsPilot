# OpsPilot

[![CI](https://github.com/ArbiaAyadi/OpsPilot/actions/workflows/ci.yml/badge.svg)](https://github.com/ArbiaAyadi/OpsPilot/actions/workflows/ci.yml)
[![CD](https://github.com/ArbiaAyadi/OpsPilot/actions/workflows/cd.yml/badge.svg)](https://github.com/ArbiaAyadi/OpsPilot/actions/workflows/cd.yml)

**Supervision d'infrastructure Proxmox VE assistée par intelligence artificielle.**

OpsPilot surveille un cluster Proxmox, détecte les anomalies, en analyse les
causes et propose des actions correctives que l'ingénieur valide avant
exécution.

---

## Le problème

Les outils de supervision classiques signalent qu'un seuil est dépassé.
Ils ne disent pas *pourquoi*, ni *quoi faire*. L'ingénieur reçoit une
alerte « RAM à 95% » et doit reconstruire seul le contexte : quelle VM,
depuis quand, quel service, quelle conséquence.

OpsPilot comble cet écart. Pour chaque anomalie, il produit une analyse
des causes probables et un plan d'action exécutable — tout en laissant la
décision à l'humain.

## Ce qui le distingue

**Aucun seuil écrit à la main.** Les seuils sont générés par un modèle de
langage à partir de la documentation Proxmox et de l'état réel du
cluster. Ils s'adaptent à l'infrastructure au lieu d'être devinés.

**Détection hybride.** Un Isolation Forest opérationnel dès le démarrage,
complété par un autoencodeur LSTM qui apprend le comportement normal de
la machine. Le second affine le premier au fil du temps.

**Human-in-the-loop.** L'agent propose, l'ingénieur juge, le système
exécute et trace. Chaque action est réversible et journalisée.

**Repli déterministe.** Quand le modèle de langage est indisponible, un
moteur de règles prend le relais et produit une analyse structurée à
partir de l'état réel du cluster. Le système ne tombe jamais silencieusement.

---

## Architecture

```
                     Cluster Proxmox VE
                    ┌──────────────────┐
                    │  pve1     pve2   │
                    │   │        │     │
                    │  VM101   VM103   │
                    └───┬────────┬─────┘
                        │        │
              pve_exporter   node_exporter
              postgres_exporter  process-exporter
                        │        │
                    ┌───▼────────▼───┐
                    │   Prometheus   │
                    └────────┬───────┘
                             │
         ┌───────────────────▼────────────────────┐
         │             OpsPilot                   │
         │                                        │
         │  Collecte ──► Détection ──► Analyse    │
         │              (IF + LSTM)     (LLM)     │
         │                                │       │
         │                        Recommandations │
         │                                │       │
         │                        Exécution (SSH  │
         │                        + API Proxmox)  │
         └────────────────┬───────────────────────┘
                          │
                  ┌───────▼────────┐
                  │  PostgreSQL    │
                  │  (historique)  │
                  └────────────────┘
```

**Détail des composants** : voir [`docs/`](docs/) et les en-têtes de
chaque module — chaque fichier documente ses choix de conception.

---

## Démarrage rapide

### Avec Docker Compose

```bash
cp .env.example .env        # puis compléter les secrets
docker compose up -d
```

L'interface est disponible sur `https://localhost:8088`.

Profils optionnels :

```bash
docker compose --profile observability up -d   # + Grafana, Loki
docker compose --profile onprem up -d          # + Ollama (modèle local)
```

### Sur Kubernetes

```bash
# Le Secret se crée explicitement -- jamais versionné
kubectl create namespace opspilot
kubectl create secret generic opspilot-secrets -n opspilot \
  --from-literal=DB_PASSWORD='...' \
  --from-literal=GROQ_API_KEY='...'

kubectl apply -k k8s/overlays/prod
```

Ou via Helm :

```bash
helm install opspilot oci://ghcr.io/arbiaayadi/charts/opspilot \
  --version 1.0.0 -n opspilot --create-namespace \
  --set secrets.data.DB_PASSWORD='...'
```

### En développement local

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cd frontend && npm ci && npm run build && cd ..
python -m agent.main
```

---

## Stack technique

| Couche | Technologie |
|---|---|
| Backend | Python 3.12, FastAPI, WebSocket |
| Frontend | React 18, Vite, Chart.js |
| Base de données | PostgreSQL 15 |
| Détection | scikit-learn (Isolation Forest), PyTorch (LSTM) |
| Intelligence | Groq (Llama, Qwen), repli Mistral et Ollama |
| Métriques | Prometheus, pve_exporter, node_exporter |
| Infrastructure | Proxmox VE 8 |

---

## Pratiques DevOps

Ce projet applique la chaîne complète, chaque outil pour son rôle propre.

| Outil | Rôle | Emplacement |
|---|---|---|
| **GitHub Actions** | Intégration et livraison continues | [`.github/workflows/`](.github/workflows/) |
| **Docker** | Conteneurisation, image multi-étapes | [`Dockerfile`](Dockerfile) |
| **Terraform** | Provisionnement des VMs Proxmox | [`terraform/`](terraform/) |
| **Ansible** | Configuration des nœuds | [`ansible/`](ansible/) |
| **Kubernetes** | Orchestration | [`k8s/`](k8s/) |
| **Helm** | Distribution du chart | [`helm/`](helm/) |
| **ArgoCD** | Déploiement GitOps | [`k8s/gitops/`](k8s/gitops/) |
| **Grafana + Loki** | Tableaux de bord et logs | [`grafana/`](grafana/), [`loki/`](loki/) |

### Intégration continue

Sept contrôles à chaque `push` :

- 235 tests sur Python 3.11 **et** 3.12, contre un vrai PostgreSQL
- Analyse statique (`ruff`)
- Recherche de secrets sur tout l'historique Git (`gitleaks`)
- Vulnérabilités des dépendances (`Trivy`)
- Construction de l'image + vérification qu'elle ne tourne pas en root
- Validation des manifestes Kubernetes (`kubeconform`, `kube-score`)
- Validation du chart Helm (`helm lint`)

### Livraison continue

Déclenchée par une étiquette `v*.*.*` :

- Image publiée sur GHCR avec versionnement sémantique
- **Signature cryptographique sans clé** (cosign + Sigstore)
- **SBOM** au format SPDX
- Rapport de vulnérabilités dans l'onglet Security
- Chart Helm publié comme artefact OCI

Vérifier l'authenticité d'une image :

```bash
cosign verify ghcr.io/arbiaayadi/opspilot:v1.0.0 \
  --certificate-identity-regexp='https://github.com/ArbiaAyadi/OpsPilot/.*' \
  --certificate-oidc-issuer=https://token.actions.githubusercontent.com
```

---

## Sécurité

**Aucun secret n'est versionné.** Le fichier `k8s/base/secret.yaml`
documente la structure attendue avec des valeurs vides ; il est
volontairement exclu des ressources appliquées par Kustomize. Un Secret
Kubernetes n'étant qu'encodé en base64, le committer rempli équivaudrait
à publier un mot de passe en clair.

**Conteneurs non privilégiés.** L'image s'exécute sous l'uid 10001, avec
toutes les capacités Linux retirées et un système de fichiers racine en
lecture seule. La CI vérifie ce point à chaque construction.

**Moindre privilège.** Le `ServiceAccount` dispose d'un `Role` limité au
namespace, en lecture seule, sans aucun droit sur les pods.

**Isolation réseau.** Les `NetworkPolicy` appliquent un refus par défaut,
puis ouvrent explicitement ce qui est nécessaire.

---

## Structure du dépôt

```
.
├── agent/              Backend FastAPI, surveillance, agent LLM
├── frontend/           Interface React
├── tests/              235 tests
├── ansible/            Playbooks de configuration des nœuds
├── terraform/          Provisionnement des VMs Proxmox
├── k8s/
│   ├── base/           22 manifestes Kubernetes
│   ├── overlays/       minikube, dev, prod
│   ├── observability/  ServiceMonitor, PrometheusRule
│   └── gitops/         Application ArgoCD
├── helm/opspilot/      Chart Helm
├── grafana/            Sources de données et tableaux de bord
├── loki/               Agrégation de logs
├── Dockerfile          Image multi-étapes
└── docker-compose.yml  Orchestration locale avec profils
```

---

## Tests

```bash
pytest tests/ -v                          # 235 tests
pytest tests/ --cov=. --cov-report=term   # avec couverture

kubectl kustomize k8s/overlays/prod | kubeconform -strict -    # manifestes
helm lint helm/opspilot                                        # chart
terraform -chdir=terraform validate                            # infrastructure
ansible-playbook ansible/site.yml --check                      # configuration
```

---

## Limites connues

**Haute disponibilité de la base.** PostgreSQL est déployé en réplique
unique. Une vraie haute disponibilité exige un opérateur dédié
(CloudNativePG) qui gère l'élection du primaire. Déclarer trois répliques
donnerait trois bases indépendantes aux données divergentes, pas une base
répliquée.

**Point de métriques.** Le `ServiceMonitor` déclare la collecte sur
`/metrics`, endpoint qui reste à exposer côté application via
`prometheus_client`.

**NetworkPolicy et CNI.** Elles ne sont appliquées que par un plugin
réseau qui les supporte (Calico, Cilium). Flannel les ignore
silencieusement.

---

## Auteur

Arbia Ayadi — Projet de fin d'études.