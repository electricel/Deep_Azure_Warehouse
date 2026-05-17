$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Test-Path ".env")) {
  throw ".env not found. Copy .env.example to .env and set production secrets first."
}

if (-not (Test-Path ".git")) {
  throw "This directory is not a git checkout."
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  throw "git was not found."
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  throw "docker was not found."
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
New-Item -ItemType Directory -Force -Path "backups" | Out-Null

if (Test-Path "data") {
  $backup = Join-Path "backups" "data_before_update_$timestamp.zip"
  $paths = @("data")
  if (Test-Path ".env") {
    $paths += ".env"
  }
  Compress-Archive -Path $paths -DestinationPath $backup -Force
  Write-Host "Backup created: $backup"
}

git fetch --all --prune
git pull --ff-only

docker compose version *> $null
if ($LASTEXITCODE -eq 0) {
  docker compose up -d --build
  docker compose ps
  docker compose logs --tail=80 warehouse
} else {
  docker-compose up -d --build
  docker-compose ps
  docker-compose logs --tail=80 warehouse
}

Write-Host "Update complete."
