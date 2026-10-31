param(
    [ValidateSet('mock', 'live', 'test', 'profile')]
    [string]$Mode = 'mock',
    [string]$Behavior = 'consensus',
    [string]$TracePath = 'backend/coordination/output/trace.jsonl'
)

$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$localPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
$bundledPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
if (Test-Path -LiteralPath $localPython) {
    $pythonExecutable = $localPython
} elseif (Test-Path -LiteralPath $bundledPython) {
    $pythonExecutable = $bundledPython
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand -or $pythonCommand.Source -like '*WindowsApps*') {
        throw 'Install Python 3.11+ or create backend/coordination/.venv, then rerun.'
    }
    $pythonExecutable = $pythonCommand.Source
}

Push-Location $repoRoot
try {
    switch ($Mode) {
        'test' { & $pythonExecutable -m unittest discover -s backend/coordination/tests -v }
        'profile' { & $pythonExecutable -m backend.coordination.inspect_dataset --dataset dataset }
        default { & $pythonExecutable -m backend.coordination --mode $Mode --behavior $Behavior --trace $TracePath }
    }
    $runExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $runExitCode
