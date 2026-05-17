param(
  [string]$HostName = "127.0.0.1",
  [int]$Port = 8088,
  [string]$DataDir = "",
  [string]$AdminPassword = "",
  [string]$DataKey = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  throw "Python was not found in PATH. Install Python 3.10+ first."
}

python -c "import cryptography" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "Installing Python dependencies from requirements.txt..."
  python -m pip install -r (Join-Path $Root "requirements.txt")
}

if (-not $DataDir) {
  $DataDir = Join-Path $Root "data"
}

$EnvPath = Join-Path $Root ".env"
if (Test-Path (Join-Path $Root "configure_env.ps1")) {
  powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "configure_env.ps1") -EnvPath $EnvPath -DataDir $DataDir
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
}

if (Test-Path $EnvPath) {
  foreach ($raw in Get-Content $EnvPath) {
    $line = $raw.Trim()
    if (-not $line -or $line.StartsWith("#")) {
      continue
    }
    if ($line.StartsWith("export ")) {
      $line = $line.Substring(7).Trim()
    }
    $index = $line.IndexOf("=")
    if ($index -lt 1) {
      continue
    }
    $key = $line.Substring(0, $index).Trim()
    if ($key -notmatch "^[A-Za-z_][A-Za-z0-9_]*$") {
      continue
    }
    $value = $line.Substring($index + 1).Trim()
    if ($value.Length -ge 2) {
      $first = $value.Substring(0, 1)
      $last = $value.Substring($value.Length - 1, 1)
      if (($first -eq "'" -and $last -eq "'") -or ($first -eq '"' -and $last -eq '"')) {
        $value = $value.Substring(1, $value.Length - 2)
      }
    }
    if (-not [Environment]::GetEnvironmentVariable($key, "Process")) {
      [Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
  }
}

if (-not $AdminPassword) {
  $AdminPassword = $env:WAREHOUSE_ADMIN_PASSWORD
}

if (-not $AdminPassword) {
  throw "WAREHOUSE_ADMIN_PASSWORD is not set. Pass -AdminPassword or set the environment variable before production start."
}

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
if ($insecureAdminPasswords -contains $AdminPassword.Trim().ToLowerInvariant()) {
  throw "WAREHOUSE_ADMIN_PASSWORD is too weak or still uses a default value. Use a private strong password, for example 12+ characters with letters and numbers."
}

if (-not $DataKey) {
  $DataKey = $env:WAREHOUSE_DATA_KEY
}

if (-not $DataKey) {
  throw "WAREHOUSE_DATA_KEY is not set. Generate one and keep it backed up outside the data directory."
}

$env:WAREHOUSE_ENV = "production"
$env:WAREHOUSE_HOST = $HostName
$env:WAREHOUSE_PORT = [string]$Port
$env:WAREHOUSE_DATA_DIR = $DataDir
$env:WAREHOUSE_ADMIN_PASSWORD = $AdminPassword
$env:WAREHOUSE_DATA_ENCRYPTION = "1"
$env:WAREHOUSE_DATA_KEY = $DataKey
$env:WAREHOUSE_TUNNEL = "0"
$env:WAREHOUSE_OPEN_BROWSER = "0"

Write-Host "Starting Warehouse Inventory Server..."
Write-Host "Host: $env:WAREHOUSE_HOST"
Write-Host "Port: $env:WAREHOUSE_PORT"
Write-Host "Data: $env:WAREHOUSE_DATA_DIR"
python (Join-Path $Root "app.py")
