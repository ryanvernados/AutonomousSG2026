import { useState } from "react";
import type { Assignment, Flight, Snapshot } from "../api/types";
import { time } from "../domain";
import { routeNames, runwayDesignator, type LayoutMap } from "../map/movement";

// Role-specific working instructions derived from the authorized plan (or, before approval, the
// pending proposal), with each agent's own notes from the negotiation. Times are the plan's.
export type Role = "ATC" | "GROUND" | "PILOT";
export interface Movement {
  flight: Flight;
  a: Assignment;
}

const label = (f: Flight) => `${f.airline}${f.flight_number}`;
const minutes = (from: string, to: string) => Math.round((Date.parse(to) - Date.parse(from)) / 60000);

function assumption(f: Flight, key: string): string | undefined {
  const value = f.assumptions.find((x) => x.label === key)?.value;
  return value === undefined || value === null ? undefined : String(value);
}

/** Agent notes ("CODE: detail") for one sender, newest round first, without codes. */
function notes(snapshot: Snapshot, sender: string): string[] {
  const messages = snapshot.pending_negotiation?.messages ?? [];
  return messages
    .filter((m) => m.sender_id === sender)
    .sort((x, y) => y.round - x.round)
    .flatMap((m) => m.reasons)
    .map((r) => r.replace(/^[A-Z0-9_]+:\s*/, ""))
    .filter(Boolean);
}

/** Authorized movements, or the pending proposal's before approval (flagged by isProposal). */
export function planMovements(snapshot: Snapshot): { movements: Movement[]; isProposal: boolean } {
  const published = snapshot.flights.filter((f) => f.plan).map((f) => ({ flight: f, a: f.plan! }));
  const proposal = snapshot.pending_negotiation?.plan;
  const proposed =
    !published.length && proposal
      ? proposal.assignments.flatMap((a) => {
          const flight = snapshot.flights.find((f) => f.flight_id === a.flight_id);
          return flight ? [{ flight, a }] : [];
        })
      : [];
  const movements = (published.length ? published : proposed).sort(
    (x, y) => Date.parse(x.a.runway_time_utc) - Date.parse(y.a.runway_time_utc),
  );
  return { movements, isProposal: !published.length && proposed.length > 0 };
}

export default function StaffInstructions({
  snapshot,
  map,
  selected,
  onSelect,
  fixedRole,
  large = false,
}: {
  snapshot: Snapshot;
  map?: LayoutMap;
  selected: string;
  onSelect: (id: string) => void;
  /** Team screens show one role, without the role tabs. */
  fixedRole?: Role;
  large?: boolean;
}) {
  const [chosenRole, setRole] = useState<Role>("ATC");
  const role = fixedRole ?? chosenRole;
  const { movements, isProposal } = planMovements(snapshot);
  const t = Date.parse(snapshot.sim_time_utc);
  const closures = snapshot.active_instructions.filter((i) => i.resource_id);

  return (
    <section className={`panel staff-panel ${large ? "large" : ""}`} aria-label="Staff instructions">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">STAFF INSTRUCTIONS</span>
          <h2>
            {fixedRole === "ATC" ? "Runway sequence" : fixedRole === "GROUND" ? "Gate and pushback tasks"
              : fixedRole === "PILOT" ? "Flight clearances" : "What each team does next"}
          </h2>
        </div>
        {!fixedRole && <div className="role-tabs" role="tablist">
          {(["ATC", "GROUND", "PILOT"] as Role[]).map((r) => (
            <button key={r} role="tab" aria-selected={role === r} className={role === r ? "active" : ""}
              onClick={() => setRole(r)}>
              {r === "ATC" ? "ATC" : r === "GROUND" ? "Ground ops" : "Pilots"}
            </button>
          ))}
        </div>}
      </div>
      {!movements.length ? (
        <p className="ground-empty">
          No plan yet. Start a negotiation: once the agents agree, each team's instructions appear here, and
          they become operative when you approve the plan.
        </p>
      ) : (
        <>
          <p className={`staff-status ${isProposal ? "proposal" : ""}`}>
            {isProposal
              ? "PROPOSED · not yet authorized. Do not act until the controller approves."
              : "AUTHORIZED · these instructions are in effect."}
          </p>
          {role === "ATC" && <AtcBrief snapshot={snapshot} movements={movements} map={map} t={t}
            closures={closures} selected={selected} onSelect={onSelect} />}
          {role === "GROUND" && <GroundBrief snapshot={snapshot} movements={movements} map={map} t={t}
            selected={selected} onSelect={onSelect} />}
          {role === "PILOT" && <PilotBrief snapshot={snapshot} movements={movements} map={map} t={t}
            selected={selected} onSelect={onSelect} />}
        </>
      )}
    </section>
  );
}

interface BriefProps {
  snapshot: Snapshot;
  movements: Movement[];
  map?: LayoutMap;
  t: number;
  selected: string;
  onSelect: (id: string) => void;
}

function AgentNotes({ items, who }: { items: string[]; who: string }) {
  if (!items.length) return null;
  return (
    <div className="agent-notes">
      <strong>{who} agent notes</strong>
      {items.slice(0, 3).map((n, i) => (
        <p key={i}>{n}</p>
      ))}
    </div>
  );
}

function AtcBrief({ snapshot, movements, map, t, closures, selected, onSelect }: BriefProps & {
  closures: Snapshot["active_instructions"];
}) {
  const byRunway = new Map<string, Movement[]>();
  for (const m of movements) byRunway.set(m.a.runway_id, [...(byRunway.get(m.a.runway_id) ?? []), m]);
  return (
    <div className="staff-body">
      {closures.length > 0 && (
        <ul className="staff-alerts">
          {closures.map((c) => (
            <li key={c.instruction_id}>
              {c.kind.replaceAll("_", " ")} {c.resource_id}: {time(c.starts_at_utc)}
              {c.ends_at_utc ? `–${time(c.ends_at_utc)}` : " until further notice"}. Keep traffic clear.
            </li>
          ))}
        </ul>
      )}
      {[...byRunway].sort(([x], [y]) => x.localeCompare(y)).map(([runway, list]) => (
        <div key={runway} className="staff-block">
          <h3>
            Runway {runway} · {list.length} movements
          </h3>
          <table className="ground-table">
            <thead>
              <tr>
                <th>Slot</th>
                <th>Flight</th>
                <th>Instruction</th>
                <th>Gap</th>
              </tr>
            </thead>
            <tbody>
              {list.map(({ flight, a }, i) => (
                <tr key={flight.flight_id} onClick={() => onSelect(flight.flight_id)}
                  className={`${flight.flight_id === selected ? "selected" : ""} ${Date.parse(a.runway_time_utc) < t ? "done" : ""}`}>
                  <td>{time(a.runway_time_utc)}</td>
                  <th>{label(flight)}</th>
                  <td>
                    {a.operation === "ARRIVAL"
                      ? `Cleared to land ${runwayDesignator(map, a)}; vacate and contact ground for gate ${a.gate_id ?? "—"}`
                      : `Line up and wait ${runwayDesignator(map, a)}; cleared for takeoff at ${time(a.runway_time_utc)}`}
                  </td>
                  <td>{i ? `+${minutes(list[i - 1].a.runway_time_utc, a.runway_time_utc)} min` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
      <AgentNotes items={notes(snapshot, "ATC")} who="ATC" />
    </div>
  );
}

function GroundBrief({ snapshot, movements, map, t, selected, onSelect }: BriefProps) {
  const rows = movements
    .map((m) => ({ ...m, at: m.a.operation === "ARRIVAL" ? m.a.taxi_end_utc : m.a.taxi_start_utc }))
    .sort((x, y) => Date.parse(x.at) - Date.parse(y.at));
  return (
    <div className="staff-body">
      <table className="ground-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Flight</th>
            <th>Task</th>
            <th>Crew · tug</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ flight, a, at }) => {
            const route = routeNames(a).join(" → ") || "direct";
            const crew = a.crew_reservations[0]?.resource_id ?? "—";
            const tug = a.equipment_reservations[0]?.resource_id ?? "—";
            return (
              <tr key={flight.flight_id} onClick={() => onSelect(flight.flight_id)}
                className={`${flight.flight_id === selected ? "selected" : ""} ${Date.parse(at) < t ? "done" : ""}`}>
                <td>{time(at)}</td>
                <th>{label(flight)}</th>
                <td>
                  {a.operation === "ARRIVAL" ? (
                    <>
                      Marshal into gate <b>{a.gate_id ?? "—"}</b> (arrives via {route}); turnaround until{" "}
                      {a.gate_end_utc ? time(a.gate_end_utc) : "—"}
                    </>
                  ) : (
                    <>
                      Push back from gate <b>{a.gate_id ?? "—"}</b>; taxi {route} to hold short{" "}
                      {runwayDesignator(map, a)} by {time(a.taxi_end_utc)}
                    </>
                  )}
                </td>
                <td>
                  {crew}
                  {a.operation === "DEPARTURE" ? ` · ${tug}` : ""}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <AgentNotes items={notes(snapshot, "GROUND")} who="Ground" />
    </div>
  );
}

function PilotBrief({ snapshot, movements, map, t, selected, onSelect }: BriefProps) {
  const ordered = [...movements].sort(
    (x, y) => Number(y.flight.flight_id === selected) - Number(x.flight.flight_id === selected),
  );
  return (
    <div className="staff-body pilot-cards">
      {ordered.map(({ flight, a }) => {
        const rwy = runwayDesignator(map, a);
        const route = routeNames(a).join(" → ") || "direct";
        const fuel = assumption(flight, "latest_safe_arrival_utc");
        const margin = fuel && a.operation === "ARRIVAL" ? minutes(a.runway_time_utc, fuel) : null;
        const agent = notes(snapshot, `PILOT:${flight.flight_id}`);
        return (
          <article key={flight.flight_id} onClick={() => onSelect(flight.flight_id)}
            className={`pilot-card ${flight.flight_id === selected ? "selected" : ""} ${Date.parse(a.runway_time_utc) < t ? "done" : ""}`}>
            <header>
              <strong>{label(flight)}</strong> {flight.origin} → {flight.destination}
              {flight.tail_number ? ` · ${flight.tail_number}` : ""}
            </header>
            {a.operation === "ARRIVAL" ? (
              <ol>
                <li>Expect runway {rwy}; landing slot {time(a.runway_time_utc)}.</li>
                <li>Vacate {a.runway_id}, taxi via {route} to gate {a.gate_id ?? "—"}.</li>
                <li>On stand by {time(a.taxi_end_utc)}.</li>
              </ol>
            ) : (
              <ol>
                <li>Board at gate {a.gate_id ?? "—"}; pushback approved at {time(a.taxi_start_utc)}.</li>
                <li>Taxi via {route}; hold short {rwy} by {time(a.taxi_end_utc)}.</li>
                <li>Takeoff slot {time(a.runway_time_utc)} from {rwy}.</li>
              </ol>
            )}
            {margin !== null && (
              <p className={margin < 15 ? "fuel warn" : "fuel"}>
                Fuel: latest safe arrival {time(fuel!)} · {margin} min margin at the landing slot.
              </p>
            )}
            {agent[0] && <p className="agent-line">Pilot agent: {agent[0]}</p>}
          </article>
        );
      })}
    </div>
  );
}
