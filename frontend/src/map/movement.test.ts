import { describe, expect, it } from "vitest";
import type { Assignment } from "../api/types";
import { aircraftAt, pathPoints, routeNames, type LayoutMap } from "./movement";

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
});
