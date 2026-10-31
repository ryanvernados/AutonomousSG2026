import { afterEach, describe, expect, it, vi } from "vitest";
import {
  KIND_TO_CANONICAL,
  toCanonicalInstruction,
  toUiNegotiation,
  toUiSnapshot,
} from "./canonical";
import { LiveClient } from "./live";

afterEach(() => vi.unstubAllGlobals());

const canonicalSnapshot = {
  schema_version: "1",
  scenario_id: "sfo-synthetic-v1",
  state_version: 2,
  sim_time_utc: "2015-01-01T16:00:00+00:00",
  mode: "ASSISTED",
  clock_status: "PAUSED",
  instruction_version: 1,
  flights: [
    {
      flight_id: "F1",
      destination: "SFO",
      plan: null,
      assumptions: {
        wake_category: { value: "MEDIUM", source_type: "SYNTHETIC", unit: "category", explanation: "" },
      },
    },
  ],
  resource_reservations: [],
  active_instructions: [{ kind: "RUNWAY_UNAVAILABLE", resource_id: "10R-28L", instruction_version: 1 }],
  data_quality: { source_manifest: { member2: {} }, coverage_limits: ["Domestic legs only."] },
  infrastructure: {
    source_type: "SOURCED",
    effective_date: "2026-10-29",
    resources: [
      { resource_id: "10R-28L", kind: "RUNWAY" },
      { resource_id: "B:x-y#1", kind: "TAXIWAY" },
      { resource_id: "EQ-PUSHBACK-TUGS", kind: "EQUIPMENT" },
      { resource_id: "SIM-A01", kind: "GATE" },
    ],
  },
};

describe("canonical API mapping", () => {
  it("maps every UI instruction kind to the canonical enum and back", () => {
    expect(KIND_TO_CANONICAL).toEqual({
      RUNWAY_CLOSURE: "RUNWAY_UNAVAILABLE",
      TAXIWAY_BLOCKAGE: "TAXIWAY_BLOCKED",
      EQUIPMENT_OUTAGE: "EQUIPMENT_FAILURE",
      PROTECTED_SLOT: "PROTECTED_SLOT",
    });
    const i = toCanonicalInstruction(
      { kind: "TAXIWAY_BLOCKAGE", resource_id: "B:x-y#1", starts_at_utc: "a", ends_at_utc: "b", flight_id: null, parameters: {} },
      "scn",
      3,
    );
    expect(i).toMatchObject({ scenario_id: "scn", base_state_version: 3, kind: "TAXIWAY_BLOCKED" });
  });

  it("unwraps SnapshotResponse into the UI snapshot with discovered resources", () => {
    const s = toUiSnapshot({ snapshot: canonicalSnapshot, proposals: [], negotiations: [] }, 7);
    expect(s.clock_running).toBe(false);
    expect(s.last_event_seq).toBe(7);
    expect(s.pending_negotiation).toBeNull();
    expect(s.active_instructions[0].kind).toBe("RUNWAY_CLOSURE");
    expect(s.flights[0].assumptions).toEqual([
      { label: "wake_category", value: "MEDIUM", unit: "category", source_type: "SYNTHETIC" },
    ]);
    expect(s.data_quality.source_type).toBe("HISTORICAL");
    expect(s.resource_catalog).toEqual({
      runways: ["10R-28L"],
      taxiways: ["B:x-y#1"],
      equipment: ["EQ-PUSHBACK-TUGS"],
    });
  });

  it("derives negotiation outcome, participants and proposal status", () => {
    const record = {
      negotiation_id: "n1", status: "AWAITING_AUTHORIZATION", round: 2, affected_ids: ["F1"],
      messages: [], model_mode: "MOCK", model_calls: 6, duration_seconds: 0.42, reason: "CONSENSUS",
      proposal_id: "p1",
    };
    const proposal = {
      status: "AUTHORIZED", checks: [{ severity: "HARD", status: "PASS" }],
      plan: { proposal_id: "p1", source: "CONSENSUS", assignments: [], impact: { aggregate: { delay_minutes: 4, max_wait_minutes: 4 }, versus_baseline: { delay_minutes: -2 }, per_flight: { F1: { delay_minutes: 4 } } } },
    };
    const n = toUiNegotiation(record, [proposal]);
    expect(n.status).toBe("AUTHORIZED");
    expect(n.outcome).toBe("CONSENSUS");
    expect(n.required_participants).toEqual(["ATC", "GROUND", "PILOT:F1"]);
    expect(n.duration_ms).toBe(420);
    expect(n.plan?.impact).toMatchObject({ delay_minutes: 4, baseline_delay_minutes: 6, maximum_wait_minutes: 4, sacrifices: ["F1 +4 min"] });
    expect(toUiNegotiation({ ...record, status: "RUNNING", proposal_id: null }, []).outcome).toBe("PENDING");
  });

  it("creates sessions as a versioned command and keeps scenario identity for previews", async () => {
    const f = vi
      .fn()
      .mockResolvedValueOnce(Response.json({ session_id: "s1", state_version: 0, event_ids: [], snapshot: canonicalSnapshot }))
      .mockResolvedValueOnce(Response.json({ instruction: { kind: "RUNWAY_UNAVAILABLE", resource_id: "10R-28L", starts_at_utc: "a" }, issues: [], state_version: 2 }));
    vi.stubGlobal("fetch", f);
    const c = new LiveClient(async () => "token", "https://api.example/api/v1");
    const created = await c.createSession("sfo-synthetic-v1");
    const body = JSON.parse(f.mock.calls[0][1].body);
    expect(body.base_state_version).toBe(0);
    expect(body.scenario_id).toBe("sfo-synthetic-v1");
    expect(typeof body.command_id).toBe("string");
    expect(created.snapshot.state_version).toBe(2);
    const preview = await c.preview("s1", {
      kind: "RUNWAY_CLOSURE", resource_id: "10R-28L", starts_at_utc: "a", ends_at_utc: "b", flight_id: null, parameters: {},
    });
    expect(JSON.parse(f.mock.calls[1][1].body)).toMatchObject({
      scenario_id: "sfo-synthetic-v1", base_state_version: 2, kind: "RUNWAY_UNAVAILABLE",
    });
    expect(preview.instruction.kind).toBe("RUNWAY_CLOSURE");
  });
});
