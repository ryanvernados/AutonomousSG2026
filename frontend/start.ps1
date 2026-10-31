param(
    [ValidateSet('dev', 'install', 'build', 'test', 'e2e')]
    [string]$Mode = 'dev'
)

$ErrorActionPreference = 'Stop'
$runtimeRoot = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies'
$bundledNode = Join-Path $runtimeRoot 'node/bin/node.exe'
$bundledPnpm = Join-Path $runtimeRoot 'bin/fallback/pnpm.cmd'
$previousPath = $env:PATH

Push-Location $PSScriptRoot
try {
    if (Test-Path -LiteralPath $bundledNode) {
        $env:PATH = (Split-Path $bundledNode) + ';' + $env:PATH
    }
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
        throw 'Install Node.js 22.12+ and pnpm 11, then run this script again.'
    }
    $nodeVersion = & node --version
    if ($LASTEXITCODE -ne 0 -or [version]$nodeVersion.TrimStart('v') -lt [version]'22.12.0') {
        throw 'Node.js 22.12+ is required.'
    }

    if (Test-Path -LiteralPath $bundledPnpm) {
        $pnpmExecutable = $bundledPnpm
    } else {
        $pnpmCommand = Get-Command pnpm -ErrorAction SilentlyContinue
        if (-not $pnpmCommand) { throw 'Install pnpm 11 (npm install -g pnpm@11), then rerun.' }
        $pnpmExecutable = $pnpmCommand.Source
    }

    # Reconcile dependencies with the committed lockfile on every invocation.
    & $pnpmExecutable install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }

    switch ($Mode) {
        'install' { $runExitCode = 0 }
        'e2e' {
            & $pnpmExecutable exec playwright install chromium
            if ($LASTEXITCODE -ne 0) { throw 'Chromium installation failed.' }
            & $pnpmExecutable run test:e2e
            $runExitCode = $LASTEXITCODE
        }
        default {
            & $pnpmExecutable run $Mode
            $runExitCode = $LASTEXITCODE
        }
    }
} finally {
    Pop-Location
    $env:PATH = $previousPath
}
exit $runExitCode
