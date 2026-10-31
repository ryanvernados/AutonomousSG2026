import {
  Check,
  CheckCircle2,
  ShieldCheck,
  ArrowRight,
  Users,
  AlertTriangle,
} from "lucide-react";
import type { Snapshot, Connection, CheckResult } from "../api/types";
import { approvalBlock, time, dateTime } from "../domain";

const CHECK_DETAIL_LIMIT = 200;

function checkGroups(checks: CheckResult[]) {
  const groups = new Map<string, CheckResult[]>();
  for (const c of checks) {
    const list = groups.get(c.domain);
    if (list) list.push(c);
    else groups.set(c.domain, [c]);
  }
  return [...groups]
    .map(([domain, list]) => ({
      domain,
      checks: list,
      passed: list.filter((c) => c.status === "PASS").length,
      failed: list.filter((c) => c.status !== "PASS"),
    }))
    .sort((a, b) => b.failed.length - a.failed.length || a.domain.localeCompare(b.domain));
}

export default function ProposalReview({
  snapshot,
  connection,
  now,
  busy,
  onNegotiate,
  onDecision,
  onRevise,
}: {
  snapshot: Snapshot;
  connection: Connection;
  now: number;
  busy: boolean;
  onNegotiate: () => void;
  onDecision: (d: "APPROVE" | "REJECT") => void;
  onRevise: () => void;
}) {
  const n = snapshot.pending_negotiation,
    p = n?.plan;
  const block = approvalBlock(snapshot, n, connection, now);
  const negotiating = n?.status === "NEGOTIATING";
  return (
    <section className="panel proposal-panel" aria-label="Recovery proposal">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">HUMAN AUTHORIZATION</span>
          <h2>Recovery workspace</h2>
        </div>
        <ShieldCheck size={21} className="muted" />
      </div>
      <div className="workflow-steps">
        <span className="done">
          1 <small>Instruct</small>
        </span>
        <i />
        <span className={n ? "done" : ""}>
          2 <small>Negotiate</small>
        </span>
        <i />
        <span className={n?.status === "AUTHORIZED" ? "done" : ""}>
          3 <small>Authorize</small>
        </span>
      </div>
      {!n && (
        <div className="empty-proposal">
          <div className="empty-icon">
            <Users size={28} />
          </div>
          <h3>A coordinated recovery starts here.</h3>
          <p>
            Set a restriction, then ask ATC, ground and flight agents to agree
            on a revised schedule.
          </p>
          <div className="mini-note">
            <ShieldCheck size={17} />
            <span>Every change stays a proposal until you authorize it.</span>
          </div>
        </div>
      )}
      {negotiating && (
        <div className="negotiating" role="status">
          <div className="pulse-ring">
            <Users size={26} />
          </div>
          <h3>Round {n.round} of 2</h3>
          <p>
            {n.round === 1
              ? "Collecting domain offers…"
              : "Checking agreement on one immutable plan…"}
          </p>
          <small>Simulation paused · published schedule unchanged</small>
        </div>
      )}
      {n && !negotiating && (
        <>
          <div
            className={`proposal-status ${n.outcome === "FALLBACK" ? "warning" : ""}`}
          >
            <span>
              {n.outcome === "FALLBACK" ? (
                <AlertTriangle size={18} />
              ) : (
                <CheckCircle2 size={18} />
              )}{" "}
              {n.status === "AWAITING_AUTHORIZATION"
                ? "Awaiting authorization"
                : n.status.replaceAll("_", " ").toLowerCase()}
            </span>
            <span className="pill">{n.outcome}</span>
          </div>
          <p className="subtle">{n.reason}</p>
        </>
      )}
      {p && !negotiating && (
        <>
          <div className="proposal-metrics">
            <div>
              <strong>{p.assignments.length}</strong>
              <span>affected flights</span>
            </div>
            <div>
              <strong>
                +{p.impact.delay_minutes.toFixed(0)}
                <small> min</small>
              </strong>
              <span>
                {n!.model_mode === "MOCK"
                  ? "fixture added delay"
                  : "reported delay"}
              </span>
            </div>
            <div>
              <strong>
                {n!.round}
                <small> / 2</small>
              </strong>
              <span>protocol rounds</span>
            </div>
          </div>
          {p.impact.baseline_delay_minutes !== p.impact.delay_minutes && (() => {
            const holdingBase = (p.impact as { baseline_holding_minutes?: number }).baseline_holding_minutes;
            const delayDelta = p.impact.delay_minutes - p.impact.baseline_delay_minutes;
            const holdDelta = holdingBase === undefined ? null : p.impact.holding_minutes - holdingBase;
            const fmt = (v: number) => `${v > 0 ? "+" : "−"}${Math.abs(v).toFixed(0)} min`;
            return (
              <p className={`vs-engine ${delayDelta < 0 ? "better" : "worse"}`}>
                Agent-negotiated plan vs engine-only plan: {fmt(delayDelta)} total delay
                {holdDelta !== null ? `, ${fmt(holdDelta)} holding` : ""}.
              </p>
            );
          })()}
          <div className="review-scroll">
            <table className="comparison-table">
              <caption>Published → proposed runway slots</caption>
              <thead>
                <tr>
                  <th>Flight</th>
                  <th>Current</th>
                  <th>Proposed</th>
                  <th>Δ min</th>
                </tr>
              </thead>
              <tbody>
                {p.assignments.map((a) => {
                  const f = snapshot.flights.find(
                    (f) => f.flight_id === a.flight_id,
                  );
                  return (
                    <tr key={a.flight_id}>
                      <th>{f ? f.airline + f.flight_number : a.flight_id}</th>
                      <td>
                        {f?.plan ? time(f.plan.runway_time_utc) : "Unscheduled"}
                        <small>
                          {f?.plan
                            ? `${f.plan.runway_id} · ${f.plan.gate_id}`
                            : "no published slot"}
                        </small>
                      </td>
                      <td className="proposed-time">
                        {time(a.runway_time_utc)}
                        <small>
                          {a.runway_id} · {a.gate_id}
                        </small>
                      </td>
                      <td>
                        {f?.plan
                          ? Math.round(
                              (Date.parse(a.runway_time_utc) -
                                Date.parse(f.plan.runway_time_utc)) /
                                60000,
                            )
                          : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <details>
            <summary>Routes, gates & concessions</summary>
            {p.impact.sacrifices.map((s) => (
              <p key={s}>{s}</p>
            ))}
            <div className="detail-scroll">
              {p.assignments.map((a) => (
                <p key={a.flight_id}>
                  <strong>
                    {
                      snapshot.flights.find((f) => f.flight_id === a.flight_id)
                        ?.flight_number
                    }
                  </strong>{" "}
                  ·{" "}
                  {snapshot.flights
                    .find((f) => f.flight_id === a.flight_id)
                    ?.plan?.taxi_path_ids.join(" → ") ?? "unscheduled"}{" "}
                  → <b>{a.taxi_path_ids.join(" → ")}</b>
                  <br />
                  Gate {a.gate_id} ·{" "}
                  {a.gate_start_utc ? time(a.gate_start_utc) : "—"}–
                  {a.gate_end_utc ? time(a.gate_end_utc) : "—"}
                </p>
              ))}
            </div>
          </details>
          <details open>
            <summary>
              Configured checks{" "}
              <span className="pill">
                {n!.checks.filter((c) => c.status === "PASS").length}/
                {n!.checks.length} pass
              </span>
            </summary>
            {n!.checks.length === 0 && (
              <p className="alert danger">
                Missing checks. Authorization blocked.
              </p>
            )}
            {/* SFO plans carry thousands of checks: summarize per domain, always show problems. */}
            <div className="checks-grid">
              {checkGroups(n!.checks).map((g) => (
                <div
                  key={g.domain}
                  className={`check ${g.failed.length ? "check-failed" : ""}`}
                >
                  {g.failed.length ? (
                    <AlertTriangle size={13} />
                  ) : (
                    <Check size={13} />
                  )}
                  <span>{g.domain}</span>
                  <small>
                    {g.passed}/{g.checks.length}
                  </small>
                </div>
              ))}
            </div>
            {n!.checks.some((c) => c.status !== "PASS") && (
              <div className="detail-scroll check-problems">
                {n!.checks
                  .filter((c) => c.status !== "PASS")
                  .slice(0, CHECK_DETAIL_LIMIT)
                  .map((c) => (
                    <p key={c.constraint_id} className="check-failed">
                      <strong>
                        {c.status} · {c.severity} · {c.domain}
                      </strong>
                      <br />
                      {c.explanation}
                    </p>
                  ))}
              </div>
            )}
            <details>
              <summary>All checks by domain</summary>
              {checkGroups(n!.checks).map((g) => (
                <details key={g.domain}>
                  <summary>
                    {g.domain} · {g.passed}/{g.checks.length} pass
                  </summary>
                  <div className="detail-scroll">
                    {g.checks.slice(0, CHECK_DETAIL_LIMIT).map((c) => (
                      <p
                        key={c.constraint_id}
                        className={c.status !== "PASS" ? "check-failed" : ""}
                      >
                        <strong>{c.status}</strong> · {c.explanation}
                      </p>
                    ))}
                    {g.checks.length > CHECK_DETAIL_LIMIT && (
                      <p className="fine-print">
                        {g.checks.length - CHECK_DETAIL_LIMIT} more not shown.
                      </p>
                    )}
                  </div>
                </details>
              ))}
            </details>
            <p className="fine-print">
              {n!.model_mode === "MOCK"
                ? "Scripted check results for UI testing; no operational validation."
                : "Backend validation applies to the configured simulation rules."}
            </p>
          </details>
          <details>
            <summary>Agent agreement & plan identity</summary>
            <p>
              {
                n!.messages.filter((m) => m.round === 2 && m.type === "ACCEPT")
                  .length
              }
              /{n!.required_participants.length} accepted · {n!.model_calls}{" "}
              model calls · {n!.duration_ms} ms · {n!.model_mode}
            </p>
            <p>
              State v{p.base_state_version} · instruction v
              {p.instruction_version} · proposal v{p.proposal_version}
            </p>
            <code className="hash">
              {p.proposal_id}
              <br />
              {p.plan_hash}
            </code>
            <p>Expires {dateTime(p.expires_at_utc)} (wall clock).</p>
            <div className="detail-scroll">
              {n!.messages.map((m) => (
                <p key={m.message_id}>
                  <strong>
                    R{m.round} · {m.role} · {m.type}
                  </strong>
                  <br />
                  {m.sender_id}
                  <br />
                  {m.reasons.join(" ")}
                </p>
              ))}
            </div>
            <p className="fine-print">
              Agent acceptance is a planning ballot, not a pilot readback.
            </p>
          </details>
          {block && (
            <p className="alert warning" role="alert">
              <AlertTriangle size={17} />
              {block}
            </p>
          )}
          {n!.status === "AWAITING_AUTHORIZATION" && (
            <div className="decision-actions">
              <button
                className="button primary full"
                disabled={!!block || busy}
                onClick={() => onDecision("APPROVE")}
              >
                <ShieldCheck size={17} /> Approve in simulator
              </button>
              <div className="button-row">
                <button
                  className="button"
                  disabled={busy || connection !== "connected"}
                  onClick={() => onDecision("REJECT")}
                >
                  Reject proposal
                </button>
                <button className="text-button" onClick={onRevise}>
                  Revise instructions
                </button>
              </div>
              <p className="fine-print">
                Authorizes the complete demo schedule as one action.
              </p>
            </div>
          )}
        </>
      )}
      {!negotiating && n?.status !== "AWAITING_AUTHORIZATION" && (
        <button
          className="button primary full"
          disabled={
            busy || connection !== "connected" || snapshot.mode === "MANUAL"
          }
          onClick={onNegotiate}
        >
          {n ? "Request a new plan" : "Start negotiation"}
          <ArrowRight size={17} />
        </button>
      )}
    </section>
  );
}
