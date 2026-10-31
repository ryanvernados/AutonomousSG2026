# Integration compatibility review — 31 October 2026

The components can form the intended application, but the branches do not currently work together
without integration changes. A direct merge is not an end-to-end implementation.

## Reviewed inputs

- Member 1: current local working tree, including the configured Supabase HTTPS persistence adapter.
- Members 3 and 4: `origin/Patrick/dev`, commit `1a8c7e441f16e84b6f51668a65e6cb2bf7eaf5d0`.
- Member 2: `origin/member2/data`, commit `228a958ab506fcdc40ac7ae846518c4fb4f754ee`.
- Both feature branches were fetched and checked out into isolated temporary worktrees for review.
  The running backend, Supabase data, frontend configuration and main source implementation were not changed.

## Executed checks

| Check | Result |
| --- | --- |
| Member 3 standalone protocol/provider tests | 37 passed |
| Member 4 unit/component/adapter tests | 23 passed |
| Member 4 TypeScript + Vite production build | Passed |
| Member 2 standalone data tests | 22 passed on the installed Python 3.15 runtime |
| Member 2 pinned dependencies on Member 1's Python 3.11 | Resolution failed: numpy 2.5.4 requires Python >=3.12 |
| Member 2 development Scenario against Member 1 Pydantic model | Failed: 883 validation errors across repeated fields |
| Member 2 full Scenario against Member 1 Pydantic model | Failed: 2,011 validation errors across repeated fields |
| Member 2's three disruption Instructions against Member 1 model | All fail enum validation |
| Member 4's session-create payload against isolated Member 1 HTTP API | 422: missing command_id and base_state_version |
| Member 4's example instruction-preview payload against Member 1 API | 422: missing scenario_id/base_state_version and incompatible kind |
| Member 4 snapshot expectation versus actual HTTP result | Incompatible: UI expects Snapshot; backend returns snapshot/proposals/negotiations wrapper |
| Member 3 coordinator given an unmodified canonical snapshot through a minimal snapshot bridge | CANCELLED / UNSUPPORTED_SCHEMA_VERSION |
| Canonical engine constructive-search probe with 101 conflict-free synthetic flights | No complete plan; stopped at 100 placements, with no rejected placements |

Passing standalone mock tests does not demonstrate live integration. No paid model calls, hosted schema
changes, real user sign-in or new live browser workflow were performed for this review.

## Blocking integration changes

### 1. Translate the historical Scenario and import rows

Member 2 exports schema_version `contract-v1-draft`; the canonical value is the string `1`. Member 3's
current coordinator expects the integer 1. Standardize on the canonical model at the integration boundary.

Member 2's Flight assumptions are lists of named records. Member 1 expects a dictionary of Assumption
models, including non-null units. Other Flight metadata currently appears as unapproved top-level fields.
Preserve that provenance through agreed optional fields or source_manifest metadata instead of silently
discarding it or disabling strict validation.

Member 2's `infrastructure.layout/equipment/crews/connections` must map into canonical typed resources.
Translate graph endpoints and runway dependencies, category names/separation configuration, gate choices,
equipment requirements, crew duty limits and initial gate occupancy into complete interval reservations.
Map `latest_safe_landing_utc` to the engine's required `latest_safe_arrival_utc` without changing its value.
The current data does not provide gate_id/equipment_id as per-flight assumptions in the engine's format;
these must be assigned consistently, rather than invented by a UI adapter.

Member 2's importer targets different table columns (`iata_code`, flattened Scenario/Flight fields) and
uses `SUPABASE_DB_URL`. Member 1 stores project-scoped JSON documents. Align the importer with existing
`project_id`, `airport_iata` and `document` columns, supporting the configured persistence transport.
Do not apply a competing database schema over the configured project.

### 2. Implement the real coordinator integration adapter

Member 1 calls `coordinate(snapshot, affected_ids, negotiation_id)` and requires CoordinationOutcome.
Member 3 provides `run(session_id, affected_ids, instruction_version=..., objective=..., baseline_plan=...)`
and returns a dictionary with `proposal`, `decision`, `metrics` and `trace`.

Member 3's EnginePort additionally needs async snapshot, dependency, domain-view, candidate, validation,
impact, fallback and hash wrappers. Its candidate and fallback results have wrapper metadata rather than
the canonical list/FallbackResult shapes. Offload the synchronous engine work to keep the API loop usable.

Translate confirmed clock status into the coordinator's paused-clock assertion, or update the coordinator
to consume `Snapshot.clock_status`. Map objective metrics deliberately: Member 1 stores values under
Impact.aggregate while Member 3 scores top-level values. Preserve Member 1's fixed objective weights.

Agent IDs currently use `atc`, `ground`, `pilot:<id>`; Member 1 requires `ATC`, `GROUND`, `PILOT:<id>`.
Message reasons are structured objects in Member 3 and strings in the current canonical AgentMessage.
Align these fields and expose complete required-participant information to the UI.

Use the canonical Plan model and hash before round 2. Plan.source must be CONSENSUS before sealing for
consensus ballots; changing it afterward changes the hash. Preserve and revalidate all exact-plan ballots.
Member 3's narrowed offer windows are not typed Instruction objects; implement an explicit candidate
constraint mapping rather than passing those dictionaries directly into active_instructions.

Implement PersistencePort.append through the durable backend without changing the operational version.
The bundled MemoryPersistence and MockEngine are development doubles, not production integrations.
The stock CLI still uses MockEngine even when its model adapter is LIVE.

### 3. Align the UI's live adapter and resources

Fix createSession to send a fresh command_id and base_state_version=0. Unwrap SnapshotResponse and hydrate
the latest negotiation/proposal using the negotiation GET route. Map clock_status, event cursor, proposal
source, ballots, validation results and Impact fields into the view model consumed by components.

The UI's draft enum names map as follows:

| UI / Member 2 draft | Canonical backend |
| --- | --- |
| RUNWAY_CLOSURE | RUNWAY_UNAVAILABLE |
| TAXIWAY_BLOCKAGE | TAXIWAY_BLOCKED |
| EQUIPMENT_OUTAGE | EQUIPMENT_FAILURE |
| PROTECTED_SLOT | PROTECTED_SLOT |

Preview drafts also need canonical scenario/state identity. Regenerate types from the actual backend
OpenAPI and retain a deliberate UI view-model mapping; simply regenerating the existing draft types
does not reconcile component assumptions.

Replace the form's fixed runways/taxiways/gates/equipment and the indicative map with resource discovery
and geometry from the selected scenario. Member 2 uses IDs such as 10R-28L, unlike the UI's 28L/28R.
The data has a 30-flight development fixture and 218-flight full scenario, with 102 simulated gates.

Set VITE_API_MODE=live, the API URL, and only the publishable Supabase values. The backend CORS list must
include the exact Vite origin: the launcher uses 127.0.0.1:5173 while the current backend default is
localhost:5173. Those are different origins. Keep backend secret keys out of frontend files.

### 4. Fix the canonical search budget before full-traffic acceptance

The shared specification limits candidate plans to 100. The current engine instead limits individual
flight-placement attempts to 100. It can only return a complete plan after placing every selected flight,
so a new session with 218 unscheduled flights cannot produce a full plan under this budget.

Separate the complete-plan limit from the placement/search-work budget. Retain explicit time/work limits,
metadata, hard checks and NO_FEASIBLE_PLAN_FOUND semantics. Test the 30-flight development fixture first,
then the full 218-flight traffic slice and nonempty initialization occupancy.

### 5. Integrate repository configuration and runtime deliberately

The current Member 1 HEAD and both reviewed feature branches have no common Git merge base. A normal
merge fails with unrelated histories. Import owned folders selectively or use an explicit integration
branch/worktree with a reviewed conflict resolution; preserve Member 1 root configuration and contracts.

Standardize a Python runtime compatible with Member 2's pinned numeric dependencies and freeze it in the
shared lockfile. Current Member 1 local HTTPS persistence changes are also uncommitted and must be included
in any integration checkpoint; the remote main commit alone does not contain them.

## Suggested acceptance sequence

1. Freeze one shared contract and capture actual snapshot/negotiation response fixtures.
2. Load and validate Member 2's mapped 30-flight fixture; verify import and provenance.
3. Exercise the UI against Member 1 with explicit FALLBACK plans and staff approval.
4. Connect Member 3 through the real engine/persistence adapters in MOCK model mode.
5. Verify consensus, veto, timeout, stale state, manual cancellation and authorization against one backend.
6. Fix/verify full-slice search and repeat with all 218 flights, including occupied gates and rotations.
7. Run independent LIVE model calls with actual budgets/metrics, then collect controller feedback.

The existing modules are useful integration inputs. They are not currently a combined, validated system.
