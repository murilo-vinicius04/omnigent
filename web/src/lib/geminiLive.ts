/**
 * Browser-side session for the OmniLive (Gemini Realtime) voice stream.
 *
 * The server proxies `/v1/live/gemini/ws`; the browser never touches the
 * API key and never sends a setup frame (the SERVER sends it). Relay rule:
 * text frames stay text, binary stays binary — Google answers in binary
 * JSON, so both shapes must parse.
 */
import { resolveWebSocketUrl } from "@/lib/host";
import { workletUrl } from "@/lib/dictation";

const CAPTURE_RATE = 16_000;
const PLAYBACK_RATE = 24_000;

/**
 * Stall detection. Google's live endpoint sometimes accepts audio and never
 * answers — no transcript, no speech, no close — so the reader ends up talking
 * into a session that is already dead. Speech is detected only to know when an
 * answer is owed; frames are never gated on it.
 */
const SPEECH_RMS = 900;
// Waiting on a reply. The old single 7s deadline hung up on healthy sessions:
// measured first audio in real conversations reached 10.6s, and a handoff needs
// ~5.5s more for the tool round trip, so "yes, send it to Claude" was routinely
// killed mid-thought. Say something at the first mark, hang up only at the
// second, which is past anything observed working.
const REPLY_SLOW_MS = 8_000;
const REPLY_STALL_MS = 30_000;
// Speech this long with nothing transcribed means Gemini is not hearing it.
// Input transcripts stream while the reader talks, so a healthy session sends
// one well inside this.
const NOT_HEARD_MS = 5_000;
// How far ahead of the playhead the first chunk of a reply is scheduled.
// Audio arrives over a socket whose pacing we do not control, so the queue
// needs slack: with none, a packet that is a few ms late plays into a gap and
// the reply stutters. 150ms is under the ear's threshold for added latency and
// is small next to the ~800ms we already wait for the first chunk.
const JITTER_LEAD_S = 0.15;

/** Convert little-endian PCM16 to standard base64 (no padding stripping). */
export function pcm16ToBase64(pcm: Int16Array): string {
  const bytes = new Uint8Array(pcm.byteLength);
  for (let i = 0; i < pcm.length; i++) {
    const v = pcm[i];
    bytes[i * 2] = v & 0xff;
    bytes[i * 2 + 1] = (v >> 8) & 0xff;
  }
  let binary = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

/** Client -> server audio frame (base64 PCM16 LE at 16 kHz). */
export function buildAudioFrame(pcm: Int16Array): object {
  return {
    realtimeInput: {
      audio: {
        data: pcm16ToBase64(pcm),
        mimeType: `audio/pcm;rate=${CAPTURE_RATE}`,
      },
    },
  };
}

/** Whether a captured frame carries speech rather than room noise. */
export function isSpeech(pcm: Int16Array): boolean {
  if (pcm.length === 0) return false;
  let sum = 0;
  // Every 4th sample: 25 ms of 16 kHz audio is ample for an energy estimate.
  for (let i = 0; i < pcm.length; i += 4) sum += pcm[i] * pcm[i];
  return Math.sqrt(sum / Math.ceil(pcm.length / 4)) > SPEECH_RMS;
}

/** Decode base64 little-endian PCM16 to Float32 in [-1, 1]. */
export function decodePcm16Base64(b64: string): Float32Array {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  const view = new DataView(bytes.buffer);
  const out = new Float32Array(Math.floor(bytes.length / 2));
  for (let i = 0; i < out.length; i++) {
    out[i] = view.getInt16(i * 2, true) / 0x8000;
  }
  return out as Float32Array<ArrayBuffer>;
}

/**
 * Normalize a worklet message payload to Int16Array. The dictation helper posts
 * an Int16Array (`this.port.postMessage(out, [out.buffer])`, the buffer is
 * transferred, the object is an Int16Array); the normalizer is defensive.
 */
export function toPcm16(data: unknown): Int16Array | null {
  if (data instanceof Int16Array) return data;
  if (data instanceof ArrayBuffer) return new Int16Array(data);
  return null;
}

export interface GeminiToolCallItem {
  id?: string;
  name: string;
  args?: Record<string, unknown>;
}

export interface GeminiToolResponseItem {
  id?: string;
  name?: string;
  response: Record<string, unknown>;
}

/** Discriminated union of everything the server can push at us. */
export type GeminiLiveEvent =
  | { type: "audio"; mimeType: string; data: string }
  | { type: "inputTranscript"; text: string }
  | { type: "outputTranscript"; text: string }
  | { type: "interrupted" }
  | { type: "turnComplete" }
  | { type: "setupComplete" }
  | { type: "toolCall"; calls: GeminiToolCallItem[] };

/**
 * Parse a server frame: string OR binary JSON (Google sends binary).
 * Returns every event the message carries, in a stable order: setupComplete,
 * toolCall, interrupted, audio parts, transcripts, turnComplete. Garbage yields [].
 */
export function parseServerMessages(data: string | ArrayBuffer): GeminiLiveEvent[] {
  let text: string;
  if (typeof data === "string") {
    text = data;
  } else {
    text = new TextDecoder().decode(data);
  }
  let msg: unknown;
  try {
    msg = JSON.parse(text);
  } catch {
    return [];
  }
  const msgRec = msg as Record<string, unknown> | null;
  if (!msgRec || typeof msgRec !== "object") return [];
  const content = msgRec.serverContent as Record<string, unknown> | undefined;
  const events: GeminiLiveEvent[] = [];
  if ("setupComplete" in msgRec) events.push({ type: "setupComplete" });
  const toolCall = msgRec.toolCall as Record<string, unknown> | undefined;
  if (toolCall && typeof toolCall === "object" && Array.isArray(toolCall.functionCalls)) {
    const calls: GeminiToolCallItem[] = [];
    for (const fc of toolCall.functionCalls) {
      if (
        fc &&
        typeof fc === "object" &&
        typeof (fc as Record<string, unknown>).name === "string"
      ) {
        const fcRec = fc as Record<string, unknown>;
        calls.push({
          id: typeof fcRec.id === "string" ? fcRec.id : undefined,
          name: fcRec.name as string,
          args:
            fcRec.args && typeof fcRec.args === "object"
              ? (fcRec.args as Record<string, unknown>)
              : undefined,
        });
      }
    }
    if (calls.length > 0) {
      events.push({ type: "toolCall", calls });
    }
  }
  if (content?.interrupted) events.push({ type: "interrupted" });
  const parts = content?.modelTurn && (content.modelTurn as Record<string, unknown>).parts;
  if (Array.isArray(parts)) {
    for (const part of parts) {
      const inline = (part as Record<string, unknown>).inlineData as
        Record<string, unknown> | undefined;
      if (inline && typeof inline.mimeType === "string" && typeof inline.data === "string") {
        events.push({
          type: "audio",
          mimeType: inline.mimeType,
          data: inline.data,
        });
      }
    }
  }
  const inputText = (content?.inputTranscription as Record<string, unknown> | undefined)?.text;
  if (typeof inputText === "string") {
    events.push({ type: "inputTranscript", text: inputText });
  }
  const outputText = (content?.outputTranscription as Record<string, unknown> | undefined)?.text;
  if (typeof outputText === "string") {
    events.push({ type: "outputTranscript", text: outputText });
  }
  if (content?.turnComplete) events.push({ type: "turnComplete" });
  return events;
}

/**
 * Categorize a WS close code.
 * 1008 = deliberate policy stop (session cap or 60 s idle) — never reconnect.
 */
export function classifyClose(code: number): "normal" | "policy" | "error" {
  if (code === 1000) return "normal";
  if (code === 1008) return "policy";
  return "error"; // covers 1011 and anything unexpected
}

export interface GeminiLiveSession {
  /** Tear down: close socket with 1000, release mic and audio contexts. */
  stop: () => void;
  sendToolResponse: (responses: GeminiToolResponseItem[]) => void;
  /** Resolves when all currently scheduled audio playback has completed. */
  untilPlaybackDrained: () => Promise<void>;
}

/** State callbacks: connected once, closed exactly once with its kind. */
export type GeminiLiveState =
  | { state: "connected" }
  // Google has acknowledged the setup frame: from here the model actually
  // hears the mic. The socket opens well before this, so "connected" alone is
  // not a cue to start talking.
  | { state: "ready" }
  // Owed a reply for a while. Not fatal: the model is often just slow.
  | { state: "waiting"; sinceMs: number }
  // The reader has been talking and nothing came back transcribed: Gemini is
  // not hearing them, so they are talking to themselves.
  | { state: "not-hearing" }
  // A reply's audio ran out mid-sentence: Google is sending it slower than it
  // plays, which is the freeze the reader hears.
  | { state: "lagging" }
  // A warning above no longer applies.
  | { state: "clear" }
  | {
      state: "closed";
      kind: "normal" | "policy" | "error";
      code: number;
      reason: string;
    };

export interface GeminiLiveOptions {
  sessionId?: string | null;
  onEvent?: (event: GeminiLiveEvent) => void;
  onStateChange?: (state: GeminiLiveState) => void;
  onError?: (error: Error) => void;
}

/**
 * Start a live voice session: capture mic audio via the shared PCM16 worklet,
 * open the relayed WS, stream 100 ms chunks as realtimeInput frames, and play
 * model audio back through an AudioContext scheduled back-to-back.
 * Mic and audio graphs come up BEFORE the socket so a slow permission prompt
 * cannot trip the server's idle guard. Resolves once the socket is open.
 */
export async function startGeminiLive(opts: GeminiLiveOptions = {}): Promise<GeminiLiveSession> {
  const { onEvent, onStateChange, onError } = opts;

  let mediaStream: MediaStream | null = null;
  let captureCtx: AudioContext | null = null;
  let playbackCtx: AudioContext | null = null;
  let ws: WebSocket | null = null;
  let stopped = false;
  let startResolved = false;
  let closedReported = false;
  let receivedAnyMessage = false;
  let pingInterval: ReturnType<typeof setInterval> | null = null;
  let stallInterval: ReturnType<typeof setInterval> | null = null;
  let lastSpeechAt = 0;
  let lastAnswerAt = 0;
  // One "still waiting" per unanswered utterance, not one per tick.
  let slowNotified = false;
  // When the reader started talking with nothing transcribed yet (0: not).
  let speechStartedAt = 0;
  let deafNotified = false;
  let lagNotified = false;
  // The next audio chunk opens a reply, so an empty queue is not a freeze.
  let firstChunkOfReply = true;
  // Playback scheduling: model audio chunks are placed end-to-end.
  let nextStartTime = 0;
  // Times the queue ran dry mid-reply. Not reset per turn: it is a health
  // count for the whole session, reported when the socket closes.
  let underruns = 0;
  let sources: AudioBufferSourceNode[] = [];
  let drainResolvers: (() => void)[] = [];

  const notifyDrain = () => {
    if (sources.length === 0) {
      const resolvers = drainResolvers;
      drainResolvers = [];
      for (const r of resolvers) r();
    }
  };

  const releaseAll = () => {
    if (pingInterval) {
      clearInterval(pingInterval);
      pingInterval = null;
    }
    if (stallInterval) {
      clearInterval(stallInterval);
      stallInterval = null;
    }
    for (const src of sources) {
      try {
        src.stop();
      } catch {
        // already finished
      }
    }
    sources = [];
    notifyDrain();
    for (const track of mediaStream?.getTracks() ?? []) track.stop();
    mediaStream = null;
    if (captureCtx && captureCtx.state !== "closed") void captureCtx.close();
    captureCtx = null;
    if (playbackCtx && playbackCtx.state !== "closed") void playbackCtx.close();
    playbackCtx = null;
  };

  const reportClosed = (kind: "normal" | "policy" | "error", code: number, reason: string) => {
    if (closedReported) return;
    closedReported = true;
    // A stuttering reply is a queue that ran dry, which nothing else records.
    // Report it once per session so the cause is visible without a repro.
    if (underruns > 0) {
      console.warn(`gemini live: audio queue ran dry ${underruns}x this session`);
    }
    onStateChange?.({ state: "closed", kind, code, reason });
  };

  const stopScheduledAudio = () => {
    // Barge-in: kill everything queued and reset the schedule.
    for (const src of sources) {
      try {
        src.stop();
      } catch {
        // already finished
      }
    }
    sources = [];
    nextStartTime = 0;
    notifyDrain();
  };

  const handleEvent = (event: GeminiLiveEvent) => {
    // Only an answer clears the debt. Google keeps transcribing the reader on a
    // session that has stopped replying — counting that as life is how a wedged
    // session looks healthy.
    if (event.type === "audio" || event.type === "turnComplete" || event.type === "toolCall") {
      lastAnswerAt = Date.now();
      // The answer came: "still thinking" is no longer true.
      if (slowNotified) onStateChange?.({ state: "clear" });
      slowNotified = false;
    }
    // Transcribed or answered: it heard them.
    if (event.type === "inputTranscript" || event.type === "audio") {
      speechStartedAt = 0;
      if (deafNotified) {
        deafNotified = false;
        onStateChange?.({ state: "clear" });
      }
    }
    if (event.type === "turnComplete" || event.type === "interrupted") {
      firstChunkOfReply = true;
      if (lagNotified) {
        lagNotified = false;
        onStateChange?.({ state: "clear" });
      }
    }
    if (event.type === "setupComplete") {
      onStateChange?.({ state: "ready" });
    }
    if (event.type === "audio" && playbackCtx) {
      const floats = decodePcm16Base64(event.data);
      const buffer = playbackCtx.createBuffer(1, floats.length, PLAYBACK_RATE);
      buffer.copyToChannel(new Float32Array(floats), 0);
      const src = playbackCtx.createBufferSource();
      src.buffer = buffer;
      src.connect(playbackCtx.destination);
      // Schedule back-to-back, but never at exactly `now`. Starting a chunk
      // the instant it lands leaves the playhead with zero slack, so the next
      // packet that is even slightly late plays into a gap -- and because the
      // old clamp restarted at `now`, every later chunk inherited that same
      // zero slack and the reply stuttered the rest of the way. Re-establish
      // the lead whenever the queue does run dry.
      const now = playbackCtx.currentTime;
      const midReply = !firstChunkOfReply;
      firstChunkOfReply = false;
      // `<=`, not `<`: a chunk that lands exactly as the previous one ends has
      // no slack either, and it is the first chunk's case when the context
      // clock still reads 0.
      if (nextStartTime <= now) {
        // Only mid-reply is an empty queue a freeze: before a reply's first
        // chunk it is just the gap between turns.
        if (midReply) {
          underruns += 1;
          if (!lagNotified) {
            lagNotified = true;
            onStateChange?.({ state: "lagging" });
          }
        }
        nextStartTime = now + JITTER_LEAD_S;
      }
      src.start(nextStartTime);
      nextStartTime += buffer.duration;
      sources.push(src);
      src.onended = () => {
        sources = sources.filter((s) => s !== src);
        notifyDrain();
      };
    } else if (event.type === "interrupted") {
      stopScheduledAudio();
    }
    onEvent?.(event);
  };

  try {
    // Mic first: a permission prompt must not race the server idle guard,
    // and a denial must not open a Google session at all.
    // Same constraints as dictation. Without echo cancellation the model hears
    // its own voice from the speakers and turn-taking wedges after its first
    // reply; without noise suppression a noisy room never reads as a finished
    // sentence, so Google's voice detection keeps waiting instead of answering.
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        sampleRate: CAPTURE_RATE,
      },
    });
    try {
      captureCtx = new AudioContext({ sampleRate: CAPTURE_RATE });
    } catch {
      captureCtx = new AudioContext();
    }
    await captureCtx.resume();
    await captureCtx.audioWorklet.addModule(workletUrl());
    const source = captureCtx.createMediaStreamSource(mediaStream);
    const node = new AudioWorkletNode(captureCtx, "omnigent-pcm16-downsampler");
    const mute = captureCtx.createGain();
    mute.gain.value = 0;
    source.connect(node);
    node.connect(mute);
    mute.connect(captureCtx.destination);
    node.port.onmessage = (ev: MessageEvent<unknown>) => {
      const pcm = toPcm16(ev.data);
      // ANY-FRAME idle guard: while the mic is open we must keep sending;
      // never gate frames on client-side VAD.
      if (pcm && !stopped && ws && ws.readyState === WebSocket.OPEN) {
        if (isSpeech(pcm)) {
          lastSpeechAt = Date.now();
          // Not while Gemini is talking: echo that leaks past cancellation is
          // not the reader, and must not read as "not hearing you".
          if (!speechStartedAt && sources.length === 0) speechStartedAt = lastSpeechAt;
        }
        ws.send(JSON.stringify(buildAudioFrame(pcm)));
      }
    };
    playbackCtx = new AudioContext({ sampleRate: PLAYBACK_RATE });
    await playbackCtx.resume();

    const wsPath = opts.sessionId
      ? `/v1/live/gemini/ws?session_id=${encodeURIComponent(opts.sessionId)}`
      : "/v1/live/gemini/ws";
    ws = new WebSocket(resolveWebSocketUrl(wsPath));
    const socket = ws;
    socket.binaryType = "arraybuffer";

    socket.addEventListener("message", (ev: MessageEvent<string | ArrayBuffer>) => {
      receivedAnyMessage = true;
      for (const event of parseServerMessages(ev.data)) handleEvent(event);
    });

    socket.addEventListener("close", (ev: CloseEvent) => {
      releaseAll();
      let kind = classifyClose(ev.code);
      let reason = ev.reason;
      if (ev.code === 1000 && !receivedAnyMessage && !stopped) {
        kind = "error";
        reason = "gemini never answered";
      }
      reportClosed(kind, ev.code, reason);
      // The start rejection is the error during startup; onError is for
      // failures after a successful start only, and never after stop().
      if (kind === "error" && startResolved && !stopped) {
        onError?.(new Error(reason || `gemini live socket closed: ${ev.code}`));
      }
    });

    socket.addEventListener("error", () => {
      if (startResolved && !stopped) {
        onError?.(new Error("gemini live socket error"));
      }
    });

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
        reject(new Error(`gemini live socket closed early: ${ev.code}`));
      };
      const onErrorEarly = () => {
        socket.removeEventListener("open", onOpen);
        socket.removeEventListener("close", onCloseEarly);
        socket.removeEventListener("error", onErrorEarly);
        reject(new Error("gemini live socket failed to open"));
      };
      socket.addEventListener("open", onOpen);
      socket.addEventListener("close", onCloseEarly);
      socket.addEventListener("error", onErrorEarly);
    });

    startResolved = true;
    // Owed an answer and nothing came back: hang up so the reader is told the
    // session died, instead of waiting on an endpoint that stopped listening.
    stallInterval = setInterval(() => {
      if (
        !stopped &&
        !deafNotified &&
        speechStartedAt &&
        Date.now() - speechStartedAt >= NOT_HEARD_MS
      ) {
        deafNotified = true;
        onStateChange?.({ state: "not-hearing" });
      }
      if (stopped || !lastSpeechAt || lastAnswerAt > lastSpeechAt) return;
      const owedMs = Date.now() - lastSpeechAt;
      if (owedMs >= REPLY_SLOW_MS && !slowNotified) {
        // Tell the reader we are still waiting rather than sitting in silence
        // that is indistinguishable from a dead session.
        slowNotified = true;
        onStateChange?.({ state: "waiting", sinceMs: owedMs });
      }
      if (owedMs < REPLY_STALL_MS) return;
      lastSpeechAt = 0;
      releaseAll();
      reportClosed("error", 4000, "gemini stopped responding");
      try {
        ws?.close(1000, "gemini stopped responding");
      } catch {
        // socket already dead
      }
      if (startResolved && !stopped) {
        onError?.(new Error("gemini stopped responding"));
      }
    }, 1_000);
    pingInterval = setInterval(() => {
      if (!stopped && ws && ws.readyState === WebSocket.OPEN) {
        try {
          ws.send(JSON.stringify({ omnigentPing: true }));
        } catch {
          // Best-effort keepalive
        }
      }
    }, 20_000);
  } catch (error) {
    releaseAll();
    if (ws) {
      try {
        ws.close(1000, "client teardown");
      } catch {
        // socket already dead
      }
    }
    throw error;
  }

  onStateChange?.({ state: "connected" });

  return {
    sendToolResponse(responses: GeminiToolResponseItem[]) {
      if (stopped || !ws || ws.readyState !== WebSocket.OPEN) return;
      try {
        const frame = {
          toolResponse: {
            functionResponses: responses.map((r) => {
              const item: Record<string, unknown> = { response: r.response };
              if (r.id !== undefined) item.id = r.id;
              if (r.name !== undefined) item.name = r.name;
              return item;
            }),
          },
        };
        ws.send(JSON.stringify(frame));
      } catch {
        // Best-effort: failures must not break conversation
      }
    },
    untilPlaybackDrained() {
      if (sources.length === 0) return Promise.resolve();
      return new Promise<void>((resolve) => {
        drainResolvers.push(resolve);
      });
    },
    stop() {
      if (stopped) return;
      stopped = true;
      releaseAll();
      if (ws && ws.readyState === WebSocket.OPEN) {
        try {
          // With automatic voice activity detection on, that event flushes cached
          // audio when the stream pauses (Gemini Multimodal Live API docs).
          ws.send(JSON.stringify({ realtimeInput: { audioStreamEnd: true } }));
        } catch {
          // Best-effort: a send failure must not break teardown.
        }
      }
      if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
        ws.close(1000, "client stop");
      }
      reportClosed("normal", 1000, "client stop");
    },
  };
}
