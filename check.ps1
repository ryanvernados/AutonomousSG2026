param([switch]$Browser)

$ErrorActionPreference = 'Stop'
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'backend/coordination/run.ps1') -Mode test
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$modes = @('test', 'build')
if ($Browser) { $modes += 'e2e' }
foreach ($mode in $modes) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'frontend/start.ps1') -Mode $mode
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
Write-Host 'All requested checks passed.'
