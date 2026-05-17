param(
  [string]$EnvPath = "",
  [string]$DataDir = ""
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $EnvPath) {
  $EnvPath = Join-Path $Root ".env"
}
if (-not $DataDir) {
  $DataDir = Join-Path $Root "data"
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

function Read-EnvFile([string]$Path) {
  $values = [ordered]@{}
  if (-not (Test-Path $Path)) {
    return $values
  }
  foreach ($raw in Get-Content $Path) {
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
    $values[$key] = $value
  }
  return $values
}

function Save-EnvFile([string]$Path, [System.Collections.IDictionary]$Values) {
  $order = @(
    "WAREHOUSE_ENV",
    "WAREHOUSE_PORT",
    "WAREHOUSE_ADMIN_USER",
    "WAREHOUSE_ADMIN_PASSWORD",
    "WAREHOUSE_DATA_ENCRYPTION",
    "WAREHOUSE_DATA_KEY"
  )
  $lines = New-Object System.Collections.Generic.List[string]
  $seen = @{}
  foreach ($key in $order) {
    if ($Values.Contains($key)) {
      $lines.Add("$key=$($Values[$key])")
      $seen[$key] = $true
    }
  }
  foreach ($key in $Values.Keys) {
    if (-not $seen.ContainsKey($key)) {
      $lines.Add("$key=$($Values[$key])")
    }
  }
  Set-Content -Path $Path -Value $lines -Encoding UTF8
}

function Read-PlainSecret([string]$Prompt) {
  $secure = Read-Host $Prompt -AsSecureString
  $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
  try {
    return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
  }
  finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
  }
}

function Test-WeakAdminPassword([string]$Password) {
  if (-not $Password) {
    return $true
  }
  return $insecureAdminPasswords -contains $Password.Trim().ToLowerInvariant()
}

function New-DataKey {
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $bytes = New-Object byte[] 32
    $rng.GetBytes($bytes)
    return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
  }
  finally {
    $rng.Dispose()
  }
}

$envValues = Read-EnvFile $EnvPath
$changed = $false

if (-not $envValues.Contains("WAREHOUSE_ENV") -or -not $envValues["WAREHOUSE_ENV"]) {
  $envValues["WAREHOUSE_ENV"] = "production"
  $changed = $true
}
if (-not $envValues.Contains("WAREHOUSE_PORT") -or -not $envValues["WAREHOUSE_PORT"]) {
  $envValues["WAREHOUSE_PORT"] = "8088"
  $changed = $true
}
if (-not $envValues.Contains("WAREHOUSE_ADMIN_USER") -or -not $envValues["WAREHOUSE_ADMIN_USER"]) {
  $envValues["WAREHOUSE_ADMIN_USER"] = "admin"
  $changed = $true
}
if (-not $envValues.Contains("WAREHOUSE_DATA_ENCRYPTION") -or -not $envValues["WAREHOUSE_DATA_ENCRYPTION"]) {
  $envValues["WAREHOUSE_DATA_ENCRYPTION"] = "1"
  $changed = $true
}

while (-not $envValues.Contains("WAREHOUSE_ADMIN_PASSWORD") -or (Test-WeakAdminPassword $envValues["WAREHOUSE_ADMIN_PASSWORD"])) {
  Write-Host ""
  Write-Host "Set admin password for account: admin"
  Write-Host "The password will be saved in local .env and will not be uploaded to Git."
  $password = Read-PlainSecret "Admin password"
  $confirm = Read-PlainSecret "Confirm admin password"
  if ($password -ne $confirm) {
    Write-Host "ERROR: passwords do not match."
    continue
  }
  if (Test-WeakAdminPassword $password) {
    Write-Host "ERROR: password is too weak or still uses a default value."
    continue
  }
  $envValues["WAREHOUSE_ADMIN_PASSWORD"] = $password
  $changed = $true
}

$dataKeyIsMissing = -not $envValues.Contains("WAREHOUSE_DATA_KEY") -or -not $envValues["WAREHOUSE_DATA_KEY"]
$dataKeyIsPlaceholder = $envValues.Contains("WAREHOUSE_DATA_KEY") -and $envValues["WAREHOUSE_DATA_KEY"] -in @(
  "change-this-32-byte-data-encryption-key",
  "replace-with-generated-secret"
)
if ($dataKeyIsMissing -or $dataKeyIsPlaceholder) {
  $encryptedDb = Join-Path $DataDir "inventory.db.enc"
  if (Test-Path $encryptedDb) {
    Write-Host ""
    Write-Host "Existing encrypted data was found."
    Write-Host "Enter the original WAREHOUSE_DATA_KEY used by this data directory."
    $envValues["WAREHOUSE_DATA_KEY"] = Read-PlainSecret "Data encryption key"
  }
  else {
    $envValues["WAREHOUSE_DATA_KEY"] = New-DataKey
    Write-Host ""
    Write-Host "Generated and saved a new WAREHOUSE_DATA_KEY in local .env."
  }
  $changed = $true
}

if ($changed -or -not (Test-Path $EnvPath)) {
  Save-EnvFile $EnvPath $envValues
  Write-Host "Saved local runtime configuration: $EnvPath"
}
else {
  Write-Host "Loaded local runtime configuration: $EnvPath"
}

Write-Host "Keep .env backed up securely. Losing WAREHOUSE_DATA_KEY means encrypted data cannot be recovered."
