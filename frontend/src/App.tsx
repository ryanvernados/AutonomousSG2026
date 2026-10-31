import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowDownLeft,
  ArrowUpRight,
  ChevronRight,
  CircleHelp,
  Clock3,
  Download,
  Layers,
  Map,
  Pause,
  Play,
  Plus,
  Radio,
  RotateCcw,
  Search,
  Shield,
  SkipForward,
  SlidersHorizontal,
  TowerControl,
  TriangleAlert,
  Wifi,
  WifiOff,
} from "lucide-react";
import {
  auth,
  client,
  demoToken,
  isLive,
  configurationError,
} from "./api/client";
import type {
  AuditEvent,
  Connection,
  DemoOutcome,
  Snapshot,
  ScenarioSummary,
} from "./api/types";
import {
  dateTime,
  instructionLabel,
  operationOf,
  scheduledTime,
  time,
  zone,
} from "./domain";
import AirportMap from "./components/AirportMap";
import GroundMovements from "./components/GroundMovements";
import StaffInstructions from "./components/StaffInstructions";
import type { LiveSnapshot } from "./api/canonical";
import Instructions from "./components/Instructions";
import ProposalReview from "./components/ProposalReview";
import Timeline from "./components/Timeline";

function Login({ onReady }: { onReady: () => void }) {
  const [email, setEmail] = useState(""),
    [password, setPassword] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  return (
    <main className="login">
      <div className="login-card">
        <div className="brand">
          <TowerControl />
          <strong>
            groundline<span>CONTROLLER WORKSPACE</span>
          </strong>
        </div>
        <h1>Welcome to the tower.</h1>
        <p>Sign in with your project’s Supabase account.</p>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            try {
              const { error } = await auth!.auth.signInWithPassword({
                email,
                password,
              });
              if (error) setError(error.message);
              else onReady();
            } catch (error) {
              setError(String(error));
            } finally {
              setBusy(false);
            }
          }}
        >
          <label htmlFor="email">Email</label>
          <input
            id="email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {error && (
            <p role="alert" className="alert danger">
              {error}
            </p>
          )}
          <button className="button primary full" disabled={busy}>
            Sign in
          </button>
        </form>
        <p className="fine-print">
          Access and project roles are verified by the backend.
        </p>
      </div>
    </main>
  );
}
export default function App() {
  const [signedIn, setSignedIn] = useState(!isLive || Boolean(demoToken));
  useEffect(() => {
    if (!auth) return;
    void auth.auth.getSession().then(({ data }) => setSignedIn(!!data.session));
    const { data } = auth.auth.onAuthStateChange((_event, session) =>
      setSignedIn(!!session),
    );
    return () => data.subscription.unsubscribe();
  }, []);
  if (configurationError)
    return (
      <main className="loading">
        <TriangleAlert size={32} />
        <h1>Live connection needs configuration</h1>
        <p role="alert">{configurationError}</p>
        <p>
          Set the frontend environment variables and restart Vite, or choose
          VITE_API_MODE=mock for the offline demo.
        </p>
      </main>
    );
  return signedIn ? (
    <Workspace onSignOut={() => void auth?.auth.signOut()} />
  ) : (
    <Login onReady={() => setSignedIn(true)} />
  );
}
function Workspace({ onSignOut }: { onSignOut: () => void }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null),
    [sessionId, setSessionId] = useState(""),
    [scenarios, setScenarios] = useState<ScenarioSummary[]>([]),
    [chosen, setChosen] = useState("");
  const [connection, setConnection] = useState<Connection>("connecting"),
    [lastUpdate, setLastUpdate] = useState(Date.now()),
    [now, setNow] = useState(Date.now());
  const [events, setEvents] = useState<AuditEvent[]>([]),
    [history, setHistory] = useState<Snapshot[]>([]),
    [replayIndex, setReplayIndex] = useState(-1);
  const [tab, setTab] = useState<"operations" | "replay" | "comparison">(
      "operations",
    ),
    [selected, setSelected] = useState(""),
    [search, setSearch] = useState(""),
    [filter, setFilter] = useState("ALL");
  const [editor, setEditor] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [demoOutcome, setDemoOutcome] = useState<DemoOutcome>("consensus"),
    [demoConnected, setDemoConnected] = useState(true),
    [help, setHelp] = useState(false);
  const refreshCounter = useRef(0);
  const mounted = useRef(true);
  const sessionRef = useRef("");
  const transport = useRef<Connection>("connecting");
  const commandInFlight = useRef(false);
  const sessions = useRef(
    new globalThis.Map<string, ReturnType<typeof client.createSession>>(),
  );
  const ingest = useCallback((s: Snapshot) => {
    setSnapshot(s);
    setLastUpdate(Date.now());
    setHistory((h) =>
      h.at(-1)?.last_event_seq === s.last_event_seq &&
      h.at(-1)?.state_version === s.state_version
        ? h
        : [...h, s].slice(-100),
    );
  }, []);
  const refresh = useCallback(
    async (id: string) => {
      const request = ++refreshCounter.current;
      try {
        const next = await client.snapshot(id);
        if (
          mounted.current &&
          request === refreshCounter.current &&
          id === sessionRef.current &&
          transport.current === "connected"
        ) {
          ingest(next);
          setConnection("connected");
        }
      } catch (e) {
        if (mounted.current) {
          setError(String(e));
          setConnection("disconnected");
        }
      }
    },
    [ingest],
  );
  useEffect(() => {
    mounted.current = true;
    void client
      .scenarios()
      .then((items) => {
        if (!mounted.current) return;
        setScenarios(items);
        setChosen(items[0]?.scenario_id ?? "");
        if (!items.length) setError("No scenarios available for this account.");
      })
      .catch((e) => setError(String(e)));
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      mounted.current = false;
      clearInterval(t);
    };
  }, []);
  useEffect(() => {
    if (!chosen) return;
    let active = true;
    setConnection("connecting");
    if (!sessions.current.has(chosen))
      sessions.current.set(chosen, client.createSession(chosen));
    void sessions.current
      .get(chosen)!
      .then((result) => {
        if (!active) return;
        sessionRef.current = result.session_id;
        setSessionId(result.session_id);
        setHistory([]);
        setEvents([]);
        ingest(result.snapshot);
        setSelected(result.snapshot.flights[0]?.flight_id ?? "");
      })
      .catch((e) => {
        if (active) setError(String(e));
      });
    return () => {
      active = false;
    };
  }, [chosen, ingest]);
  useEffect(() => {
    if (!sessionId) return;
    return client.subscribe(
      sessionId,
      0,
      (e) => {
        if (e)
          setEvents((list) =>
            list.some((x) => x.event_id === e.event_id)
              ? list
              : [...list, e].slice(-500),
          );
        void refresh(sessionId);
      },
      (state) => {
        transport.current = state;
        if (state !== "connected") {
          refreshCounter.current++;
          setConnection(state);
        } else void refresh(sessionId);
      },
    );
  }, [sessionId, refresh]);
  async function command(
    path: string,
    body: Record<string, unknown>,
    version = snapshot?.state_version,
  ) {
    if (!snapshot || version === undefined) return;
    if (
      transport.current !== "connected" ||
      Date.now() - lastUpdate > 15000 ||
      tab === "replay"
    )
      throw new Error(
        "Commands are blocked until a current connected state is available.",
      );
    if (commandInFlight.current)
      throw new Error("A command is already in progress.");
    commandInFlight.current = true;
    setBusy(true);
    setError("");
    try {
      await client.command(sessionId, path, {
        ...body,
        command_id: crypto.randomUUID(),
        base_state_version: version,
      });
      await refresh(sessionId);
    } catch (e) {
      setError(String(e));
      await refresh(sessionId);
      throw e;
    } finally {
      commandInFlight.current = false;
      setBusy(false);
    }
  }
  const act = (path: string, body: Record<string, unknown>) => {
    void command(path, body).catch(() => {});
  };
  function exportTrace() {
    const lines = events
      .map(
        ({
          event_id,
          event_seq,
          scenario_id,
          state_version,
          sim_time_utc,
          recorded_at_utc,
          event_type,
          correlation_id,
        }) =>
          JSON.stringify({
            event_id,
            event_seq,
            scenario_id,
            state_version,
            sim_time_utc,
            recorded_at_utc,
            event_type,
            correlation_id,
          }),
      )
      .join("\n");
    const url = URL.createObjectURL(
      new Blob([lines], { type: "application/x-ndjson" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = "sfo-redacted-trace.jsonl";
    a.click();
    URL.revokeObjectURL(url);
    setNotice(
      "Redacted session events exported. Actor and free-form payload fields omitted.",
    );
  }
  if (!snapshot)
    return (
      <div className="loading">
        <TowerControl size={40} />
        <h1>Opening the controller workspace…</h1>
        <p role="alert">
          {error || "Loading the available scenario and authoritative state."}
        </p>
        {error && (
          <button className="button" onClick={() => location.reload()}>
            Retry connection
          </button>
        )}
      </div>
    );
  const age = Math.max(0, Math.floor((now - lastUpdate) / 1000));
  const effectiveConnection: Connection =
    age > 15 ? "disconnected" : connection;
  const visible =
    tab === "replay" && replayIndex >= 0
      ? (history[replayIndex] ?? snapshot)
      : snapshot;
  const flight = visible.flights.find((f) => f.flight_id === selected);
  const flights = visible.flights.filter(
    (f) =>
      (filter === "ALL" || operationOf(f) === filter) &&
      `${f.airline}${f.flight_number} ${f.origin} ${f.destination}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  const locked =
    busy || effectiveConnection !== "connected" || tab === "replay";
  const n = snapshot.pending_negotiation;
  const negotiating = n?.status === "NEGOTIATING";
  const scenario = scenarios.find((s) => s.scenario_id === chosen);
  const conflicts = snapshot.resource_reservations.filter((r) =>
    snapshot.active_instructions.some(
      (i) =>
        i.resource_id === r.resource_id &&
        i.kind !== "PROTECTED_SLOT" &&
        Date.parse(r.ends_at_utc) > Date.parse(i.starts_at_utc) &&
        (!i.ends_at_utc ||
          Date.parse(r.starts_at_utc) < Date.parse(i.ends_at_utc)),
    ),
  );
  return (
    <div className="app-layout">
      <aside className="sidebar">
        <a href="#main" className="skip-link">
          Skip to controller
        </a>
        <div className="brand">
          <div className="brand-icon">
            <TowerControl size={25} />
          </div>
          <strong>
            groundline<span>AIRPORT OPERATIONS</span>
          </strong>
        </div>
        <div className="workspace-tag">
          <span className="green-dot" /> SFO CONTROL ROOM{" "}
          <ChevronRight size={14} />
        </div>
        <span className="nav-label">WORKSPACE</span>
        <nav aria-label="Workspace">
          <button
            className={tab === "operations" ? "active" : ""}
            onClick={() => setTab("operations")}
          >
            <Map size={19} /> Live operations <span>01</span>
          </button>
          <button
            className={tab === "replay" ? "active" : ""}
            onClick={() => {
              setTab("replay");
              setReplayIndex(history.length - 1);
            }}
          >
            <RotateCcw size={18} /> Session replay
          </button>
          <button
            className={tab === "comparison" ? "active" : ""}
            onClick={() => setTab("comparison")}
          >
            <Layers size={18} /> Compare outcomes
          </button>
        </nav>
        <div className="sidebar-scenario">
          <span className="eyebrow">ACTIVE SCENARIO</span>
          <strong>{scenario?.name ?? "SFO scenario"}</strong>
          <span>San Francisco International</span>
          <div className="scenario-lines">
            <span>TRAFFIC WINDOW</span>
            <b>
              {scenario
                ? `${time(scenario.start_utc)}–${time(scenario.end_utc)} ${zone(scenario.start_utc)}`
                : "Not supplied"}
            </b>
            <span>FIXTURE SEED</span>
            <b>{scenario?.seed ?? "Not supplied"} · reproducible</b>
          </div>
          <span className="pill amber">
            {client.mode === "mock" ? "SYNTHETIC FIXTURE" : "BACKEND SCENARIO"}
          </span>
        </div>
        <div className="sidebar-bottom">
          <button onClick={() => setHelp(!help)}>
            <CircleHelp size={18} /> Demo guide & coverage
          </button>
          <div className="profile">
            <div>JC</div>
            <span>
              <strong>
                {isLive ? "Project controller" : "Demo controller"}
              </strong>
              <small>
                {isLive ? "Authenticated staff" : "Member 04 · UI workspace"}
              </small>
            </span>
            {isLive && <button onClick={onSignOut}>Exit</button>}
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumbs">
            Workspace <ChevronRight size={13} />{" "}
            <strong>San Francisco · SFO</strong>
          </div>
          <div className="topbar-right">
            <button
              className="icon-button"
              aria-label="Toggle guide and data coverage"
              onClick={() => setHelp(!help)}
            >
              <CircleHelp size={16} />
            </button>
            <span className="pill amber">
              {client.mode === "mock"
                ? "MOCK MODE"
                : `LIVE API · ${n?.model_mode ?? "UNREPORTED"} MODEL`}
            </span>
            <span
              className={`connection ${effectiveConnection !== "connected" ? "offline" : ""}`}
            >
              {effectiveConnection === "connected" ? (
                <Wifi size={14} />
              ) : (
                <WifiOff size={14} />
              )}{" "}
              {effectiveConnection} · {age}s ago
            </span>
          </div>
        </header>
        <main id="main">
          <div className="page-heading">
            <div>
              <div className="eyebrow">SFO / CONTROLLER WORKSPACE</div>
              <h1>
                {tab === "operations"
                  ? "Keep the recovery moving."
                  : tab === "replay"
                    ? "Review every decision."
                    : "Compare the trade-offs."}
              </h1>
              <p>
                {tab === "operations"
                  ? "One shared view. Coordinated agents. You make the call."
                  : tab === "replay"
                    ? "Read-only snapshots recorded during this browser session."
                    : "Published and proposed outcomes, with their assumptions in view."}
              </p>
            </div>
            <button className="button" onClick={exportTrace}>
              <Download size={16} /> Export trace
            </button>
          </div>
          <div className="simulation-bar">
            <div className="clock">
              <Clock3 size={18} />
              <strong>
                {time(snapshot.sim_time_utc)}
                <span> {zone(snapshot.sim_time_utc)}</span>
              </strong>
              <small>
                {dateTime(snapshot.sim_time_utc)
                  .split(",")
                  .slice(0, 2)
                  .join(",")}
              </small>
            </div>
            <span
              className={`clock-status ${snapshot.clock_running ? "running" : ""}`}
            >
              {snapshot.clock_running ? "● RUNNING" : "Ⅱ PAUSED"}
            </span>
            <div className="clock-actions">
              <button
                className="icon-button"
                aria-label={
                  snapshot.clock_running
                    ? "Pause simulation"
                    : "Resume simulation"
                }
                disabled={locked || (!snapshot.clock_running && negotiating)}
                title={negotiating ? "Agents are negotiating; the clock stays paused" : undefined}
                onClick={() =>
                  act("clock", {
                    action: snapshot.clock_running ? "PAUSE" : "RESUME",
                  })
                }
              >
                {snapshot.clock_running ? (
                  <Pause size={17} />
                ) : (
                  <Play size={17} />
                )}
              </button>
              <label className="speed-select">
                <span>Speed</span>
                <select
                  aria-label="Simulation speed"
                  disabled={locked}
                  value={(snapshot as LiveSnapshot).clock_speed ?? 1}
                  onChange={(e) =>
                    act("clock", { action: "SPEED", speed: Number(e.target.value) })
                  }
                >
                  {[...new Set([1, 5, 10, 30, 60, 120, (snapshot as LiveSnapshot).clock_speed ?? 1])]
                    .sort((a, b) => a - b)
                    .map((v) => (
                      <option key={v} value={v}>
                        {v === 1 ? "1× real time" : `${v}× (1 s = ${v >= 60 ? `${v / 60} min` : `${v} s`})`}
                      </option>
                    ))}
                </select>
              </label>
              <button
                className="button small"
                disabled={locked || snapshot.clock_running || negotiating}
                onClick={() => act("clock", { action: "STEP", seconds: 60 })}
              >
                <SkipForward size={14} /> +1 min
              </button>
              <button
                className="button small"
                disabled={locked || snapshot.clock_running || negotiating}
                onClick={() => act("clock", { action: "STEP", seconds: 600 })}
              >
                <SkipForward size={14} /> +10 min
              </button>
              <button
                className="icon-button"
                aria-label="Reset simulation"
                disabled={locked || negotiating}
                onClick={() => {
                  if (
                    window.confirm(
                      "Reset this simulation? Current restrictions and authorized changes will be cleared; the audit history remains.",
                    )
                  )
                    act("clock", { action: "RESET" });
                }}
              >
                <RotateCcw size={16} />
              </button>
            </div>
            <div className="control-mode">
              <span>CONTROL MODE</span>
              <button
                className={snapshot.mode === "ASSISTED" ? "selected" : ""}
                disabled={locked || snapshot.mode === "ASSISTED"}
                onClick={() => act("control", { mode: "ASSISTED" })}
              >
                Assisted
              </button>
              <button
                className={snapshot.mode === "MANUAL" ? "selected manual" : ""}
                disabled={locked || snapshot.mode === "MANUAL"}
                onClick={() => act("control", { mode: "MANUAL" })}
              >
                Manual
              </button>
            </div>
          </div>
          {effectiveConnection !== "connected" && (
            <div className="alert danger" role="alert">
              <WifiOff size={18} /> Updates are unavailable. Commands and
              authorization are blocked until the authoritative state is
              refreshed.
            </div>
          )}
          {snapshot.mode === "MANUAL" && (
            <div className="alert warning" role="alert">
              <Shield size={18} /> Manual control is active. Pending automation
              is cancelled; confirmed restrictions and published reservations
              remain.
            </div>
          )}
          {error && (
            <div className="alert danger" role="alert">
              {error}
              <button className="text-button" onClick={() => setError("")}>
                Dismiss
              </button>
            </div>
          )}
          {notice && (
            <div className="alert info" role="status">
              {notice}
              <button className="text-button" onClick={() => setNotice("")}>
                Dismiss
              </button>
            </div>
          )}
          <section className="restrictions" aria-label="Confirmed restrictions">
            <div>
              <TriangleAlert size={17} />
              <strong>
                {snapshot.active_instructions.length
                  ? "Confirmed restrictions"
                  : "No active restrictions"}
              </strong>
              {!snapshot.active_instructions.length && (
                <span>Ready for your first disruption.</span>
              )}
            </div>
            {snapshot.active_instructions.map((i) => (
              <div className="restriction-chip" key={i.instruction_id}>
                <b>
                  {instructionLabel[i.kind]} ·{" "}
                  {i.resource_id ??
                    snapshot.flights.find((f) => f.flight_id === i.flight_id)
                      ?.flight_number}
                </b>
                <span>
                  {time(i.starts_at_utc)}–
                  {i.ends_at_utc ? time(i.ends_at_utc) : "open"}{" "}
                  {zone(i.starts_at_utc)} · issued · acknowledgment not modelled
                </span>
              </div>
            ))}
            <button
              className="text-button"
              disabled={locked}
              onClick={() => {
                setEditor(true);
                setTab("operations");
              }}
            >
              <Plus size={16} /> Add instruction
            </button>
          </section>
          {conflicts.length > 0 && (
            <div className="alert danger" role="alert">
              <TriangleAlert size={18} />
              {conflicts.length} published resource reservations overlap
              confirmed restrictions. Review a recovery plan before advancing
              the simulation.
            </div>
          )}
          {help && (
            <section className="panel help-panel">
              <h2>Demo guide & data coverage</h2>
              <p>
                <b>{snapshot.data_quality.source_type}</b> ·{" "}
                {snapshot.data_quality.coverage}
              </p>
              <p>{snapshot.data_quality.layout_note}</p>
              <ol>
                <li>Add a runway closure; preview it, then confirm.</li>
                <li>Start negotiation and inspect the two-round proposal.</li>
                <li>Review changes and checks, then approve or reject.</li>
                <li>
                  Use the demo conditions below to test unknown checks,
                  fallback, expiration and disconnection.
                </li>
              </ol>
              <p>
                Scheduled gate times and runway slots are separate.{" "}
                {client.mode === "mock"
                  ? "This UI fixture does not load the raw historical CSV and makes zero live model calls."
                  : "Traffic and model provenance come from the backend; the surface schematic remains illustrative."}{" "}
                The map shows simulated positions, not radar tracks. Simulation
                approval is a demo convenience, not operational certification.
              </p>
              <label htmlFor="scenario-picker">Available scenario</label>
              <select
                id="scenario-picker"
                value={chosen}
                onChange={(e) => setChosen(e.target.value)}
              >
                {scenarios.map((s) => (
                  <option key={s.scenario_id} value={s.scenario_id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </section>
          )}
          {tab === "replay" && (
            <section className="panel replay-panel">
              <h2>
                Session replay <span className="pill">READ ONLY</span>
              </h2>
              <p>
                {history.length} observed snapshots · latest 100 retained in
                memory. Current restrictions remain above.
              </p>
              <label htmlFor="replay">
                Snapshot {Math.max(0, replayIndex) + 1} of {history.length} ·
                state v{visible.state_version} ·{" "}
                {dateTime(visible.sim_time_utc)}
              </label>
              <input
                id="replay"
                type="range"
                min="0"
                max={Math.max(0, history.length - 1)}
                value={Math.max(0, replayIndex)}
                onChange={(e) => setReplayIndex(Number(e.target.value))}
              />
              <p className="fine-print">
                No durable replay endpoint has been supplied. Reloading this
                page clears the local replay.
              </p>
            </section>
          )}
          {tab === "comparison" ? (
            <section className="panel comparison-panel">
              <h2>Counterfactual comparison</h2>
              <p>
                Scenario seed {scenario?.seed ?? "not supplied"} ·{" "}
                {client.mode === "mock"
                  ? "same fixture traffic and instruction inputs; no live evaluation performed."
                  : "metrics reported by the backend; verify matched evaluation inputs."}
              </p>
              {n?.plan ? (
                <>
                  <div className="metric-cards">
                    <div>
                      <span>
                        {client.mode === "mock"
                          ? "Illustrative baseline"
                          : "Reported baseline"}
                      </span>
                      <strong>
                        {n.plan.impact.baseline_delay_minutes}
                        <small> min</small>
                      </strong>
                      <p>
                        {client.mode === "mock"
                          ? "Scripted reference delay"
                          : "Backend reference delay"}
                      </p>
                    </div>
                    <div>
                      <span>Proposed recovery</span>
                      <strong>
                        {n.plan.impact.delay_minutes}
                        <small> min</small>
                      </strong>
                      <p>
                        {client.mode === "mock"
                          ? "Added fixture delay"
                          : "Reported delay"}
                      </p>
                    </div>
                    <div>
                      <span>Maximum wait</span>
                      <strong>
                        {n.plan.impact.maximum_wait_minutes}
                        <small> min</small>
                      </strong>
                      <p>
                        {n.plan.impact.unresolved_flights} unresolved flights
                        reported
                      </p>
                    </div>
                  </div>
                  {client.mode === "mock" && (
                    <p className="alert warning">
                      These mock values demonstrate the comparison UI. They are
                      not measured savings, a validated baseline, or evidence of
                      feasibility.
                    </p>
                  )}
                  <table>
                    <thead>
                      <tr>
                        <th>Metric</th>
                        <th>Proposal report</th>
                        <th>Provenance</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td>Airborne holding</td>
                        <td>{n.plan.impact.holding_minutes} min</td>
                        <td>
                          {client.mode === "mock"
                            ? "Scripted fixture"
                            : "Backend report"}
                        </td>
                      </tr>
                      <tr>
                        <td>Taxi time</td>
                        <td>{n.plan.impact.taxi_minutes} min</td>
                        <td>
                          {client.mode === "mock"
                            ? "Scripted fixture"
                            : "Backend report"}
                        </td>
                      </tr>
                      <tr>
                        <td>
                          Completed movements / turnaround / connections /
                          overtime
                        </td>
                        <td>Not supplied</td>
                        <td>Pending backend evaluation</td>
                      </tr>
                    </tbody>
                  </table>
                </>
              ) : (
                <div className="empty-proposal">
                  <Layers size={32} />
                  <h3>No comparison yet</h3>
                  <p>
                    Create a recovery proposal to inspect its reported impact.
                  </p>
                  <button
                    className="button"
                    onClick={() => setTab("operations")}
                  >
                    Return to operations
                  </button>
                </div>
              )}
            </section>
          ) : (
            <div className="workspace-grid">
              <div className="operations-column">
                <section className="panel airport-panel">
                  <div className="panel-heading">
                    <div>
                      <span className="eyebrow">AIRPORT OVERVIEW</span>
                      <h2>
                        San Francisco International{" "}
                        <span className="airport-code">SFO</span>
                      </h2>
                    </div>
                    <span className="pill">
                      {visible.flights.length} FLIGHTS
                    </span>
                  </div>
                  <AirportMap
                    snapshot={visible}
                    selected={selected}
                    onSelect={setSelected}
                  />
                  <div className="map-footer">
                    <span>
                      <Radio size={13} /> Simulated positions ·{" "}
                      {visible.data_quality.source_type.toLowerCase()}
                    </span>
                    <span>State v{visible.state_version}</span>
                  </div>
                </section>
                <GroundMovements
                  snapshot={visible}
                  map={(visible as LiveSnapshot).layout_map}
                  selected={selected}
                  onSelect={setSelected}
                />
                <StaffInstructions
                  snapshot={visible}
                  map={(visible as LiveSnapshot).layout_map}
                  selected={selected}
                  onSelect={setSelected}
                />
                <Timeline snapshot={visible} />
                <section className="panel flights-panel">
                  <div className="panel-heading">
                    <div>
                      <span className="eyebrow">PUBLISHED SCHEDULE</span>
                      <h2>
                        Flight board{" "}
                        <span className="count">{flights.length}</span>
                      </h2>
                    </div>
                    <div className="flight-tools">
                      <label className="search">
                        <Search size={15} />
                        <input
                          aria-label="Search flights"
                          placeholder="Find a flight…"
                          value={search}
                          onChange={(e) => setSearch(e.target.value)}
                        />
                      </label>
                      <select
                        aria-label="Filter operations"
                        value={filter}
                        onChange={(e) => setFilter(e.target.value)}
                      >
                        <option value="ALL">All flights</option>
                        <option value="ARRIVAL">Arrivals</option>
                        <option value="DEPARTURE">Departures</option>
                      </select>
                    </div>
                  </div>
                  <div className="flight-table-scroll">
                    <table className="flight-table">
                      <thead>
                        <tr>
                          <th>Flight / route</th>
                          <th>Movement</th>
                          <th>Runway slot</th>
                          <th>Gate</th>
                          <th>Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {flights.map((f) => (
                          <tr
                            key={f.flight_id}
                            className={
                              f.flight_id === selected ? "selected-row" : ""
                            }
                          >
                            <td>
                              <button
                                className="flight-name"
                                onClick={() => setSelected(f.flight_id)}
                              >
                                {f.airline}
                                <b>{f.flight_number}</b>
                              </button>
                              <small>
                                {f.origin} → {f.destination}
                              </small>
                            </td>
                            <td>
                              <span className="movement">
                                {operationOf(f) === "ARRIVAL" ? (
                                  <ArrowDownLeft size={15} />
                                ) : (
                                  <ArrowUpRight size={15} />
                                )}{" "}
                                {operationOf(f) === "ARRIVAL"
                                  ? "Arrival"
                                  : "Departure"}
                              </span>
                            </td>
                            <td>
                              {f.plan ? (
                                <>
                                  {time(f.plan.runway_time_utc)}{" "}
                                  <small>RWY {f.plan.runway_id}</small>
                                </>
                              ) : (
                                <>
                                  {time(scheduledTime(f))}{" "}
                                  <small>UNSCHEDULED</small>
                                </>
                              )}
                            </td>
                            <td>{f.plan?.gate_id ?? "—"}</td>
                            <td>
                              <span className="flight-state">
                                {f.state.replaceAll("_", " ")}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {flights.length === 0 && (
                      <p className="empty-search">
                        No flights match this search.
                      </p>
                    )}
                  </div>
                  {flight && (
                    <details className="flight-detail">
                      <summary>
                        Selected {flight.airline}
                        {flight.flight_number} · provenance & gate times
                      </summary>
                      <p>
                        Leg ID: <code>{flight.flight_id}</code> · source row{" "}
                        {flight.source_row_id}
                      </p>
                      <p>
                        Scheduled gate departure:{" "}
                        {dateTime(flight.scheduled_gate_departure_utc)}
                        <br />
                        Scheduled gate arrival:{" "}
                        {dateTime(flight.scheduled_gate_arrival_utc)}
                      </p>
                      {flight.assumptions.map((a) => (
                        <p key={a.label}>
                          {a.label}: {a.value} {a.unit} · {a.source_type}
                        </p>
                      ))}
                    </details>
                  )}
                </section>
              </div>
              <aside className="recovery-column">
                {editor && tab !== "replay" ? (
                  <Instructions
                    snapshot={snapshot}
                    client={client}
                    sessionId={sessionId}
                    onClose={() => setEditor(false)}
                    onConfirm={async (draft, version) => {
                      await command(
                        "instructions",
                        { instruction: draft },
                        version,
                      );
                      setNotice(
                        "Instruction issued. No acknowledgment or execution is implied.",
                      );
                    }}
                  />
                ) : tab === "replay" ? (
                  <section className="panel">
                    <h2>Recorded state</h2>
                    <p>
                      State v{visible.state_version} · {visible.mode}
                    </p>
                    <p>
                      Proposal: {visible.pending_negotiation?.status ?? "None"}
                    </p>
                    <p>Replay cannot send operational commands.</p>
                  </section>
                ) : (
                  <ProposalReview
                    snapshot={snapshot}
                    connection={effectiveConnection}
                    now={now}
                    busy={busy}
                    onNegotiate={() =>
                      act("negotiations", {
                        affected_ids: snapshot.flights.map((f) => f.flight_id),
                      })
                    }
                    onDecision={(d) => {
                      const p = snapshot.pending_negotiation?.plan;
                      if (p)
                        act(`proposals/${p.proposal_id}/decision`, {
                          plan_hash: p.plan_hash,
                          decision: d,
                        });
                    }}
                    onRevise={() => setEditor(true)}
                  />
                )}
                <section className="panel activity-panel">
                  <div className="panel-heading">
                    <h2>Session activity</h2>
                    <Activity size={16} />
                  </div>
                  {events.length === 0 ? (
                    <p className="subtle">
                      Your decisions and simulation events will appear here.
                    </p>
                  ) : (
                    <ol>
                      {events
                        .slice(-6)
                        .reverse()
                        .map((e) => (
                          <li key={e.event_id}>
                            <span className="activity-dot" />
                            <div>
                              <strong>
                                {e.event_type
                                  .toLowerCase()
                                  .replaceAll("_", " ")}
                              </strong>
                              <small>
                                {time(e.sim_time_utc)} {zone(e.sim_time_utc)} ·
                                event {e.event_seq} · state v{e.state_version}
                              </small>
                            </div>
                          </li>
                        ))}
                    </ol>
                  )}
                </section>
              </aside>
            </div>
          )}
          {client.mode === "mock" && (
            <details className="demo-controls">
              <summary>
                <SlidersHorizontal size={15} /> Demo conditions{" "}
                <span>MOCK ONLY</span>
              </summary>
              <div className="demo-controls-inner">
                <label htmlFor="demo-outcome">Next negotiation outcome</label>
                <select
                  id="demo-outcome"
                  value={demoOutcome}
                  onChange={(e) => {
                    const value = e.target.value as DemoOutcome;
                    setDemoOutcome(value);
                    client.setDemoOutcome?.(value);
                  }}
                >
                  <option value="consensus">Consensus</option>
                  <option value="fallback">Ground veto → fallback</option>
                  <option value="no-plan">No feasible candidate found</option>
                  <option value="unknown">Unknown fuel check</option>
                  <option value="expired">Expired proposal</option>
                </select>
                <button
                  className="button"
                  onClick={() => {
                    client.setConnected?.(!demoConnected);
                    setDemoConnected(!demoConnected);
                  }}
                >
                  {demoConnected ? "Simulate disconnect" : "Reconnect demo"}
                </button>
              </div>
            </details>
          )}
          <footer>
            <span>
              <Shield size={13} /> Decision support simulation · not for
              operational use
            </span>
            <span>
              {client.mode === "mock"
                ? "24 synthetic flights · schematic layout · scripted agents"
                : "Authenticated API · backend authority"}{" "}
              · Contract v{snapshot.schema_version}
            </span>
          </footer>
        </main>
      </div>
    </div>
  );
}
