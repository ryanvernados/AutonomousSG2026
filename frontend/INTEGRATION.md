# Member 4 integration handoff

## Contract ownership

Member 1 remains the canonical contract owner. `contracts/ui-draft.openapi.json` is a local draft made because no backend or canonical OpenAPI was present. It preserves the shared specification's required entity fields. Additional response fields and the structures below are **requests for alignment**, not unilateral changes to the shared contract. Generated client types are consumed through `src/api/types.ts`; all transport behavior lives behind `AirportClient`.

The raw `datasets/flights.csv`, airlines and airports files exist, but this frontend deliberately does not normalize or load them. Member 2 owns selecting the qualifying SFO day, UTC normalization, manifests and graph geometry. The demo date is synthetic and has no claimed qualifying source row.

## HTTP expectations

All routes are under the configured `/api/v1` base. All requests carry `Authorization: Bearer <Supabase access token>`. Every operational command carries a UUID `command_id` and `base_state_version`. Backend commands must be idempotent, verify membership, enforce versions and constraints, and durably commit before acknowledging. The frontend never writes reservations, approvals or audit events to Supabase.

| Route | Adapter expectation |
| --- | --- |
| `GET /scenarios` | Array of scenario summaries with ID, name, airport, UTC window, seed and coverage |
| `POST /sessions` | Body `{ "scenario_id": "..." }`; returns `{ "session_id": "...", "snapshot": { ... } }` |
| `GET /sessions/{id}/snapshot` | Snapshot itself, including clock status, last sequence and complete current negotiation or null |
| `POST /sessions/{id}/clock` | Envelope plus `action: PAUSE/RESUME/STEP/RESET`; `seconds` for STEP |
| `POST /sessions/{id}/instructions/preview` | Typed draft directly; returns `{ "instruction": { ...normalizedDraft }, "issues": [] }` |
| `POST /sessions/{id}/instructions` | Envelope plus `instruction: normalizedDraft`; refresh snapshot after success |
| `POST /sessions/{id}/negotiations` | Envelope plus `affected_ids`; accept 202 and obtain progress from events/snapshots |
| `GET /sessions/{id}/negotiations/{nid}` | Documented in draft; current adapter uses negotiation embedded in snapshot instead |
| `POST /sessions/{id}/proposals/{pid}/decision` | Envelope plus exact `plan_hash` and `decision: APPROVE/REJECT` |
| `POST /sessions/{id}/control` | Envelope plus `mode: MANUAL/ASSISTED` |
| `GET /sessions/{id}/events?after_seq=N` | SSE frames containing an AuditEvent; header `Last-Event-ID: N` also sent |

Mutations may return JSON or 204. Expected JSON success is `{ "state_version": 3, "event_ids": ["..."] }`; the UI reloads the authoritative snapshot instead of applying optimistic operational changes. JSON error messages for 401/403/409/422/503 are surfaced. A timeout never implies success; the snapshot is refreshed to resolve uncertainty. No blind command retry occurs.

Example instruction preview:

```json
{
  "kind": "RUNWAY_CLOSURE",
  "resource_id": "28R",
  "starts_at_utc": "2015-07-15T17:00:00.000Z",
  "ends_at_utc": "2015-07-15T17:20:00.000Z",
  "flight_id": null,
  "parameters": {}
}
```

Example confirmation:

```json
{
  "command_id": "b6f9d8d8-c5ba-4c31-9b2d-0333921ee249",
  "base_state_version": 1,
  "instruction": {
    "kind": "RUNWAY_CLOSURE",
    "resource_id": "28R",
    "starts_at_utc": "2015-07-15T17:00:00.000Z",
    "ends_at_utc": "2015-07-15T17:20:00.000Z",
    "flight_id": null,
    "parameters": {}
  }
}
```

Preview leaves state at v1. Confirmation records an instruction and advances state to v2, without publishing a recovery plan. Negotiation returns a plan tied to v2, instruction v1 and its exact hash. Approval sends those identities; backend revalidates and atomically advances to v3. Rejection only changes planning status/audit history, leaving operational reservations unchanged.

## Required alignment before LIVE acceptance

1. **Response shapes and optional fields.** Confirm scenario summaries, `clock_running`, `last_event_seq`, `pending_negotiation`, reservation intervals, flight states, data quality and assumptions. If snapshots carry only negotiation metadata, have `LiveClient.snapshot` hydrate the negotiation via the GET route. No UI component should need to change.
2. **Instruction enum and resources.** Draft values are `RUNWAY_CLOSURE`, `TAXIWAY_BLOCKAGE`, `EQUIPMENT_OUTAGE`, `PROTECTED_SLOT`. Confirm normalized preview shape, issues structure, resource discovery and protected-slot parameters. The current form offers the synthetic resource subset; real resource discovery needs Member 2 infrastructure data.
3. **Geometry and position samples.** The current map is an illustrative SFO schematic with two modelled runways, one out-of-scope crossing runway, A/B/C taxiways and G1–G6. Add sourced graph coordinates, effective date, dependencies and timestamped positions before claiming a historical airport model. The view derives illustrative motion locally and never persists frames.
4. **Negotiation.** Agree on planning states, required participant IDs, outcome (`CONSENSUS/FALLBACK/PENDING/NONE`), model mode and counters. All ballots must match plan ID/hash/state/instruction versions. Missing, contradictory or nonmatching votes block consensus approval. Fallback has no consensus requirement but still needs checks and human authorization. Backend must independently enforce this.
5. **Validation completeness.** The UI blocks no checks, no HARD checks, and any failed/unknown HARD check. Backend must attest to the complete scenario-specific required check suite, revalidate on decision, and refuse absent required inputs. Frontend checks are not a replacement for engine validation.
6. **Stream and reconnect.** Send `id: <event_seq>` and one JSON AuditEvent in `data:`. Send a comment heartbeat within 15 seconds. Support replay from the cursor; expired cursors can reconnect after a full snapshot. After every connection, the UI refreshes the authoritative snapshot before enabling commands. CORS must permit the frontend origin, Authorization, Content-Type and Last-Event-ID. Disable proxy buffering for SSE.
7. **Evaluation and replay.** No durable replay, historical snapshot, baseline-run or counterfactual endpoint is specified. Current replay is the last 100 observed snapshots in this browser; JSONL export is the last 500 received events with actor and arbitrary payload omitted. Comparison shows draft impact fields; mock baseline, holding and taxi metrics are scripted. Add matched-run IDs/seed/inputs, real gate-delay metrics, completion horizon, unresolved flights and evaluation provenance before reporting performance.
8. **Control scope.** Clock controls and manual/assisted mode are implemented. Arbitrary manual reservation editing is not supplied because no validated manual-edit endpoint exists. Manual mode must stop agents, preserve restrictions and never bypass hard checks.
9. **Real access/recovery tests.** Configure authorized and unauthorized users; verify API 401/403, RLS, transactional approval, duplicate commands, durable restart recovery and multi-controller conflicts. Current tests stub access errors; they do not prove backend security.

## Mock boundaries

The mock is an in-memory frontend workflow fixture, not a competing SimPy engine. It emits two rounds, zero model calls, scripted PASS/UNKNOWN checks and reproducible shifted schedules. It does not enforce aircraft performance, graph routing, runway intersections, true gate/crew/equipment capacity or protected-slot feasibility. The normal fixture has no real operational certification. All UI check descriptions disclose this. Reload resets it; snapshots and audit history are not durable.

## Next integration action

Member 1 exports canonical OpenAPI and one real snapshot/negotiation response. Regenerate `src/api/generated.ts`, align `src/api/types.ts` and `LiveClient` with that response, then run the same browser approval-boundary workflow against the backend with Supabase test accounts. Member 2 supplies validated SFO geometry/traffic; Member 3 supplies independent live negotiation evidence. Controller usability feedback remains to be collected.
