import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { frameForReading, narrateViaGeminiLive } from "./geminiNarrator";
import { LiveVoiceUnavailable } from "./liveVoice";

const h = vi.hoisted(() => ({
  sockets: [] as FakeWebSocket[],
}));

vi.mock("@/lib/host", () => ({
  resolveWebSocketUrl: (p: string) => `ws://stub${p}`,
}));

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
  disconnect: ReturnType<typeof vi.fn>;
  start: ReturnType<typeof vi.fn>;
  onended: (() => void) | null;
}[] = [];

class FakeMediaStream {
  getTracks() {
    return [];
  }
}

class FakeAudioContext {
  state = "running";
  currentTime = 0;
  destination = {};
  resume = vi.fn(async () => {});
  close = vi.fn(async () => {
    this.state = "closed";
  });
  createMediaStreamDestination = () => ({
    stream: new FakeMediaStream() as unknown as MediaStream,
  });
  createBuffer = () => ({ duration: 0.1, copyToChannel: () => {} });
  createBufferSource = () => {
    const src = {
      buffer: null,
      connect: vi.fn(),
      disconnect: vi.fn(),
      start: vi.fn(),
      stop: vi.fn(),
      onended: null as (() => void) | null,
    };
    createdAudioSources.push(src);
    return src;
  };
}

async function flush() {
  await new Promise<void>((resolve) => {
    setTimeout(resolve, 0);
  });
}

describe("geminiNarrator", () => {
  beforeEach(() => {
    h.sockets.length = 0;
    createdAudioSources.length = 0;
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.stubGlobal("AudioContext", FakeAudioContext);
    vi.stubGlobal("MediaStream", FakeMediaStream);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  describe("frameForReading", () => {
    it("wraps text in the read-aloud framing instruction", () => {
      const framed = frameForReading("A migração terminou com sucesso.");
      expect(framed).toBe(
        "Read the following status update aloud to the listener, in your own " +
          "speaking voice, keeping every fact and adding nothing. Do not reply to " +
          "it, do not advise, do not continue it. Just say it:\n\n" +
          "A migração terminou com sucesso.",
      );
    });
  });

  describe("narrateViaGeminiLive", () => {
    it("throws LiveVoiceUnavailable on empty text", async () => {
      await expect(narrateViaGeminiLive("")).rejects.toBeInstanceOf(LiveVoiceUnavailable);
      await expect(narrateViaGeminiLive("   ")).rejects.toBeInstanceOf(LiveVoiceUnavailable);
    });

    it("connects to /v1/live/gemini/ws?mode=narrate and sends single clientContent turn", async () => {
      const p = narrateViaGeminiLive("Relatório pronto.");
      await flush();
      const socket = h.sockets[h.sockets.length - 1];
      expect(socket.url).toBe("ws://stub/v1/live/gemini/ws?mode=narrate");
      expect(socket.binaryType).toBe("arraybuffer");

      socket.open();
      const live = await p;

      expect(live.stream).toBeDefined();
      expect(typeof live.stop).toBe("function");
      expect(live.finished).toBeInstanceOf(Promise);

      expect(socket.sent).toHaveLength(1);
      const turn = JSON.parse(String(socket.sent[0]));
      expect(turn).toEqual({
        clientContent: {
          turns: [
            {
              role: "user",
              parts: [{ text: frameForReading("Relatório pronto.") }],
            },
          ],
          turnComplete: true,
        },
      });
    });

    it("finished promise resolves after playback drains, not on generation end", async () => {
      const p = narrateViaGeminiLive("Texto para falar.");
      await flush();
      const socket = h.sockets[h.sockets.length - 1];
      socket.open();
      const live = await p;

      let finishedResolved = false;
      void live.finished.then(() => {
        finishedResolved = true;
      });

      // Send audio chunk then turnComplete
      const pcmB64 = btoa("\x00\x01\x00\x02");
      socket.emit("message", {
        data: JSON.stringify({
          serverContent: {
            modelTurn: {
              parts: [
                {
                  inlineData: {
                    mimeType: "audio/pcm;rate=24000",
                    data: pcmB64,
                  },
                },
              ],
            },
            turnComplete: true,
          },
        }),
      });

      await flush();
      expect(createdAudioSources).toHaveLength(1);
      expect(createdAudioSources[0].start).toHaveBeenCalled();
      // turnComplete arrived, but audio is still playing!
      expect(finishedResolved).toBe(false);

      // Playback drains (onended)
      createdAudioSources[0].onended?.();
      await flush();
      expect(finishedResolved).toBe(true);
    });

    it("sends keepalive pings every 20s and stops on close", async () => {
      vi.useFakeTimers();
      try {
        const p = narrateViaGeminiLive("Texto para falar.");
        await vi.advanceTimersByTimeAsync(0);
        const socket = h.sockets[h.sockets.length - 1];
        socket.open();
        const live = await p;

        expect(socket.sent).not.toContain(JSON.stringify({ omnigentPing: true }));
        await vi.advanceTimersByTimeAsync(20_000);
        expect(socket.sent).toContain(JSON.stringify({ omnigentPing: true }));

        const pings1 = socket.sent.filter(
          (s) => typeof s === "string" && s.includes("omnigentPing"),
        ).length;
        expect(pings1).toBe(1);

        // A live narration keeps answering; silence is what ends a session.
        socket.emit("message", { data: JSON.stringify({ serverContent: {} }) });
        await vi.advanceTimersByTimeAsync(20_000);
        const pings2 = socket.sent.filter(
          (s) => typeof s === "string" && s.includes("omnigentPing"),
        ).length;
        expect(pings2).toBe(2);

        live.stop();
        await vi.advanceTimersByTimeAsync(40_000);
        const pingsEnd = socket.sent.filter(
          (s) => typeof s === "string" && s.includes("omnigentPing"),
        ).length;
        expect(pingsEnd).toBe(2);
      } finally {
        vi.useRealTimers();
      }
    });

    it("stop() closes socket, stops scheduled audio, and prevents subsequent audio scheduling", async () => {
      const p = narrateViaGeminiLive("Texto.");
      await flush();
      const socket = h.sockets[h.sockets.length - 1];
      socket.open();
      const live = await p;

      // Play one chunk
      const pcmB64 = btoa("\x00\x01\x00\x02");
      socket.emit("message", {
        data: JSON.stringify({
          serverContent: {
            modelTurn: {
              parts: [
                {
                  inlineData: {
                    mimeType: "audio/pcm;rate=24000",
                    data: pcmB64,
                  },
                },
              ],
            },
          },
        }),
      });

      expect(createdAudioSources).toHaveLength(1);
      const src1 = createdAudioSources[0];

      // Call stop()
      live.stop();
      expect(src1.stop).toHaveBeenCalled();
      expect(src1.disconnect).toHaveBeenCalled();
      expect(socket.readyState).toBe(FakeWebSocket.CLOSED);

      // Sending another chunk after stop() must NOT schedule anything
      socket.emit("message", {
        data: JSON.stringify({
          serverContent: {
            modelTurn: {
              parts: [
                {
                  inlineData: {
                    mimeType: "audio/pcm;rate=24000",
                    data: pcmB64,
                  },
                },
              ],
            },
          },
        }),
      });

      expect(createdAudioSources).toHaveLength(1);
    });

    it("hangs up when Google accepts the turn and never speaks", async () => {
      vi.useFakeTimers();
      try {
        const p = narrateViaGeminiLive("Texto para falar.");
        await vi.advanceTimersByTimeAsync(0);
        const socket = h.sockets[h.sockets.length - 1];
        socket.open();
        const live = await p;
        let finishedResolved = false;
        void live.finished.then(() => {
          finishedResolved = true;
        });

        await vi.advanceTimersByTimeAsync(19_000);
        expect(finishedResolved).toBe(false);
        expect(socket.readyState).not.toBe(3);

        await vi.advanceTimersByTimeAsync(3_000);
        expect(finishedResolved).toBe(true);
        expect(socket.readyState).toBe(3);
      } finally {
        vi.useRealTimers();
      }
    });

    it("rejects when socket closes early before open", async () => {
      const p = narrateViaGeminiLive("Texto.");
      await flush();
      const socket = h.sockets[h.sockets.length - 1];
      socket.emit("close", { code: 1011, reason: "upstream failed" });

      await expect(p).rejects.toBeInstanceOf(LiveVoiceUnavailable);
    });
  });
});
