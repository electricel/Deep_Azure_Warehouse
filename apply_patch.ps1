param(
  [Parameter(Mandatory = $true)]
  [string]$PatchZip,
  [string]$AppDir = "",
  [string]$HostName = "127.0.0.1",
  [int]$Port = 8088,
  [string]$DataDir = "",
  [string]$AdminPassword = "",
  [int]$HealthTimeoutSeconds = 20,
  [switch]$NoRestart
)

$ErrorActionPreference = "Stop"

function Resolve-FullPath([string]$PathValue, [string]$BasePath) {
  if ([System.IO.Path]::IsPathRooted($PathValue)) {
    return [System.IO.Path]::GetFullPath($PathValue)
  }
  return [System.IO.Path]::GetFullPath((Join-Path $BasePath $PathValue))
}

function Assert-InAppDir([string]$Target, [string]$Root) {
  $rootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
  $targetFull = [System.IO.Path]::GetFullPath($Target)
  if (-not $targetFull.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to write outside app directory: $targetFull"
  }
}

function Stop-WarehouseServer([int]$ListenPort) {
  $listeners = @(Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue)
  foreach ($item in $listeners) {
    $proc = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $item.OwningProcess) -ErrorAction SilentlyContinue
    if ($proc -and $proc.CommandLine -match "python" -and $proc.CommandLine -match "app\.py") {
      Stop-Process -Id $item.OwningProcess -Force
      Write-Host "Stopped old Warehouse server PID $($item.OwningProcess) on port $ListenPort"
    }
  }
}

function Start-WarehouseServer([string]$Root, [string]$HostValue, [int]$ListenPort, [string]$DataPath, [string]$Password) {
  if (-not $Password) {
    $Password = $env:WAREHOUSE_ADMIN_PASSWORD
  }
  if (-not $Password) {
    throw "WAREHOUSE_ADMIN_PASSWORD is not set. Pass -AdminPassword or set the environment variable."
  }
  if (-not $DataPath) {
    $DataPath = Join-Path $Root "data"
  }
  $env:WAREHOUSE_ENV = "production"
  $env:WAREHOUSE_HOST = $HostValue
  $env:WAREHOUSE_PORT = [string]$ListenPort
  $env:WAREHOUSE_DATA_DIR = $DataPath
  $env:WAREHOUSE_ADMIN_PASSWORD = $Password
  $env:WAREHOUSE_TUNNEL = "0"
  $env:WAREHOUSE_OPEN_BROWSER = "0"
  $logs = Join-Path $Root "logs"
  New-Item -ItemType Directory -Force -Path $logs | Out-Null
  $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
  return Start-Process -FilePath python -ArgumentList "app.py" -WorkingDirectory $Root -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logs "server_$stamp.out.log") `
    -RedirectStandardError (Join-Path $logs "server_$stamp.err.log")
}

function Wait-Health([string]$HostValue, [int]$ListenPort, [int]$TimeoutSeconds) {
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  $urlHost = if ($HostValue -eq "0.0.0.0" -or $HostValue -eq "::") { "127.0.0.1" } else { $HostValue }
  $url = "http://$urlHost`:$ListenPort/login"
  do {
    try {
      $response = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 3
      if ($response.StatusCode -eq 200) {
        return $true
      }
    } catch {
      Start-Sleep -Milliseconds 700
    }
  } while ((Get-Date) -lt $deadline)
  return $false
}

function Restore-Backup($ManifestFiles, [string]$BackupDir, [string]$Root) {
  foreach ($file in $ManifestFiles) {
    $rel = [string]$file.path
    $target = Resolve-FullPath $rel $Root
    $backup = Join-Path $BackupDir $rel
    if (Test-Path -LiteralPath $backup) {
      New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
      Copy-Item -LiteralPath $backup -Destination $target -Force
    } elseif (Test-Path -LiteralPath $target) {
      Remove-Item -LiteralPath $target -Force
    }
  }
}

if (-not $AppDir) {
  $AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}
$AppDir = [System.IO.Path]::GetFullPath($AppDir)
$PatchZip = Resolve-FullPath $PatchZip (Get-Location).Path
if (-not (Test-Path -LiteralPath $PatchZip)) {
  throw "Patch zip not found: $PatchZip"
}
if (-not (Test-Path -LiteralPath (Join-Path $AppDir "app.py"))) {
  throw "App directory does not contain app.py: $AppDir"
}
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  throw "Python was not found in PATH."
}

$workRoot = Join-Path $env:TEMP ("warehouse_patch_" + [Guid]::NewGuid().ToString("N"))
$backupRoot = Join-Path $AppDir "_patch_backups"
$backupDir = Join-Path $backupRoot (Get-Date -Format "yyyyMMdd_HHmmss")
New-Item -ItemType Directory -Force -Path $workRoot, $backupDir | Out-Null

try {
  Expand-Archive -LiteralPath $PatchZip -DestinationPath $workRoot -Force
  $manifestPath = Join-Path $workRoot "PATCH_MANIFEST.json"
  if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw "PATCH_MANIFEST.json is missing from patch zip."
  }
  $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
  $files = @($manifest.files)
  if (-not $files.Count) {
    throw "Patch manifest has no files."
  }

  foreach ($file in $files) {
    $rel = [string]$file.path
    if ($rel -match "^[A-Za-z]:|^/|(^|/)\.\.(/|$)" -or $rel -match "^(data|tmp|logs|_patch_backups)/") {
      throw "Unsafe patch path: $rel"
    }
    $source = Join-Path $workRoot $rel
    if (-not (Test-Path -LiteralPath $source)) {
      throw "Patch file missing: $rel"
    }
    $target = Resolve-FullPath $rel $AppDir
    Assert-InAppDir $target $AppDir
  }

  Stop-WarehouseServer -ListenPort $Port

  foreach ($file in $files) {
    $rel = [string]$file.path
    $target = Resolve-FullPath $rel $AppDir
    $backup = Join-Path $backupDir $rel
    if (Test-Path -LiteralPath $target) {
      New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backup) | Out-Null
      Copy-Item -LiteralPath $target -Destination $backup -Force
    }
  }

  foreach ($file in $files) {
    $rel = [string]$file.path
    $source = Join-Path $workRoot $rel
    $target = Resolve-FullPath $rel $AppDir
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
    Copy-Item -LiteralPath $source -Destination $target -Force
  }

  Push-Location $AppDir
  try {
    python -m py_compile app.py
  } finally {
    Pop-Location
  }

  if (-not $NoRestart) {
    $proc = Start-WarehouseServer -Root $AppDir -HostValue $HostName -ListenPort $Port -DataPath $DataDir -Password $AdminPassword
    if (-not (Wait-Health -HostValue $HostName -ListenPort $Port -TimeoutSeconds $HealthTimeoutSeconds)) {
      if (-not $proc.HasExited) {
        Stop-Process -Id $proc.Id -Force
      }
      throw "Health check failed after patch."
    }
  }

  Write-Host "Patch applied successfully."
  Write-Host "Backup: $backupDir"
} catch {
  Write-Host "Patch failed: $($_.Exception.Message)" -ForegroundColor Red
  try {
    Stop-WarehouseServer -ListenPort $Port
    if (Test-Path -LiteralPath $backupDir) {
      Restore-Backup -ManifestFiles $files -BackupDir $backupDir -Root $AppDir
      Push-Location $AppDir
      try { python -m py_compile app.py } finally { Pop-Location }
      if (-not $NoRestart) {
        $rollbackProc = Start-WarehouseServer -Root $AppDir -HostValue $HostName -ListenPort $Port -DataPath $DataDir -Password $AdminPassword
        if (-not (Wait-Health -HostValue $HostName -ListenPort $Port -TimeoutSeconds $HealthTimeoutSeconds)) {
          if (-not $rollbackProc.HasExited) { Stop-Process -Id $rollbackProc.Id -Force }
          Write-Host "Rollback restored files but health check still failed." -ForegroundColor Red
        } else {
          Write-Host "Rollback completed and service is healthy." -ForegroundColor Yellow
        }
      }
    }
  } catch {
    Write-Host "Rollback failed: $($_.Exception.Message)" -ForegroundColor Red
  }
  throw
} finally {
  if (Test-Path -LiteralPath $workRoot) {
    Remove-Item -LiteralPath $workRoot -Recurse -Force
  }
}
