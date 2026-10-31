$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$runtimeRoot = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies'
$bundledNode = Join-Path $runtimeRoot 'node\bin\node.exe'
$bundledPnpm = Join-Path $runtimeRoot 'bin\fallback\pnpm.cmd'
if (Test-Path -LiteralPath $bundledNode) {
    $env:PATH = (Split-Path $bundledNode) + ';' + $env:PATH
    if (!(Test-Path -LiteralPath 'node_modules\vite\bin\vite.js')) {
        & $bundledPnpm install --frozen-lockfile
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    }
    & $bundledNode 'node_modules\vite\bin\vite.js' --host 127.0.0.1
} elseif (Get-Command node -ErrorAction SilentlyContinue) {
    if (!(Test-Path -LiteralPath 'node_modules\vite\bin\vite.js')) {
        if (Get-Command pnpm -ErrorAction SilentlyContinue) { pnpm install --frozen-lockfile }
        else { npm install }
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    }
    node 'node_modules\vite\bin\vite.js' --host 127.0.0.1
} else {
    throw 'Install Node.js 22.12+ or use the Codex bundled runtime, then run this script again.'
}
