# Airport disruption simulator — Member 1

Member 1 owns the platform: canonical v1 models, FastAPI API, SimPy clock, resource reservations,
deterministic checks, baseline/fallback search, staff approval, persistence and Supabase access policies.
The implementation follows the supplied common specification and Member 1 assignment.

The team stack is **React + TypeScript + Vite + React Konva** (Member 4), **Supabase Auth + Postgres**
(Member 1), **Python + FastAPI + Pydantic + SimPy** (Member 1), **pandas** (Member 2), and independent
ATC/pilot/ground model adapters (Member 3). Python dependencies are pinned in `uv.lock`; contract tooling
is pinned in `package-lock.json`.

This delivery is the runnable Member 1 backend. Historical processing, a sourced SFO map, live model
agents and the controller frontend remain the respective teammates' workstreams. The local 24-flight
fixture is explicitly synthetic. Passing the checks demonstrates simulation feasibility only.

## Start locally (Windows PowerShell)

Install Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/), then run:

```powershell
.\scripts\start.ps1
```

This installs locked dependencies, copies `.env.example` to `.env` if needed, and starts one worker on
loopback. Open [interactive API documentation](http://127.0.0.1:8000/docs), click **Authorize**, and enter
the `DEMO_TOKEN` value from `.env`. The first local demo token is `local-demo-change-me`.

Equivalent command after environment setup:

```powershell
uv run uvicorn backend.api.app:app --host 127.0.0.1 --port 8000 --workers 1
```

Run the HTTP walkthrough in another terminal:

```powershell
uv run python -m scripts.demo
```

It creates a session, previews and confirms a runway restriction, starts asynchronous planning,
reviews the labelled fallback, explicitly approves it, advances the clock, and checks SSE reconnection.
The expected result contains 24 assignments, `proposal_source: FALLBACK`, `state_version: 3`, and
`hard_checks: PASS`. No LLM key or CSV processing is required.

`AUTH_MODE=demo` and a nonempty token must be explicitly configured. Without configuration the app
requires Supabase. Demo mode uses durable SQLite and a synthetic controller identity, and is intended
for loopback development. Keep one backend process/worker per database; cross-process session leases
and distributed workers are outside this release.

## Verify

Install Node.js 22+ for generated types and embedded PostgreSQL verification, then run:

```powershell
.\scripts\verify.ps1
```

The command runs Python integration tests, lint, PostgreSQL migration/RLS/transaction tests, and
regenerates OpenAPI and TypeScript types. Individual commands:

```powershell
uv sync --frozen
uv run pytest -q
uv run ruff check backend scripts tests
npm ci --ignore-scripts
npm run test:sql
uv run python -m scripts.export_contracts
npm run types
```

The SQL suite runs the actual migration and seed in PGlite's embedded PostgreSQL engine. It creates
Supabase-compatible Auth stubs solely inside its disposable test database. Hosted Supabase Auth,
connection settings and deployment infrastructure still need testing with the team's project.

## API v1

Every route except health/docs requires an `Authorization: Bearer <access_token>` header. Every command
requires `command_id` and `base_state_version`, including session creation (initial version `0`).
Retries of the same request return the original response. Reusing an ID with different content gives
`409`. Check project roles before reading, replaying commands or writing.

| Route | Behavior |
| --- | --- |
| `GET /api/v1/scenarios` | Accessible scenario inputs and provenance |
| `POST /api/v1/sessions` | Create a session and initial snapshot (`201`) |
| `GET /api/v1/sessions/{id}/snapshot` | `{snapshot, proposals, negotiations}` |
| `POST /api/v1/sessions/{id}/clock` | `PAUSE`, `RESUME`, `STEP` (seconds), `RESET` |
| `POST /api/v1/sessions/{id}/instructions/preview` | Read-only normalization and issues |
| `POST /api/v1/sessions/{id}/instructions` | Confirm typed restriction; invalidate pending work |
| `POST /api/v1/sessions/{id}/negotiations` | Pause/freeze, expand affected set, return job ID (`202`) |
| `GET /api/v1/sessions/{id}/negotiations/{nid}` | Structured trace, proposal and validation results |
| `POST /api/v1/sessions/{id}/proposals/{pid}/decision` | `APPROVE` or `REJECT` with exact `plan_hash` |
| `POST /api/v1/sessions/{id}/control` | `MANUAL` or `ASSISTED` |
| `GET /api/v1/sessions/{id}/events?after_seq=N` | Authenticated SSE; also accepts `Last-Event-ID` |
| `GET /api/v1/sessions/{id}/events/export` | Redacted JSONL audit copy |
| `GET /api/v1/sessions/{id}/baseline` | Read-only safety-constrained FCFS result |
| `GET /api/v1/sessions/{id}/metrics` | Fixed-horizon metrics, unresolved flights, limits and protocol counts |

Successful commands return `session_id`, `state_version`, `event_ids`, and `snapshot`; planning also
returns `negotiation_id`. Error bodies have `code`, `message`, `details`, `current_state_version`.
Invalid inputs use `422`, stale state/plan conflicts use `409`, unavailable dependencies use `503`.

Examples are in [docs/api-examples.http](docs/api-examples.http). The normative schemas are
[backend/contracts/openapi.json](backend/contracts/openapi.json) and
[backend/contracts/scenario.schema.json](backend/contracts/scenario.schema.json).
Member 4 can import the generated types from [backend/contracts/client.d.ts](backend/contracts/client.d.ts).

Use authenticated `fetch()` to consume SSE; native `EventSource` cannot supply the bearer header.
On reconnect, reload the snapshot and resume after the last processed event sequence. Never put access
tokens in URLs. The stream periodically rechecks authentication and membership. Each event is committed
before publication; event sequence numbers are ordered per session. JSONL and SSE redact credential,
prompt and identity fields. Raw durable audit records stay in the backend store.

## Supabase setup

1. Apply both SQL files in `supabase/migrations/` in filename order to your Supabase database, then `supabase/seed.sql`.
2. Create real users through Supabase Auth. Add their project memberships from a privileged SQL session:

```sql
insert into public.project_members(project_id,user_id,role)
values ('00000000-0000-0000-0000-000000000010','YOUR_AUTH_USER_UUID','controller');
```

3. Configure `.env` with `AUTH_MODE=supabase`, `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` and
   server-only `DATABASE_URL`. Use a TLS-enabled direct/session-pooler Postgres connection. A server-only
   `postgres` connection works; the transaction function is also granted to `service_role`.
4. Member 4 signs users in with Supabase Auth and sends their access tokens to FastAPI. FastAPI validates
   each token through Supabase `/auth/v1/user` and checks membership and role. Populate only publishable
   client configuration from `frontend.env.example`.

If `DATABASE_URL` contains the literal `[YOUR-PASSWORD]` placeholder, privately enter your existing
database password using `uv run python -m scripts.configure_supabase` in your own terminal. The helper
percent-encodes special characters, checks the connection and airport schema with read-only queries,
then saves the connection and sets `AUTH_MODE=supabase` only after verification succeeds. It never
prints the password or full connection string. Restart the backend afterward. This helper uses your
existing database password; it does not create users, change passwords or apply migrations.

If the network cannot reach Postgres ports, use the HTTPS adapter instead:

```dotenv
AUTH_MODE=supabase
PERSISTENCE_MODE=supabase_http
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_PUBLISHABLE_KEY=sb_publishable_YOUR_KEY
SUPABASE_SECRET_KEY=sb_secret_YOUR_BACKEND_ONLY_KEY
DATABASE_URL=
```

Obtain the existing secret key from the project's Server connection panel and keep it in the backend's
ignored `.env` only. Never put it in frontend variables. The second migration grants backend reads;
operational writes still use the original atomic Postgres transaction function over HTTPS. Browser
access continues to use the signed-in user's token, explicit read grants and RLS. This mode does not
need the database password. Restart the backend after configuration changes.

To get a signed-in user token for Swagger before the frontend is available, run:

```powershell
uv run python -m scripts.login
```

Enter your simulator application's email and password at the local prompts. The helper verifies login,
checks access to the local backend and prints a short-lived access token for Swagger's **Authorize**
field. Keep that token private. This uses the application's Auth user, not the Supabase dashboard login.

All tables enable RLS. Browser roles receive project-scoped reads and no operational mutation grants.
The backend-only `commit_simulation_session` function holds a session lock, compares the durable
revision, checks actor membership and pending plan identity/version/expiry, persists reservations,
approvals, checkpoint, command response and audit events in one transaction. Published plan bodies
are immutable. Audit/approval ledgers reject updates and deletes.

Deterministic Python checks run immediately before approval. Database compare-and-swap proves the
validated input did not change before the transaction commits, and expiration is rechecked inside
the transaction. SQLite additionally reruns the Python checks while holding its write transaction.
Nothing updates an authoritative in-memory copy ahead of persistence: reads reload the durable document.

## Rules, assumptions and recovery

- UTC timestamps must include a timezone. Intervals use `[start,end)`; touching reservations do not
  overlap. Display SFO times with `America/Los_Angeles` in the frontend, including date and zone.
- Runway occupancy is 60 seconds; configured wake pairs range from 120–180 seconds. The synthetic
  28L/28R pair is conservatively dependent. These are declared demo parameters, not sourced procedures.
- Each taxi edge reserves its equal share of the declared taxi interval and must receive its minimum
  traversal time. Paths must connect runway and gate; closures and segment capacity are checked.
- Gates require compatible categories and remain reserved through minimum turnaround (20 minutes)
  or departure readiness. Adjacent same-tail arrival/departure legs require turnaround and continuous
  gate occupancy. Shared gate intervals for the same known aircraft count as one occupied aircraft.
- Crews require identity, availability, duty limit and movement coverage. Equipment uses a two-unit
  synthetic resource pool. All assumptions include source type and units.
- Each arrival requires a timezone-aware `latest_safe_arrival_utc` assumption. Missing or malformed fuel,
  wake or crew data yields `UNKNOWN` and blocks approval. Hard constraints never become objective costs.
- Baseline and fallback use the identical validator. Search examines at most 100 placements (including
  rejected placements), reports rejection reasons, and ranks full feasible plans with fixed weights:
  delay `1`, holding `2`, taxi `0.2`, max wait `0.5`. This bounded constructive search is not exhaustive.
- No coordinator is bundled. Without one, planning returns `COORDINATOR_UNAVAILABLE` and a labelled
  `FALLBACK` proposal for staff authorization, or `NO_FEASIBLE_PLAN_FOUND` with limits. It never claims
  live consensus. Inject Member 3's implementation through `create_app(coordinator=...)`.
- Coordinator integration requires all offers and exact-plan ballots from ATC, ground and every
  affected pilot within two rounds. Member 3 enforces the per-round deadline; the platform enforces
  the total deadline, trace shape, exact identity, versions and hard checks. Veto, malformed output or
  timeout triggers fallback. Unavailable call counts remain unknown rather than invented.
- The affected set conservatively includes the whole unfinished traffic slice. This covers dependencies
  at the cost of extra participants. Search uses fixed declared gates and up to four routes per runway;
  Member 2 can supply additional graph edges and the full traffic slice.
- `MANUAL` cancels pending jobs/proposals, preserves restrictions/reservations and retains conflict alerts.
  Staff control, instructions and clock changes invalidate pending proposals. Existing schedules remain
  distinct from proposals; rejection does not change the schedule or operational state version.
- `RESUME` advances SimPy in one-second persisted steps. `STEP` requires pause. `RESET` restores initial
  traffic/time while preserving restrictions, mode and monotonic state versions; use a new session for
  a pristine scenario. Stop the process and restart to recover its last committed checkpoint. Recovered
  running clocks pause explicitly; interrupted negotiations get a labelled restart fallback.
- Metrics retain unresolved and unfinished flights. Holding is a gate-delay proxy with fixed taxi time;
  no fuel savings are claimed. Connection penalties, overtime and detailed turnaround-delay metrics
  are returned as unsupported/null until their input models arrive. The fixture models crew work only
  within the declared scenario; it has no historical pre-window duty data.

Approving the combined schedule is a simulator convenience, not an operational pilot readback or
transfer of real ATC/airline/ground authority.

## Teammate handoff

Read [docs/member1-handoff.md](docs/member1-handoff.md) for exact integration boundaries and outstanding
team acceptance gates. Raw `dataset/` files are ignored; the existing `flights.csv` is about 592 MB and
remains untouched. Regenerate the synthetic inputs with `uv run python -m scripts.build_fixture`.

Implementation references: [FastAPI bearer auth](https://fastapi.tiangolo.com/tutorial/security/first-steps/),
[Supabase RLS](https://supabase.com/docs/guides/database/postgres/row-level-security),
[Supabase database functions](https://supabase.com/docs/guides/database/functions), and
[SimPy Environment](https://simpy.readthedocs.io/en/stable/api_reference/simpy.core.html).

## Run the integrated system

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-integrated.ps1
```

Opens the Member 1 API with Member 3's coordinator injected (http://127.0.0.1:8000/docs) and the
Member 4 UI in live mode (http://127.0.0.1:5173), serving Member 1's synthetic scenario and Member 2's
SFO 2015-12-21 scenarios. It uses `AUTH_MODE=demo` with local SQLite and agents in MOCK model mode
(`-ModelMode live` for real model calls); settings are passed as environment variables, so `.env` is
not changed. Verify with:

```powershell
$env:DEMO_TOKEN='local-integration-token'; uv run python -m backend.coordination.walkthrough
cd frontend; $env:LIVE_E2E='1'; node scripts/test-e2e.mjs e2e/live-integration.spec.ts
```

The bridge between the coordinator and the platform is `backend/coordination/platform_adapter.py`;
the UI maps the canonical API in `frontend/src/api/canonical.ts`. Pick `sfo-2015-12-21-2000-dev` for the
historical SFO evening: the map draws the FAA runways, taxiways and simulated gates, and after approval
the Ground control panel lists each flight's gate, taxi route and runway slot. Use **+10 min** / **+1 min**
to move the clock (Resume runs in real time).

## Members 3 and 4: agents and controller UI

The React controller (`frontend/`) and the agent coordinator (`backend/coordination/`,
`backend/agents/`) can also run as standalone **mock demos**, as below. Member 2's data pipeline is
documented in [backend/data/README.md](backend/data/README.md).

### Run on Windows

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

### Verify

```powershell
# Python tests, frontend tests, and production build
powershell -NoProfile -ExecutionPolicy Bypass -File .\check.ps1

# Also install Chromium and exercise the UI in a browser
powershell -NoProfile -ExecutionPolicy Bypass -File .\check.ps1 -Browser
```

For other operating systems, run `pnpm install --frozen-lockfile`, `pnpm dev`,
`pnpm test`, and `pnpm build` from `frontend/`. Run `python -m backend.coordination`
and `python -m unittest discover -s backend/coordination/tests -v` from the root.

### Repository layout

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

### Component documentation

- [Frontend setup and workflow](frontend/README.md)
- [Frontend integration requirements](frontend/INTEGRATION.md)
- [Agent setup and protocol](backend/coordination/README.md)
- [Common specification](docs/handoff/00_READ_FIRST_COMMON_SPECIFICATION.txt)
