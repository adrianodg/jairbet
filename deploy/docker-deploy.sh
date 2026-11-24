#!/usr/bin/env bash
# Deploy (or update) the bot on the server over ssh, from your own machine.
#
# Usage:
#   ./deploy/docker-deploy.sh user@server            # sync code, rebuild, restart
#   ./deploy/docker-deploy.sh user@server --with-db  # first deploy: also copy
#                                                    # the local jairbet.db
# The remote folder defaults to ~/jairbet (override with REMOTE_DIR=...).
# The local .env is copied only if the server has none yet.
set -euo pipefail

blue() { printf '\033[34m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
red() { printf '\033[31m%s\033[0m\n' "$*" >&2; }

HOST="${1:-}"
if [[ -z "$HOST" ]]; then
  red "Usage: $0 user@server [--with-db]"
  exit 1
fi
WITH_DB="${2:-}"
REMOTE_DIR="${REMOTE_DIR:-jairbet}"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

blue "Syncing code to $HOST:$REMOTE_DIR"
ssh "$HOST" "mkdir -p '$REMOTE_DIR/data/backups'"
rsync -az --delete \
  --exclude .git --exclude .venv --exclude .env --exclude data \
  --exclude '*.db' --exclude '*.db-*' --exclude backups \
  --exclude __pycache__ --exclude '*.egg-info' --exclude .pytest_cache \
  --exclude .ruff_cache --exclude 'Jairbet_*.xlsx' \
  "$SOURCE_DIR/" "$HOST:$REMOTE_DIR/"

if ! ssh "$HOST" "test -f '$REMOTE_DIR/.env'"; then
  if [[ -f "$SOURCE_DIR/.env" ]]; then
    blue "Copying .env to the server (first deploy)"
    scp -q "$SOURCE_DIR/.env" "$HOST:$REMOTE_DIR/.env"
    ssh "$HOST" "chmod 600 '$REMOTE_DIR/.env'"
  else
    red "No .env on the server or here. Create $REMOTE_DIR/.env on the server"
    red "from .env.example, then run this script again."
    exit 1
  fi
fi

if [[ "$WITH_DB" == "--with-db" ]]; then
  if ssh "$HOST" "test -f '$REMOTE_DIR/data/jairbet.db'"; then
    yellow "The server already has data/jairbet.db; not overwriting it."
  else
    blue "Copying a consistent snapshot of jairbet.db"
    snapshot="$(mktemp -d)/jairbet.db"
    sqlite3 "$SOURCE_DIR/jairbet.db" ".backup '$snapshot'"
    scp -q "$snapshot" "$HOST:$REMOTE_DIR/data/jairbet.db"
    rm -f "$snapshot"
  fi
fi

blue "Building and starting the container"
ssh "$HOST" "cd '$REMOTE_DIR' && APP_UID=\$(id -u) APP_GID=\$(id -g) \
  docker compose up -d --build && docker image prune -f >/dev/null \
  && docker compose ps"

green "Deployed. Logs: ssh $HOST 'cd $REMOTE_DIR && docker compose logs -f'"
