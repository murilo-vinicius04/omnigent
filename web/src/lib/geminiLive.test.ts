// Tests for the Gemini live session: pure helpers plus stubbed integration
// tests for start ordering and close reporting. No network, no real audio.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildAudioFrame,
  classifyClose,
  decodePcm16Base64,
  parseServerMessages,
  pcm16ToBase64,
  startGeminiLive,
  toPcm16,
} from "@/lib/geminiLive";

const h = vi.hoisted(() => ({
  order: [] as string[],
  sockets: [] as FakeWebSocket[],
}));

vi.mock("@/lib/dictation", () => ({ workletUrl: () => "blob:worklet" }));
vi.mock("@/lib/host", () => ({
  resolveWebSocketUrl: (p: string) => `ws://stub${p}`,
}));

/** Minimal hand-drivable WebSocket stub. */
class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  readyState = 0;
  binaryType = "";
  sent: unknown[] = [];
  handlers: Record<string, ((ev: unknown) => void)[]> = {};
  url: string;
  constructor(url: string) {
    this.url = url;
    h.order.push("ws");
    h.sockets.push(this);
  }
  addEventListener(type: string, fn: (ev: unknown) => void) {
    (this.handlers[type] ??= []).push(fn);
  }
  removeEventListener(type: string, fn: (ev: unknown) => void) {
    this.handlers[type] = (this.handlers[type] ?? []).filter((f) => f !== fn);
  }
  send(data: unknown) {
    this.sent.push(data);
  }
  close(code = 1000, reason = "") {
    this.readyState = 3;
    this.emit("close", { code, reason });
  }
  emit(type: string, ev: unknown) {
    for (const fn of [...(this.handlers[type] ?? [])]) fn(ev);
  }
  open() {
    this.readyState = 1;
    this.emit("open", {});
  }
}

const createdAudioSources: {
  stop: ReturnType<typeof vi.fn>;
  start: ReturnType<typeof vi.fn>;
  onended: (() => void) | null;
}[] = [];

class FakeAudioContext {
  state = "running";
  currentTime = 0;
  destination = {};
  audioWorklet = { addModule: async () => {} };
  resume = vi.fn(async () => {});
  close = async () => {
    this.state = "closed";
  };
  createMediaStreamSource = () => ({ connect: () => {} });
  createGain = () => ({ gain: { value: 1 }, connect: () => {} });
  createBuffer = () => ({ duration: 0.1, copyToChannel: () => {} });
  createBufferSource = () => {
    const src = {
      buffer: null,
      connect: () => {},
      start: vi.fn(),
      stop: vi.fn(),
      onended: null,
    };
    createdAudioSources.push(src);
    return src;
  };
}

class FakeAudioWorkletNode {
  port = { onmessage: null as null, postMessage: () => {} };
  connect = () => {};
}

const getUserMedia = vi.fn(async () => {
  h.order.push("getUserMedia");
  return { getTracks: () => [] };
});

/** Let pending microtasks settle without awaiting a real timer. */
async function flush() {
  await new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

describe("pcm16ToBase64 / buildAudioFrame", () => {
  it("round-trips a known Int16Array", () => {
    const pcm = new Int16Array([0, 1, -1, 32767, -32768, 12345, -12345]);
    const b64 = pcm16ToBase64(pcm);
    const floats = decodePcm16Base64(b64);
    expect(floats.length).toBe(pcm.length);
    for (let i = 0; i < pcm.length; i++) {
      // exact round-trip: int16 -> float -> int16 truncation is identity
      expect(Math.round(floats[i] * 0x8000)).toBe(pcm[i]);
    }
  });

  it("builds a realtimeInput frame with 16 kHz pcm mimeType", () => {
    const frame = buildAudioFrame(new Int16Array([100, -100])) as Record<
      string,
      { audio: { mimeType: string; data: string } }
    >;
    expect(frame.realtimeInput.audio.mimeType).toBe("audio/pcm;rate=16000");
    expect(typeof frame.realtimeInput.audio.data).toBe("string");
    expect(frame.realtimeInput.audio.data).toBe(
      pcm16ToBase64(new Int16Array([100, -100])),
    );
  });
});

describe("decodePcm16Base64", () => {
  it("maps 32767 to ~1 and -32768 to -1", () => {
    const b64 = pcm16ToBase64(new Int16Array([32767, -32768]));
    const floats = decodePcm16Base64(b64);
    expect(floats[0]).toBeCloseTo(1, 4);
    expect(floats[0]).toBeLessThan(1); // strictly below 1
    expect(floats[1]).toBe(-1);
  });
});

describe("toPcm16", () => {
  it("passes an Int16Array through unchanged", () => {
    const pcm = new Int16Array([1, 2, 3]);
    expect(toPcm16(pcm)).toBe(pcm);
  });
  it("wraps an ArrayBuffer", () => {
    const pcm = new Int16Array([4, -4]);
    const buf = pcm.buffer.slice(0);
    const out = toPcm16(buf);
    expect(out).toBeInstanceOf(Int16Array);
    expect(Array.from(out ?? [])).toEqual([4, -4]);
  });
  it("returns null for anything else, including null", () => {
    expect(toPcm16(null)).toBeNull();
    expect(toPcm16("nope")).toBeNull();
    expect(toPcm16(42)).toBeNull();
  });
});

describe("parseServerMessages", () => {
  it("parses the same JSON from a string and an ArrayBuffer", () => {
    const json = JSON.stringify({
      serverContent: {
        modelTurn: {
          parts: [
            {
              inlineData: {
                mimeType: "audio/pcm;rate=24000",
                data: pcm16ToBase64(new Int16Array([7, -7])),
              },
            },
          ],
        },
      },
    });
    const fromString = parseServerMessages(json);
    const fromBuffer = parseServerMessages(
      new TextEncoder().encode(json).buffer,
    );
    expect(fromBuffer).toEqual(fromString);
    expect(fromString).toEqual([
      {
        type: "audio",
        mimeType: "audio/pcm;rate=24000",
        data: pcm16ToBase64(new Int16Array([7, -7])),
      },
    ]);
  });

  it("parses input and output transcripts", () => {
    expect(
      parseServerMessages(
        JSON.stringify({
          serverContent: { inputTranscription: { text: "hello" } },
        }),
      ),
    ).toEqual([{ type: "inputTranscript", text: "hello" }]);
    expect(
      parseServerMessages(
        JSON.stringify({
          serverContent: { outputTranscription: { text: "hi there" } },
        }),
      ),
    ).toEqual([{ type: "outputTranscript", text: "hi there" }]);
  });

  it("parses interrupted, turnComplete, setupComplete; garbage is []", () => {
    expect(
      parseServerMessages(
        JSON.stringify({ serverContent: { interrupted: true } }),
      ),
    ).toEqual([{ type: "interrupted" }]);
    expect(
      parseServerMessages(JSON.stringify({ serverContent: { turnComplete: true } })),
    ).toEqual([{ type: "turnComplete" }]);
    expect(parseServerMessages(JSON.stringify({ setupComplete: {} }))).toEqual([
      { type: "setupComplete" },
    ]);
    expect(
      parseServerMessages(
        JSON.stringify({
          toolCall: {
            functionCalls: [
              {
                id: "call_abc",
                name: "ask_claude",
                args: { question: "how does this work?" },
              },
            ],
          },
        }),
      ),
    ).toEqual([
      {
        type: "toolCall",
        calls: [
          {
            id: "call_abc",
            name: "ask_claude",
            args: { question: "how does this work?" },
          },
        ],
      },
    ]);
    expect(
      parseServerMessages(
        JSON.stringify({
          toolCall: {
            functionCalls: [{ invalid: "shape" }],
          },
        }),
      ),
    ).toEqual([]);
    expect(parseServerMessages("not json")).toEqual([]);
  });

  it("keeps every event in one multi-part message, in order", () => {
    const a1 = pcm16ToBase64(new Int16Array([1]));
    const a2 = pcm16ToBase64(new Int16Array([2]));
    const events = parseServerMessages(
      JSON.stringify({
        serverContent: {
          interrupted: true,
          modelTurn: {
            parts: [
              { inlineData: { mimeType: "audio/pcm;rate=24000", data: a1 } },
              { text: "ignored" },
              { inlineData: { mimeType: "audio/pcm;rate=24000", data: a2 } },
            ],
          },
          outputTranscription: { text: "hello there" },
          turnComplete: true,
        },
      }),
    );
    expect(events).toEqual([
      { type: "interrupted" },
      { type: "audio", mimeType: "audio/pcm;rate=24000", data: a1 },
      { type: "audio", mimeType: "audio/pcm;rate=24000", data: a2 },
      { type: "outputTranscript", text: "hello there" },
      { type: "turnComplete" },
    ]);
  });
});

describe("classifyClose", () => {
  it("maps 1000/1008/1011", () => {
    expect(classifyClose(1000)).toBe("normal");
    expect(classifyClose(1008)).toBe("policy");
    expect(classifyClose(1011)).toBe("error");
  });
});

describe("startGeminiLive (stubbed)", () => {
  beforeEach(() => {
    createdAudioSources.length = 0;
    h.order.length = 0;
    h.sockets.length = 0;
    getUserMedia.mockClear();
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.stubGlobal("AudioContext", FakeAudioContext);
    vi.stubGlobal("AudioWorkletNode", FakeAudioWorkletNode);
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  async function startSession() {
    const states: unknown[] = [];
    const errors: Error[] = [];
    const p = startGeminiLive({
      onStateChange: (s) => states.push(s),
      onError: (e) => errors.push(e),
    });
    await flush();
    const socket = h.sockets[h.sockets.length - 1];
    socket.open();
    const session = await p;
    return { session, socket, states, errors };
  }

  it("captures the mic before opening the socket", async () => {
    await startSession();
    expect(h.order.indexOf("getUserMedia")).toBeGreaterThanOrEqual(0);
    expect(h.order.indexOf("ws")).toBeGreaterThan(h.order.indexOf("getUserMedia"));
  });

  it("rejects on denied permission without constructing a socket", async () => {
    getUserMedia.mockRejectedValueOnce(new Error("denied"));
    await expect(
      startGeminiLive({ onStateChange: () => {}, onError: () => {} }),
    ).rejects.toThrow("denied");
    await flush();
    expect(h.sockets).toHaveLength(0);
  });

  it("reports a 1008 close once as policy, with no onError", async () => {
    const { socket, states, errors } = await startSession();
    socket.emit("close", { code: 1008, reason: "session cap" });
    expect(states).toEqual([
      { state: "connected" },
      { state: "closed", kind: "policy", code: 1008, reason: "session cap" },
    ]);
    expect(errors).toEqual([]);
  });

  it("stop() plus the socket close event yields exactly one closed state", async () => {
    const { session, socket, states, errors } = await startSession();
    session.stop();
    socket.emit("close", { code: 1000, reason: "client stop" });
    expect(states).toEqual([{ state: "connected" }, {
      state: "closed",
      kind: "normal",
      code: 1000,
      reason: "client stop",
    }]);
    expect(errors).toEqual([]);
  });

  it("stop() sends audioStreamEnd frame before closing when socket is OPEN", async () => {
    const { session, socket } = await startSession();
    expect(socket.readyState).toBe(FakeWebSocket.OPEN);
    session.stop();
    expect(socket.sent).toContain(
      JSON.stringify({ realtimeInput: { audioStreamEnd: true } }),
    );
    expect(socket.readyState).toBe(FakeWebSocket.CLOSED);
  });

  it("stop() does not send audioStreamEnd when socket is not OPEN", async () => {
    const { session, socket } = await startSession();
    socket.readyState = FakeWebSocket.CLOSED;
    socket.sent.length = 0;
    session.stop();
    expect(socket.sent).not.toContain(
      JSON.stringify({ realtimeInput: { audioStreamEnd: true } }),
    );
  });

  it("reports a 1000 close with zero received messages as error (gemini never answered)", async () => {
    const { socket, states, errors } = await startSession();
    socket.emit("close", { code: 1000, reason: "" });
    expect(states).toEqual([
      { state: "connected" },
      {
        state: "closed",
        kind: "error",
        code: 1000,
        reason: "gemini never answered",
      },
    ]);
    expect(errors).toHaveLength(1);
    expect(errors[0]?.message).toBe("gemini never answered");
  });

  it("reports a 1000 close after at least one message as normal", async () => {
    const { socket, states, errors } = await startSession();
    socket.emit("message", { data: JSON.stringify({ setupComplete: {} }) });
    socket.emit("close", { code: 1000, reason: "upstream finished" });
    expect(states).toEqual([
      { state: "connected" },
      {
        state: "closed",
        kind: "normal",
        code: 1000,
        reason: "upstream finished",
      },
    ]);
    expect(errors).toEqual([]);
  });

  it("appends encodeURIComponent(sessionId) to WebSocket URL when sessionId is provided", async () => {
    const p = startGeminiLive({ sessionId: "conv 123/xyz" });
    await flush();
    const socket = h.sockets[h.sockets.length - 1];
    expect(socket?.url).toBe("ws://stub/v1/live/gemini/ws?session_id=conv%20123%2Fxyz");
    socket?.open();
    const session = await p;
    session.stop();
  });

  it("opens plain /v1/live/gemini/ws when sessionId is omitted", async () => {
    const p = startGeminiLive();
    await flush();
    const socket = h.sockets[h.sockets.length - 1];
    expect(socket?.url).toBe("ws://stub/v1/live/gemini/ws");
    socket?.open();
    const session = await p;
    session.stop();
  });

  it("sendToolResponse sends documented frame shape as TEXT when socket is OPEN", async () => {
    const { session, socket } = await startSession();
    expect(socket.readyState).toBe(FakeWebSocket.OPEN);
    session.sendToolResponse([
      {
        id: "call_123",
        name: "ask_claude",
        response: { output: "The answer is 42" },
      },
    ]);
    expect(socket.sent).toContain(
      JSON.stringify({
        toolResponse: {
          functionResponses: [
            {
              response: { output: "The answer is 42" },
              id: "call_123",
              name: "ask_claude",
            },
          ],
        },
      }),
    );
  });

  it("sendToolResponse is a no-op when socket is not OPEN", async () => {
    const { session, socket } = await startSession();
    socket.readyState = FakeWebSocket.CLOSED;
    socket.sent.length = 0;
    session.sendToolResponse([
      {
        id: "call_123",
        name: "ask_claude",
        response: { output: "no-op" },
      },
    ]);
    expect(socket.sent).toHaveLength(0);
  });

  it("untilPlaybackDrained resolves immediately when no audio is scheduled", async () => {
    const { session } = await startSession();
    let drained = false;
    void session.untilPlaybackDrained().then(() => {
      drained = true;
    });
    await flush();
    expect(drained).toBe(true);
  });

  it("untilPlaybackDrained waits for audio playback to finish before resolving", async () => {
    const { session, socket } = await startSession();
    const pcm = pcm16ToBase64(new Int16Array([100, 200]));
    socket.emit("message", {
      data: JSON.stringify({
        serverContent: {
          modelTurn: {
            parts: [{ inlineData: { mimeType: "audio/pcm;rate=24000", data: pcm } }],
          },
        },
      }),
    });

    expect(createdAudioSources).toHaveLength(1);
    let drained = false;
    void session.untilPlaybackDrained().then(() => {
      drained = true;
    });

    await flush();
    expect(drained).toBe(false);

    // Audio finishes playing
    createdAudioSources[0].onended?.();
    await flush();
    expect(drained).toBe(true);
  });

  it("sends keepalive pings every 20s and stops on close", async () => {
    vi.useFakeTimers();
    try {
      const p = startGeminiLive();
      await vi.advanceTimersByTimeAsync(0);
      const socket = h.sockets[h.sockets.length - 1];
      socket.open();
      const session = await p;

      expect(socket.sent).not.toContain(JSON.stringify({ omnigentPing: true }));
      await vi.advanceTimersByTimeAsync(20_000);
      expect(socket.sent).toContain(JSON.stringify({ omnigentPing: true }));

      const pings1 = socket.sent.filter(
        (s) => typeof s === "string" && s.includes("omnigentPing"),
      ).length;
      expect(pings1).toBe(1);

      await vi.advanceTimersByTimeAsync(20_000);
      const pings2 = socket.sent.filter(
        (s) => typeof s === "string" && s.includes("omnigentPing"),
      ).length;
      expect(pings2).toBe(2);

      session.stop();
      await vi.advanceTimersByTimeAsync(40_000);
      const pingsEnd = socket.sent.filter(
        (s) => typeof s === "string" && s.includes("omnigentPing"),
      ).length;
      expect(pingsEnd).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });
});

