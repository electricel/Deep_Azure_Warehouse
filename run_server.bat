@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found in PATH. Install Python 3.10+ first.
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

if "%WAREHOUSE_ENV%"=="" set WAREHOUSE_ENV=production
if "%WAREHOUSE_HOST%"=="" set WAREHOUSE_HOST=127.0.0.1
if "%WAREHOUSE_PORT%"=="" set WAREHOUSE_PORT=8088
if "%WAREHOUSE_TUNNEL%"=="" set WAREHOUSE_TUNNEL=0
if "%WAREHOUSE_OPEN_BROWSER%"=="" set WAREHOUSE_OPEN_BROWSER=0
if "%WAREHOUSE_DATA_DIR%"=="" set WAREHOUSE_DATA_DIR=%~dp0data
if "%WAREHOUSE_DATA_ENCRYPTION%"=="" set WAREHOUSE_DATA_ENCRYPTION=1

if "%WAREHOUSE_ADMIN_PASSWORD%"=="" (
  echo.
  echo Set admin password for account: admin
  echo The password input is hidden by PowerShell.
  for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=Read-Host 'Admin password' -AsSecureString; $b=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($p); try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }"`) do set "WAREHOUSE_ADMIN_PASSWORD=%%P"
  echo.
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

echo Starting Warehouse Inventory Server...
echo Host: %WAREHOUSE_HOST%
echo Port: %WAREHOUSE_PORT%
echo Data: %WAREHOUSE_DATA_DIR%
python "%~dp0app.py"
