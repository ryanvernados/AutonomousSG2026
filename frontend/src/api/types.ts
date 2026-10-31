import type { components } from "./generated";
type S = components["schemas"];
export type Snapshot = S["Snapshot"];
export type Flight = S["Flight"];
export type Assignment = S["Assignment"];
export type Instruction = S["Instruction"];
export type InstructionDraft = S["InstructionDraft"];
export type Plan = S["Plan"];
export type Negotiation = S["Negotiation"];
export type CheckResult = S["CheckResult"];
export type AuditEvent = S["AuditEvent"];
export type ScenarioSummary = S["ScenarioSummary"];
export type Command = S["Command"];
export type Preview = S["Preview"];
export type Connection = "connected" | "connecting" | "disconnected";
export type DemoOutcome =
  | "consensus"
  | "fallback"
  | "no-plan"
  | "unknown"
  | "expired";
export interface AirportClient {
  readonly mode: "mock" | "live";
  scenarios(): Promise<ScenarioSummary[]>;
  createSession(
    scenarioId: string,
  ): Promise<{ session_id: string; snapshot: Snapshot }>;
  snapshot(sessionId: string): Promise<Snapshot>;
  preview(sessionId: string, draft: InstructionDraft): Promise<Preview>;
  command(
    sessionId: string,
    path: string,
    command: Command & Record<string, unknown>,
  ): Promise<void>;
  subscribe(
    sessionId: string,
    afterSeq: number,
    onEvent: (event: AuditEvent | null) => void,
    onConnection: (state: Connection) => void,
  ): () => void;
  setDemoOutcome?(outcome: DemoOutcome): void;
  setConnected?(connected: boolean): void;
}
