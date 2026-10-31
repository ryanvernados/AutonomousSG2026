import { describe, expect, it } from "vitest";
import type { Assignment, Snapshot } from "../api/types";
import {
  aircraftAt,
  aircraftPositions,
  countdown,
  etaTag,
  milestones,
  nextMilestone,
  pathPoints,
  routeNames,
  runwayDesignator,
  taxiMetres,
  type LayoutMap,
} from "./movement";

const map: LayoutMap = {
  runways: [{ runway_id: "10R-28L", ends: { "10R": [0, 0], "28L": [1000, 0] } }],
  segments: {
    "RWYLINK:10R-28L@28L": [[1000, 0], [1000, -100]],
    "B:x-y#1": [[500, -100], [1000, -100]], // stored "backwards" relative to travel
    "STAND:G1": [[500, -200], [500, -300]],
    "RAMP_A:APRON_A-B": [[500, -200], [500, -100]],
  },
  gates: { G1: [500, -300] },
  taxiway_labels: [],
  boarding_areas: {},
};
const t = (m: number) => new Date(Date.UTC(2015, 11, 22, 4) + m * 60000).toISOString();
const arrival = {
  flight_id: "F1", operation: "ARRIVAL", runway_id: "10R-28L", runway_time_utc: t(10),
  taxi_path_ids: ["RWYLINK:10R-28L@28L", "B:x-y#1", "RAMP_A:APRON_A-B", "STAND:G1"],
  taxi_start_utc: t(11), taxi_end_utc: t(15), gate_id: "G1", gate_start_utc: t(15), gate_end_utc: t(35),
  crew_reservations: [], equipment_reservations: [],
} as unknown as Assignment;
const departure = {
  ...arrival, flight_id: "F2", operation: "DEPARTURE", runway_time_utc: t(30),
  taxi_path_ids: ["STAND:G1", "RAMP_A:APRON_A-B", "B:x-y#1", "RWYLINK:10R-28L@28L"],
  taxi_start_utc: t(20), taxi_end_utc: t(25), gate_start_utc: t(0), gate_end_utc: t(20),
} as unknown as Assignment;
const at = (a: Assignment, m: number) => aircraftAt(map, a, Date.parse(t(m)));

describe("aircraft movement from authorized assignments", () => {
  it("orients each segment along the direction of travel", () => {
    expect(pathPoints(map, arrival)).toEqual([[1000, 0], [1000, -100], [500, -100], [500, -200], [500, -300]]);
    expect(pathPoints(map, departure)[0]).toEqual([500, -300]);
    expect(pathPoints(map, departure).at(-1)).toEqual([1000, 0]);
  });

  it("walks an arrival through approach, landing roll, taxi-in and gate", () => {
    expect(at(arrival, 5)).toBeNull();
    expect(at(arrival, 9)?.phase).toBe("APPROACH");
    const landing = at(arrival, 10.5);
    expect(landing?.phase).toBe("LANDING");
    expect(landing!.at[0]).toBeGreaterThan(0); // rolling from the 10R threshold toward 28L
    expect(at(arrival, 13)?.phase).toBe("TAXI_IN");
    expect(at(arrival, 20)).toEqual({ at: [500, -300], phase: "AT_GATE" });
    expect(at(arrival, 40)).toBeNull();
  });

  it("walks a departure through gate, taxi-out, holding short and takeoff", () => {
    expect(at(departure, 10)?.phase).toBe("AT_GATE");
    expect(at(departure, 22)?.phase).toBe("TAXI_OUT");
    expect(at(departure, 27)).toEqual({ at: [1000, 0], phase: "HOLDING" });
    expect(at(departure, 30.5)?.phase).toBe("TAKEOFF");
    expect(at(departure, 32)).toBeNull();
  });

  it("names the taxiways without runway links, ramps or stands", () => {
    expect(routeNames(departure)).toEqual(["B"]);
  });

  it("keeps an arrived aircraft at its gate until the same tail departs", () => {
    const turn = { ...departure, gate_start_utc: t(50), gate_end_utc: t(60), taxi_start_utc: t(60),
      taxi_end_utc: t(65), runway_time_utc: t(70) } as unknown as Assignment;
    const snapshot = { flights: [
      { flight_id: "F1", tail_number: "N1", plan: arrival },
      { flight_id: "F2", tail_number: "N1", plan: turn },
    ] } as unknown as Snapshot;
    const ids = (m: number) => aircraftPositions(snapshot, map, Date.parse(t(m))).map((p) => [p.flight.flight_id, p.phase]);
    expect(ids(40)).toEqual([["F1", "AT_GATE"]]); // past the arrival's own gate_end
    expect(ids(55)).toEqual([["F2", "AT_GATE"]]); // handed over, never drawn twice
    expect(at(arrival, 40)).toBeNull(); // unchanged without a known next leg
  });

  it("gives each movement a timeline, runway in use and ETA", () => {
    expect(runwayDesignator(map, departure)).toBe("28L"); // rolls from the 28L threshold
    expect(runwayDesignator(map, arrival)).toBe("10R"); // lands from the far end toward 28L
    expect(milestones(departure, map).map((m) => m.label)).toEqual([
      "At gate G1", "Pushback from G1", "Hold short 28L", "Takeoff 28L"]);
    expect(nextMilestone(departure, Date.parse(t(10)), map)?.key).toBe("PUSHBACK");
    expect(countdown(Date.parse(t(20)), Date.parse(t(15.5)))).toBe("in 4m 30s");
    expect(etaTag(departure, Date.parse(t(22)), map)).toBe("28L 8m");
    expect(etaTag(arrival, Date.parse(t(12)), map)).toBe("gate G1 3m");
    expect(Math.round(taxiMetres(map, arrival))).toBe(800);
  });
});
