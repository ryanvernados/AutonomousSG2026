# Member 3: independent agents and bounded coordination

Implemented from [the delivery plan](../../docs/plans/Airport_Simulation_Team_Delivery_Plan.docx), the common
specification, and [the member 3 assignment](../../docs/handoff/03_MEMBER_3_AGENT_ASSIGNMENT.txt). Changes stay inside
`backend/agents/` and `backend/coordination/`. The existing Git branch is retained.

This module provides ATC, ground, and one pilot agent per affected flight; strict
role-specific offer validation; two-round unanimous ballots; injected engine and
persistence interfaces; mock and live model adapters; JSONL traces; and tests.
It has **no schedule commit capability**. Consensus and fallback both require
staff authorization through Member 1's API.

## Run on this Windows computer

From the repository root in PowerShell:

```powershell
# No installation or model key needed. Finds the bundled Python automatically.
./backend/coordination/run.ps1
./backend/coordination/run.ps1 -Mode test
./backend/coordination/run.ps1 -Behavior pilot_veto
```

With any Python 3.11+ installation, on any OS:

```sh
python -m backend.coordination
python -m unittest discover -s backend/coordination/tests -v
python -m backend.coordination --behavior ground_conflict
python -m backend.coordination --behavior invalid_message
python -m backend.coordination --behavior missing_ballot
python -m backend.coordination --behavior timeout --round-seconds 0.05 --total-seconds 1
python -m backend.coordination --engine-failure unknown_fuel
```

Mock execution and tests use only the standard library. No root dependency files
or teammate-owned modules were created. Python namespace packages allow imports
from the repository root without a new `backend/__init__.py`.

The successful demo returns this subset (IDs, timestamps, and durations vary):

```json
{
  "status": "AWAITING_AUTHORIZATION",
  "decision": "CONSENSUS",
  "model_mode": "MOCK",
  "engine_mode": "MOCK",
  "persistence_mode": "MOCK",
  "historical_traffic": false,
  "authorized": false,
  "metrics": {"rounds": 2, "model_calls": 8, "message_count": 8}
}
```

The default trace is `backend/coordination/output/trace.jsonl` (gitignored).
`fixtures/mock_consensus.jsonl`, `mock_pilot_veto.jsonl`, and `mock_timeout.jsonl`
are saved execution evidence. They contain expiring proposals and must never be
submitted for authorization; run the demo to generate a fresh proposal.

## What the demonstration shows

There are three synthetic flights and two participating pilots. Selecting only
`DEPART-1` expands to `ARRIVE-1` through the engine dependency graph. One unaffected
flight remains in the snapshot used by every deterministic validation.

After a synthetic runway restriction ends, an FCFS comparison places the departure
at 16:10 UTC and the arrival at 16:14 UTC. The arrival has a declared synthetic
latest-arrival limit of 16:12 UTC. The recovery gives the arrival 16:10 and moves
the departure to 16:14: the departure concedes four minutes and the arrival gains
four. Two distinct synthetic taxi paths and a gate interval allow this fixture.
These are runway-time differences, not measured fuel savings or gate-delay metrics.
The FCFS comparison is intentionally infeasible for this fuel example; it is not
the safety-constrained baseline required for the team's final evaluation.

`MockEngine` checks only simplified closure, runway separation, nonparticipant
occupancy, arrival limit, taxi segment, gate interval and ground availability.
It is **not** Member 1's full wake/intersection/crew/equipment validator or a sourced
SFO airport model. Both successful model consensus and all fallback proposals
still need the real validator and approval transaction during integration.

## Protocol and authority

1. Require schema version 1, ASSISTED control and an engine-attested paused clock.
   Freeze the JSON snapshot, instruction version, objective and baseline.
2. Expand flight dependencies to a fixed point; create ATC, ground and separate
   pilots with separate views and histories. Engines must include runway, taxi,
   gate, crew, equipment and aircraft-rotation dependencies.
3. Round 1 makes one outbound request per participant, concurrently. Each offer
   must use its own authorized resource/flight windows and UTC units/provenance.
   Agents may narrow windows. Arrival pilots must supply a known latest-safe-arrival
   bound. Malformed, absent, unauthorized or timed-out replies stop negotiation.
4. Request at most 100 candidate plans. Validate against the **entire** frozen
   schedule, independently check offer windows, compute impact through the engine,
   and rank by the frozen weighted objective with stable assignment/ID tie breaks.
   Empty reports, invalid checks, HARD FAIL and HARD UNKNOWN prevent consensus.
5. Hash the final plan, including impact and versions, using the injected shared
   hash utility. Round 2 sends the same exact proposal to every participant;
   ballots cannot amend it. Require every ACCEPT with matching identity/versions.
6. Recheck snapshot freshness, expiry, hash and all deterministic constraints.
   Persist consensus and return `AWAITING_AUTHORIZATION`. No schedule is changed.
7. Failure requests Member 1's deterministic fallback, revalidates it and labels
   it `FALLBACK`. A fallback does not imply agent agreement and may differ from
   agents' soft offers; it must still satisfy all engine hard limits. A bounded
   search failure says `NO_FEASIBLE_PLAN_FOUND`, including search limits.

Defaults: 30 seconds per round, 60 seconds total, with two seconds reserved inside
that total for fallback/final persistence. Both engine work and persistence are
inside the total deadline. No transport retries or corrective model calls are
made. `model_calls` counts dispatched adapter attempts, including failed requests;
`message_count` counts valid persisted replies. Per-call model identity, latency,
usage when available, mode and errors are recorded separately.

Cancelled tasks cancel sibling calls and attempt a bounded cancellation record,
then propagate cancellation to the API owner. Manual or stale sessions return no
proposal and never run fallback on an old snapshot. If the deadline or persistence
fails, the coordinator returns `FAILED` without a proposal; the API owner must
persist its terminal command status. Async injected implementations must cooperate
with cancellation and must not block the event loop.

## Live model run

The provider-neutral `ModelAdapter.generate(request, timeout_seconds)` returns
`ModelResponse(payload, model, usage)`. `ResponsesAdapter` implements OpenAI's
[Responses structured-output format](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)
using a caller-owned [HTTPX async client](https://www.python-httpx.org/async/).
All domain calls are distinct; no provider conversation is shared between agents.
No tools capable of mutation or extra model turns are exposed. Read-only domain
information is provided by the injected engine's authorized views.

With Python on PATH:

```powershell
python -m venv backend/coordination/.venv
./backend/coordination/.venv/Scripts/python.exe -m pip install -r backend/coordination/requirements-live.txt
$env:OPENAI_API_KEY = 'your-key'
$env:AIRPORT_MODEL = 'your-available-structured-output-model'
./backend/coordination/run.ps1 -Mode live
```

On this computer, if `python` opens the Microsoft Store, create the environment
with the bundled interpreter:

```powershell
$airportPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
& $airportPython -m venv backend/coordination/.venv
```

On Linux/macOS use `.venv/bin/python` and shell `export` instead. `.env.example`
contains placeholders; environment files are not automatically loaded. Secrets stay
backend-only. Provider responses are not stored by the provider request (`store:
false`); local trace persistence is controlled separately. Export redaction masks
credential fields, bearer/API-key patterns, email addresses and tail numbers.
Review free-text exports before sharing; pattern redaction is not a general DLP tool.

Assumptions: the chosen model supports Responses strict JSON Schema; the account
has API access; normal TLS/network connectivity exists; HTTPX 0.28.1 is installed.
Missing credentials fail rather than switching to MOCK. Provider refusal, truncated
output and invalid JSON stop negotiation without retries. The model is explicitly
configured instead of guessing a model available to the user's account.

**Live acceptance is pending:** no model credentials were available during
implementation. Transport parsing/request construction were tested with a fake
HTTP client. No successful live API call or historical live negotiation is claimed.
Even in `--mode live`, this standalone demo labels its engine and persistence MOCK.

## Dataset inspection

`fixtures/dataset_profile.json` records SHA-256 checksums, exact row counts, headers,
quality counts and three traceable qualifying SFO delay rows from all supplied CSVs.
The read-only scanner uses streaming CSV reads and does not rewrite raw inputs.

```sh
python -m backend.coordination.inspect_dataset --dataset dataset
```

Observed: 5,819,079 flight rows, 322 airports and 14 airlines. There are 295,974 rows
with literal SFO origin/destination identifiers and 486,165 rows with numeric airport
identifiers; these are not silently mapped or discarded. The scan also records
missing data, cancellations, diversions and `2400` clock strings. Source row numbers
are one-based data rows excluding the header, paired with the complete file checksum.

These CSVs do not contain fuel limits, runway allocations, gates, crew, equipment
or a taxi graph. Local HHMM values are not UTC, delay minutes do not establish a
closure duration, and October numeric airport identifiers need Member 2's sourced
lookup. The profile is evidence for integration, **not** a historical Scenario.
Member 2 still owns airport-day selection, UTC normalization, provenance and full
window coverage. No historical scenario or operational assumption was fabricated
from the CSVs for the mock demo.

## Integration handoff to Member 1

The repository had no canonical contracts, engine, fixtures or Supabase adapter.
No competing `backend/contracts` models or database schemas were introduced.
`protocol.py` defines only the local agent-reply payload schema; `ports.py` defines
the boundary to the shared implementation. Adapt Pydantic objects with
`model_dump(mode="json")`, and validate results with the canonical models.

```python
coordinator = Coordinator(engine_adapter, model_adapter, persistence_adapter)
result = await coordinator.run(
    session_id,
    affected_ids,
    instruction_version=current_instruction_version,
    objective=frozen_objective,
    baseline_plan=baseline_plan.model_dump(mode="json"),
    negotiation_id=negotiation_id,
)
```

The next integration action is to implement `EnginePort` and `PersistencePort`:

| Interface | Required mapping |
| --- | --- |
| `get_snapshot` | Whole schedule, versioned confirmed instructions, `schema_version: 1`; adapter attests `data_quality.clock_paused: true` only while paused |
| `dependencies` | Complete flight dependency neighbors, not just directly disrupted flights |
| `domain_view` | Role-authorized windows and operational facts; pilots get only their own private limits and `operation` |
| `enumerate_candidates` | Common engine function plus `{plans, limit, search_exhausted}` search metadata |
| `validate_plan` | Every configured hard check, including missing information as UNKNOWN, across all flights |
| `compute_impact` | Metrics for the same frozen objective and baseline; must return every weighted metric with documented units |
| `plan_hash` | Canonical Member 1 utility; mock hashing is only a development substitute |
| `select_fallback` | Deterministic common engine fallback plus `{plan, limit, search_exhausted}`; hash and expiry included |
| `append(record)` | Persist validated agent messages, checks and negotiation events through Member 1's Supabase adapter; use `record_id` idempotently |

Candidate assignments must cover exactly the expanded affected set. Leave unchanged
nonparticipants in the snapshot instead of repeating their assignments in the
proposal. A candidate that changes another flight cannot receive consensus without
that flight's pilot; report incomplete dependencies and start a new authorized run
against a correctly expanded set rather than adding a third round.

The local view/offer convention requiring contract-owner agreement is:

```json
{
  "operation": "ARRIVAL",
  "authorized_windows": [{
    "kind": "LATEST_SAFE_ARRIVAL",
    "subject_id": "ARRIVE-1",
    "resource_id": null,
    "starts_at_utc": null,
    "ends_at_utc": "2015-01-01T16:12:00+00:00",
    "unit": "UTC",
    "source_type": "SYNTHETIC"
  }]
}
```

ATC uses `RUNWAY_WINDOW`; pilots use `TIMING_WINDOW` and, for arrivals,
`LATEST_SAFE_ARRIVAL`; ground uses `GROUND_WINDOW`. Ground windows enclose full
gate, taxi, crew or equipment reservations. Nested crew/equipment intervals must
be adapted to `{resource_id, starts_at_utc, ends_at_utc}`; the common document did
not specify that nested shape. Taxi segment occupancy needs the full engine check.
Every pilot arrival view must include its latest-safe-arrival limit; unknown limits
are not invented. With no active instruction, use instruction version 0; the API
owner also checks command versions and invalidates runs after instruction removal.

`objective.weights` maps named numeric impact metrics to finite nonnegative
weights. The mock uses runway-wait minutes only. The production objective and
units are Member 1's responsibility and must remain fixed across comparisons.

`AGENT_MESSAGE.payload` contains only the validated offer constraints. Model output
cannot choose sender IDs, negotiation IDs, message IDs, database tables or commands.
Trace `sequence` is negotiation-local; Member 1 allocates global audit `event_seq`.
Persist messages in `agent_messages`, checks in `validation_results`, and status in
`negotiations` using the existing adapter, not arbitrary writes from LLM payloads.

The API owner retains authentication, command idempotency, per-disruption run
deduplication, pause/resume, immediate cancellation on manual mode, asynchronous
202 responses, SSE, and final atomic approval. Reusing a `negotiation_id` does not
deduplicate model calls here; prevent duplicate starts before invoking `run()`.
On approval, recheck versions, expiry, immutable hash and all hard constraints.

## Verification and remaining gates

Verified locally with Python 3.12.14: **37 tests passed** using the documented
PowerShell launcher. The mock CLI also produced consensus, pilot-veto fallback
and timeout fallback traces. The successful run used two rounds, eight calls and
eight valid messages; timeout stopped in round one after four calls.

The standard-library suite covers consensus, pilot and ground vetoes, invalid
messages, missing/wrong ballots, fuel-limit tampering and missing data, candidate
offer violations, objective ranking, nonparticipant conflicts, stale snapshots and
instructions, manual mode, clock pause, expiry, persistence errors, cancellation,
deadline exhaustion, trace ordering/redaction and live transport response handling.
Every scenario asserts at most two rounds and eight calls for four participants.

The local mock gate is verified. Still pending: canonical model compatibility,
Member 1's real validator and Supabase persistence, Member 2's historical scenario,
the live-model acceptance run, and the integrated UI/approval workflow. They are
not represented as passing by the mock trace or unit tests.
