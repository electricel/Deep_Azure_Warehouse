@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found in PATH. Please install Python 3.10+ and run this file again.
  pause
  exit /b 1
)

python -c "import cryptography" >nul 2>nul
if errorlevel 1 (
  echo Installing Python dependencies from requirements.txt...
  python -m pip install -r "%~dp0requirements.txt"
  if errorlevel 1 (
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
  )
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0configure_env.ps1" -EnvPath "%~dp0.env" -DataDir "%~dp0data"
if errorlevel 1 (
  pause
  exit /b 1
)

for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%~dp0.env") do (
  if not "%%A"=="" set "%%A=%%B"
)

echo Checking for an old Warehouse server on the configured port...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$cfg='server_config.json'; $port=8088; if (Test-Path $cfg) { try { $json=Get-Content $cfg -Raw | ConvertFrom-Json; if ($json.port) { $port=[int]$json.port } } catch {} }; " ^
  "$listeners=Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue; " ^
  "foreach ($item in $listeners) { $proc=Get-CimInstance Win32_Process -Filter ('ProcessId=' + $item.OwningProcess) -ErrorAction SilentlyContinue; " ^
  "if ($proc -and $proc.CommandLine -match 'python' -and $proc.CommandLine -match 'app\.py') { Stop-Process -Id $item.OwningProcess -Force; Write-Host ('Stopped old Warehouse server PID ' + $item.OwningProcess + ' on port ' + $port) } }"

if "%WAREHOUSE_ENV%"=="" set WAREHOUSE_ENV=production
if "%WAREHOUSE_HOST%"=="" set WAREHOUSE_HOST=0.0.0.0
if "%WAREHOUSE_PORT%"=="" set WAREHOUSE_PORT=8088
if "%WAREHOUSE_TUNNEL%"=="" set WAREHOUSE_TUNNEL=auto
if "%WAREHOUSE_OPEN_BROWSER%"=="" set WAREHOUSE_OPEN_BROWSER=1
if "%WAREHOUSE_DATA_ENCRYPTION%"=="" set WAREHOUSE_DATA_ENCRYPTION=1

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$cfg='server_config.json'; " ^
  "$data=[ordered]@{}; if (Test-Path $cfg) { try { $obj=Get-Content $cfg -Raw | ConvertFrom-Json; if ($obj) { $obj.PSObject.Properties | ForEach-Object { $data[$_.Name]=$_.Value } } } catch { $data=[ordered]@{} } }; " ^
  "$data.host=$env:WAREHOUSE_HOST; $data.port=$env:WAREHOUSE_PORT; $data.tunnel_mode=$env:WAREHOUSE_TUNNEL; " ^
  "$data | ConvertTo-Json -Depth 8 | Set-Content -Path $cfg -Encoding UTF8"

if "%WAREHOUSE_ADMIN_PASSWORD%"=="" (
  echo.
  echo Set admin password for account: admin
  echo The password input is hidden by PowerShell.
  for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=Read-Host 'Admin password' -AsSecureString; $b=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($p); try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }"`) do set "WAREHOUSE_ADMIN_PASSWORD=%%P"
)

if "%WAREHOUSE_ADMIN_PASSWORD%"=="" (
  echo ERROR: WAREHOUSE_ADMIN_PASSWORD is empty. Server was not started.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p=($env:WAREHOUSE_ADMIN_PASSWORD + '').Trim().ToLowerInvariant(); " ^
  "$bad=@('admin','admin123','change-this-strong-password','letmein','password','qwerty','root','123456','12345678'); " ^
  "if ($bad -contains $p) { Write-Host 'ERROR: WAREHOUSE_ADMIN_PASSWORD is too weak or still uses a default value.'; Write-Host 'Use a private strong password, for example 12+ characters with letters and numbers.'; exit 1 }"
if errorlevel 1 (
  pause
  exit /b 1
)

if "%WAREHOUSE_DATA_KEY%"=="" (
  echo.
  echo Set data encryption key. Keep this key backed up outside the data folder.
  echo To generate one: python -c "import secrets; print(secrets.token_urlsafe(32))"
  for /f "usebackq delims=" %%K in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=Read-Host 'Data encryption key' -AsSecureString; $b=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($p); try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }"`) do set "WAREHOUSE_DATA_KEY=%%K"
)

if "%WAREHOUSE_DATA_KEY%"=="" (
  echo ERROR: WAREHOUSE_DATA_KEY is empty. Server was not started.
  pause
  exit /b 1
)

where cloudflared >nul 2>nul
if errorlevel 1 (
  where ngrok >nul 2>nul
  if errorlevel 1 (
    echo NOTE: cloudflared/ngrok was not found. LAN access will still work, but external tunnel mapping will not be created automatically.
  )
)

python "%~dp0app.py"
pause
