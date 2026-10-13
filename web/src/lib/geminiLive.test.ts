// Tests for the Gemini live session: pure helpers plus stubbed integration
// tests for start ordering and close reporting. No network, no real audio.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildAudioFrame,
  classifyClose,
  decodePcm16Base64,
  NUDGE_TEXT,
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

const createdAudioContexts: FakeAudioContext[] = [];

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
  constructor() {
    createdAudioContexts.push(this);
  }
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

const createdWorklets: FakeAudioWorkletNode[] = [];

class FakeAudioWorkletNode {
  port: { onmessage: ((ev: MessageEvent<unknown>) => void) | null; postMessage: () => void } = {
    onmessage: null,
    postMessage: () => {},
  };
  connect = () => {};
  constructor() {
    createdWorklets.push(this);
  }
  /** Feed one frame of loud PCM16, i.e. the reader saying something. */
  speak() {
    const pcm = new Int16Array(320).fill(6_000);
    this.port.onmessage?.({ data: pcm.buffer } as MessageEvent<unknown>);
  }
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
    expect(frame.realtimeInput.audio.data).toBe(pcm16ToBase64(new Int16Array([100, -100])));
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
    const fromBuffer = parseServerMessages(new TextEncoder().encode(json).buffer);
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
    expect(parseServerMessages(JSON.stringify({ serverContent: { interrupted: true } }))).toEqual([
      { type: "interrupted" },
    ]);
    expect(parseServerMessages(JSON.stringify({ serverContent: { turnComplete: true } }))).toEqual([
      { type: "turnComplete" },
    ]);
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
    createdAudioContexts.length = 0;
    createdWorklets.length = 0;
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

  it("leaves the queue slack instead of starting a reply at the playhead", async () => {
    const { socket } = await startSession();
    const playback = createdAudioContexts[createdAudioContexts.length - 1];
    const chunk = pcm16ToBase64(new Int16Array([1]));
    const sendAudio = () =>
      socket.emit("message", {
        data: JSON.stringify({
          serverContent: {
            modelTurn: {
              parts: [{ inlineData: { mimeType: "audio/pcm;rate=24000", data: chunk } }],
            },
          },
        }),
      });

    sendAudio();
    // Mock buffers are 0.1s, so this chunk occupies 0.15 -> 0.25.
    expect(createdAudioSources[0].start).toHaveBeenCalledWith(0.15);

    // The next packet arrives late: the queue has already run dry.
    playback.currentTime = 0.3;
    sendAudio();

    // Restarting at the playhead is what made replies stutter -- the chunk
    // plays with zero slack, so the one after it lands in a gap too.
    expect(createdAudioSources[1].start).not.toHaveBeenCalledWith(0.3);
    expect(createdAudioSources[1].start).toHaveBeenCalledWith(0.44999999999999996);
  });

  it("keeps chunks gapless while the queue still has audio in it", async () => {
    const { socket } = await startSession();
    const playback = createdAudioContexts[createdAudioContexts.length - 1];
    const chunk = pcm16ToBase64(new Int16Array([1]));
    const sendAudio = () =>
      socket.emit("message", {
        data: JSON.stringify({
          serverContent: {
            modelTurn: {
              parts: [{ inlineData: { mimeType: "audio/pcm;rate=24000", data: chunk } }],
            },
          },
        }),
      });

    sendAudio();
    // Still mid-playback: the lead must not be re-added, or every chunk would
    // push the reply further behind and stretch it.
    playback.currentTime = 0.2;
    sendAudio();
    expect(createdAudioSources[1].start).toHaveBeenCalledWith(0.25);
  });

  it("captures the mic before opening the socket", async () => {
    await startSession();
    expect(h.order.indexOf("getUserMedia")).toBeGreaterThanOrEqual(0);
    expect(h.order.indexOf("ws")).toBeGreaterThan(h.order.indexOf("getUserMedia"));
  });

  it("rejects on denied permission without constructing a socket", async () => {
    getUserMedia.mockRejectedValueOnce(new Error("denied"));
    await expect(startGeminiLive({ onStateChange: () => {}, onError: () => {} })).rejects.toThrow(
      "denied",
    );
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
    expect(states).toEqual([
      { state: "connected" },
      {
        state: "closed",
        kind: "normal",
        code: 1000,
        reason: "client stop",
      },
    ]);
    expect(errors).toEqual([]);
  });

  it("stop() sends audioStreamEnd frame before closing when socket is OPEN", async () => {
    const { session, socket } = await startSession();
    expect(socket.readyState).toBe(FakeWebSocket.OPEN);
    session.stop();
    expect(socket.sent).toContain(JSON.stringify({ realtimeInput: { audioStreamEnd: true } }));
    expect(socket.readyState).toBe(FakeWebSocket.CLOSED);
  });

  it("stop() reports what the speakers got, so a silent call can be diagnosed", async () => {
    const { session, socket } = await startSession();
    session.stop();
    const report = socket.sent
      .map((frame) => JSON.parse(String(frame)) as Record<string, unknown>)
      .find((frame) => "omnigentStats" in frame);
    expect(report?.omnigentStats).toMatchObject({ chunks: 0, playedS: 0, interrupts: 0 });
  });

  it("stop() does not send audioStreamEnd when socket is not OPEN", async () => {
    const { session, socket } = await startSession();
    socket.readyState = FakeWebSocket.CLOSED;
    socket.sent.length = 0;
    session.stop();
    expect(socket.sent).not.toContain(JSON.stringify({ realtimeInput: { audioStreamEnd: true } }));
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
      // setupComplete is when the model starts hearing the mic, so it is
      // reported: the reader needs a cue for when to start talking.
      { state: "ready" },
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

  it("says it is still thinking instead of hanging up on a slow reply", async () => {
    vi.useFakeTimers();
    try {
      const states: unknown[] = [];
      const p = startGeminiLive({ onStateChange: (st) => states.push(st), onError: () => {} });
      await vi.advanceTimersByTimeAsync(0);
      const socket = h.sockets[h.sockets.length - 1];
      socket.open();
      await p;
      socket.emit("message", { data: JSON.stringify({ setupComplete: {} }) });
      createdWorklets[createdWorklets.length - 1].speak();

      // A real handoff needs ~5.5s for the tool round trip, and measured first
      // audio reached 10.6s. The old 7s deadline killed both.
      await vi.advanceTimersByTimeAsync(9_000);
      expect(states).toContainEqual(expect.objectContaining({ state: "waiting" }));
      expect(states).not.toContainEqual(expect.objectContaining({ state: "closed" }));
      expect(socket.readyState).not.toBe(3);

      // Only a silence far past anything observed working ends the call.
      await vi.advanceTimersByTimeAsync(22_000);
      expect(states).toContainEqual(
        expect.objectContaining({ state: "closed", reason: "gemini stopped responding" }),
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it("says when Gemini is not hearing the reader, and clears once it does", async () => {
    vi.useFakeTimers();
    try {
      const states: unknown[] = [];
      const p = startGeminiLive({ onStateChange: (st) => states.push(st), onError: () => {} });
      await vi.advanceTimersByTimeAsync(0);
      const socket = h.sockets[h.sockets.length - 1];
      socket.open();
      await p;
      socket.emit("message", { data: JSON.stringify({ setupComplete: {} }) });

      // Talking, with nothing transcribed back: they are talking to themselves.
      const worklet = createdWorklets[createdWorklets.length - 1];
      worklet.speak();
      await vi.advanceTimersByTimeAsync(6_000);
      expect(states).toContainEqual({ state: "not-hearing" });

      socket.emit("message", {
        data: JSON.stringify({ serverContent: { inputTranscription: { text: "hello" } } }),
      });
      expect(states[states.length - 1]).toEqual({ state: "clear" });
    } finally {
      vi.useRealTimers();
    }
  });

  it("flags a reply that runs dry mid-sentence, but not the gap between replies", async () => {
    const { socket, states } = await startSession();
    const playback = createdAudioContexts[createdAudioContexts.length - 1];
    const chunk = pcm16ToBase64(new Int16Array([1]));
    const sendAudio = () =>
      socket.emit("message", {
        data: JSON.stringify({
          serverContent: {
            modelTurn: {
              parts: [{ inlineData: { mimeType: "audio/pcm;rate=24000", data: chunk } }],
            },
          },
        }),
      });

    sendAudio(); // plays 0.15 -> 0.25
    socket.emit("message", { data: JSON.stringify({ serverContent: { turnComplete: true } }) });
    // The next reply starts long after: an empty queue between replies is normal.
    playback.currentTime = 5;
    sendAudio();
    expect(states).not.toContainEqual({ state: "lagging" });

    // Mid-reply, the next chunk lands after the queue already ran out: a freeze.
    playback.currentTime = 6;
    sendAudio();
    expect(states).toContainEqual({ state: "lagging" });

    socket.emit("message", { data: JSON.stringify({ serverContent: { turnComplete: true } }) });
    expect(states[states.length - 1]).toEqual({ state: "clear" });
  });

  it("clears 'still thinking' once the answer arrives", async () => {
    vi.useFakeTimers();
    try {
      const states: unknown[] = [];
      const p = startGeminiLive({ onStateChange: (st) => states.push(st), onError: () => {} });
      await vi.advanceTimersByTimeAsync(0);
      const socket = h.sockets[h.sockets.length - 1];
      socket.open();
      await p;
      socket.emit("message", { data: JSON.stringify({ setupComplete: {} }) });
      socket.emit("message", {
        data: JSON.stringify({ serverContent: { inputTranscription: { text: "hi" } } }),
      });
      createdWorklets[createdWorklets.length - 1].speak();
      await vi.advanceTimersByTimeAsync(9_000);
      expect(states).toContainEqual(expect.objectContaining({ state: "waiting" }));

      socket.emit("message", { data: JSON.stringify({ serverContent: { turnComplete: true } }) });
      expect(states[states.length - 1]).toEqual({ state: "clear" });
    } finally {
      vi.useRealTimers();
    }
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
  describe("a session that hears the reader but never answers", () => {
    async function live() {
      vi.useFakeTimers();
      const states: { state: string; reason?: string }[] = [];
      const p = startGeminiLive({
        onStateChange: (st) => states.push(st as { state: string; reason?: string }),
        onError: () => {},
      });
      await vi.advanceTimersByTimeAsync(0);
      const socket = h.sockets[h.sockets.length - 1];
      socket.open();
      const session = await p;
      socket.emit("message", { data: JSON.stringify({ setupComplete: {} }) });
      const worklet = createdWorklets[createdWorklets.length - 1];
      const nudges = () =>
        socket.sent.filter((f) => typeof f === "string" && f.includes(NUDGE_TEXT)).length;
      const say = async (ms: number) => {
        for (let t = 0; t < ms; t += 100) {
          worklet.speak();
          await vi.advanceTimersByTimeAsync(100);
        }
      };
      return { socket, session, states, nudges, say };
    }
    afterEach(() => vi.useRealTimers());

    it("keeps counting when they ask again, so the stall is still caught", async () => {
      const { states, say } = await live();
      // The 13:45 call: a question, then "are you hearing me?" twice, ~8 s apart.
      await say(3_000);
      await vi.advanceTimersByTimeAsync(7_000);
      await say(1_500);
      await vi.advanceTimersByTimeAsync(11_000);
      await say(1_500);
      // Asking again holds the clock while they ask; it does not restart it.
      await vi.advanceTimersByTimeAsync(9_000);
      expect(states).not.toContainEqual(expect.objectContaining({ state: "closed" }));
      await vi.advanceTimersByTimeAsync(6_000);
      expect(states).toContainEqual(
        expect.objectContaining({ state: "closed", reason: "gemini stopped responding" }),
      );
    });

    const activity = (type: "ACTIVITY_START" | "ACTIVITY_END") =>
      JSON.stringify({ voiceActivity: { type, audioOffset: "1.0s" } });

    it("never hangs up while Gemini says they are still talking", async () => {
      const { socket, states, nudges, say } = await live();
      // The 18:18 call: a few words, a pause, then a long turn that Gemini's
      // detector kept open (start, no end) while the page hung up on it.
      await say(1_000);
      await vi.advanceTimersByTimeAsync(2_500);
      socket.emit("message", { data: activity("ACTIVITY_START") });
      for (let i = 0; i < 8; i++) {
        await say(4_000);
        // Thinking pauses longer than the page's own quiet mark.
        await vi.advanceTimersByTimeAsync(2_000);
      }
      expect(states).not.toContainEqual(expect.objectContaining({ state: "closed" }));
      expect(states).not.toContainEqual(expect.objectContaining({ state: "waiting" }));
      expect(states).not.toContainEqual({ state: "not-hearing" });
      expect(nudges()).toBe(0);
      // Once Gemini says they stopped, the reply is owed again.
      socket.emit("message", { data: activity("ACTIVITY_END") });
      await vi.advanceTimersByTimeAsync(11_000);
      expect(nudges()).toBe(1);
      await vi.advanceTimersByTimeAsync(20_000);
      expect(states).toContainEqual(
        expect.objectContaining({ state: "closed", reason: "gemini stopped responding" }),
      );
    });

    it("never hangs up mid-sentence, even without Gemini's detection", async () => {
      const { states, say } = await live();
      await say(1_000);
      await vi.advanceTimersByTimeAsync(2_500);
      await say(40_000);
      expect(states).not.toContainEqual(expect.objectContaining({ state: "closed" }));
    });

    it("nudges a quiet model to answer, at most twice, never over the reader", async () => {
      const { states, nudges, say } = await live();
      await say(2_000);
      await vi.advanceTimersByTimeAsync(8_000);
      expect(nudges()).toBe(0);
      await vi.advanceTimersByTimeAsync(2_500);
      expect(nudges()).toBe(1);
      expect(states).toContainEqual(expect.objectContaining({ state: "nudged" }));
      // Talking again does not get talked over.
      await say(10_000);
      expect(nudges()).toBe(1);
      await vi.advanceTimersByTimeAsync(5_000);
      expect(nudges()).toBe(2);
      await vi.advanceTimersByTimeAsync(5_000);
      expect(nudges()).toBe(2);
    });

    it("does not take its own voice leaking back for a question", async () => {
      const { socket, nudges, say } = await live();
      socket.emit("message", {
        data: JSON.stringify({
          serverContent: {
            modelTurn: {
              parts: [{ inlineData: { mimeType: "audio/pcm;rate=24000", data: "AAAA" } }],
            },
          },
        }),
      });
      socket.emit("message", { data: JSON.stringify({ serverContent: { turnComplete: true } }) });
      // Still playing: the speakers' echo reads as speech on the mic.
      await say(2_000);
      await vi.advanceTimersByTimeAsync(40_000);
      expect(nudges()).toBe(0);
    });

    it("an answer clears what is owed", async () => {
      const { socket, states, nudges, say } = await live();
      await say(2_000);
      await vi.advanceTimersByTimeAsync(3_000);
      socket.emit("message", { data: JSON.stringify({ serverContent: { turnComplete: true } }) });
      await vi.advanceTimersByTimeAsync(40_000);
      expect(nudges()).toBe(0);
      expect(states).not.toContainEqual(expect.objectContaining({ state: "closed" }));
    });

    it("waits out a tool call on a model that is deaf until it is answered", async () => {
      const { socket, session, states, nudges, say } = await live();
      await say(2_000);
      socket.emit("message", {
        data: JSON.stringify({
          toolCall: { functionCalls: [{ id: "c1", name: "check_machine" }] },
        }),
      });
      await say(1_000);
      await vi.advanceTimersByTimeAsync(40_000);
      expect(nudges()).toBe(0);
      expect(states).not.toContainEqual(expect.objectContaining({ state: "closed" }));
      // Answered, it owes the reader the result.
      session.sendToolResponse([{ id: "c1", name: "check_machine", response: { output: "ok" } }]);
      await vi.advanceTimersByTimeAsync(11_000);
      expect(nudges()).toBe(1);
    });

    it("counts again once Gemini drops a call they talked over", async () => {
      const { socket, nudges, say } = await live();
      socket.emit("message", {
        data: JSON.stringify({
          toolCall: { functionCalls: [{ id: "c1", name: "check_machine" }] },
        }),
      });
      socket.emit("message", { data: JSON.stringify({ toolCallCancellation: { ids: ["c1"] } }) });
      await say(1_500);
      await vi.advanceTimersByTimeAsync(11_000);
      expect(nudges()).toBe(1);
    });

    it("on a model that talks during tools, watches it and schedules results", async () => {
      const { socket, session, nudges, say } = await live();
      socket.emit("message", {
        data: JSON.stringify({ omnigentSession: { model: "gemini-3.8-live", toolsBlock: false } }),
      });
      socket.emit("message", {
        data: JSON.stringify({
          toolCall: { functionCalls: [{ id: "c1", name: "check_machine" }] },
        }),
      });
      // "Are you hearing me?" mid-check is owed an answer now, not after the check.
      await say(1_500);
      await vi.advanceTimersByTimeAsync(11_000);
      expect(nudges()).toBe(1);
      session.sendToolResponse([{ id: "c1", name: "check_machine", response: { output: "ok" } }]);
      const sent = socket.sent.filter((f) => typeof f === "string" && f.includes("toolResponse"));
      expect(JSON.parse(sent[sent.length - 1] as string).toolResponse.functionResponses[0]).toEqual(
        {
          id: "c1",
          name: "check_machine",
          response: { scheduling: "WHEN_IDLE", output: "ok" },
        },
      );
    });
  });

  it("parses Google's voice detection frames", () => {
    expect(
      parseServerMessages(
        JSON.stringify({ voiceActivity: { type: "ACTIVITY_START", audioOffset: "2.120s" } }),
      ),
    ).toEqual([{ type: "voiceActivity", active: true }]);
    expect(
      parseServerMessages(JSON.stringify({ voiceActivity: { type: "ACTIVITY_END" } })),
    ).toEqual([{ type: "voiceActivity", active: false }]);
    expect(parseServerMessages(JSON.stringify({ voiceActivity: { type: "OTHER" } }))).toEqual([]);
  });

  it("parses the relay's tool-done frame, and it clears the pending call", async () => {
    expect(
      parseServerMessages(
        JSON.stringify({
          omnigentToolDone: { id: "t1", name: "claude_status", project: "", output: "idle" },
        }),
      ),
    ).toEqual([
      {
        type: "toolDone",
        id: "t1",
        name: "claude_status",
        project: "",
        output: "idle",
        sent: false,
      },
    ]);
  });

  it("does not count the relay's own hello as Gemini answering", async () => {
    const { socket, states } = await startSession();
    socket.emit("message", {
      data: JSON.stringify({ omnigentSession: { model: "m", toolsBlock: true } }),
    });
    socket.emit("close", { code: 1000, reason: "" });
    expect(states).toContainEqual(
      expect.objectContaining({ state: "closed", kind: "error", reason: "gemini never answered" }),
    );
  });
});
