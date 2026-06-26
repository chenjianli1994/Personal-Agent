@echo off
setlocal

set "ROOT=%~dp0"
cd /d "%ROOT%"

set "BACKEND_PORT=7870"
set "FRONTEND_PORT=5173"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Missing .venv\Scripts\python.exe
  echo Please create the virtual environment and install dependencies first.
  pause
  exit /b 1
)

if not exist "frontend\package.json" (
  echo [ERROR] Missing frontend\package.json
  pause
  exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
  echo [ERROR] npm is not available in PATH.
  echo Please install Node.js first.
  pause
  exit /b 1
)

for /f %%P in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$port=%BACKEND_PORT%; while (Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue) { $port++ }; Write-Output $port"') do set "BACKEND_PORT=%%P"
for /f %%P in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$port=%FRONTEND_PORT%; while (Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue) { $port++ }; Write-Output $port"') do set "FRONTEND_PORT=%%P"

set "FRONTEND_URL=http://127.0.0.1:%FRONTEND_PORT%/"

echo Backend port: %BACKEND_PORT%
echo Frontend port: %FRONTEND_PORT%

echo Starting backend...
start "PersonalAgent Backend" powershell -NoExit -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath '%ROOT%'; & '.\.venv\Scripts\python.exe' -m personal_agent serve --workspace . --db .personal_agent\agent.db --port %BACKEND_PORT%"

echo Starting frontend...
start "PersonalAgent Frontend" powershell -NoExit -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath '%ROOT%frontend'; npm run dev -- --host 127.0.0.1 --port %FRONTEND_PORT%"

echo Waiting for services to become ready...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$backendReady = $false; $frontendReady = $false; $deadline = (Get-Date).AddSeconds(90);" ^
  "while ((Get-Date) -lt $deadline) {" ^
  "  if (-not $backendReady) { try { $backendReady = [bool](Test-NetConnection -ComputerName 127.0.0.1 -Port %BACKEND_PORT% -InformationLevel Quiet -WarningAction SilentlyContinue) } catch {} }" ^
  "  if (-not $frontendReady) { try { $frontendReady = [bool](Test-NetConnection -ComputerName 127.0.0.1 -Port %FRONTEND_PORT% -InformationLevel Quiet -WarningAction SilentlyContinue) } catch {} }" ^
  "  if ($backendReady -and $frontendReady) { exit 0 }" ^
  "  Start-Sleep -Seconds 1" ^
  "}" ^
  "exit 1"

if errorlevel 1 (
  echo Services are still starting. Opening the page anyway: %FRONTEND_URL%
) else (
  echo Services are ready. Opening: %FRONTEND_URL%
)

start "" "%FRONTEND_URL%"
exit /b 0
