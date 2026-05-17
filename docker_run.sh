#!/usr/bin/env sh
set -eu

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example."
  echo "Edit .env and set WAREHOUSE_ADMIN_PASSWORD before running again."
  exit 1
fi

if grep -Eq '^WAREHOUSE_ADMIN_PASSWORD=change-this-strong-password\s*$' .env; then
  echo "ERROR: edit .env and replace WAREHOUSE_ADMIN_PASSWORD with a strong password."
  exit 1
fi

if grep -Eq '^WAREHOUSE_DATA_KEY=change-this-32-byte-data-encryption-key\s*$' .env; then
  echo "ERROR: edit .env and replace WAREHOUSE_DATA_KEY with a generated data encryption key."
  exit 1
fi

mkdir -p data

if docker compose version >/dev/null 2>&1; then
  docker compose up -d --build
else
  docker-compose up -d --build
fi

echo "Warehouse Inventory is starting."
echo "Open: http://SERVER_IP:${WAREHOUSE_PORT:-8088}"
