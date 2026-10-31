import type { Snapshot } from "../api/types";
import { time } from "../domain";
export default function Timeline({ snapshot }: { snapshot: Snapshot }) {
  const first =
    Math.min(
      Date.parse(snapshot.sim_time_utc) + 8 * 60000,
      ...snapshot.flights.map((f) => Date.parse(f.plan.runway_time_utc)),
    ) -
    8 * 60000;
  const span = 120 * 60000;
  const pct = (iso: string) => ((Date.parse(iso) - first) / span) * 100;
  const p =
    snapshot.pending_negotiation?.status === "AWAITING_AUTHORIZATION"
      ? snapshot.pending_negotiation.plan
      : null;
  return (
    <section className="panel timeline-panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">RESOURCE RESERVATIONS</span>
          <h2>Operations timeline</h2>
        </div>
        <div className="timeline-key">
          <span>━ Published</span>
          <span>┄ Proposed</span>
        </div>
      </div>
      <div className="timeline-scroll">
        <div className="timeline">
          <div className="timeline-header">
            <span>RESOURCE</span>
            <div>
              {[0, 30, 60, 90, 120].map((m) => (
                <span key={m} style={{ left: `${(m / 120) * 100}%` }}>
                  {time(new Date(first + m * 60000).toISOString())}
                </span>
              ))}
            </div>
          </div>
          {["28R", "28L", "G1", "G2", "G3", "G4", "G5", "G6"].map((id) => (
            <div className="timeline-row" key={id}>
              <strong>
                {id}
                <small>{id.startsWith("G") ? "GATE" : "RUNWAY"}</small>
              </strong>
              <div className="timeline-track">
                {[0, 25, 50, 75, 100].map((x) => (
                  <i key={x} style={{ left: `${x}%` }} />
                ))}
                {snapshot.resource_reservations
                  .filter((r) => r.resource_id === id)
                  .map((r, i) => (
                    <span
                      key={i}
                      className={`reservation ${id.startsWith("G") ? "gate-reservation" : ""}`}
                      style={{
                        left: `${Math.max(0, pct(r.starts_at_utc))}%`,
                        width: `${Math.max(0.8, Math.min(100, pct(r.ends_at_utc)) - Math.max(0, pct(r.starts_at_utc)))}%`,
                      }}
                      title={`${r.flight_id} · ${time(r.starts_at_utc)}–${time(r.ends_at_utc)}`}
                    />
                  ))}
                {snapshot.active_instructions
                  .filter((i) => i.resource_id === id)
                  .map((i) => (
                    <span
                      key={i.instruction_id}
                      className="closure-reservation"
                      style={{
                        left: `${Math.max(0, pct(i.starts_at_utc))}%`,
                        width: `${Math.max(0, pct(i.ends_at_utc ?? i.starts_at_utc) - Math.max(0, pct(i.starts_at_utc)))}%`,
                      }}
                      title="Confirmed restriction"
                    />
                  ))}
                {p?.assignments
                  .filter((a) => a.runway_id === id)
                  .map((a) => (
                    <span
                      key={a.flight_id}
                      className="proposed-reservation"
                      style={{ left: `${pct(a.runway_time_utc)}%` }}
                      title={`Proposed ${time(a.runway_time_utc)}`}
                    />
                  ))}
                <b
                  className="now-line"
                  style={{ left: `${pct(snapshot.sim_time_utc)}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
