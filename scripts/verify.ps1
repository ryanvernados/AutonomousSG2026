$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
uv sync --frozen
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run ruff check backend scripts tests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run pytest -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
npm ci --ignore-scripts
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
npm run test:sql
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run python -m scripts.export_contracts
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
npm run types
exit $LASTEXITCODE
