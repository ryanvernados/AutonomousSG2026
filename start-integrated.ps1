# Run the integrated system locally: Member 1 API + Member 3 agents + Member 2 scenarios + Member 4 UI.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\start-integrated.ps1
#
# Loopback demo only: AUTH_MODE=demo with local SQLite. Settings are passed as process
# environment variables, which override .env, so your Supabase configuration is untouched.
# Agents run in MOCK model mode unless you pass -ModelMode live: set ANTHROPIC_API_KEY in this
# shell for Claude (default model claude-sonnet-5-5; override with AIRPORT_MODEL), or
# OPENAI_API_KEY and AIRPORT_MODEL for OpenAI. Opens two windows: API on :8000 and UI on :5173.
param(
    [ValidateSet('mock', 'live')][string]$ModelMode = 'mock',
    [string]$DemoToken = 'local-integration-token',
    # Simulated seconds per real second while the clock runs (1 = real time; 30 plays 2 h in 4 min).
    [ValidateRange(1, 600)][int]$SimSpeed = 30
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

# A server left over from an earlier run (e.g. the API-only backend.api.app, which has no agents)
# would answer on these ports instead of the integrated stack. Stop it if it belongs to this repo.
if ($ModelMode -eq 'live' -and -not ($env:ANTHROPIC_API_KEY -or $env:OPENAI_API_KEY)) {
    throw 'Live mode needs ANTHROPIC_API_KEY (Claude) or OPENAI_API_KEY + AIRPORT_MODEL in this shell.'
}
foreach ($port in 8000, 5173) {
    $owners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($owner in $owners) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $owner"
        if ($proc.CommandLine -match 'uvicorn|vite|node' -and
            ($proc.CommandLine -like "*$root*" -or $proc.CommandLine -match 'backend\.(api|coordination)')) {
            Write-Host "Stopping old server on port $port (PID $owner): $($proc.CommandLine)"
            Stop-Process -Id $owner -Force
        } else {
            throw "Port $port is used by another program (PID $owner): $($proc.CommandLine). Stop it and retry."
        }
    }
}
$run = Join-Path $env:LOCALAPPDATA 'Temp\airport-run'
$scenarios = Join-Path $run 'scenarios'
New-Item -ItemType Directory -Force $scenarios | Out-Null
Copy-Item (Join-Path $root 'fixtures\sfo-synthetic-v1.json') $scenarios -Force
Copy-Item (Join-Path $root 'data\processed\scenarios\*.json') $scenarios -Force

# Each service gets its own generated script, started with -File to avoid argument quoting issues.
$apiScript = Join-Path $run 'run-api.ps1'
$uiScript = Join-Path $run 'run-ui.ps1'
@"
`$Host.UI.RawUI.WindowTitle = 'Airport API :8000'
`$env:AUTH_MODE = 'demo'
`$env:DEMO_TOKEN = '$DemoToken'
`$env:SQLITE_PATH = '$run\airport.sqlite3'
`$env:SCENARIO_DIR = '$scenarios'
`$env:AIRPORT_MODEL_MODE = '$ModelMode'
`$env:SIM_SPEED = '$SimSpeed'
`$env:CORS_ORIGINS = '["http://127.0.0.1:5173","http://localhost:5173"]'
Set-Location '$root'
uv sync --frozen
uv run uvicorn backend.coordination.server:app --host 127.0.0.1 --port 8000 --workers 1
"@ | Set-Content -Encoding utf8 $apiScript
@"
`$Host.UI.RawUI.WindowTitle = 'Airport UI :5173'
`$env:VITE_API_MODE = 'live'
`$env:VITE_AUTH_MODE = 'demo'
`$env:VITE_DEMO_TOKEN = '$DemoToken'
`$env:VITE_API_BASE_URL = 'http://127.0.0.1:8000/api/v1'
Set-Location '$root\frontend'
if (Get-Command pnpm -ErrorAction SilentlyContinue) { pnpm install --frozen-lockfile; pnpm dev }
else { npx -y pnpm@11 install --frozen-lockfile; npx -y pnpm@11 dev }
"@ | Set-Content -Encoding utf8 $uiScript

foreach ($script in $apiScript, $uiScript) {
    Start-Process powershell -ArgumentList '-NoExit', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$script`""
}
Write-Host 'Waiting for the UI (first run installs dependencies)...'
$deadline = (Get-Date).AddMinutes(5)
while ((Get-Date) -lt $deadline -and -not (Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Sleep -Seconds 2
}
if (Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue) {
    Start-Process 'http://127.0.0.1:5173'
} else {
    Write-Warning 'UI did not start within 5 minutes; check the "Airport UI :5173" window.'
}
Write-Host "API:  http://127.0.0.1:8000/docs  (Authorize with token: $DemoToken)"
Write-Host "UI:   http://127.0.0.1:5173  (clock runs at ${SimSpeed}x when resumed; -SimSpeed to change)"
Write-Host ("Walkthrough: `$env:DEMO_TOKEN='$DemoToken'; uv run python -m backend.coordination.walkthrough")
