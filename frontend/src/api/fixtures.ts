import type { Assignment, Snapshot, ScenarioSummary } from "./types";
export const START = "2015-07-15T17:00:00.000Z";
export const at = (minutes: number) =>
  new Date(Date.parse(START) + minutes * 60000).toISOString();
export const scenario: ScenarioSummary = {
  scenario_id: "sfo-synthetic-24",
  name: "SFO · Midday recovery",
  airport_iata: "SFO",
  start_utc: START,
  end_utc: at(120),
  seed: 42,
  coverage: "24 synthetic flights · 2 hours · 6 simulated gates",
};
export function initialSnapshot(): Snapshot {
  const routes = ["LAX", "JFK", "SEA", "ORD", "DEN", "LAS", "BOS", "PHX"];
  const airlines = ["UA", "DL", "AA", "AS"];
  const flights = Array.from({ length: 24 }, (_, i) => {
    const arrival = i % 3 !== 1;
    const plan: Assignment = {
      flight_id: `synthetic-42:${i + 1}`,
      operation: arrival ? "ARRIVAL" : "DEPARTURE",
      runway_id: i % 2 ? "28L" : "28R",
      runway_time_utc: at(8 + i * 4),
      taxi_path_ids: [i % 2 ? "A" : "B", "C"],
      taxi_start_utc: at(arrival ? 9 + i * 4 : 1 + i * 4),
      taxi_end_utc: at(arrival ? 16 + i * 4 : 8 + i * 4),
      gate_id: `G${(i % 6) + 1}`,
      gate_start_utc: at(arrival ? 16 + i * 4 : i * 4 - 12),
      gate_end_utc: at(arrival ? 36 + i * 4 : 1 + i * 4),
      crew_reservations: [],
      equipment_reservations: [],
    };
    return {
      flight_id: plan.flight_id,
      source_row_id: `synthetic-42:${i + 1}`,
      airline: airlines[i % 4],
      flight_number: String(412 + i * 37),
      tail_number: null,
      origin: arrival ? routes[i % 8] : "SFO",
      destination: arrival ? "SFO" : routes[i % 8],
      scheduled_gate_departure_utc: at(arrival ? -120 + i * 4 : i * 4 + 1),
      scheduled_gate_arrival_utc: at(arrival ? 16 + i * 4 : 120 + i * 4),
      state: arrival ? "INBOUND" : "AT_GATE",
      plan,
      assumptions: [
        {
          label: "Aircraft category",
          value: "Medium",
          unit: "category",
          source_type: "SYNTHETIC" as const,
        },
        {
          label: "Fuel endurance",
          value: "60",
          unit: "minutes",
          source_type: "SYNTHETIC" as const,
        },
      ],
    };
  });
  return {
    schema_version: "1",
    scenario_id: scenario.scenario_id,
    state_version: 1,
    sim_time_utc: START,
    mode: "ASSISTED",
    clock_running: false,
    flights,
    resource_reservations: reservations(flights.map((f) => f.plan)),
    active_instructions: [],
    data_quality: {
      source_type: "SYNTHETIC",
      coverage: scenario.coverage,
      layout_note:
        "Illustrative SFO schematic; no sourced diagram or effective date. Positions, gates, crews and checks are synthetic.",
      unresolved_records: 0,
    },
    pending_negotiation: null,
    last_event_seq: 0,
  };
}
export function reservations(assignments: Assignment[]) {
  return assignments.flatMap((a) => [
    {
      resource_id: a.runway_id,
      flight_id: a.flight_id,
      starts_at_utc: a.runway_time_utc,
      ends_at_utc: new Date(
        Date.parse(a.runway_time_utc) + 90000,
      ).toISOString(),
    },
    ...(a.gate_id && a.gate_start_utc && a.gate_end_utc
      ? [
          {
            resource_id: a.gate_id,
            flight_id: a.flight_id,
            starts_at_utc: a.gate_start_utc,
            ends_at_utc: a.gate_end_utc,
          },
        ]
      : []),
  ]);
}
