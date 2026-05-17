$ErrorActionPreference = "Stop"

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "Created .env from .env.example."
  Write-Host "Edit .env and set WAREHOUSE_ADMIN_PASSWORD before running again."
  exit 1
}

$envText = Get-Content ".env" -Raw
if ($envText -match "(?m)^WAREHOUSE_ADMIN_PASSWORD=change-this-strong-password\s*$") {
  throw "Edit .env and replace WAREHOUSE_ADMIN_PASSWORD with a strong password."
}

$adminMatch = [regex]::Match($envText, "(?m)^WAREHOUSE_ADMIN_PASSWORD=(.+?)\s*$")
if ($adminMatch.Success) {
  $adminPassword = $adminMatch.Groups[1].Value.Trim().Trim("'").Trim('"')
  $insecureAdminPasswords = @(
    "admin",
    "admin123",
    "change-this-strong-password",
    "letmein",
    "password",
    "qwerty",
    "root",
    "123456",
    "12345678"
  )
  if ($insecureAdminPasswords -contains $adminPassword.ToLowerInvariant()) {
    throw "Edit .env and replace WAREHOUSE_ADMIN_PASSWORD with a private strong password."
  }
}

if ($envText -match "(?m)^WAREHOUSE_DATA_KEY=change-this-32-byte-data-encryption-key\s*$") {
  throw "Edit .env and replace WAREHOUSE_DATA_KEY with a generated data encryption key."
}

New-Item -ItemType Directory -Force -Path "data" | Out-Null

$compose = docker compose version 2>$null
if ($LASTEXITCODE -eq 0) {
  docker compose up -d --build
} else {
  docker-compose up -d --build
}

Write-Host "Warehouse Inventory is starting."
Write-Host "Open: http://SERVER_IP:8088"
