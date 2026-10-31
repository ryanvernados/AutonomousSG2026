import { describe, it, expect } from "vitest";
import { MockClient } from "./mock";
import { approvalBlock } from "../domain";
import type { InstructionDraft, DemoOutcome } from "./types";
const draft: InstructionDraft = {
  kind: "RUNWAY_CLOSURE",
  resource_id: "28R",
  flight_id: null,
  starts_at_utc: "2015-07-15T17:00:00.000Z",
  ends_at_utc: "2015-07-15T17:20:00.000Z",
  parameters: {},
};
async function send(
  c: MockClient,
  path: string,
  body: Record<string, unknown> = {},
) {
  await c.command("demo", path, {
    command_id: crypto.randomUUID(),
    base_state_version: (await c.snapshot()).state_version,
    ...body,
  });
}
async function ready(c: MockClient, outcome: DemoOutcome = "consensus") {
  c.setDemoOutcome(outcome);
  await send(c, "negotiations");
  await expect
    .poll(async () => (await c.snapshot()).pending_negotiation?.status)
    .not.toBe("NEGOTIATING");
  return (await c.snapshot()).pending_negotiation!;
}
describe("mock authorization boundary", () => {
  it("blocks contradictory and duplicate ballots even when acceptance exists", async () => {
    const c = new MockClient(1),
      n = await ready(c),
      s = await c.snapshot();
    const vote = n.messages.find((m) => m.round === 2)!;
    expect(
      approvalBlock(
        s,
        { ...n, messages: [...n.messages, { ...vote, type: "REJECT" }] },
        "connected",
      ),
    ).toMatch(/incomplete/);
    expect(
      approvalBlock(s, { ...n, messages: [...n.messages, vote] }, "connected"),
    ).toMatch(/incomplete/);
  });
  it("preview does not mutate and confirmation retains the published schedule", async () => {
    const c = new MockClient(1),
      before = await c.snapshot();
    expect((await c.preview("demo", draft)).issues).toEqual([]);
    expect(await c.snapshot()).toEqual(before);
    await send(c, "instructions", { instruction: draft });
    const after = await c.snapshot();
    expect(after.active_instructions).toHaveLength(1);
    expect(after.flights).toEqual(before.flights);
    expect(after.state_version).toBe(before.state_version + 1);
  });
  it("negotiation and rejection never publish a proposed schedule", async () => {
    const c = new MockClient(1),
      before = await c.snapshot();
    const n = await ready(c);
    expect((await c.snapshot()).flights).toEqual(before.flights);
    expect(n.round).toBe(2);
    expect(n.model_calls).toBe(0);
    await send(c, `proposals/${n.plan!.proposal_id}/decision`, {
      decision: "REJECT",
      plan_hash: n.plan!.plan_hash,
    });
    expect((await c.snapshot()).flights).toEqual(before.flights);
    expect((await c.snapshot()).pending_negotiation?.status).toBe("REJECTED");
  });
  it("approval atomically publishes the full plan; duplicate commands are idempotent", async () => {
    const c = new MockClient(1);
    const n = await ready(c);
    const command = {
      command_id: crypto.randomUUID(),
      base_state_version: (await c.snapshot()).state_version,
      decision: "APPROVE",
      plan_hash: n.plan!.plan_hash,
    };
    const path = `proposals/${n.plan!.proposal_id}/decision`;
    await c.command("demo", path, command);
    const committed = await c.snapshot();
    expect(committed.flights.map((f) => f.plan)).toEqual(n.plan!.assignments);
    expect(committed.pending_negotiation?.status).toBe("AUTHORIZED");
    await c.command("demo", path, command);
    expect(await c.snapshot()).toEqual(committed);
  });
  it.each(["unknown", "expired"] as const)(
    "blocks %s proposals",
    async (outcome) => {
      const c = new MockClient(1);
      const n = await ready(c, outcome);
      expect(approvalBlock(await c.snapshot(), n, "connected")).toBeTruthy();
      await expect(
        send(c, `proposals/${n.plan!.proposal_id}/decision`, {
          decision: "APPROVE",
          plan_hash: n.plan!.plan_hash,
        }),
      ).rejects.toThrow();
    },
  );
  it("a state change makes an old plan stale", async () => {
    const c = new MockClient(1);
    const n = await ready(c);
    await send(c, "clock", { action: "STEP", seconds: 60 });
    await expect(
      send(c, `proposals/${n.plan!.proposal_id}/decision`, {
        decision: "APPROVE",
        plan_hash: n.plan!.plan_hash,
      }),
    ).rejects.toThrow();
  });
  it("manual mode cancels pending work and preserves instructions and reservations", async () => {
    const c = new MockClient(10);
    await send(c, "instructions", { instruction: draft });
    const before = await c.snapshot();
    await send(c, "negotiations");
    await send(c, "control", { mode: "MANUAL" });
    await new Promise((r) => setTimeout(r, 40));
    const after = await c.snapshot();
    expect(after.pending_negotiation?.status).toBe("STALE");
    expect(after.active_instructions).toEqual(before.active_instructions);
    expect(after.resource_reservations).toEqual(before.resource_reservations);
    await expect(send(c, "negotiations")).rejects.toThrow(/assisted/);
  });
  it("fallback is explicitly labeled and still needs authorization", async () => {
    const c = new MockClient(1);
    const n = await ready(c, "fallback");
    expect(n.outcome).toBe("FALLBACK");
    expect(n.status).toBe("AWAITING_AUTHORIZATION");
    expect(n.messages.some((m) => m.type === "REJECT")).toBe(true);
    expect(approvalBlock(await c.snapshot(), n, "connected")).toBeNull();
  });
  it("no-plan outcome never supplies an approvable candidate", async () => {
    const c = new MockClient(1);
    const n = await ready(c, "no-plan");
    expect(n.status).toBe("NO_FEASIBLE_PLAN_FOUND");
    expect(n.plan).toBeNull();
  });
  it("rejects invalid times, stale command versions, and changed plan identity", async () => {
    const c = new MockClient(1);
    expect(
      (await c.preview("demo", { ...draft, ends_at_utc: draft.starts_at_utc }))
        .issues.length,
    ).toBeGreaterThan(0);
    await expect(
      c.command("demo", "clock", {
        command_id: "stale",
        base_state_version: 0,
        action: "STEP",
        seconds: 60,
      }),
    ).rejects.toThrow(/409/);
    const n = await ready(c);
    await expect(
      send(c, `proposals/${n.plan!.proposal_id}/decision`, {
        decision: "APPROVE",
        plan_hash: "tampered",
      }),
    ).rejects.toThrow(/identity/);
  });
  it("blocks missing checks, a missing ballot, and a disconnected UI", async () => {
    const c = new MockClient(1);
    const n = await ready(c),
      s = await c.snapshot();
    expect(approvalBlock(s, { ...n, checks: [] }, "connected")).toMatch(
      /missing/,
    );
    expect(
      approvalBlock(
        s,
        { ...n, messages: n.messages.slice(0, -1) },
        "connected",
      ),
    ).toMatch(/incomplete/);
    expect(approvalBlock(s, n, "disconnected")).toMatch(/Connection/);
  });
});
