import { afterEach, describe, expect, it, vi } from "vitest";
import { LiveClient, parseSse } from "./live";
afterEach(() => vi.unstubAllGlobals());
describe("authenticated live adapter", () => {
  it("reconnects with the last event sequence and cancels cleanly", async () => {
    let stream: ReadableStreamDefaultController<Uint8Array>;
    const first = new ReadableStream<Uint8Array>({
      start(c) {
        stream = c;
      },
    });
    const second = new ReadableStream<Uint8Array>({ start() {} });
    const f = vi
      .fn()
      .mockResolvedValueOnce(new Response(first))
      .mockResolvedValueOnce(new Response(second));
    vi.stubGlobal("fetch", f);
    const event = vi.fn(),
      connection = vi.fn();
    const c = new LiveClient(
      async () => "private-token",
      "https://api.example/api/v1",
    );
    const stop = c.subscribe("session", 4, event, connection);
    try {
      await expect.poll(() => f.mock.calls.length).toBe(1);
      stream!.enqueue(
        new TextEncoder().encode(
          'id: 5\r\ndata: {"event_seq":5,"event_id":"e5"}\r\n\r\n',
        ),
      );
      await expect
        .poll(() => event.mock.calls.some(([e]) => e?.event_seq === 5))
        .toBe(true);
      stream!.close();
      await expect.poll(() => f.mock.calls.length, { timeout: 3000 }).toBe(2);
      expect(f.mock.calls[1][0]).toBe(
        "https://api.example/api/v1/sessions/session/events?after_seq=5",
      );
      expect(f.mock.calls[1][1].headers["Last-Event-ID"]).toBe("5");
      expect(f.mock.calls[1][1].headers.Authorization).toBe(
        "Bearer private-token",
      );
      expect(
        connection.mock.calls.some(([state]) => state === "disconnected"),
      ).toBe(true);
    } finally {
      stop();
    }
  });
  it("parses CRLF, partial data, multi-line frames and heartbeats", () => {
    const result = parseSse(
      ': heartbeat\r\n\r\nid: 7\r\ndata: {"event_seq":\r\ndata: 7}\r\n\r\ndata: partial',
    );
    expect(result.frames).toEqual([
      { id: "", data: "" },
      { id: "7", data: '{"event_seq":\n7}' },
    ]);
    expect(result.remaining).toBe("data: partial");
  });
  it("sends bearer auth in headers and commands only to FastAPI", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);
    const c = new LiveClient(
      async () => "test-token",
      "https://api.example/api/v1",
    );
    await c.command("s-1", "control", {
      command_id: "c-1",
      base_state_version: 2,
      mode: "MANUAL",
    });
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.example/api/v1/sessions/s-1/control");
    expect(url).not.toContain("test-token");
    expect(options.headers.Authorization).toBe("Bearer test-token");
    expect(JSON.parse(options.body)).toMatchObject({
      command_id: "c-1",
      base_state_version: 2,
    });
  });
  it.each([401, 403, 409, 422, 503])(
    "surfaces %s without pretending the command succeeded",
    async (status) => {
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(
            new Response(JSON.stringify({ message: "denied" }), { status }),
          ),
      );
      const c = new LiveClient(async () => "test-token");
      await expect(c.snapshot("s-1")).rejects.toThrow(`${status} · denied`);
    },
  );
  it("does not send a request when authentication is unavailable", async () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    const c = new LiveClient(async () => {
      throw new Error("Sign in");
    });
    await expect(c.snapshot("s")).rejects.toThrow("Sign in");
    expect(f).not.toHaveBeenCalled();
  });
});
