import type { Snapshot } from "../api/types";
import { time } from "../domain";
import { groundMovements, routeNames, type LayoutMap, type Phase } from "../map/movement";

const PHASE_LABEL: Record<Phase, string> = {
  APPROACH: "On approach",
  LANDING: "Landing",
  TAXI_IN: "Taxiing in",
  AT_GATE: "At gate",
  TAXI_OUT: "Taxiing out",
  HOLDING: "Holding short",
  TAKEOFF: "Departing",
};

// The ground controller's working list: authorized movements with gate, route and runway slot.
export default function GroundMovements({
  snapshot,
  map,
  selected,
  onSelect,
}: {
  snapshot: Snapshot;
  map?: LayoutMap;
  selected: string;
  onSelect: (id: string) => void;
}) {
  const t = Date.parse(snapshot.sim_time_utc);
  const all = groundMovements(snapshot, map, t);
  const pending = all.filter((m) => m.status !== "DONE");
  const done = all.length - pending.length;
  return (
    <section className="panel ground-panel" aria-label="Ground movements">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">GROUND CONTROL</span>
          <h2>Authorized movements</h2>
        </div>
        <span className="pill">
          {pending.length} pending · {done} done
        </span>
      </div>
      {!all.length ? (
        <p className="ground-empty">
          No authorized plan yet. Add an instruction, start a negotiation and approve the proposal: each
          flight then gets a gate, a taxi route and a runway slot, and appears here and on the map.
        </p>
      ) : (
        <div className="ground-table-wrap">
          <table className="ground-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Flight</th>
                <th>Movement</th>
                <th>Taxi route</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {pending.slice(0, 14).map(({ flight, assignment: a, kind, startsAt, status, phase }) => {
                const route = routeNames(a);
                return (
                  <tr
                    key={flight.flight_id}
                    className={`${flight.flight_id === selected ? "selected" : ""} ${status === "IN_PROGRESS" ? "active" : ""}`}
                    onClick={() => onSelect(flight.flight_id)}
                  >
                    <td>{time(startsAt)}</td>
                    <th>
                      {flight.airline}
                      {flight.flight_number}
                    </th>
                    <td>
                      {kind === "ARRIVAL"
                        ? `Land ${a.runway_id} → gate ${a.gate_id ?? "—"}`
                        : `Push from ${a.gate_id ?? "—"} → ${a.runway_id} at ${time(a.runway_time_utc)}`}
                    </td>
                    <td>{route.length ? route.join(" → ") : "direct"}</td>
                    <td>{status === "UPCOMING" ? "Upcoming" : phase ? PHASE_LABEL[phase] : "In progress"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
