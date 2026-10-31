// Aircraft positions and ground movements derived from published (authorized) assignments.
// Positions are simulated from the plan's own timestamps and the scenario's map geometry;
// the source data has no radar tracks.
import type { Assignment, Flight, Snapshot } from "../api/types";

export type Point = [number, number];
export interface LayoutMap {
  runways: { runway_id: string; ends: Record<string, Point> }[];
  segments: Record<string, [Point, Point]>;
  gates: Record<string, Point>;
  taxiway_labels: { text: string; at: Point }[];
  boarding_areas: Record<string, Point>;
}
export type Phase =
  | "APPROACH"
  | "LANDING"
  | "TAXI_IN"
  | "AT_GATE"
  | "TAXI_OUT"
  | "HOLDING"
  | "TAKEOFF";

const APPROACH_SECONDS = 240;
/** Final approach and climb-out drawn beyond each threshold; the map frame includes them. */
export const FINAL_METRES = 1000;
const APPROACH_METRES = FINAL_METRES;
const ROLL_SECONDS = 60;
const CLIMB_SECONDS = 45;
const CLIMB_METRES = FINAL_METRES;
const HEADING_LOOKAHEAD_MS = 3000;

const dist = (a: Point, b: Point) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const lerp = (a: Point, b: Point, f: number): Point => [
  a[0] + (b[0] - a[0]) * f,
  a[1] + (b[1] - a[1]) * f,
];
const ms = (iso: string | null | undefined) => (iso ? Date.parse(iso) : NaN);

/** Points along the assigned taxi path, oriented from where the movement starts. */
export function pathPoints(map: LayoutMap, a: Assignment): Point[] {
  const segments = a.taxi_path_ids.map((id) => map.segments[id]).filter(Boolean);
  if (!segments.length) return [];
  let current: Point =
    a.operation === "DEPARTURE" && a.gate_id && map.gates[a.gate_id]
      ? map.gates[a.gate_id]
      : segments[0][0];
  const points: Point[] = [current];
  for (const [p, q] of segments) {
    const [from, to] = dist(current, p) <= dist(current, q) ? [p, q] : [q, p];
    if (dist(current, from) > 1) points.push(from);
    points.push(to);
    current = to;
  }
  return points;
}

function along(points: Point[], fraction: number): Point {
  if (points.length < 2) return points[0] ?? [0, 0];
  const lengths = points.slice(1).map((p, i) => dist(points[i], p));
  let remaining = Math.max(0, Math.min(1, fraction)) * lengths.reduce((s, l) => s + l, 0);
  for (let i = 0; i < lengths.length; i++) {
    if (remaining <= lengths[i] || i === lengths.length - 1)
      return lerp(points[i], points[i + 1], lengths[i] ? remaining / lengths[i] : 1);
    remaining -= lengths[i];
  }
  return points[points.length - 1];
}

/** The runway end a movement uses at the runway (arrival exit end / departure entry end). */
function runwayEnds(map: LayoutMap, a: Assignment): { near: Point; far: Point } | null {
  const runway = map.runways.find((r) => r.runway_id === a.runway_id);
  if (!runway) return null;
  const link = a.taxi_path_ids.find((id) => id.startsWith(`RWYLINK:${a.runway_id}@`));
  const ends = Object.entries(runway.ends);
  const nearEnd = link ? link.split("@").pop()! : ends[0][0];
  const near = runway.ends[nearEnd] ?? ends[0][1];
  const far = ends.find(([end]) => end !== nearEnd)?.[1] ?? ends[1][1];
  return { near, far };
}

/**
 * Position at time t. ``holdUntil`` keeps an arrived aircraft at its gate past the end of its
 * turnaround reservation until the same aircraft's next departure appears on the map.
 */
export function aircraftAt(
  map: LayoutMap,
  a: Assignment,
  t: number,
  holdUntil?: number,
): { at: Point; phase: Phase } | null {
  const ends = runwayEnds(map, a);
  const path = pathPoints(map, a);
  if (!ends || !path.length) return null;
  const runway = ms(a.runway_time_utc),
    taxiStart = ms(a.taxi_start_utc),
    taxiEnd = ms(a.taxi_end_utc),
    gateStart = ms(a.gate_start_utc),
    gateEnd = ms(a.gate_end_utc);
  const frac = (start: number, end: number) => (end > start ? (t - start) / (end - start) : 1);
  const gate = a.gate_id ? map.gates[a.gate_id] : undefined;
  if (a.operation === "ARRIVAL") {
    // Land from the far threshold toward the exit end, then taxi the assigned path to the gate.
    const outward: Point = lerp(ends.far, ends.near, -APPROACH_METRES / Math.max(1, dist(ends.far, ends.near)));
    if (t >= runway - APPROACH_SECONDS * 1000 && t < runway)
      return { at: lerp(outward, ends.far, frac(runway - APPROACH_SECONDS * 1000, runway)), phase: "APPROACH" };
    if (t >= runway && t < taxiStart) return { at: lerp(ends.far, ends.near, frac(runway, taxiStart)), phase: "LANDING" };
    if (t >= taxiStart && t < taxiEnd) return { at: along(path, frac(taxiStart, taxiEnd)), phase: "TAXI_IN" };
    if (gate && t >= gateStart && t < Math.max(gateEnd, holdUntil ?? -Infinity))
      return { at: gate, phase: "AT_GATE" };
    return null;
  }
  if (gate && t >= gateStart && t < taxiStart) return { at: gate, phase: "AT_GATE" };
  if (t >= taxiStart && t < taxiEnd) return { at: along(path, frac(taxiStart, taxiEnd)), phase: "TAXI_OUT" };
  if (t >= taxiEnd && t < runway) return { at: path[path.length - 1], phase: "HOLDING" };
  if (t >= runway && t < runway + ROLL_SECONDS * 1000)
    return { at: lerp(ends.near, ends.far, frac(runway, runway + ROLL_SECONDS * 1000)), phase: "TAKEOFF" };
  // Climb out along the runway heading so the departure leaves the field visibly.
  const airborne = runway + ROLL_SECONDS * 1000;
  if (t >= airborne && t < airborne + CLIMB_SECONDS * 1000) {
    const beyond = lerp(ends.near, ends.far, 1 + CLIMB_METRES / Math.max(1, dist(ends.near, ends.far)));
    return { at: lerp(ends.far, beyond, frac(airborne, airborne + CLIMB_SECONDS * 1000)), phase: "TAKEOFF" };
  }
  return null;
}

/** When a departure first appears on the map: at its gate if it has one, else at taxi start. */
function firstShown(a: Assignment): number {
  const gateStart = ms(a.gate_start_utc);
  return a.gate_id && !Number.isNaN(gateStart) ? Math.min(gateStart, ms(a.taxi_start_utc)) : ms(a.taxi_start_utc);
}

/**
 * For each published arrival, when the same tail's next published departure from this airport
 * first appears. Without it, the aircraft would vanish between its turnaround and departure.
 */
export function turnaroundHolds(snapshot: Snapshot): Map<string, number> {
  const departures = new Map<string, number[]>();
  for (const f of snapshot.flights) {
    if (f.plan?.operation === "DEPARTURE" && f.tail_number) {
      const list = departures.get(f.tail_number) ?? [];
      list.push(firstShown(f.plan));
      departures.set(f.tail_number, list);
    }
  }
  const holds = new Map<string, number>();
  for (const f of snapshot.flights) {
    if (f.plan?.operation !== "ARRIVAL" || !f.tail_number) continue;
    const arrived = ms(f.plan.taxi_end_utc);
    const next = (departures.get(f.tail_number) ?? []).filter((d) => d >= arrived).sort((x, y) => x - y)[0];
    if (next !== undefined) holds.set(f.flight_id, next);
  }
  return holds;
}

export interface ShownAircraft {
  flight: Flight;
  at: Point;
  phase: Phase;
  /** Where the aircraft will be shortly, for its heading; null when stationary. */
  ahead: Point | null;
}

/** Aircraft moving or at a gate under an authorized plan at time t, one per airframe. */
export function aircraftPositions(snapshot: Snapshot, map: LayoutMap, t: number): ShownAircraft[] {
  const holds = turnaroundHolds(snapshot);
  return snapshot.flights.flatMap((f) => {
    if (!f.plan) return [];
    const hold = holds.get(f.flight_id);
    const pos = aircraftAt(map, f.plan, t, hold);
    if (!pos) return [];
    const next = pos.phase === "AT_GATE" ? null : aircraftAt(map, f.plan, t + HEADING_LOOKAHEAD_MS, hold);
    const ahead = next && dist(next.at, pos.at) > 0.5 ? next.at : null;
    return [{ flight: f, ...pos, ahead }];
  });
}

function assumedGate(f: Flight): string | undefined {
  const value = f.assumptions.find((a) => a.label === "gate_id")?.value;
  return value ? String(value) : undefined;
}

export interface ParkedAircraft {
  gate: string;
  at: Point;
  /** The scenario flight using the gate, when known; otherwise traffic outside the scenario. */
  flight?: Flight;
  /** Scheduled departure still waiting for an authorized plan. */
  awaitingPlan: boolean;
}

/**
 * Aircraft parked at gates that are not already drawn by a plan: authoritative gate
 * reservations (initial occupancy, flights outside the slice) and, before any plan is approved,
 * scheduled departures waiting at their assumed gate. One aircraft per gate.
 */
export function parkedAircraft(snapshot: Snapshot, map: LayoutMap, t: number, shown: ShownAircraft[]): ParkedAircraft[] {
  const drawnFlights = new Set(shown.map((s) => s.flight.flight_id));
  const takenGates = new Set(shown.filter((s) => s.phase === "AT_GATE").map((s) => s.flight.plan?.gate_id));
  const flights = new Map(snapshot.flights.map((f) => [f.flight_id, f]));
  const parked = new Map<string, ParkedAircraft>();
  const place = (gate: string, flight: Flight | undefined, awaitingPlan: boolean) => {
    if (!map.gates[gate] || takenGates.has(gate) || parked.has(gate)) return;
    parked.set(gate, { gate, at: map.gates[gate], flight, awaitingPlan });
  };
  for (const r of snapshot.resource_reservations) {
    if (!map.gates[r.resource_id] || drawnFlights.has(r.flight_id)) continue;
    if (ms(r.starts_at_utc) <= t && t < ms(r.ends_at_utc)) place(r.resource_id, flights.get(r.flight_id), false);
  }
  const airport = airportOf(snapshot);
  for (const f of snapshot.flights) {
    const gate = assumedGate(f);
    if (!f.plan && gate && f.origin === airport && t < ms(f.scheduled_gate_departure_utc)) place(gate, f, true);
  }
  return [...parked.values()];
}

const airportOf = (snapshot: Snapshot) => (snapshot as Snapshot & { airport_iata?: string }).airport_iata ?? "SFO";

export interface Inbound {
  flight: Flight;
  runway: string;
  eta: number;
  awaitingPlan: boolean;
  /** Past its scheduled arrival with no authorized slot: airborne in a holding pattern. */
  holding: boolean;
}

/** Arrivals not yet on the surface map, within the next ``minutes``: the controller's inbound queue. */
export function inboundQueue(snapshot: Snapshot, map: LayoutMap | undefined, t: number, minutes = 45): Inbound[] {
  const airport = airportOf(snapshot);
  return snapshot.flights
    .flatMap((f) => {
      if (f.destination !== airport) return [];
      const eta = f.plan ? ms(f.plan.runway_time_utc) : ms(f.scheduled_gate_arrival_utc);
      const holding = !f.plan && eta <= t;
      const onMap = f.plan ? eta - APPROACH_SECONDS * 1000 : Infinity;
      if (!(onMap > t) || eta - t > minutes * 60000) return [];
      return [{ flight: f, runway: f.plan ? runwayDesignator(map, f.plan) : "—", eta, awaitingPlan: !f.plan, holding }];
    })
    // Holding aircraft first (longest-holding at the top), then by expected touchdown.
    .sort((a, b) => Number(b.holding) - Number(a.holding) || a.eta - b.eta);
}

/** Runway designator in use: departures roll from the entry end, arrivals land from the far end. */
export function runwayDesignator(map: LayoutMap | undefined, a: Assignment): string {
  const link = a.taxi_path_ids.find((id) => id.startsWith(`RWYLINK:${a.runway_id}@`));
  const entry = link?.split("@").pop();
  if (!entry) return a.runway_id;
  if (a.operation === "DEPARTURE") return entry;
  const ends = map?.runways.find((r) => r.runway_id === a.runway_id)?.ends;
  return (ends && Object.keys(ends).find((end) => end !== entry)) ?? a.runway_id;
}

/** Taxi distance in metres along the assigned path (layout units are metres). */
export function taxiMetres(map: LayoutMap, a: Assignment): number {
  const pts = pathPoints(map, a);
  return pts.slice(1).reduce((sum, p, i) => sum + dist(pts[i], p), 0);
}

export interface Milestone {
  key: "GATE_IN" | "PUSHBACK" | "HOLD_SHORT" | "TAKEOFF" | "TOUCHDOWN" | "VACATE" | "ON_STAND" | "TURNAROUND";
  label: string;
  at: number;
}

/** The authorized timeline of one movement, in order, from the plan's own timestamps. */
export function milestones(a: Assignment, map?: LayoutMap): Milestone[] {
  const rwy = runwayDesignator(map, a);
  const gate = a.gate_id ?? "stand";
  const list: Milestone[] =
    a.operation === "DEPARTURE"
      ? [
          { key: "GATE_IN", label: `At gate ${gate}`, at: ms(a.gate_start_utc) },
          { key: "PUSHBACK", label: `Pushback from ${gate}`, at: ms(a.taxi_start_utc) },
          { key: "HOLD_SHORT", label: `Hold short ${rwy}`, at: ms(a.taxi_end_utc) },
          { key: "TAKEOFF", label: `Takeoff ${rwy}`, at: ms(a.runway_time_utc) },
        ]
      : [
          { key: "TOUCHDOWN", label: `Touchdown ${rwy}`, at: ms(a.runway_time_utc) },
          { key: "VACATE", label: `Vacate ${a.runway_id}, taxi in`, at: ms(a.taxi_start_utc) },
          { key: "ON_STAND", label: `On stand ${gate}`, at: ms(a.taxi_end_utc) },
          { key: "TURNAROUND", label: `Turnaround done at ${gate}`, at: ms(a.gate_end_utc) },
        ];
  return list.filter((m) => !Number.isNaN(m.at));
}

export function nextMilestone(a: Assignment, t: number, map?: LayoutMap): Milestone | undefined {
  return milestones(a, map).find((m) => m.at > t);
}

/** "now", "in 45s", "in 4m 05s", "in 1h 02m". */
export function countdown(at: number, t: number): string {
  const s = Math.round((at - t) / 1000);
  if (s <= 0) return "now";
  if (s < 60) return `in ${s}s`;
  if (s < 3600) return `in ${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
  return `in ${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
}

/** Short ETA tag for a moving aircraft label, e.g. "28L 3m" or "G A12 2m". */
export function etaTag(a: Assignment, t: number, map?: LayoutMap): string {
  const target =
    a.operation === "DEPARTURE"
      ? milestones(a, map).find((m) => m.key === "TAKEOFF")
      : milestones(a, map).find((m) => m.key === "ON_STAND");
  if (!target || target.at <= t) return "";
  const where = a.operation === "DEPARTURE" ? runwayDesignator(map, a) : `gate ${a.gate_id ?? "—"}`;
  return `${where} ${Math.max(1, Math.round((target.at - t) / 60000))}m`;
}

/** Runways with an aircraft landing or taking off at time t, with who is using them. */
export function runwayActivity(shown: ShownAircraft[]): Map<string, string> {
  const active = new Map<string, string>();
  for (const s of shown) {
    if ((s.phase === "LANDING" || s.phase === "TAKEOFF") && s.flight.plan)
      active.set(s.flight.plan.runway_id,
        `${s.phase === "LANDING" ? "LANDING" : "DEPARTING"} ${s.flight.airline}${s.flight.flight_number}`);
  }
  return active;
}

/** Taxiway names along a path, without runway links, stands or repeated names. */
export function routeNames(a: Assignment): string[] {
  const names: string[] = [];
  for (const id of a.taxi_path_ids) {
    if (id.startsWith("RWYLINK:") || id.startsWith("STAND:") || id.startsWith("RAMP_")) continue;
    const name = id.split(":")[0];
    if (name && name !== "A-B" && names[names.length - 1] !== name) names.push(name);
  }
  return names;
}

export interface GroundMovement {
  flight: Flight;
  assignment: Assignment;
  kind: "ARRIVAL" | "DEPARTURE";
  startsAt: string;
  status: "UPCOMING" | "IN_PROGRESS" | "DONE";
  phase: Phase | null;
}

/** Published movements a ground controller works from, ordered by taxi start. */
export function groundMovements(snapshot: Snapshot, map: LayoutMap | undefined, t: number): GroundMovement[] {
  const holds = turnaroundHolds(snapshot);
  return snapshot.flights
    .filter((f) => f.plan)
    .map((f) => {
      const a = f.plan!;
      const start = Math.min(ms(a.taxi_start_utc), a.operation === "ARRIVAL" ? ms(a.runway_time_utc) : Infinity);
      const end = a.operation === "ARRIVAL" ? ms(a.taxi_end_utc) : ms(a.runway_time_utc) + ROLL_SECONDS * 1000;
      const status: GroundMovement["status"] = t < start ? "UPCOMING" : t < end ? "IN_PROGRESS" : "DONE";
      return {
        flight: f,
        assignment: a,
        kind: a.operation,
        startsAt: a.operation === "ARRIVAL" ? a.runway_time_utc : a.taxi_start_utc,
        status,
        phase: map ? (aircraftAt(map, a, t, holds.get(f.flight_id))?.phase ?? null) : null,
      };
    })
    .sort((x, y) => Date.parse(x.startsAt) - Date.parse(y.startsAt));
}

/** Gates occupied at time t, from authoritative reservations (initial occupancy and approved plans). */
export function occupiedGates(snapshot: Snapshot, gateIds: Set<string>, t: number): Map<string, string> {
  const occupied = new Map<string, string>();
  for (const r of snapshot.resource_reservations) {
    if (gateIds.has(r.resource_id) && ms(r.starts_at_utc) <= t && t < ms(r.ends_at_utc))
      occupied.set(r.resource_id, r.flight_id);
  }
  return occupied;
}
