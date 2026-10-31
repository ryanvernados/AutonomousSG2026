# Autonomous SG 2026

Airport disruption simulator project with a React controller frontend and Python
agent coordinator. Both currently run as separate **mock demos**, without API keys.
The frontend is not yet connected to the Python coordinator; live integration
requires the team's backend API and canonical data contracts.

## Run on Windows

From the repository root in PowerShell:

```powershell
# Start the controller UI; leave this terminal running.
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1

# Run the standalone agent demo in another terminal.
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -Target backend
```

Open http://127.0.0.1:5173 for the UI. Stop it with Ctrl+C. The backend demo
prints its negotiation result and exits; it is not an HTTP server. Its trace is
saved under `backend/coordination/output/`.

The launchers use the bundled runtimes on this machine when available. On another
Windows machine, install Node.js 22.12+, pnpm 11, and Python 3.11+. The frontend
launcher installs dependencies using the committed lockfile. Mock Python mode
uses only the standard library. No `.env` file is needed for either mock demo.
The frontend uses an ignored `.pnpm-store/` cache inside its folder to keep its
dependencies separate from other projects' local caches.

## Verify

```powershell
# Python tests, frontend tests, and production build
powershell -NoProfile -ExecutionPolicy Bypass -File .\check.ps1

# Also install Chromium and exercise the UI in a browser
powershell -NoProfile -ExecutionPolicy Bypass -File .\check.ps1 -Browser
```

For other operating systems, run `pnpm install --frozen-lockfile`, `pnpm dev`,
`pnpm test`, and `pnpm build` from `frontend/`. Run `python -m backend.coordination`
and `python -m unittest discover -s backend/coordination/tests -v` from the root.

## Repository layout

| Folder | Contents |
| --- | --- |
| `frontend/` | Controller UI, API adapters, contracts, and frontend tests |
| `backend/agents/` | Agent roles, models, and adapters |
| `backend/coordination/` | Coordinator, mock engine, fixtures, and Python tests |
| `docs/handoff/` | Common specification and member assignments |
| `docs/plans/` | Both original Word delivery plans |
| `docs/archive/` | Original handoff ZIP |

Dependencies, build output, caches, logs, test reports, local `.env` files, and
raw `dataset/` / `datasets/` folders are ignored. Keep lockfiles and `.env.example`
templates tracked. Browser tests write new screenshots to ignored
`frontend/test-results/`; the existing `frontend/evidence/` images are saved
handoff examples.

## Component documentation

- [Frontend setup and workflow](frontend/README.md)
- [Frontend integration requirements](frontend/INTEGRATION.md)
- [Agent setup and protocol](backend/coordination/README.md)
- [Common specification](docs/handoff/00_READ_FIRST_COMMON_SPECIFICATION.txt)
