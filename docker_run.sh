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

admin_password=$(awk -F= '/^WAREHOUSE_ADMIN_PASSWORD=/{print substr($0, index($0,$2))}' .env | tail -n 1 | sed "s/^['\"]//; s/['\"]$//" | tr '[:upper:]' '[:lower:]')
case "$admin_password" in
  admin|admin123|change-this-strong-password|letmein|password|qwerty|root|123456|12345678)
    echo "ERROR: edit .env and replace WAREHOUSE_ADMIN_PASSWORD with a private strong password."
    exit 1
    ;;
esac

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
