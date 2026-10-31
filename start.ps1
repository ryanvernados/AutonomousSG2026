param(
    [ValidateSet('frontend', 'backend')]
    [string]$Target = 'frontend'
)

$ErrorActionPreference = 'Stop'
if ($Target -eq 'frontend') {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'frontend/start.ps1')
} else {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'backend/coordination/run.ps1')
}
exit $LASTEXITCODE
