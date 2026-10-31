import type { Flight, Negotiation, Snapshot, Connection } from "./api/types";
export const time = (iso: string) =>
  new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Los_Angeles",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(iso));
export const dateTime = (iso: string) =>
  new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Los_Angeles",
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZoneName: "short",
  }).format(new Date(iso));
export const zone = (iso: string) =>
  new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Los_Angeles",
    timeZoneName: "short",
  })
    .formatToParts(new Date(iso))
    .find((p) => p.type === "timeZoneName")!.value;
// Canonical sessions start with no published plan; derive direction from the flight leg.
export const operationOf = (f: Flight): "ARRIVAL" | "DEPARTURE" =>
  f.plan?.operation ??
  (f.destination === "SFO" ? "ARRIVAL" : "DEPARTURE");
export const scheduledTime = (f: Flight) =>
  operationOf(f) === "ARRIVAL"
    ? f.scheduled_gate_arrival_utc
    : f.scheduled_gate_departure_utc;
export const instructionLabel = {
  RUNWAY_CLOSURE: "Runway closure",
  TAXIWAY_BLOCKAGE: "Taxiway blockage",
  EQUIPMENT_OUTAGE: "Equipment outage",
  PROTECTED_SLOT: "Protected flight slot",
};
export function approvalBlock(
  snapshot: Snapshot,
  n: Negotiation | null,
  connection: Connection,
  now = Date.now(),
): string | null {
  if (connection !== "connected")
    return "Connection lost. Refresh the authoritative state before authorizing.";
  if (snapshot.mode === "MANUAL")
    return "Manual control is active. Pending automation is suspended.";
  if (!n?.plan) return "No proposal is ready for review.";
  if (n.status !== "AWAITING_AUTHORIZATION")
    return `Proposal is ${n.status.toLowerCase().replaceAll("_", " ")}.`;
  const p = n.plan;
  if (
    p.base_state_version !== snapshot.state_version ||
    p.instruction_version !==
      Math.max(
        0,
        ...snapshot.active_instructions.map((i) => i.instruction_version),
      )
  )
    return "Stale proposal. Operations or instructions have changed.";
  if (
    !Number.isFinite(Date.parse(p.expires_at_utc)) ||
    Date.parse(p.expires_at_utc) <= now
  )
    return "Expired proposal. Request a new plan.";
  if (
    !n.checks.length ||
    !n.checks.some((c) => c.severity === "HARD") ||
    n.checks.some((c) => c.severity === "HARD" && c.status !== "PASS")
  )
    return "Hard checks are missing, failed or unknown. Authorization is blocked.";
  if (
    n.outcome !== "FALLBACK" &&
    (n.outcome !== "CONSENSUS" ||
      !n.required_participants.length ||
      n.messages.some((m) => m.round === 2 && m.type === "REJECT") ||
      n.required_participants.some(
        (id) =>
          n.messages.filter((m) => m.round === 2 && m.sender_id === id)
            .length !== 1 ||
          !n.messages.some(
            (m) =>
              m.round === 2 &&
              m.sender_id === id &&
              m.type === "ACCEPT" &&
              m.plan_hash === p.plan_hash &&
              m.proposal_id === p.proposal_id &&
              m.base_state_version === p.base_state_version &&
              m.instruction_version === p.instruction_version,
          ),
      ))
  )
    return "Agreement is incomplete for this exact plan identity.";
  return null;
}
