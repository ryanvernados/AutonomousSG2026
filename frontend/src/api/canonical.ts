// Mapping between Member 1's canonical v1 API (backend/contracts) and the UI view model
// (contracts/ui-draft.openapi.json). Components keep consuming the UI types; only
// LiveClient talks canonical. Unknown or missing canonical fields stay visible as
// empty/zero values rather than being invented.
import type {
  Assignment,
  CheckResult,
  Flight,
  Instruction,
  InstructionDraft,
  Negotiation,
  Plan,
  ScenarioSummary,
  Snapshot,
} from "./types";


type Json = Record<string, any>;

export type UiKind = InstructionDraft["kind"];
export const KIND_TO_CANONICAL: Record<UiKind, string> = {
  RUNWAY_CLOSURE: "RUNWAY_UNAVAILABLE",
  TAXIWAY_BLOCKAGE: "TAXIWAY_BLOCKED",
  EQUIPMENT_OUTAGE: "EQUIPMENT_FAILURE",
  PROTECTED_SLOT: "PROTECTED_SLOT",
};
const KIND_TO_UI: Record<string, UiKind> = Object.fromEntries(
  Object.entries(KIND_TO_CANONICAL).map(([ui, c]) => [c, ui as UiKind]),
);

/** Resource IDs discovered from the scenario infrastructure, for the instruction form. */
export interface ResourceCatalog {
  runways: string[];
  taxiways: string[];
  equipment: string[];
}
export type LiveSnapshot = Snapshot & { resource_catalog?: ResourceCatalog };

const z = (v: unknown) => (typeof v === "string" && v.endsWith("+00:00") ? v.replace("+00:00", "Z") : v);

function assumptions(a: Json | undefined): Flight["assumptions"] {
  return Object.entries((a ?? {}) as Record<string, Json>).map(([label, x]: [string, Json]) => ({
    label,
    value: typeof x.value === "string" ? x.value : JSON.stringify(x.value),
    unit: x.unit ?? "",
    source_type: x.source_type,
  })) as Flight["assumptions"];
}

function assignment(a: Json | null | undefined): Assignment | null {
  if (!a) return null;
  const withFlight = (list: Json[] = []) => list.map((r) => ({ ...r, flight_id: a.flight_id }));
  return {
    ...a,
    crew_reservations: withFlight(a.crew_reservations),
    equipment_reservations: withFlight(a.equipment_reservations),
  } as Assignment;
}

export function toUiInstruction(i: Json): Instruction {
  return { ...i, kind: KIND_TO_UI[i.kind] ?? i.kind } as Instruction;
}

export function toCanonicalInstruction(draft: InstructionDraft, scenarioId: string, baseStateVersion: number): Json {
  return {
    scenario_id: scenarioId,
    base_state_version: baseStateVersion,
    kind: KIND_TO_CANONICAL[draft.kind],
    resource_id: draft.resource_id ?? null,
    starts_at_utc: draft.starts_at_utc,
    ends_at_utc: draft.ends_at_utc ?? null,
    flight_id: draft.flight_id ?? null,
    parameters: draft.parameters ?? {},
  };
}

export function toUiDraft(i: Json): InstructionDraft {
  return {
    kind: KIND_TO_UI[i.kind] ?? i.kind,
    resource_id: i.resource_id ?? null,
    starts_at_utc: z(i.starts_at_utc),
    ends_at_utc: z(i.ends_at_utc ?? null),
    flight_id: i.flight_id ?? null,
    parameters: i.parameters ?? {},
  } as InstructionDraft;
}

function impact(i: Json | undefined): Plan["impact"] {
  const agg = i?.aggregate ?? {},
    vs = i?.versus_baseline ?? {};
  const delay = Number(agg.delay_minutes ?? 0);
  const sacrifices = Object.entries((i?.per_flight ?? {}) as Record<string, Json>)
    .filter(([, v]: [string, Json]) => Number(v.delay_minutes) > 0)
    .sort(([, a]: [string, Json], [, b]: [string, Json]) => Number(b.delay_minutes) - Number(a.delay_minutes))
    .slice(0, 10)
    .map(([id, v]: [string, Json]) => `${id} +${Number(v.delay_minutes).toFixed(0)} min`);
  return {
    delay_minutes: delay,
    baseline_delay_minutes: delay - Number(vs.delay_minutes ?? 0),
    holding_minutes: Number(agg.holding_minutes ?? 0),
    taxi_minutes: Number(agg.taxi_minutes ?? 0),
    maximum_wait_minutes: Number(agg.max_wait_minutes ?? 0),
    unresolved_flights: Number(agg.unresolved_flights ?? 0),
    sacrifices,
  } as Plan["impact"];
}

function plan(p: Json): Plan {
  return {
    ...p,
    assignments: (p.assignments ?? []).map(assignment),
    impact: impact(p.impact),
  } as Plan;
}

const NEGOTIATION_STATUS: Record<string, Negotiation["status"]> = {
  RUNNING: "NEGOTIATING",
  PROPOSED: "PROPOSED",
  CONSENSUS_REACHED: "AWAITING_AUTHORIZATION",
  AWAITING_AUTHORIZATION: "AWAITING_AUTHORIZATION",
  AUTHORIZED: "AUTHORIZED",
  REJECTED: "REJECTED",
  STALE: "STALE",
  EXPIRED: "EXPIRED",
  NO_FEASIBLE_PLAN_FOUND: "NO_FEASIBLE_PLAN_FOUND",
  CANCELLED: "STALE",
};

export function toUiNegotiation(record: Json, proposals: Json[]): Negotiation {
  const proposal = proposals.find((p) => p.plan?.proposal_id === record.proposal_id);
  // The proposal's own status (AUTHORIZED, REJECTED, STALE...) supersedes the negotiation's.
  const status = NEGOTIATION_STATUS[proposal?.status ?? record.status] ?? "STALE";
  const p = proposal?.plan;
  return {
    negotiation_id: record.negotiation_id,
    status,
    round: record.round ?? 0,
    plan: p ? plan(p) : null,
    messages: record.messages ?? [],
    checks: (proposal?.checks ?? []) as CheckResult[],
    required_participants: ["ATC", "GROUND", ...(record.affected_ids ?? []).map((id: string) => `PILOT:${id}`)],
    outcome: p ? (p.source === "CONSENSUS" ? "CONSENSUS" : "FALLBACK") : record.status === "RUNNING" ? "PENDING" : "NONE",
    reason: record.reason ?? "",
    model_mode: record.model_mode,
    model_calls: record.model_calls ?? 0,
    duration_ms: Math.round(Number(record.duration_seconds ?? 0) * 1000),
  } as Negotiation;
}

function catalog(infrastructure: Json | undefined): ResourceCatalog {
  const ids = (kind: string) =>
    (infrastructure?.resources ?? []).filter((r: Json) => r.kind === kind).map((r: Json) => r.resource_id as string);
  return { runways: ids("RUNWAY"), taxiways: ids("TAXIWAY"), equipment: ids("EQUIPMENT") };
}

function dataQuality(s: Json): Snapshot["data_quality"] {
  const dq = s.data_quality ?? {},
    manifest = dq.source_manifest ?? {},
    infra = s.infrastructure ?? {};
  const historical = Boolean(manifest.member2);
  const limits: string[] = dq.coverage_limits ?? infra.coverage_limits ?? [];
  return {
    source_type: historical ? "HISTORICAL" : "SYNTHETIC",
    coverage: limits.join(" "),
    layout_note: `Infrastructure ${infra.source_type ?? "UNKNOWN"}, effective ${infra.effective_date ?? "unknown"}.`,
    unresolved_records: Number(manifest.member2?.data_quality?.unresolved_rows ?? 0),
  } as Snapshot["data_quality"];
}

/** Canonical SnapshotResponse ({snapshot, proposals, negotiations}) to the UI Snapshot. */
export function toUiSnapshot(response: Json, lastEventSeq: number): LiveSnapshot {
  const s = response.snapshot ?? response;
  const negotiations: Json[] = response.negotiations ?? [];
  const latest = negotiations.at(-1);
  return {
    schema_version: s.schema_version,
    scenario_id: s.scenario_id,
    state_version: s.state_version,
    sim_time_utc: z(s.sim_time_utc),
    mode: s.mode,
    flights: (s.flights ?? []).map((f: Json) => ({
      ...f,
      plan: assignment(f.plan),
      assumptions: assumptions(f.assumptions),
    })),
    resource_reservations: s.resource_reservations ?? [],
    active_instructions: (s.active_instructions ?? []).map(toUiInstruction),
    data_quality: dataQuality(s),
    clock_running: s.clock_status === "RUNNING",
    pending_negotiation: latest ? toUiNegotiation(latest, response.proposals ?? []) : null,
    last_event_seq: lastEventSeq,
    resource_catalog: catalog(s.infrastructure),
  } as LiveSnapshot;
}

export function toUiScenario(s: Json): ScenarioSummary {
  const limits: string[] = s.infrastructure?.coverage_limits ?? [];
  return {
    scenario_id: s.scenario_id,
    name: `${s.scenario_id} · ${(s.flights ?? []).length} flights`,
    airport_iata: s.airport_iata,
    start_utc: z(s.start_utc),
    end_utc: z(s.end_utc),
    seed: s.seed,
    coverage: limits[0] ?? "",
  } as ScenarioSummary;
}
