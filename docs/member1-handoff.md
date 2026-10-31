# Member 1 platform handoff — contract v1

The supplied Word delivery plan and all four member packets were reviewed. This work implements
Member 1's platform assignment while preserving the other owners' folders. The full application still
needs the historical scenario, live domain agents and controller UI.

## Deliverables

| Location | Delivered |
| --- | --- |
| `backend/contracts/` | Strict Pydantic v1 models, SHA-256 plan identity, OpenAPI, Scenario JSON Schema, generated TypeScript |
| `backend/engine/` | Scenario loader, SimPy clock, reservations, deterministic checks, bounded candidate search, FCFS baseline, fallback, metrics |
| `backend/api/` | Authenticated HTTP, read-only preview, asynchronous coordinator injection, approval boundary, SSE and redacted JSONL |
| `backend/persistence/` | Transactional SQLite offline adapter, direct Supabase Postgres and HTTPS/RPC adapters, idempotent command store, checkpoints |
| `supabase/` | All required tables, RLS, immutable ledgers/plans, backend-only atomic transaction function, generated synthetic seed |
| `fixtures/` | 24-flight synthetic scenario with initial occupancy and three typed disruption examples |
| `scripts/` | Start, verification, contract generation, fixture generation and HTTP walkthrough |
| `tests/` | Cross-module Python cases and actual PostgreSQL migration/RLS/transaction assertions |

## Contract decisions

- Contract identifier is the string `"1"`; timestamps normalize to timezone-aware UTC.
- `Scenario.project_id` is an optional extension with the demo project default. Assign the real project
  when importing a historical scenario. Infrastructure and operational assumptions have explicit typed
  models; reuse these rather than creating an independent graph/schema.
- Snapshot extensions include infrastructure, rules, horizon, airport, seed, clock status and conflict
  alerts so deterministic tools can be pure functions. These additions are optional in v1 and present
  in every backend response. Omitted context yields `UNKNOWN` and blocks approval; agents should consume
  the backend snapshot rather than rebuild a smaller one.
- `GET snapshot` returns `{snapshot,proposals,negotiations}`. Proposed assignments remain inside `Plan`;
  published assignments remain inside `Snapshot.flights[].plan`.
- `state_version` changes only for operational changes. `SessionDocument.revision` separately protects
  all persistence changes, including negotiation status and rejection. Every command has an ID/version.
- `plan_hash()` hashes the entire normalized Plan JSON, excluding only `plan_hash`, with sorted keys and
  compact separators. Finish impact/source/expiry before sealing. Do not mutate it after requesting ballots.
- Intervals are half-open. Runway reservations begin at runway time; taxi route segments split the taxi
  interval in order; gate intervals extend through turnaround/departure. Crew/equipment intervals are explicit.
- New instructions are a read-only preview followed by a confirmed command. `entered_by` and
  `instruction_version` are server-owned. There is no restriction removal endpoint yet; create a new
  session for a pristine scenario. Clock reset retains confirmed restrictions.
- Manual replanning is suspended. Direct arbitrary manual schedule editing has no public endpoint in
  this release; an extension must create a `MANUAL` proposal through the same validation/approval path.

## Member 2 — next integration action

Validate an exported Scenario through `Scenario.model_validate_json(...)`, put it in `SCENARIO_DIR`,
and import the identical JSON to `scenarios`/`scenario_flights` in Supabase. Reference the project UUID
already granted to staff. Use `initial_occupancy` for buffer resources; include wake categories for
initial runway occupants outside the selected traffic slice. Preserve source checksum/row IDs and
original local time fields in the source manifest or provenance assumptions. No historical times,
delay categories or actual airport geometry have been inferred by this platform.

Required flight assumptions for this engine: `wake_category` (unit `category`), `gate_id`, `crew_id`,
`equipment_id` (unit `resource_id`), plus arrival `latest_safe_arrival_utc` (unit `UTC`). Values require
source type and units. Use published enum/category strings and supply pairwise wake values when adding
categories. Taxi nodes use runway/gate resource IDs as endpoints. Gate categories and crew duty windows
must be populated. Historical HHMM, 2400, mixed airport IDs and overnight normalization remain Member 2's work.

## Member 3 — next integration action

Implement `backend.api.coordinator.Coordinator.coordinate(snapshot, affected_ids, negotiation_id)` returning
`CoordinationOutcome`. Inject it with `create_app(coordinator=YourCoordinator(...))` in your application entrypoint.
Read the snapshot copy; use the public engine functions. Never call persistence or commit functions from an agent.

Sender IDs are `ATC`, `GROUND`, `PILOT:<flight_id>`. Return one round-1 `OFFER` and one round-2 `ACCEPT` per
participant for consensus. Every ballot must match proposal ID, hash, base state and instruction version.
Use independent contexts/calls and report actual calls. Set the plan source to `CONSENSUS` before hashing.
Include all affected flights with no existing assignments; validate across untouched reservations too.
Return partial trace plus `reason` on veto/failure. Unknown call counts after an exception/timeout are
reported as unknown. Platform total time is bounded; enforce each round's deadline in the coordinator.
Offload engine search with `asyncio.to_thread` to keep the event loop responsive. Honor cancellation.

Public functions: `load_scenario`, `validate_plan`, `enumerate_candidates`, `compute_impact`,
`select_fallback`; trusted service methods provide `get_snapshot` and `commit_plan`. `search_candidates`
additionally returns placement budgets and rejection metadata. Do not label a fallback consensus.

## Member 4 — next integration action

Import generated `paths` and `components` from `backend/contracts/client.d.ts`; configure the API base
URL and Supabase publishable values. Use Supabase access tokens for all operational calls. Swagger at
`/docs` and `docs/api-examples.http` show requests. Keep current/proposed reservations distinct. Display
`Snapshot.alerts`, source limits, `UNKNOWN` checks, fallback source and model mode. Poll negotiation status
after `202`. Consume SSE with authenticated fetch; reload snapshot and replay with `Last-Event-ID` after
reconnect. Cancel local approval affordances when state/version/hash/expiration changes.

## Verification and remaining gates

Run `./scripts/verify.ps1` and the README's live HTTP walkthrough. Python tests prove separation,
intersection, taxi, gate, crew, equipment, missing fuel, versions, expiry, retry, atomic rollback,
membership, recovery, manual suspension, malformed/vetoed ballots and timeout fallback. PostgreSQL tests
execute migration/seed, compare atomic transaction results and test project RLS with three users.
The embedded runtime uses Auth stubs; it is not a hosted Supabase integration test.

For networks that block Postgres ports, set `PERSISTENCE_MODE=supabase_http` with the backend-only
`SUPABASE_SECRET_KEY`. Apply `202610310002_https_backend_reads.sql` after the main migration. Both remote
adapters commit through the same transaction function; project membership, versions and atomicity are
unchanged. The HTTPS adapter supports paginated reads and keeps privileged credentials off browser clients.

Pending team gates: historical qualifying row/window and sourced map (Member 2), genuine independent
live model negotiation with measured trace (Member 3), controller UI/login/usability/disconnection review
(Member 4), and hosted Supabase smoke tests once credentials are configured. No live-model result, delay
saving, fuel saving or controller usability outcome is claimed. Search is bounded and can miss a feasible
schedule, particularly with fixed gate choices or large connected dependency sets.
