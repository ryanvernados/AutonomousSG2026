import type { Snapshot } from "../api/types";
import type { LiveSnapshot } from "../api/canonical";
import { time } from "../domain";
import { countdown, nextMilestone, routeNames, runwayDesignator } from "../map/movement";
import AirportMap from "./AirportMap";
import StaffInstructions, { planMovements, type Role } from "./StaffInstructions";

// A focused, large-type screen for one team: what happens next, where, and the full task list.
const TITLE: Record<Role, string> = { ATC: "ATC tower", GROUND: "Ground operations", PILOT: "Flight deck" };

interface Card {
  id: string;
  at: number;
  headline: string;
  detail: string;
  tone: "arrival" | "departure" | "gate";
}

export default function TeamScreen({
  role,
  snapshot,
  selected,
  onSelect,
}: {
  role: Role;
  snapshot: Snapshot;
  selected: string;
  onSelect: (id: string) => void;
}) {
  const map = (snapshot as LiveSnapshot).layout_map;
  const t = Date.parse(snapshot.sim_time_utc);
  const { movements, isProposal } = planMovements(snapshot);
  const label = (m: (typeof movements)[number]) => `${m.flight.airline}${m.flight.flight_number}`;

  let cards: Card[] = [];
  if (role === "ATC") {
    cards = movements.map((m) => ({
      id: m.flight.flight_id,
      at: Date.parse(m.a.runway_time_utc),
      headline: `${label(m)} · ${m.a.operation === "ARRIVAL" ? "LAND" : "DEPART"} ${runwayDesignator(map, m.a)}`,
      detail: m.a.operation === "ARRIVAL" ? `from ${m.flight.origin} → gate ${m.a.gate_id ?? "—"}`
        : `to ${m.flight.destination} from gate ${m.a.gate_id ?? "—"}`,
      tone: m.a.operation === "ARRIVAL" ? "arrival" : "departure",
    }));
  } else if (role === "GROUND") {
    cards = movements.map((m) => {
      const arrival = m.a.operation === "ARRIVAL";
      return {
        id: m.flight.flight_id,
        at: Date.parse(arrival ? m.a.taxi_end_utc : m.a.taxi_start_utc),
        headline: `${arrival ? "MARSHAL IN" : "PUSHBACK"} · gate ${m.a.gate_id ?? "—"}`,
        detail: `${label(m)} · ${arrival ? "arriving via" : "taxi"} ${routeNames(m.a).join(" → ") || "direct"}`
          + (arrival ? "" : ` · tug ${m.a.equipment_reservations[0]?.resource_id ?? "—"}`),
        tone: arrival ? "gate" : "departure",
      };
    });
  } else {
    const mine = movements.find((m) => m.flight.flight_id === selected) ?? movements.find(
      (m) => Date.parse(m.a.runway_time_utc) > t);
    const next = mine ? nextMilestone(mine.a, t, map) : undefined;
    if (mine && next)
      cards = [{ id: mine.flight.flight_id, at: next.at, headline: `${label(mine)} · ${next.label}`,
        detail: `${mine.flight.origin} → ${mine.flight.destination} · runway ${runwayDesignator(map, mine.a)}`,
        tone: mine.a.operation === "ARRIVAL" ? "arrival" : "departure" }];
  }
  const upcoming = cards.filter((c) => c.at > t).sort((x, y) => x.at - y.at).slice(0, role === "PILOT" ? 1 : 4);

  return (
    <div className="team-screen">
      <div className="team-header">
        <span className="eyebrow">{TITLE[role].toUpperCase()} · NEXT UP</span>
        <span className={`team-status ${isProposal ? "proposal" : movements.length ? "live" : ""}`}>
          {isProposal ? "PROPOSED · wait for approval" : movements.length ? "AUTHORIZED PLAN IN EFFECT" : "NO PLAN YET"}
        </span>
      </div>
      {snapshot.active_instructions.length > 0 && (
        <div className="team-alerts" role="alert">
          {snapshot.active_instructions.map((i) => (
            <span key={i.instruction_id}>
              ⚠ {i.kind.replaceAll("_", " ")} {i.resource_id ?? ""} {time(i.starts_at_utc)}
              {i.ends_at_utc ? `–${time(i.ends_at_utc)}` : ""}
            </span>
          ))}
        </div>
      )}
      <div className="next-up" aria-label="Next up">
        {upcoming.length ? (
          upcoming.map((c, i) => (
            <button key={c.id + c.at} type="button" onClick={() => onSelect(c.id)}
              className={`next-card ${c.tone} ${i === 0 ? "first" : ""} ${c.id === selected ? "selected" : ""}`}>
              <span className="next-when">{countdown(c.at, t).replace("in ", "")}</span>
              <span className="next-time">{time(new Date(c.at).toISOString())}</span>
              <strong>{c.headline}</strong>
              <span>{c.detail}</span>
            </button>
          ))
        ) : (
          <p className="next-empty">
            {movements.length ? "No further movements for this team." : "Waiting for an approved plan."}
            {role === "PILOT" && movements.length ? " Select a flight on the map." : ""}
          </p>
        )}
      </div>
      <div className="team-grid">
        <section className="panel airport-panel">
          <AirportMap snapshot={snapshot} selected={selected} onSelect={onSelect} />
        </section>
        <StaffInstructions snapshot={snapshot} map={map} selected={selected} onSelect={onSelect}
          fixedRole={role} large />
      </div>
    </div>
  );
}
