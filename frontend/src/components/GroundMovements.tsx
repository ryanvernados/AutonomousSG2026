import type { Snapshot } from "../api/types";
import { time } from "../domain";
import {
  countdown,
  groundMovements,
  milestones,
  nextMilestone,
  routeNames,
  runwayDesignator,
  taxiMetres,
  type LayoutMap,
  type Phase,
} from "../map/movement";

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
  const selectedMovement = all.find((m) => m.flight.flight_id === selected);
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
        <>
          <div className="ground-table-wrap">
            <table className="ground-table">
              <thead>
                <tr>
                  <th>Flight</th>
                  <th>Movement</th>
                  <th>Off gate / on runway</th>
                  <th>Taxi</th>
                  <th>Next event</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {pending.slice(0, 14).map(({ flight, assignment: a, kind, status, phase }) => {
                  const route = routeNames(a);
                  const rwy = runwayDesignator(map, a);
                  const next = nextMilestone(a, t, map);
                  const taxiMin = Math.round((Date.parse(a.taxi_end_utc) - Date.parse(a.taxi_start_utc)) / 60000);
                  return (
                    <tr
                      key={flight.flight_id}
                      className={`${flight.flight_id === selected ? "selected" : ""} ${status === "IN_PROGRESS" ? "active" : ""}`}
                      onClick={() => onSelect(flight.flight_id)}
                    >
                      <th>
                        {flight.airline}
                        {flight.flight_number}
                        <small>
                          {kind === "ARRIVAL" ? `from ${flight.origin}` : `to ${flight.destination}`}
                        </small>
                      </th>
                      <td>
                        {kind === "ARRIVAL"
                          ? `Land ${rwy} → gate ${a.gate_id ?? "—"}`
                          : `Gate ${a.gate_id ?? "—"} → ${rwy}`}
                      </td>
                      <td>
                        {kind === "ARRIVAL" ? (
                          <>
                            Land {time(a.runway_time_utc)}
                            <small>on stand {time(a.taxi_end_utc)}</small>
                          </>
                        ) : (
                          <>
                            Push {time(a.taxi_start_utc)}
                            <small>takeoff {time(a.runway_time_utc)}</small>
                          </>
                        )}
                      </td>
                      <td>
                        {route.length ? route.join(" → ") : "direct"}
                        <small>
                          {map ? `${(taxiMetres(map, a) / 1000).toFixed(1)} km · ` : ""}
                          {taxiMin} min
                        </small>
                      </td>
                      <td>
                        {next ? next.label : "Complete"}
                        {next && <small>{countdown(next.at, t)}</small>}
                      </td>
                      <td>{status === "UPCOMING" ? "Upcoming" : phase ? PHASE_LABEL[phase] : "In progress"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {selectedMovement && (
            <div className="ground-detail" aria-label="Selected flight timeline">
              <strong>
                {selectedMovement.flight.airline}
                {selectedMovement.flight.flight_number} · {selectedMovement.flight.origin} →{" "}
                {selectedMovement.flight.destination}
                {selectedMovement.flight.tail_number ? ` · ${selectedMovement.flight.tail_number}` : ""}
              </strong>
              <ol className="milestones">
                {milestones(selectedMovement.assignment, map).map((m) => (
                  <li key={m.key} className={m.at <= t ? "passed" : ""}>
                    <span>{time(new Date(m.at).toISOString())}</span>
                    {m.label}
                    <small>{m.at <= t ? "done" : countdown(m.at, t)}</small>
                  </li>
                ))}
              </ol>
              <p className="fine-print">
                Route {selectedMovement.assignment.taxi_path_ids.length ? routeNames(selectedMovement.assignment).join(" → ") || "direct" : "—"}
                {" · "}times are the authorized plan; positions between them are simulated.
              </p>
            </div>
          )}
        </>
      )}
    </section>
  );
}
