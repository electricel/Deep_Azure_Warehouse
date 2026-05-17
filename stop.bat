@echo off
setlocal
cd /d "%~dp0"

if "%WAREHOUSE_PORT%"=="" set WAREHOUSE_PORT=8088

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$port=[int]$env:WAREHOUSE_PORT; " ^
  "$cfg=Join-Path (Get-Location) 'server_config.json'; " ^
  "if (Test-Path $cfg) { try { $json=Get-Content $cfg -Raw | ConvertFrom-Json; if ($json.port) { $port=[int]$json.port } } catch {} }; " ^
  "$listeners=@(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue); " ^
  "$stopped=0; " ^
  "foreach ($item in $listeners) { " ^
  "  $proc=Get-CimInstance Win32_Process -Filter ('ProcessId=' + $item.OwningProcess) -ErrorAction SilentlyContinue; " ^
  "  if ($proc -and $proc.CommandLine -match 'python' -and $proc.CommandLine -match 'app\.py') { " ^
  "    Stop-Process -Id $item.OwningProcess -Force; " ^
  "    Write-Host ('Stopped Warehouse server PID ' + $item.OwningProcess + ' on port ' + $port); " ^
  "    $stopped++ " ^
  "  } " ^
  "}; " ^
  "if ($stopped -eq 0) { Write-Host ('No Warehouse python app.py listener found on port ' + $port) }"

if errorlevel 1 exit /b 1
