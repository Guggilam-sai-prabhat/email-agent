@echo off
rem One-command launcher (Windows): installs Python deps on first run, migrates the DB,
rem starts the backend, then opens the companion. COMPANION_CMD overrides the app path.
setlocal
cd /d "%~dp0"
if "%APP_PORT%"=="" set APP_PORT=8000
if "%PYTHON%"=="" set PYTHON=python
if "%COMPANION_CMD%"=="" set COMPANION_CMD=companion\desktop-companion.exe

%PYTHON% -c "import sys; sys.exit(sys.version_info < (3, 10))" || (
  echo Python 3.10+ is required. Install it from https://python.org and tick "Add Python to PATH".
  exit /b 1
)

if not exist .venv\Scripts\python.exe (
  echo First run: installing Python dependencies, this takes a few minutes...
  %PYTHON% -m venv .venv || exit /b 1
  .venv\Scripts\pip install --quiet . || exit /b 1
)

.venv\Scripts\python -m alembic upgrade head || exit /b 1

powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:%APP_PORT%/health | Out-Null; exit 0 } catch { exit 1 }"
if %errorlevel%==0 (
  echo Backend already running on port %APP_PORT%.
  set BACKEND_PID=
) else (
  for /f %%i in ('powershell -NoProfile -Command "(Start-Process .venv\Scripts\python.exe -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','%APP_PORT%' -WindowStyle Hidden -PassThru -RedirectStandardOutput mail-agent.log -RedirectStandardError mail-agent.err.log).Id"') do set BACKEND_PID=%%i
  powershell -NoProfile -Command "for ($i=0; $i -lt 30; $i++) { try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:%APP_PORT%/health | Out-Null; exit 0 } catch { Start-Sleep 1 } }; exit 1"
  if errorlevel 1 (
    echo Backend failed to start; see mail-agent.err.log
    taskkill /F /PID %BACKEND_PID% >nul 2>&1
    exit /b 1
  )
)

if exist "%COMPANION_CMD%" (
  echo Backend ready. Opening the companion...
  "%COMPANION_CMD%"
) else (
  echo Backend ready. Open the Mail Agent Companion app now. Press any key here to stop the backend.
  pause >nul
)
if defined BACKEND_PID taskkill /F /PID %BACKEND_PID% >nul 2>&1
