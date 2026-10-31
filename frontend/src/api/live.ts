import { createClient } from "@supabase/supabase-js";
import type {
  AirportClient,
  AuditEvent,
  Command,
  Connection,
  InstructionDraft,
  Preview,
  ScenarioSummary,
  Snapshot,
} from "./types";
import {
  toCanonicalInstruction,
  toUiDraft,
  toUiScenario,
  toUiSnapshot,
} from "./canonical";
export function createAuth() {
  const url = import.meta.env.VITE_SUPABASE_URL,
    key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY;
  if (!url || !key)
    throw new Error(
      "LIVE mode requires VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY.",
    );
  if (key.startsWith("sb_secret_"))
    throw new Error("Use a publishable Supabase key, never a secret key.");
  return createClient(url, key);
}
// Streaming parser handles split chunks, multi-line data, CRLF, and comment heartbeats.
export function parseSse(buffer: string): {
  frames: { id: string; data: string }[];
  remaining: string;
} {
  const frames: { id: string; data: string }[] = [];
  let match: RegExpExecArray | null;
  while ((match = /\r?\n\r?\n/.exec(buffer))) {
    const block = buffer.slice(0, match.index);
    buffer = buffer.slice(match.index + match[0].length);
    const lines = block.split(/\r?\n/);
    frames.push({
      id:
        lines
          .find((l) => l.startsWith("id:"))
          ?.slice(3)
          .trim() ?? "",
      data: lines
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice(5).replace(/^ /, ""))
        .join("\n"),
    });
  }
  return { frames, remaining: buffer };
}
// Talks Member 1's canonical v1 API and maps responses to the UI view model (canonical.ts).
export class LiveClient implements AirportClient {
  readonly mode = "live" as const;
  // The REST snapshot carries no event cursor; track the highest sequence seen on the stream.
  private lastEventSeq = 0;
  private identity = new Map<
    string,
    { scenarioId: string; stateVersion: number }
  >();
  constructor(
    private token: () => Promise<string>,
    private base = import.meta.env.VITE_API_BASE_URL ||
      "http://localhost:8000/api/v1",
  ) {}
  private async request<T>(path: string, body?: unknown): Promise<T> {
    const response = await fetch(this.base + path, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        Authorization: `Bearer ${await this.token()}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(15000),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(
        `${response.status} · ${error.message ?? error.detail ?? response.statusText}`,
      );
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }
  private remember(id: string, snapshot: Snapshot) {
    this.identity.set(id, {
      scenarioId: snapshot.scenario_id,
      stateVersion: snapshot.state_version,
    });
    return snapshot;
  }
  private async current(id: string) {
    if (!this.identity.has(id)) await this.snapshot(id);
    return this.identity.get(id)!;
  }
  async scenarios() {
    const list = await this.request<Record<string, unknown>[]>("/scenarios");
    return list.map(toUiScenario);
  }
  async createSession(scenarioId: string) {
    // Session creation is a command: fresh command_id and initial base_state_version 0.
    const result = await this.request<{
      session_id: string;
      snapshot: Record<string, unknown>;
    }>("/sessions", {
      command_id: crypto.randomUUID(),
      base_state_version: 0,
      scenario_id: scenarioId,
    });
    const snapshot = toUiSnapshot(
      { snapshot: result.snapshot, proposals: [], negotiations: [] },
      this.lastEventSeq,
    );
    return {
      session_id: result.session_id,
      snapshot: this.remember(result.session_id, snapshot),
    };
  }
  async snapshot(id: string): Promise<Snapshot> {
    const response = await this.request<Record<string, unknown>>(
      `/sessions/${encodeURIComponent(id)}/snapshot`,
    );
    return this.remember(id, toUiSnapshot(response, this.lastEventSeq));
  }
  async preview(id: string, draft: InstructionDraft): Promise<Preview> {
    const { scenarioId, stateVersion } = await this.current(id);
    const result = await this.request<{
      instruction: Record<string, unknown>;
      issues: string[];
    }>(
      `/sessions/${encodeURIComponent(id)}/instructions/preview`,
      toCanonicalInstruction(draft, scenarioId, stateVersion),
    );
    return {
      instruction: toUiDraft(result.instruction),
      issues: result.issues,
    } as Preview;
  }
  async command(
    id: string,
    path: string,
    c: Command & Record<string, unknown>,
  ) {
    let body: Record<string, unknown> = c;
    if (path === "instructions" && c.instruction) {
      const { scenarioId } = await this.current(id);
      body = {
        ...c,
        instruction: toCanonicalInstruction(
          c.instruction as InstructionDraft,
          scenarioId,
          c.base_state_version,
        ),
      };
    }
    await this.request(`/sessions/${encodeURIComponent(id)}/${path}`, body);
  }
  subscribe(
    id: string,
    afterSeq: number,
    event: (e: AuditEvent | null) => void,
    connection: (s: Connection) => void,
  ) {
    let stopped = false,
      cursor = afterSeq,
      retry = 0,
      timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController;
    const connect = async () => {
      controller = new AbortController();
      connection("connecting");
      let watchdog: ReturnType<typeof setInterval> | undefined;
      const handshakeTimeout = setTimeout(() => controller.abort(), 15000);
      try {
        const response = await fetch(
          `${this.base}/sessions/${encodeURIComponent(id)}/events?after_seq=${cursor}`,
          {
            headers: {
              Authorization: `Bearer ${await this.token()}`,
              Accept: "text/event-stream",
              "Last-Event-ID": String(cursor),
            },
            signal: controller.signal,
          },
        );
        clearTimeout(handshakeTimeout);
        if (stopped) return;
        if (!response.ok || !response.body)
          throw new Error(`Event stream: ${response.status}`);
        // Refresh authoritative state on every successful connection before enabling commands.
        event(null);
        connection("connected");
        retry = 0;
        const reader = response.body.getReader(),
          decoder = new TextDecoder();
        let buffer = "",
          last = Date.now();
        watchdog = setInterval(() => {
          if (Date.now() - last > 15000) controller.abort();
        }, 3000);
        while (!stopped) {
          const chunk = await reader.read();
          if (chunk.done) throw new Error("Stream closed");
          last = Date.now();
          buffer += decoder.decode(chunk.value, { stream: true });
          const parsed = parseSse(buffer);
          buffer = parsed.remaining;
          for (const frame of parsed.frames) {
            if (!frame.data) {
              event(null);
              continue;
            }
            const e = JSON.parse(frame.data) as AuditEvent;
            if (!Number.isSafeInteger(e.event_seq))
              throw new Error("Invalid event sequence");
            if (e.event_seq <= cursor) continue;
            cursor = e.event_seq;
            this.lastEventSeq = Math.max(this.lastEventSeq, cursor);
            event(e);
          }
        }
      } catch {
        if (!stopped) {
          connection("disconnected");
          timer = setTimeout(
            () => void connect(),
            Math.min(1000 * 2 ** retry++, 10000),
          );
        }
      } finally {
        clearTimeout(handshakeTimeout);
        if (watchdog) clearInterval(watchdog);
      }
    };
    void connect();
    return () => {
      stopped = true;
      controller?.abort();
      clearTimeout(timer);
    };
  }
}
