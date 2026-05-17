#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 was not found in PATH. Install Python 3.10+ first." >&2
  exit 1
fi

if ! python3 -c "import cryptography" >/dev/null 2>&1; then
  echo "Installing Python dependencies from requirements.txt..."
  python3 -m pip install -r "$ROOT/requirements.txt"
fi

: "${WAREHOUSE_ENV:=production}"
: "${WAREHOUSE_HOST:=127.0.0.1}"
: "${WAREHOUSE_PORT:=8088}"
: "${WAREHOUSE_TUNNEL:=0}"
: "${WAREHOUSE_OPEN_BROWSER:=0}"
: "${WAREHOUSE_DATA_DIR:=$ROOT/data}"

if [ -z "${WAREHOUSE_ADMIN_PASSWORD:-}" ]; then
  echo "ERROR: WAREHOUSE_ADMIN_PASSWORD is not set." >&2
  echo "Run: export WAREHOUSE_ADMIN_PASSWORD='YourStrongPassword'" >&2
  exit 1
fi

if [ -z "${WAREHOUSE_DATA_KEY:-}" ]; then
  echo "ERROR: WAREHOUSE_DATA_KEY is not set." >&2
  echo "Run once: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\"" >&2
  exit 1
fi

: "${WAREHOUSE_DATA_ENCRYPTION:=1}"

export WAREHOUSE_ENV WAREHOUSE_HOST WAREHOUSE_PORT WAREHOUSE_TUNNEL WAREHOUSE_OPEN_BROWSER WAREHOUSE_DATA_DIR WAREHOUSE_ADMIN_PASSWORD WAREHOUSE_DATA_ENCRYPTION WAREHOUSE_DATA_KEY

echo "Starting Warehouse Inventory Server..."
echo "Host: $WAREHOUSE_HOST"
echo "Port: $WAREHOUSE_PORT"
echo "Data: $WAREHOUSE_DATA_DIR"
exec python3 "$ROOT/app.py"
