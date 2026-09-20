/**
 * Spoken summary narrator powered by Gemini Live WebSocket.
 *
 * Connects to /v1/live/gemini/ws?mode=narrate, sends a single clientContent turn
 * with framed text, decodes returned 24 kHz PCM audio into an AudioContext
 * MediaStreamDestination, and returns a LiveNarration { stream, finished, stop }.
 */

import { resolveWebSocketUrl } from "./host";
import { decodePcm16Base64, parseServerMessages } from "./geminiLive";
import { LiveVoiceUnavailable, type LiveNarration } from "./liveVoice";

// Mirrors geminiLive.ts: never start a chunk at the playhead itself.
const JITTER_LEAD_S = 0.15;
const PLAYBACK_RATE = 24_000;

/** Silence from Google this long means the session is dead, not thinking. */
const UPSTREAM_STALL_MS = 20_000;

/**
 * Wrap finished prose in the instruction that makes the model read verbatim.
 * Exact mirror of the server-side frame_for_reading text from live_voice.py.
 */
export function frameForReading(text: string): string {
  return (
    "Read the following status update aloud to the listener, in your own " +
    "speaking voice, keeping every fact and adding nothing. Do not reply to " +
    "it, do not advise, do not continue it. Just say it:\n\n" +
    text.trim()
  );
}

/**
 * Read `text` aloud through a Gemini Live narration session.
 *
 * Resolves as soon as the socket is open and the audio stream is ready, returning
 * the MediaStream so speechPlayback can attach it to an Audio element.
 *
 * @param text - The finished summary to read aloud verbatim.
 * @param options.signal - Aborts the narration and closes the session.
 * @throws LiveVoiceUnavailable when no session could be opened or text is empty.
 */
export async function narrateViaGeminiLive(
  text: string,
  options: { signal?: AbortSignal } = {},
): Promise<LiveNarration> {
  if (!text || !text.trim()) {
    throw new LiveVoiceUnavailable("nothing to read");
  }
  if (typeof AudioContext === "undefined") {
    throw new LiveVoiceUnavailable("this browser has no Web Audio API");
  }

  let ctx: AudioContext | null = null;
  let destination: MediaStreamAudioDestinationNode | null = null;
  try {
    ctx = new AudioContext({ sampleRate: PLAYBACK_RATE });
  } catch {
    ctx = new AudioContext();
  }
  await ctx.resume();
  destination = ctx.createMediaStreamDestination();

  let ws: WebSocket | null = null;
  let stopped = false;
  let turnCompleted = false;
  let pingInterval: ReturnType<typeof setInterval> | null = null;
  let settleFinished: () => void = () => {};
  const finished = new Promise<void>((resolve) => {
    settleFinished = resolve;
  });

  let nextStartTime = 0;
  let sources: AudioBufferSourceNode[] = [];
  let lastUpstreamAt = Date.now();
  let stallTimer: ReturnType<typeof setInterval> | null = null;

  const stop = (): void => {
    if (stopped) return;
    stopped = true;
    if (pingInterval) {
      clearInterval(pingInterval);
      pingInterval = null;
    }
    if (stallTimer) {
      clearInterval(stallTimer);
      stallTimer = null;
    }
    for (const src of sources) {
      try {
        src.stop();
        src.disconnect();
      } catch {
        // already finished
      }
    }
    sources = [];
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      try {
        ws.close(1000, "client stop");
      } catch {
        // already closed
      }
    }
    if (ctx && ctx.state !== "closed") {
      void ctx.close();
    }
    settleFinished();
  };

  options.signal?.addEventListener("abort", stop, { once: true });
  if (options.signal?.aborted) {
    stop();
    throw new LiveVoiceUnavailable("aborted");
  }

  const socketUrl = resolveWebSocketUrl("/v1/live/gemini/ws?mode=narrate");
  ws = new WebSocket(socketUrl);
  const socket = ws;
  socket.binaryType = "arraybuffer";

  socket.addEventListener("message", (ev: MessageEvent<string | ArrayBuffer>) => {
    if (stopped) return;
    lastUpstreamAt = Date.now();
    const events = parseServerMessages(ev.data);
    for (const event of events) {
      if (stopped) break;
      if (event.type === "audio" && ctx && destination) {
        const floats = decodePcm16Base64(event.data);
        const buffer = ctx.createBuffer(1, floats.length, PLAYBACK_RATE);
        buffer.copyToChannel(new Float32Array(floats), 0);
        const src = ctx.createBufferSource();
        src.buffer = buffer;
        src.connect(destination);
        // Same slack the conversation queue needs. Reading has not stuttered
        // because a narration streams faster than it plays, so the queue never
        // runs dry -- but the scheduling is identical, so give it the lead too
        // rather than leave the same defect waiting for a slow network.
        const now = ctx.currentTime;
        if (nextStartTime <= now) nextStartTime = now + JITTER_LEAD_S;
        src.start(nextStartTime);
        nextStartTime += buffer.duration;
        sources.push(src);
        src.onended = () => {
          sources = sources.filter((s) => s !== src);
          if (sources.length > 0) return;
          // Narration never receives turnComplete -- the server sees only
          // generationComplete -- so a drained queue with nothing more coming
          // is what "finished" means here.
          const upstreamQuiet = Date.now() - lastUpstreamAt > UPSTREAM_STALL_MS;
          if (turnCompleted || upstreamQuiet) {
            settleFinished();
          }
        };
      } else if (event.type === "turnComplete") {
        turnCompleted = true;
        if (sources.length === 0) {
          settleFinished();
        }
      }
    }
  });

  socket.addEventListener("close", () => {
    stop();
  });

  socket.addEventListener("error", () => {
    stop();
  });

  try {
    await new Promise<void>((resolve, reject) => {
      const onOpen = () => {
        socket.removeEventListener("open", onOpen);
        socket.removeEventListener("close", onCloseEarly);
        socket.removeEventListener("error", onErrorEarly);
        resolve();
      };
      const onCloseEarly = (ev: CloseEvent) => {
        socket.removeEventListener("open", onOpen);
        socket.removeEventListener("close", onCloseEarly);
        socket.removeEventListener("error", onErrorEarly);
        reject(new LiveVoiceUnavailable(`gemini live socket closed early: ${ev.code}`));
      };
      const onErrorEarly = () => {
        socket.removeEventListener("open", onOpen);
        socket.removeEventListener("close", onCloseEarly);
        socket.removeEventListener("error", onErrorEarly);
        reject(new LiveVoiceUnavailable("gemini live socket failed to open"));
      };
      socket.addEventListener("open", onOpen);
      socket.addEventListener("close", onCloseEarly);
      socket.addEventListener("error", onErrorEarly);
    });

    pingInterval = setInterval(() => {
      if (!stopped && ws && ws.readyState === WebSocket.OPEN) {
        try {
          ws.send(JSON.stringify({ omnigentPing: true }));
        } catch {
          // Best-effort keepalive
        }
      }
    }, 20_000);

    // Google's live endpoint goes quiet on some sessions and never speaks.
    // Waiting for the server's runaway cap holds the quota for half an hour,
    // so give up on a silent narration and let the caller ask again.
    stallTimer = setInterval(() => {
      if (stopped || Date.now() - lastUpstreamAt <= UPSTREAM_STALL_MS) return;
      // Google going quiet is not the end of the reading. It streams a
      // narration faster than it plays, so the last chunk arrives long before
      // it is spoken -- killing the context here cut every long narration off
      // mid-sentence, exactly 20s after the final chunk landed. Wait for the
      // queue to drain; only then is there nothing left to say.
      if (sources.length > 0) return;
      settleFinished();
      stop();
    }, 2_000);

    const clientTurn = {
      clientContent: {
        turns: [
          {
            role: "user",
            parts: [{ text: frameForReading(text) }],
          },
        ],
        turnComplete: true,
      },
    };
    socket.send(JSON.stringify(clientTurn));
  } catch (error) {
    stop();
    throw error instanceof LiveVoiceUnavailable ? error : new LiveVoiceUnavailable(String(error));
  }

  return {
    stream: destination.stream,
    finished,
    stop,
  };
}
