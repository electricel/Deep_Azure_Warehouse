#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"

if [ ! -f ".env" ]; then
  echo "ERROR: .env not found. Copy .env.example to .env and set production secrets first." >&2
  exit 1
fi

if [ ! -d ".git" ]; then
  echo "ERROR: this directory is not a git checkout." >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "ERROR: git was not found." >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker was not found." >&2
  exit 1
fi

timestamp=$(date +"%Y%m%d_%H%M%S")
mkdir -p backups

if [ -d "data" ]; then
  if command -v tar >/dev/null 2>&1; then
    tar -czf "backups/data_before_update_${timestamp}.tgz" data .env
    echo "Backup created: backups/data_before_update_${timestamp}.tgz"
  else
    echo "WARN: tar was not found; skipped automatic data backup."
  fi
fi

git fetch --all --prune
git pull --ff-only

if docker compose version >/dev/null 2>&1; then
  docker compose up -d --build
  docker compose ps
  docker compose logs --tail=80 warehouse
else
  docker-compose up -d --build
  docker-compose ps
  docker-compose logs --tail=80 warehouse
fi

echo "Update complete."
