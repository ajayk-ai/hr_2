# Registers the backend (which also serves the built frontend) as a Windows
# Scheduled Task that starts automatically at boot and restarts on crash --
# so it doesn't depend on a terminal window or a logged-in user.
#
# Run as Administrator, from anywhere, after scripts\setup.ps1 has completed
# and frontend\dist exists:
#   .\scripts\register-task.ps1
#
# Manage it afterwards:
#   Start-ScheduledTask -TaskName HRDocProcessor
#   Stop-ScheduledTask  -TaskName HRDocProcessor
#   Get-ScheduledTask   -TaskName HRDocProcessor | Get-ScheduledTaskInfo
#   Unregister-ScheduledTask -TaskName HRDocProcessor -Confirm:$false   # to remove
# Logs: logs\uvicorn.log (created next to this script's repo root).
# Host/port come from HRDOC_HOST / HRDOC_PORT in .env, not this script --
# edit .env and re-run (or just restart the task) to change them.

param(
    [string]$TaskName = "HRDocProcessor"
)

$ErrorActionPreference = "Stop"

$currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Re-run this script from an elevated (Administrator) PowerShell." -ForegroundColor Red
    exit 1
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
    Write-Host "uv not found on PATH. Install it first (see DEPLOY.md) and re-run." -ForegroundColor Red
    exit 1
}

$distPath = Join-Path $repoRoot "frontend\dist\index.html"
if (-not (Test-Path $distPath)) {
    Write-Host "frontend\dist is missing -- run scripts\setup.ps1 first (it builds the frontend)." -ForegroundColor Red
    exit 1
}

$logDir = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir "uvicorn.log"

# `python -m app.main` (not `uvicorn app.main:app --host/--port`) so host/port
# are read from HRDOC_HOST / HRDOC_PORT in .env instead of being baked in here.
# Wrapped in cmd /c so stdout/stderr -- otherwise lost for a task with no
# console -- land in a log file you can tail for troubleshooting.
$cmdArgs = "/c `"`"$uv`" run python -m app.main >> `"$logFile`" 2>&1`""

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $cmdArgs -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -AtStartup

# Runs as SYSTEM: starts at boot with no user needing to log in. SYSTEM must be
# able to read .env and the GCP service-account JSON -- default NTFS ACLs allow
# this unless permissions were locked down further.
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

Write-Host "Registered scheduled task '$TaskName' (starts at boot, restarts on crash)." -ForegroundColor Green
Write-Host "Starting it now..."
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3
Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo | Format-List TaskName, LastRunTime, LastTaskResult
Write-Host "Logs: $logFile"

$envPort = "8000"
$envFile = Join-Path $repoRoot ".env"
if (Test-Path $envFile) {
    $match = Select-String -Path $envFile -Pattern '^HRDOC_PORT=(\d+)' | Select-Object -First 1
    if ($match) { $envPort = $match.Matches[0].Groups[1].Value }
}
Write-Host "Check it's up: Invoke-WebRequest http://127.0.0.1:$envPort/health"
