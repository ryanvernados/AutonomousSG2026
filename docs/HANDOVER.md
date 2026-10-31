# Handover: airport disruption simulator

State of `main` at `482f298` (31 October 2026). This covers what works, how to run and verify it,
where each part lives, what was changed across member boundaries, and what is still open.

## 1. Current state in one paragraph

All four workstreams are merged and run as one system. The API (Member 1) serves the
historical SFO scenario built from the 2015 BTS data (Member 2). Independent ATC, ground and pilot agents
(Member 3) negotiate a two-round recovery plan through the platform. The controller UI (Member 4) shows
the real SFO layout, the agents' proposal, staff approval, and a ground-control list of each flight's gate,
taxi route and runway slot. On `sfo-2015-12-21-2000-dev` (30 flights) a runway closure leads to
CONSENSUS from 32 agents in 2 rounds in about 15 s. All 2,682 hard checks pass, and approval publishes
all 30 flights. Agents run in **MOCK** model mode; LIVE (real model) mode is wired up but not tested.

## 2. Run it

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), Node.js 22+ (pnpm 11 is fetched with
`npx` if not installed).

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-integrated.ps1
```

This opens two windows, the API on http://127.0.0.1:8000/docs and the UI on http://127.0.0.1:5173, then
opens the browser. It runs `AUTH_MODE=demo` with local SQLite under `%LOCALAPPDATA%\Temp\airport-run\`,
using token `local-integration-token`. Settings are passed as environment variables, so `.env` (Supabase)
is not read for these values or changed. Add `-ModelMode live` for real model calls; that needs
`OPENAI_API_KEY` and `AIRPORT_MODEL` set in the same shell.

### Walkthrough in the UI

1. **Demo guide & coverage** → scenario **sfo-2015-12-21-2000-dev** (Dec 21 2015, 20:55–23:00 PST).
2. **Add instruction** → Runway closure, `10R-28L` → Preview → Confirm. The runway turns red on the map.
3. **Start negotiation**. About 15 s later a CONSENSUS proposal appears; the flight board is unchanged.
4. **Approve in simulator**. The Ground control panel fills in and aircraft appear at gates.
5. **+1 min / +10 min** to watch aircraft land, taxi and depart. Resume runs in real time (1 s per s).

`sfo-synthetic-v1` (Member 1's 24-flight fixture) also works, but uses the illustrative map because it has
no layout geometry.

### Scripted walkthrough (HTTP)

```powershell
$env:DEMO_TOKEN='local-integration-token'
uv run python -m backend.coordination.walkthrough --scenario sfo-2015-12-21-2000-dev --runway 10R-28L
```

## 3. Verify

| Suite | Command | Last result |
|---|---|---|
| Members 1 and 2 (pytest) | `uv run --with pandas==3.1.0 --with numpy==2.4.6 --with tzdata==2026.6 pytest -q tests backend/data/tests` | 81 passed |
| Member 3 + adapter (unittest) | `uv run python -m unittest discover -s backend/coordination/tests` | 39 passed |
| Member 1 PostgreSQL / RLS | `npm ci --ignore-scripts; npm run test:sql` | 28 assertions passed |
| Lint | `uv run ruff check backend scripts tests` | clean |
| Frontend unit | `cd frontend; npx pnpm@11 test` | 31 passed |
| Frontend build | `cd frontend; npx pnpm@11 build` | passes |
| Frontend mock e2e | `cd frontend; node scripts/test-e2e.mjs` (no servers running) | 6 passed, live specs skipped |
| Live browser e2e | stack running, then `$env:LIVE_E2E='1'; node scripts/test-e2e.mjs e2e/live-integration.spec.ts` | 2 passed |
| Data contract | `uv run --with pandas==3.1.0 --with numpy==2.4.6 --with tzdata==2026.6 python -m backend.data.contract_check` | 5/5 PASS |

Run Python on 3.11 to match `pyproject.toml`; everything was also verified on 3.14.

## 4. Where things live

| Area | Owner | Key files |
|---|---|---|
| Contracts | Member 1 | `backend/contracts/models.py`, generated `openapi.json`, `scenario.schema.json`, `client.d.ts` |
| Engine | Member 1 | `backend/engine/validation.py` (hard checks), `planning.py` (search, baseline, fallback) |
| API / persistence | Member 1 | `backend/api/`, `backend/persistence/`, `supabase/migrations/` |
| Data pipeline | Member 2 | `backend/data/` (README has every command), `data/processed/` |
| Airport layout | Member 2 | `backend/airport/sfo_layout.py`, `data/layout/` (FAA AL-375 PDF and graph) |
| Scenarios served | Member 2 | `data/processed/scenarios/*.json` (point `SCENARIO_DIR` here) |
| Agents / protocol | Member 3 | `backend/agents/`, `backend/coordination/coordinator.py`, `protocol.py` |
| Agent ↔ platform bridge | integration | `backend/coordination/platform_adapter.py`, `server.py`, `walkthrough.py` |
| Controller UI | Member 4 | `frontend/src/App.tsx`, `components/` |
| UI ↔ API mapping | integration | `frontend/src/api/canonical.ts`, `live.ts` |
| Map / ground control | integration | `frontend/src/components/RealAirportMap.tsx`, `GroundMovements.tsx`, `src/map/movement.ts` |
| Launcher | integration | `start-integrated.ps1` |

Background documents: `docs/integration-compatibility-review.md` (Member 1's review; sections 2–4 are now
addressed), `docs/member1-handoff.md`, `backend/data/README.md`, `backend/coordination/README.md`,
`frontend/INTEGRATION.md`.

## 5. Data and scenario facts

- Source: Kaggle 2015 Flight Delays (`flights.csv` SHA-256 `afa4e421…3a83`, 5,819,079 rows). Raw files are
  not in git; `backend/data/README.md` has the acquisition steps and checksums.
- Selected day: **2015-12-21**, the SFO local day with the most rows meeting `WEATHER_DELAY > 15 or
  LATE_AIRCRAFT_DELAY > 20` (293 of 970 movements). Core window 20:00–22:00 PST, plus 60 min buffers.
- Traced qualifying row: `flights.csv` line 5663453, VX 211 ORD→SFO, 137 min late-aircraft delay.
- Scenarios: `-full` (218 flights, 19:00–23:00 PST) and `-dev` (30 flights, starting 20:55 PST).
- Layout: FAA AL-375 cycle 2611 (effective 2026-10-29). Runways, crossings and taxiway names are SOURCED;
  topology, coordinates and the 133 gates are SYNTHETIC. This 2026 layout is paired with 2015 traffic.
- Every assumption carries `source_type` and units. The synthetic values include:
  - aircraft category: HEAVY if the stage length is ≥ 2,000 mi
  - holding fuel: 30–60 min
  - crews: one per aircraft rotation
  - pushback tugs: 12
  - gates: sized from peak demand × 1.15

## 6. Changes made across member boundaries (please review)

These were needed to make the parts run together. Each is covered by tests and documented in its commit.

**Member 1 (engine and contract)**
- `Resource.intersecting_runway_ids`: a new optional v1 field. Crossing runways may not be occupied at
  the same time, and wake pairs apply only to the same or dependent runways. Previously all four SFO
  runways were wake-dependent, leaving about single-runway capacity and no feasible plan.
- `search_candidates`: `limit` caps candidate plans (≤ 100, per the specification). Placement attempts now
  have their own deterministic budget of 100 per flight, which was review section 4. `test_engine.py` was
  updated to match, and a crossing-runway test was added.
- Performance, with identical results verified against a recorded baseline:
  - reservations are grouped by resource
  - `CheckResult` objects are built without re-validation
  - route search uses a prebuilt adjacency list
  - the search validates failures only (`validate_plan(..., include_passes=False)`)
  - effect: the SFO dev fallback went from 60 s to 17 s
- Contract artifacts regenerated with `scripts.export_contracts` and `npm run types`.

**Member 3 (coordination)**
- `platform_adapter.py` implements Member 1's `Coordinator` protocol over the unchanged two-round coordinator:
  - maps sender IDs and reasons to Member 1's formats
  - ranks plans by `Impact.score`
  - sets `source=CONSENSUS` before hashing
  - derives agent windows from the engine's own candidate
  - runs one shared search per negotiation
- Lint-only fixes in Member 3 files (import order, one unused import, one lambda).

**Member 4 (UI)**
- `LiveClient` now uses the canonical API: versioned session create, canonical instruction enums and
  payloads, and an unwrapped `SnapshotResponse`. `VITE_AUTH_MODE=demo` covers loopback runs.
- Components handle flights with no published plan; canonical sessions start unscheduled.
- The timeline and instruction form use resources from the scenario.
- New: the real airport map, the Ground control panel, and a +10 min step. Mock mode is unchanged, and its
  e2e tests still pass.

**Member 2 (my own data, for the record)**
- Gate size now fits every leg of an aircraft's stay; HEAVY departures had been given MEDIUM-only gates.
- The holding-fuel floor was raised from 10 to 30 min.
- The dev scenario starts 20 min before its first flight.
- Map geometry was added under `source_manifest.member2.layout.map`.

## 7. Acceptance status (plan, page 14)

| Case | Status |
|---|---|
| Historical scenario | Done: traced row, full slice, coverage report |
| Time normalization | Done: overnight, time zones, 2400 and DST are handled or flagged; 14 tests in `test_normalize.py` |
| Resource conflicts | Done: Member 1 engine tests |
| Fuel and unknown inputs | Done: engine tests; pilot agents carry `latest_safe_arrival_utc` |
| Independent domains | **Partial**: separate agent instances and calls, but MOCK only. No live model evidence |
| Consensus success | Done in MOCK, including the historical SFO scenario |
| Veto or malformed reply | Done: adapter tests (veto, conflict, malformed, missing ballot, wrong hash) |
| No feasible candidate | Done: engine test (closed airport) |
| Approval boundary | Done: HTTP walkthrough and live e2e show nothing is published before approval |
| Stale or expired proposal | Done in engine tests and UI mock e2e; not re-run in the live UI |
| Atomicity and retries | Done: engine and PostgreSQL tests |
| Access and recovery | **Partial**: RLS proven in embedded PostgreSQL; the integrated stack is tested only in demo/SQLite mode |
| Manual mode | Done in engine tests and UI mock e2e |
| Service failure | Done: fallback path exercised; see open issue 3 |
| UI disconnect | Done in UI mock e2e |
| Baseline comparison | **Partial**: `backend.data.evaluate` runs the engine baseline with identical inputs; no agent-vs-baseline report yet |
| Controller comprehension | **Not started**: needs controller feedback |

Completion threshold: at least one successful **LIVE** negotiation is still required.

## 8. Open issues, in priority order

1. **LIVE model mode untested** (Member 3). Run `start-integrated.ps1 -ModelMode live`, then record the
   calls, latency and trace. The two-round, 60 s budget includes the roughly 15 s engine search.
2. **Full 218-flight scenario not run end to end** (Member 1/2). Its placement budget is 21,800 and
   validation cost grows with plan size, so expect minutes, not seconds. Measure before demoing it.
3. **The engine search is CPU-bound in API worker threads** (Member 1/3). Concurrent negotiations on
   several sessions slow each other down. If the coordinator times out, its search thread keeps running
   while the platform's fallback search starts. Running searches in a process pool would make them
   cancellable and keep the API responsive.
4. **Hosted Supabase not exercised with the integrated stack** (Member 1/2). Scenarios must be imported
   into the `scenarios` and `scenario_flights` tables first, using `python -m backend.data.supabase_import
   --apply` with `DATABASE_URL`. The HTTPS secret key only has SELECT on those tables.
5. **Proposal review lists every check** (about 2,700 for SFO) (Member 4). Summarize, and expand on demand.
6. **Coordinator failures labelled generically** (Member 1). Failed coordination shows
   `INVALID_OR_FAILED_COORDINATION:ValueError` instead of the coordinator's reason, such as `AGENT_VETO`.
7. **Map details** (Member 4/2):
   - an aircraft disappears between the end of its arrival turnaround and its departure's gate time
   - engine routes are the fewest-hop paths, so a few look indirect (e.g. `L → B → F → B`)
   - the canvas map has only an `aria-label` for accessibility
   - narrow-screen layout is not checked for the new map
8. **`EQUIPMENT_FAILURE` blocks the whole tug pool** (Member 1). A partial outage needs a capacity parameter.
9. **Data caveats** (Member 2):
   - October 2015 numeric airport IDs are unresolved (needs the BTS lookup)
   - airport time-zone assignment is curated and awaits review
   - the dev scenario ignores gate use by flights outside its 30
10. **Repository housekeeping** (Member 1):
    - `pandas`, `numpy` and `tzdata` are not in the root lockfile (see `backend/data/requirements.txt`)
    - `uv sync` replaces the root `.venv`
    - merged branches (`member2/data`, `integration/*`) can be deleted

## 9. Configuration and secrets

- `.env` is git-ignored and holds the Supabase settings (`SUPABASE_URL`, publishable key, secret key,
  `PERSISTENCE_MODE=supabase_http`). Keep secret keys out of `frontend/` and out of commits.
- For a Supabase-backed UI, set `CORS_ORIGINS` to include `http://127.0.0.1:5173`, the Vite origin.
- Member 3's LIVE adapter reads `OPENAI_API_KEY` and `AIRPORT_MODEL` from the process environment, not `.env`.
- The demo token `local-integration-token` is for loopback only.

## 10. Suggested next steps

| Member | Next action |
|---|---|
| 1 | Review the engine and contract changes in section 6; move the search into a process pool; run the 218-flight scenario; improve the failure reason label |
| 2 | Import the scenarios into hosted Supabase; resolve the October IDs; have the time-zone table reviewed; produce the baseline-vs-agent comparison with `backend.data.evaluate` |
| 3 | Run and record a LIVE negotiation on `sfo-2015-12-21-2000-dev`; check the round and total budgets with real latency |
| 4 | Summarize checks in proposal review; test the map on narrow screens and with the keyboard; run the controller comprehension session |
