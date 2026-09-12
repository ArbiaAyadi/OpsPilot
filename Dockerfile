# ------------------------------------------------------------------------------
# OpsPilot -- Image de production
# ------------------------------------------------------------------------------
# Construction en DEUX ÉTAPES (multi-stage). L'intérêt n'est pas cosmétique :
# l'étape 1 a besoin de Node.js et des ~200 Mo de node_modules pour compiler
# le frontend React, mais l'image finale n'a besoin QUE du résultat compilé.
# Une image mono-étape embarquerait Node, npm et node_modules inutilement --
# plus lourde à transférer, et surtout une surface d'attaque plus large
# (chaque paquet npm présent dans l'image finale est une vulnérabilité
# potentielle scannée et à corriger).
#
# Construction : docker build -t opspilot:latest .
# Exécution    : voir docker-compose.yml (les secrets et certificats sont
#                montés depuis l'extérieur, jamais dans l'image)

# --------------------------------------------------------------------------
# ÉTAPE 1 -- Compilation du frontend React
# --------------------------------------------------------------------------
FROM node:20-alpine AS frontend

WORKDIR /build

# package.json copié SEUL d'abord, avant le code source. Docker met en
# cache chaque couche : tant que les dépendances ne changent pas, cette
# couche est réutilisée et npm ci est ignoré. Copier tout le code d'un
# coup invaliderait le cache à chaque modification d'un composant React,
# et relancerait une installation complète de npm à chaque build.
COPY frontend/package*.json ./

# npm ci plutôt que npm install : il installe EXACTEMENT les versions du
# package-lock.json, sans jamais le modifier. C'est ce qui garantit qu'un
# build d'aujourd'hui et un build dans six mois produisent le même
# résultat -- npm install, lui, peut récupérer des versions plus récentes
# et introduire une régression silencieuse.
RUN npm ci

COPY frontend/ ./

# <- CORRIGE (echec reel : "/build/dist: not found" alors que npm run build
# reussissait) : le chemin de sortie etait code en dur sur "dist/", la
# valeur par defaut de Vite. Mais vite.config.js peut definir un outDir
# different (build/, ../static/, ...) -- et ce Dockerfile n'a aucune
# raison de dependre d'un detail de configuration du frontend.
#
# On localise donc la sortie par son CONTENU plutot que par son chemin :
# le repertoire produit contient forcement un index.html. Il est ensuite
# copie vers /sortie, emplacement fixe connu de l'etape suivante.
#
# Le chemin trouve est affiche dans les logs du build : utile pour
# comprendre ce qui a ete produit, sans avoir a deviner. Et le message
# d'erreur explicite evite un echec cryptique deux etapes plus loin,
# comme celui rencontre.
RUN npm run build \
 && CHEMIN=$(find /build -maxdepth 4 -name index.html \
        -not -path "*/node_modules/*" -not -path "/build/index.html" \
        2>/dev/null | head -1) \
 && if [ -z "$CHEMIN" ]; then \
        echo "ERREUR: npm run build n'a produit aucun index.html dans /build" >&2; \
        echo "Contenu de /build :" >&2; ls -la /build >&2; \
        exit 1; \
    fi \
 && REPERTOIRE=$(dirname "$CHEMIN") \
 && echo "Frontend compile trouve dans : $REPERTOIRE" \
 && mkdir -p /sortie && cp -r "$REPERTOIRE"/. /sortie/ \
 && echo "Fichiers copies :" && ls /sortie

# --------------------------------------------------------------------------
# ÉTAPE 2 -- Image finale Python
# --------------------------------------------------------------------------
# slim plutôt qu'alpine pour Python : les paquets scientifiques (numpy,
# scikit-learn, torch) publient des binaires précompilés pour glibc, pas
# pour la musl d'alpine. Sur alpine, pip devrait tout recompiler depuis
# les sources -- plusieurs dizaines de minutes de build, et un gcc à
# installer dans l'image.
FROM python:3.12-slim AS runtime

# Variables d'environnement de build uniquement -- aucun secret ici.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# libpq5 : bibliothèque cliente PostgreSQL requise à l'exécution par
# psycopg2-binary. Les outils de compilation ne sont PAS installés :
# toutes nos dépendances ont des binaires précompilés, donc les inclure
# ne servirait qu'à alourdir l'image et à élargir la surface d'attaque.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

# PyTorch AVANT requirements.txt, depuis l'index CPU. Sans cette ligne,
# pip récupère la variante GPU depuis PyPI : plus de 2 Go de
# bibliothèques CUDA NVIDIA, pour un modèle LSTM qui tourne très bien sur
# processeur. Voir le commentaire correspondant dans requirements.txt --
# la variante ne peut pas être choisie depuis ce fichier de façon fiable.
RUN pip install --no-cache-dir torch \
        --index-url https://download.pytorch.org/whl/cpu

# requirements.txt copié seul avant le code, même raisonnement de cache
# que pour package.json ci-dessus : les dépendances changent rarement, le
# code souvent.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code applicatif
COPY agent/ ./agent/
COPY *.py ./

# Frontend compilé, récupéré depuis l'étape 1 -- sans Node ni node_modules
COPY --from=frontend /sortie ./frontend/dist

# Répertoires que l'application écrit à l'exécution. Créés ici avec les
# bons droits : sans cela, l'utilisateur non privilégié ci-dessous ne
# pourrait pas y écrire, et la génération de rapports échouerait.
RUN mkdir -p rapports fonts

# -- Utilisateur non privilégié -----------------------------------------
# Par défaut, un conteneur s'exécute en root. Si une faille permettait
# une évasion du conteneur, l'attaquant serait root sur l'hôte. Un
# utilisateur dédié sans privilèges réduit fortement cette conséquence --
# c'est une exigence de base pour toute image destinée à la production,
# et le premier point que relève un audit de sécurité.
RUN useradd --create-home --shell /bin/bash --uid 10001 opspilot \
    && chown -R opspilot:opspilot /app
USER opspilot

EXPOSE 8088

# Contrôle de santé : permet à Docker et à Kubernetes de savoir si
# l'application répond vraiment, et pas seulement si le processus existe.
# --insecure parce que l'application sert en HTTPS avec un certificat
# auto-signé ; --fail pour qu'un code HTTP d'erreur fasse échouer la
# commande au lieu de renvoyer un succès avec un corps d'erreur.
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl --fail --insecure https://localhost:8088/api/status || \
        curl --fail http://localhost:8088/api/status || exit 1

CMD ["python", "-m", "agent.main"]