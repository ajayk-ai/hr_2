# Sets up this repo for local development/deployment on a fresh Windows machine.
# Run from the repo root: .\scripts\setup.ps1

$ErrorActionPreference = "Stop"

function Require-Command($name, $hint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        Write-Host "Missing required tool: $name" -ForegroundColor Red
        Write-Host "  $hint"
        exit 1
    }
}

Write-Host "== Checking prerequisites ==" -ForegroundColor Cyan
Require-Command "uv" "Install from https://docs.astral.sh/uv/getting-started/installation/"
Require-Command "node" "Install Node 20+ from https://nodejs.org/"
Require-Command "npm" "Ships with Node"

$nodeVersion = (node --version) -replace 'v', '' -split '\.' | Select-Object -First 1
if ([int]$nodeVersion -lt 20) {
    Write-Host "Node 20+ required, found v$nodeVersion" -ForegroundColor Red
    exit 1
}

Write-Host "== Backend: uv sync ==" -ForegroundColor Cyan
uv sync

if (-not (Test-Path ".env")) {
    Write-Host "== Creating .env from .env.example ==" -ForegroundColor Cyan
    Copy-Item ".env.example" ".env"
    Write-Host "  Fill in HRDOC_GCP_PROJECT_ID, HRDOC_DOCAI_*, GOOGLE_APPLICATION_CREDENTIALS, HRDOC_GEMINI_API_KEY in .env" -ForegroundColor Yellow
} else {
    Write-Host "== .env already exists, leaving it untouched ==" -ForegroundColor DarkGray
}

Write-Host "== Frontend: npm install ==" -ForegroundColor Cyan
Push-Location frontend
npm install

if (-not (Test-Path ".env.local")) {
    Write-Host "== Creating frontend/.env.local from .env.example ==" -ForegroundColor Cyan
    Copy-Item ".env.example" ".env.local"
} else {
    Write-Host "== frontend/.env.local already exists, leaving it untouched ==" -ForegroundColor DarkGray
}

Write-Host "== Frontend: npm run build ==" -ForegroundColor Cyan
npm run build
Pop-Location

Write-Host ""
Write-Host "== Done ==" -ForegroundColor Green
Write-Host "Next steps:"
Write-Host "  1. Fill in secrets in .env (see DEPLOY.md for what's needed)."
Write-Host "  2. Place the GCP service-account JSON somewhere outside the repo and point"
Write-Host "     GOOGLE_APPLICATION_CREDENTIALS at it (or run 'gcloud auth application-default login')."
Write-Host "  3. Run everything from one process:"
Write-Host "       uv run uvicorn app.main:app --host 0.0.0.0 --port 8000"
Write-Host "     -> http://127.0.0.1:8000 serves both the UI and the API (frontend/dist is built above"
Write-Host "        and app/main.py mounts it automatically)."
Write-Host "  Alternative for frontend hot-reload during active dev: cd frontend; npm run dev"
