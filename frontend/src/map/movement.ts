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

const APPROACH_SECONDS = 150;
const APPROACH_METRES = 2500;
const ROLL_SECONDS = 60;

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

export function aircraftAt(
  map: LayoutMap,
  a: Assignment,
  t: number,
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
    if (gate && t >= gateStart && t < gateEnd) return { at: gate, phase: "AT_GATE" };
    return null;
  }
  if (gate && t >= gateStart && t < taxiStart) return { at: gate, phase: "AT_GATE" };
  if (t >= taxiStart && t < taxiEnd) return { at: along(path, frac(taxiStart, taxiEnd)), phase: "TAXI_OUT" };
  if (t >= taxiEnd && t < runway) return { at: path[path.length - 1], phase: "HOLDING" };
  if (t >= runway && t < runway + ROLL_SECONDS * 1000)
    return { at: lerp(ends.near, ends.far, frac(runway, runway + ROLL_SECONDS * 1000)), phase: "TAKEOFF" };
  return null;
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
        phase: map ? (aircraftAt(map, a, t)?.phase ?? null) : null,
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
