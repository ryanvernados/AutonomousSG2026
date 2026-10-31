$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
uv sync --frozen
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
    Write-Host 'Created .env for loopback demo mode. Set Supabase credentials for shared hosting.'
}
uv run uvicorn backend.api.app:app --host 127.0.0.1 --port 8000 --workers 1
