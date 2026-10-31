// Provisional UI contract. Member 1's FastAPI OpenAPI document replaces this at integration.
import { writeFileSync, mkdirSync } from "node:fs";
const str = { type: "string" },
  num = { type: "number" },
  bool = { type: "boolean" },
  obj = { type: "object", additionalProperties: true };
const ref = (name) => ({ $ref: `#/components/schemas/${name}` });
const arr = (item) => ({
  type: "array",
  items: typeof item === "string" ? ref(item) : item,
});
const en = (...values) => ({ type: "string", enum: values });
const nullable = (schema) =>
  schema.$ref
    ? { allOf: [schema], nullable: true }
    : { ...schema, nullable: true };
const schema = (properties, optional = []) => ({
  type: "object",
  properties,
  required: Object.keys(properties).filter((k) => !optional.includes(k)),
});
const schemas = {
  Reservation: schema({
    resource_id: str,
    flight_id: str,
    starts_at_utc: str,
    ends_at_utc: str,
  }),
  Assignment: schema({
    flight_id: str,
    operation: en("ARRIVAL", "DEPARTURE"),
    runway_id: str,
    runway_time_utc: str,
    taxi_path_ids: arr(str),
    taxi_start_utc: str,
    taxi_end_utc: str,
    gate_id: nullable(str),
    gate_start_utc: nullable(str),
    gate_end_utc: nullable(str),
    crew_reservations: arr("Reservation"),
    equipment_reservations: arr("Reservation"),
  }),
  Assumption: schema({
    label: str,
    value: str,
    unit: str,
    source_type: en("HISTORICAL", "SOURCED", "SYNTHETIC"),
  }),
  Flight: schema({
    flight_id: str,
    source_row_id: str,
    airline: str,
    flight_number: str,
    tail_number: nullable(str),
    origin: str,
    destination: str,
    scheduled_gate_departure_utc: str,
    scheduled_gate_arrival_utc: str,
    state: str,
    plan: ref("Assignment"),
    assumptions: arr("Assumption"),
  }),
  InstructionDraft: schema({
    kind: en(
      "RUNWAY_CLOSURE",
      "TAXIWAY_BLOCKAGE",
      "EQUIPMENT_OUTAGE",
      "PROTECTED_SLOT",
    ),
    resource_id: nullable(str),
    starts_at_utc: str,
    ends_at_utc: nullable(str),
    flight_id: nullable(str),
    parameters: obj,
  }),
  Instruction: schema({
    instruction_id: str,
    instruction_version: num,
    scenario_id: str,
    base_state_version: num,
    kind: en(
      "RUNWAY_CLOSURE",
      "TAXIWAY_BLOCKAGE",
      "EQUIPMENT_OUTAGE",
      "PROTECTED_SLOT",
    ),
    resource_id: nullable(str),
    starts_at_utc: str,
    ends_at_utc: nullable(str),
    flight_id: nullable(str),
    parameters: obj,
    entered_by: str,
  }),
  CheckResult: schema({
    constraint_id: str,
    domain: str,
    severity: en("HARD", "SOFT"),
    status: en("PASS", "FAIL", "UNKNOWN"),
    affected_ids: arr(str),
    measured_value: nullable(num),
    limit_value: nullable(num),
    unit: nullable(str),
    explanation: str,
  }),
  Impact: schema({
    delay_minutes: num,
    baseline_delay_minutes: num,
    holding_minutes: num,
    taxi_minutes: num,
    maximum_wait_minutes: num,
    unresolved_flights: num,
    sacrifices: arr(str),
  }),
  Plan: schema({
    proposal_id: str,
    proposal_version: num,
    scenario_id: str,
    base_state_version: num,
    instruction_version: num,
    plan_hash: str,
    assignments: arr("Assignment"),
    impact: ref("Impact"),
    expires_at_utc: str,
  }),
  AgentMessage: schema({
    message_id: str,
    negotiation_id: str,
    round: num,
    sender_id: str,
    role: en("ATC", "PILOT", "GROUND"),
    type: en("OFFER", "ACCEPT", "REJECT"),
    base_state_version: num,
    instruction_version: num,
    proposal_id: nullable(str),
    plan_hash: nullable(str),
    payload: obj,
    reasons: arr(str),
  }),
  Negotiation: schema({
    negotiation_id: str,
    status: en(
      "PROPOSED",
      "NEGOTIATING",
      "AWAITING_AUTHORIZATION",
      "AUTHORIZED",
      "REJECTED",
      "STALE",
      "EXPIRED",
      "NO_FEASIBLE_PLAN_FOUND",
    ),
    round: num,
    plan: nullable(ref("Plan")),
    messages: arr("AgentMessage"),
    checks: arr("CheckResult"),
    required_participants: arr(str),
    outcome: en("CONSENSUS", "FALLBACK", "PENDING", "NONE"),
    reason: str,
    model_mode: en("MOCK", "LIVE"),
    model_calls: num,
    duration_ms: num,
  }),
  DataQuality: schema({
    source_type: en("SYNTHETIC", "HISTORICAL"),
    coverage: str,
    layout_note: str,
    unresolved_records: num,
  }),
  Snapshot: schema({
    schema_version: str,
    scenario_id: str,
    state_version: num,
    sim_time_utc: str,
    mode: en("MANUAL", "ASSISTED"),
    flights: arr("Flight"),
    resource_reservations: arr("Reservation"),
    active_instructions: arr("Instruction"),
    data_quality: ref("DataQuality"),
    clock_running: bool,
    pending_negotiation: nullable(ref("Negotiation")),
    last_event_seq: num,
  }),
  ScenarioSummary: schema({
    scenario_id: str,
    name: str,
    airport_iata: str,
    start_utc: str,
    end_utc: str,
    seed: num,
    coverage: str,
  }),
  AuditEvent: schema({
    event_id: str,
    event_seq: num,
    scenario_id: str,
    state_version: num,
    sim_time_utc: str,
    recorded_at_utc: str,
    actor_id: str,
    event_type: str,
    correlation_id: str,
    payload: obj,
  }),
  Command: schema({ command_id: str, base_state_version: num }),
  CommandResult: schema({ state_version: num, event_ids: arr(str) }),
  Preview: schema({ instruction: ref("InstructionDraft"), issues: arr(str) }),
  SessionResult: schema({ session_id: str, snapshot: ref("Snapshot") }),
  ApiError: schema({
    code: str,
    message: str,
    details: obj,
    current_state_version: num,
  }),
};
const response = (type) => ({
  description: "Provisional response",
  content: { "application/json": { schema: type } },
});
const request = (type) => ({
  required: true,
  content: { "application/json": { schema: type } },
});
const paths = {};
const route = (path, method, output, input) => {
  paths[path] ??= {};
  paths[path][method] = {
    parameters: [...path.matchAll(/\{([^}]+)\}/g)].map((m) => ({
      name: m[1],
      in: "path",
      required: true,
      schema: str,
    })),
    responses: { 200: response(output), 409: response(ref("ApiError")) },
    ...(input ? { requestBody: request(input) } : {}),
  };
};
const command = (props) =>
  schema({ command_id: str, base_state_version: num, ...props });
route("/scenarios", "get", arr("ScenarioSummary"));
route("/sessions", "post", ref("SessionResult"), schema({ scenario_id: str }));
route("/sessions/{id}/snapshot", "get", ref("Snapshot"));
route(
  "/sessions/{id}/clock",
  "post",
  ref("CommandResult"),
  command({ action: en("PAUSE", "RESUME", "STEP", "RESET"), seconds: num }),
);
route(
  "/sessions/{id}/instructions/preview",
  "post",
  ref("Preview"),
  ref("InstructionDraft"),
);
route(
  "/sessions/{id}/instructions",
  "post",
  ref("CommandResult"),
  command({ instruction: ref("InstructionDraft") }),
);
route(
  "/sessions/{id}/negotiations",
  "post",
  schema({ negotiation_id: str, status: str }),
  command({ affected_ids: arr(str) }),
);
route("/sessions/{id}/negotiations/{nid}", "get", ref("Negotiation"));
route(
  "/sessions/{id}/proposals/{pid}/decision",
  "post",
  ref("CommandResult"),
  command({ plan_hash: str, decision: en("APPROVE", "REJECT") }),
);
route(
  "/sessions/{id}/control",
  "post",
  ref("CommandResult"),
  command({ mode: en("MANUAL", "ASSISTED") }),
);
paths["/sessions/{id}/events"] = {
  get: {
    parameters: [
      { name: "id", in: "path", required: true, schema: str },
      { name: "after_seq", in: "query", schema: num },
    ],
    responses: {
      200: {
        description: "SSE AuditEvent data; comments are heartbeats",
        content: { "text/event-stream": { schema: str } },
      },
    },
  },
};
mkdirSync("contracts", { recursive: true });
writeFileSync(
  "contracts/ui-draft.openapi.json",
  JSON.stringify(
    {
      openapi: "3.0.3",
      info: {
        title: "PROVISIONAL Member 4 adapter contract — not canonical",
        version: "0.1.0",
      },
      paths,
      components: { schemas },
    },
    null,
    2,
  ) + "\n",
);
