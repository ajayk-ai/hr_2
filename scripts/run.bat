@echo off
REM Starts the backend (which also serves the built frontend at /). No admin
REM needed. Double-click this file, or run it from a terminal. Closing this
REM window stops the server -- for something that survives reboot/logoff
REM without a window open, see scripts\register-task.ps1 instead (needs admin).
REM
REM Host/port come from HRDOC_HOST / HRDOC_PORT in .env (defaults 0.0.0.0:8000)
REM via `python -m app.main` -- edit .env to change them, not this file.

cd /d "%~dp0.."

where uv >nul 2>nul
if errorlevel 1 (
    echo uv not found on PATH. Install it first: https://docs.astral.sh/uv/getting-started/installation/
    pause
    exit /b 1
)

if not exist "frontend\dist\index.html" (
    echo frontend\dist is missing -- run scripts\setup.ps1 first to build the frontend.
    pause
    exit /b 1
)

echo Starting server (host/port from .env) -- close this window to stop it.
uv run python -m app.main

pause
