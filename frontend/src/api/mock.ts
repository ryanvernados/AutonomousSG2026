import type { LiveSnapshot } from "./canonical";
import type {
  AirportClient,
  AuditEvent,
  Command,
  Connection,
  DemoOutcome,
  InstructionDraft,
  Negotiation,
  Snapshot,
} from "./types";
import { initialSnapshot, reservations, scenario } from "./fixtures";
import { approvalBlock } from "../domain";
const copy = <T>(value: T): T => structuredClone(value);
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
export class MockClient implements AirportClient {
  readonly mode = "mock" as const;
  private state = initialSnapshot();
  private seen = new Set<string>();
  private listeners = new Set<{
    event: (e: AuditEvent | null) => void;
    connection: (s: Connection) => void;
  }>();
  private connected = true;
  private outcome: DemoOutcome = "consensus";
  private epoch = 0;
  private busy = false;
  private events: AuditEvent[] = [];
  constructor(private roundDelay = 800) {}
  async scenarios() {
    return [scenario];
  }
  async createSession() {
    return { session_id: "demo-session", snapshot: copy(this.state) };
  }
  async snapshot() {
    if (!this.connected)
      throw new Error("Demo connection is offline. Reconnect to continue.");
    return copy(this.state);
  }
  setDemoOutcome(outcome: DemoOutcome) {
    this.outcome = outcome;
  }
  setConnected(value: boolean) {
    this.connected = value;
    this.listeners.forEach((l) =>
      l.connection(value ? "connected" : "disconnected"),
    );
    if (value) this.listeners.forEach((l) => l.event(null));
  }
  private emit(
    type: string,
    correlationId = "demo",
    payload: Record<string, unknown> = {},
  ) {
    const e: AuditEvent = {
      event_id: crypto.randomUUID(),
      event_seq: ++this.state.last_event_seq,
      scenario_id: this.state.scenario_id,
      state_version: this.state.state_version,
      sim_time_utc: this.state.sim_time_utc,
      recorded_at_utc: new Date().toISOString(),
      actor_id: "demo-controller",
      event_type: type,
      correlation_id: correlationId,
      payload,
    };
    this.events.push(e);
    if (this.connected) this.listeners.forEach((l) => l.event(copy(e)));
  }
  subscribe(
    _id: string,
    after: number,
    event: (e: AuditEvent | null) => void,
    connection: (s: Connection) => void,
  ) {
    const listener = { event, connection };
    this.listeners.add(listener);
    connection(this.connected ? "connected" : "disconnected");
    this.events
      .filter((e) => e.event_seq > after)
      .forEach((e) => event(copy(e)));
    const timer = setInterval(() => {
      if (!this.connected) return;
      if (this.state.clock_running) {
        this.advance((this.state as LiveSnapshot).clock_speed ?? 10);
        this.emit("CLOCK_ADVANCED");
      } else event(null);
    }, 1000);
    return () => {
      clearInterval(timer);
      this.listeners.delete(listener);
    };
  }
  async preview(_id: string, draft: InstructionDraft) {
    const issues: string[] = [];
    const start = Date.parse(draft.starts_at_utc),
      end = Date.parse(draft.ends_at_utc ?? "");
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start)
      issues.push("End time must be later than start time.");
    if (start < Date.parse(this.state.sim_time_utc))
      issues.push("Start time cannot be before the simulation clock.");
    if (end > Date.parse(scenario.end_utc) + 3600000)
      issues.push("End time is outside the recovery buffer (one hour).");
    const resources = {
      RUNWAY_CLOSURE: ["28L", "28R"],
      TAXIWAY_BLOCKAGE: ["A", "B", "C"],
      EQUIPMENT_OUTAGE: ["TUG-1"],
      PROTECTED_SLOT: [],
    };
    if (draft.kind === "PROTECTED_SLOT") {
      if (!this.state.flights.some((f) => f.flight_id === draft.flight_id))
        issues.push("Select a valid flight.");
    } else if (!resources[draft.kind]?.includes(draft.resource_id ?? ""))
      issues.push("Select a valid resource.");
    return { instruction: copy(draft), issues };
  }
  private invalidate() {
    this.epoch++;
    if (
      this.state.pending_negotiation &&
      !["AUTHORIZED", "REJECTED"].includes(
        this.state.pending_negotiation.status,
      )
    )
      this.state.pending_negotiation.status = "STALE";
  }
  private advance(seconds: number) {
    this.state.sim_time_utc = new Date(
      Date.parse(this.state.sim_time_utc) + seconds * 1000,
    ).toISOString();
    this.state.state_version++;
    this.invalidate();
    this.state.flights.forEach((f) => {
      const t = Date.parse(this.state.sim_time_utc);
      f.state =
        t > Date.parse(f.plan.taxi_end_utc)
          ? f.plan.operation === "ARRIVAL"
            ? "AT_GATE"
            : "DEPARTED"
          : t >= Date.parse(f.plan.taxi_start_utc)
            ? "TAXIING"
            : f.plan.operation === "ARRIVAL"
              ? "INBOUND"
              : "AT_GATE";
    });
  }
  async command(
    _id: string,
    path: string,
    c: Command & Record<string, unknown>,
  ) {
    if (!this.connected) throw new Error("Disconnected. Command was not sent.");
    if (this.seen.has(c.command_id)) return;
    if (this.busy)
      throw new Error(
        "Another command is being processed. Refresh and try again.",
      );
    this.busy = true;
    try {
      if (c.base_state_version !== this.state.state_version)
        throw new Error("409 · Stale state. Refresh and try again.");
      if (path === "instructions") {
        const d = c.instruction as InstructionDraft;
        const preview = await this.preview(_id, d);
        if (preview.issues.length) throw new Error(preview.issues.join(" "));
        this.invalidate();
        this.state.active_instructions.push({
          ...copy(d),
          instruction_id: crypto.randomUUID(),
          instruction_version:
            Math.max(
              0,
              ...this.state.active_instructions.map(
                (i) => i.instruction_version,
              ),
            ) + 1,
          scenario_id: this.state.scenario_id,
          base_state_version: this.state.state_version,
          entered_by: "demo-controller",
        });
        this.state.state_version++;
      } else if (path === "clock") {
        if (c.action === "RESET") {
          this.invalidate();
          const v = this.state.state_version + 1,
            seq = this.state.last_event_seq;
          this.state = initialSnapshot();
          this.state.state_version = v;
          this.state.last_event_seq = seq;
        } else if (c.action === "STEP") {
          if (this.state.clock_running)
            throw new Error("Pause before stepping.");
          const seconds = Number(c.seconds);
          if (!Number.isFinite(seconds) || seconds <= 0 || seconds > 600)
            throw new Error("Step must be 1–600 seconds.");
          this.advance(seconds);
        } else if (c.action === "SPEED") {
          const speed = Number(c.speed);
          if (!Number.isInteger(speed) || speed < 1 || speed > 600)
            throw new Error("Speed must be 1–600.");
          (this.state as LiveSnapshot).clock_speed = speed;
          this.state.state_version++;
        } else {
          this.state.clock_running = c.action === "RESUME";
          this.invalidate();
          this.state.state_version++;
        }
      } else if (path === "control") {
        if (c.mode !== "MANUAL" && c.mode !== "ASSISTED")
          throw new Error("Invalid control mode.");
        this.invalidate();
        this.state.mode = c.mode;
        this.state.state_version++;
      } else if (path === "negotiations") {
        if (this.state.mode === "MANUAL")
          throw new Error("Return to assisted mode to negotiate.");
        if (this.state.pending_negotiation?.status === "NEGOTIATING")
          throw new Error("Negotiation is already running.");
        if (this.state.clock_running) {
          this.state.clock_running = false;
          this.state.state_version++;
        }
        this.invalidate();
        const epoch = this.epoch;
        const n: Negotiation = {
          negotiation_id: crypto.randomUUID(),
          status: "NEGOTIATING",
          round: 1,
          plan: null,
          messages: [],
          checks: [],
          required_participants: [],
          outcome: "PENDING",
          reason: "Collecting scripted domain offers.",
          model_mode: "MOCK",
          model_calls: 0,
          duration_ms: 0,
        };
        this.state.pending_negotiation = n;
        void this.negotiate(epoch, this.outcome).catch((error) => {
          if (epoch === this.epoch) {
            n.status = "NO_FEASIBLE_PLAN_FOUND";
            n.reason = String(error);
            this.emit("NEGOTIATION_FAILED");
          }
        });
      } else if (path.startsWith("proposals/")) {
        const n = this.state.pending_negotiation;
        if (
          !n?.plan ||
          path !== `proposals/${n.plan.proposal_id}/decision` ||
          c.plan_hash !== n.plan.plan_hash
        )
          throw new Error("409 · Plan identity has changed.");
        if (c.decision === "APPROVE") {
          const block = approvalBlock(this.state, n, "connected");
          if (block) throw new Error(block);
          this.state.flights = this.state.flights.map((f) => ({
            ...f,
            plan: copy(
              n.plan!.assignments.find((a) => a.flight_id === f.flight_id) ??
                f.plan,
            ),
          }));
          this.state.resource_reservations = reservations(
            this.state.flights.map((f) => f.plan),
          );
          this.state.state_version++;
          n.status = "AUTHORIZED";
        } else if (c.decision === "REJECT") {
          if (n.status !== "AWAITING_AUTHORIZATION")
            throw new Error("Proposal is not awaiting a decision.");
          n.status = "REJECTED";
        } else throw new Error("Invalid decision.");
      } else throw new Error("Unsupported command.");
      this.seen.add(c.command_id);
      this.emit(
        path ===
          "proposals/" +
            this.state.pending_negotiation?.plan?.proposal_id +
            "/decision"
          ? `PLAN_${c.decision === "APPROVE" ? "AUTHORIZED" : "REJECTED"}`
          : path.toUpperCase().replaceAll("/", "_"),
        c.command_id,
      );
    } finally {
      this.busy = false;
    }
  }
  private async negotiate(epoch: number, outcome: DemoOutcome) {
    await sleep(this.roundDelay);
    if (epoch !== this.epoch) return;
    const n = this.state.pending_negotiation!;
    const before = this.state.flights.map((f) => copy(f.plan));
    const restrictionEnd = Math.max(
      Date.parse(this.state.sim_time_utc),
      ...this.state.active_instructions.map((i) =>
        Date.parse(i.ends_at_utc ?? i.starts_at_utc),
      ),
    );
    const assignments = before.map((a, i) => {
      const shift =
        Math.max(0, restrictionEnd - Date.parse(a.runway_time_utc)) +
        ((i % 4) + 1) * 60000;
      const moved = { ...a };
      for (const key of [
        "runway_time_utc",
        "taxi_start_utc",
        "taxi_end_utc",
        "gate_start_utc",
        "gate_end_utc",
      ] as const) {
        if (a[key])
          moved[key] = new Date(Date.parse(a[key]!) + shift).toISOString();
      }
      return moved;
    });
    const delay = assignments.reduce(
      (sum, a, i) =>
        sum +
        (Date.parse(a.runway_time_utc) -
          Date.parse(before[i].runway_time_utc)) /
          60000,
      0,
    );
    const content = {
      proposal_id: crypto.randomUUID(),
      proposal_version: 1,
      scenario_id: this.state.scenario_id,
      base_state_version: this.state.state_version,
      instruction_version: Math.max(
        0,
        ...this.state.active_instructions.map((i) => i.instruction_version),
      ),
      assignments,
      impact: {
        delay_minutes: delay,
        baseline_delay_minutes: delay + 24,
        holding_minutes: 18,
        taxi_minutes: 168,
        maximum_wait_minutes: Math.max(
          ...assignments.map(
            (a, i) =>
              (Date.parse(a.runway_time_utc) -
                Date.parse(before[i].runway_time_utc)) /
              60000,
          ),
        ),
        unresolved_flights: 0,
        sacrifices: [
          "Departures accept later slots to preserve arrival ordering.",
          "Illustrative fixture metrics only; no backend feasibility or performance claim.",
        ],
      },
      expires_at_utc: new Date(
        Date.now() + (outcome === "expired" ? -1000 : 300000),
      ).toISOString(),
    };
    const bytes = await crypto.subtle.digest(
      "SHA-256",
      new TextEncoder().encode(JSON.stringify(content)),
    );
    if (epoch !== this.epoch) return;
    n.plan = {
      ...content,
      plan_hash: Array.from(new Uint8Array(bytes))
        .map((b) => b.toString(16).padStart(2, "0"))
        .join(""),
    };
    n.required_participants = [
      "atc",
      "ground",
      ...assignments.map((a) => `pilot:${a.flight_id}`),
    ];
    n.messages = n.required_participants.map((id) => ({
      message_id: crypto.randomUUID(),
      negotiation_id: n.negotiation_id,
      round: 1,
      sender_id: id,
      role: id === "atc" ? "ATC" : id === "ground" ? "GROUND" : "PILOT",
      type: "OFFER",
      base_state_version: content.base_state_version,
      instruction_version: content.instruction_version,
      proposal_id: content.proposal_id,
      plan_hash: n.plan!.plan_hash,
      payload: {},
      reasons: ["Scripted fixture: accept shifted reservation windows."],
    }));
    n.round = 2;
    this.emit("NEGOTIATION_ROUND_2");
    await sleep(this.roundDelay);
    if (epoch !== this.epoch) return;
    n.duration_ms = this.roundDelay * 2;
    if (outcome === "no-plan") {
      n.status = "NO_FEASIBLE_PLAN_FOUND";
      n.outcome = "NONE";
      n.reason =
        "Demo bounded search exhausted (100 candidates). This does not prove mathematical infeasibility.";
      n.plan = null;
      this.emit("NO_FEASIBLE_PLAN_FOUND");
      return;
    }
    n.checks = [
      "Runway occupancy",
      "Wake separation",
      "Runway intersections",
      "Taxi connectivity",
      "Taxi occupancy",
      "Gate compatibility",
      "Gate occupancy",
      "Crew limits",
      "Equipment capacity",
      "Fuel limits",
    ].map((label, i) => ({
      constraint_id: `fixture-${i}`,
      domain: label,
      severity: "HARD",
      status: outcome === "unknown" && i === 9 ? "UNKNOWN" : "PASS",
      affected_ids: [],
      measured_value: null,
      limit_value: null,
      unit: null,
      explanation:
        outcome === "unknown" && i === 9
          ? "Fuel input missing. Authorization is blocked."
          : "Scripted UI fixture result. Replace with Member 1 deterministic validation.",
    }));
    n.messages.push(
      ...n.messages.map((m) => ({
        ...m,
        message_id: crypto.randomUUID(),
        round: 2,
        type:
          outcome === "fallback" && m.sender_id === "ground"
            ? ("REJECT" as const)
            : ("ACCEPT" as const),
        reasons: [
          outcome === "fallback" && m.sender_id === "ground"
            ? "Scripted ground veto; deterministic fallback requested."
            : "Accept exact fixture plan hash.",
        ],
      })),
    );
    n.status = "AWAITING_AUTHORIZATION";
    n.outcome = outcome === "fallback" ? "FALLBACK" : "CONSENSUS";
    n.reason =
      outcome === "fallback"
        ? "Scripted ground veto. Rule-based fallback requires human authorization."
        : "Scripted agents agree. Published operations are unchanged.";
    this.emit("PROPOSAL_READY");
  }
}
