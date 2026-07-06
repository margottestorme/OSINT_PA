#!/usr/bin/env bash
#
# Lancement fiable de la plateforme OSINT pour la démo (ESGI).
#
#   1. Vérifie que PostgreSQL est joignable
#   2. Applique les migrations (alembic upgrade head)
#   3. Démarre uvicorn avec --reload (recharge à chaud le code modifié)
#   4. Affiche l'URL d'accès
#
# Usage : ./scripts/start_demo.sh
#
set -euo pipefail

# Couleurs
GREEN='\033[92m'; RED='\033[91m'; YELLOW='\033[93m'; CYAN='\033[96m'; BOLD='\033[1m'; RESET='\033[0m'

# Racine du projet = dossier parent de ce script (exécutable depuis n'importe où)
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

# --- 0. Charge .env (pour DATABASE_URL et les clés) sans écraser l'environnement existant ---
if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

# --- 1. PostgreSQL joignable ? ---
echo -e "${CYAN}${BOLD}[1/4] Vérification de PostgreSQL...${RESET}"
PG_HOST="${PGHOST:-localhost}"
PG_PORT="${PGPORT:-5432}"

pg_ok=0
if command -v pg_isready >/dev/null 2>&1; then
    if pg_isready -h "$PG_HOST" -p "$PG_PORT" >/dev/null 2>&1; then pg_ok=1; fi
else
    # Repli : test d'ouverture de socket TCP via /dev/tcp de bash
    if (echo > "/dev/tcp/${PG_HOST}/${PG_PORT}") >/dev/null 2>&1; then pg_ok=1; fi
fi

if [[ "$pg_ok" -ne 1 ]]; then
    echo -e "${RED}✗ PostgreSQL injoignable sur ${PG_HOST}:${PG_PORT}.${RESET}"
    echo -e "${YELLOW}  Démarrez-le, par exemple :${RESET}"
    echo -e "${YELLOW}    docker compose up -d db      # via docker-compose${RESET}"
    echo -e "${YELLOW}    sudo service postgresql start # installation locale${RESET}"
    exit 1
fi
echo -e "${GREEN}✓ PostgreSQL joignable sur ${PG_HOST}:${PG_PORT}${RESET}"

# --- 2. Migrations ---
echo -e "${CYAN}${BOLD}[2/4] Application des migrations (alembic upgrade head)...${RESET}"
if alembic upgrade head; then
    echo -e "${GREEN}✓ Schéma à jour${RESET}"
else
    echo -e "${RED}✗ Échec des migrations alembic — voir la sortie ci-dessus.${RESET}"
    exit 1
fi

# --- 3 & 4. Démarrage uvicorn ---
echo -e "${CYAN}${BOLD}[3/4] Démarrage du serveur (uvicorn --reload)...${RESET}"
echo -e "${GREEN}${BOLD}[4/4] Application accessible sur : ${RESET}${BOLD}http://${HOST}:${PORT}/login${RESET}"
echo -e "${YELLOW}    Identifiants démo : admin / osint2024${RESET}"
echo -e "${YELLOW}    (Ctrl+C pour arrêter)${RESET}\n"

exec python3 -m uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
