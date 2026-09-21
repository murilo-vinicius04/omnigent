/**
 * Speaking a finished summary through OpenAI's live voice model.
 *
 * The words are already written before any of this runs: Gemini wrote them,
 * and nothing here may change them. The live session exists only to say them
 * out loud, which it does in about 1.4 seconds against roughly 23 for local
 * synthesis.
 *
 * The browser holds the peer connection because WebRTC needs a peer, and the
 * server only brokers the handshake so the OpenAI key never reaches here.
 *
 * **The meter runs on wall clock.** A silent session bills exactly as much as
 * a talking one, and the only ways to stop it are `session.close` over the
 * data channel or dropping the connection. So a session is opened once the
 * text exists, never in anticipation, and closed the moment the speech ends.
 */

import { authenticatedFetch } from "./identity";

/** Where the server brokers the SDP exchange. */
const OFFER_URL = "/v1/live/offer";

/**
 * How long to wait after the last spoken word before hanging up.
 *
 * Transcript deltas arrive as the model speaks, so a gap in them means the
 * speech has stopped. Long enough not to clip a pause mid-sentence, short
 * enough that the tail costs a fraction of a cent.
 */
const SILENCE_BEFORE_HANGUP_MS = 2000;

/**
 * How long to wait for the first word before giving up.
 *
 * Much longer than the gap allowed between words: nothing has been said yet,
 * so there is a handshake and a backend turn still to come. Using the short
 * silence timer here hung up mid-thought and the session was billed for a
 * summary nobody heard.
 */
const FIRST_WORD_TIMEOUT_MS = 15_000;

/** Give up if the handshake or the first word never arrives. */
const CONNECT_TIMEOUT_MS = 20_000;

/**
 * How long a conversation may sit silent before it hangs itself up.
 *
 * A conversation bills by wall clock whether anyone is talking or not, and
 * the expensive mistake is a session left open after the reader walked away.
 * Ten seconds of nobody speaking is the signal. Being wrong costs one press
 * of the mic button; not doing it costs dollars an hour against silence.
 */
const CONVERSATION_IDLE_MS = 10_000;

/**
 * Nothing should ever bill longer than this for one summary, whatever goes
 * wrong. A minute of speech is already far past any summary we generate.
 */
const MAX_NARRATION_MS = 90_000;

export interface LiveNarration {
  /** The model's voice, for attaching to an audio element. */
  readonly stream: MediaStream;
  /** Resolves when the speech has finished and the session is closed. */
  readonly finished: Promise<void>;
  /** Hang up now. Safe to call repeatedly. */
  readonly stop: () => void;
}

/** Raised when live narration cannot be used, so the caller can fall back. */
export class LiveVoiceUnavailable extends Error {}

interface OfferResponse {
  session_id: string;
  sdp: string;
  speak: string | null;
}

/**
 * Read `text` aloud through a live session.
 *
 * Resolves as soon as audio is flowing, so the caller can attach the stream
 * and let it play; await `finished` to know when it is over.
 *
 * @param text - The finished summary. Spoken as written, never rewritten.
 * @param options.signal - Aborts the narration and closes the session.
 * @throws LiveVoiceUnavailable when no session could be opened.
 */
export async function narrateViaLive(
  text: string,
  options: { signal?: AbortSignal } = {},
): Promise<LiveNarration> {
  if (!text.trim()) throw new LiveVoiceUnavailable("nothing to read");
  if (typeof RTCPeerConnection === "undefined") {
    throw new LiveVoiceUnavailable("this browser has no WebRTC");
  }

  const pc = new RTCPeerConnection();
  let closed = false;
  let hangupTimer: ReturnType<typeof setTimeout> | undefined;
  let ceilingTimer: ReturnType<typeof setTimeout> | undefined;
  let settleFinished: () => void = () => {};
  const finished = new Promise<void>((resolve) => {
    settleFinished = resolve;
  });

  const stop = (): void => {
    if (closed) return;
    closed = true;
    clearTimeout(hangupTimer);
    clearTimeout(ceilingTimer);
    // Ask for a clean close first: the server acknowledges it and stops
    // billing, where a dropped connection relies on it noticing.
    try {
      if (channel.readyState === "open") {
        channel.send(JSON.stringify({ type: "session.close" }));
      }
    } catch {
      // Already gone; closing the connection below is what matters.
    }
    try {
      pc.close();
    } catch {
      // Nothing left to close.
    }
    settleFinished();
  };

  options.signal?.addEventListener("abort", stop, { once: true });

  const channel = pc.createDataChannel("oai-events");
  const inbound = new MediaStream();
  let firstAudio: (() => void) | undefined;
  const audioArrived = new Promise<void>((resolve) => {
    firstAudio = resolve;
  });

  pc.addEventListener("track", (event) => {
    for (const track of event.streams[0]?.getTracks() ?? [event.track]) {
      inbound.addTrack(track);
    }
    firstAudio?.();
  });

  // A live session negotiates audio both ways, and offering receive-only got
  // no audio back at all. So send a track -- but a silent one synthesized
  // here, never the microphone: narration must not trigger a permission
  // prompt, and there is nothing for it to hear anyway.
  const silence = silentTrack();
  if (silence) pc.addTrack(silence);
  else pc.addTransceiver("audio", { direction: "sendrecv" });

  let spoke = false;
  const bumpHangup = (): void => {
    clearTimeout(hangupTimer);
    hangupTimer = setTimeout(stop, spoke ? SILENCE_BEFORE_HANGUP_MS : FIRST_WORD_TIMEOUT_MS);
  };

  channel.addEventListener("message", (event) => {
    let payload: { type?: string };
    try {
      payload = JSON.parse(String(event.data));
    } catch {
      return;
    }
    // Transcript deltas track the spoken timeline, so they stop when the
    // voice does. That gap, not `response.completed`, marks the real end:
    // the text finishes generating well before it finishes being said.
    if (payload.type === "session.output_transcript.delta") {
      spoke = true;
      bumpHangup();
    }
    if (payload.type === "session.closed") stop();
  });

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);

  let answer: OfferResponse;
  try {
    const response = await authenticatedFetch(OFFER_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sdp: pc.localDescription?.sdp ?? offer.sdp, mode: "narrate", text }),
      signal: options.signal,
    });
    if (!response.ok) {
      throw new LiveVoiceUnavailable(`live session refused: ${response.status}`);
    }
    answer = (await response.json()) as OfferResponse;
  } catch (error) {
    stop();
    throw error instanceof LiveVoiceUnavailable ? error : new LiveVoiceUnavailable(String(error));
  }

  if (!answer.speak) {
    stop();
    throw new LiveVoiceUnavailable("server returned nothing to read");
  }

  await pc.setRemoteDescription({ type: "answer", sdp: answer.sdp });

  // Push the text as soon as the channel opens. The session generates an
  // opening turn on its own the moment it connects, and a narrator that
  // reaches that point with nothing in hand invents something to say.
  const speak = answer.speak;
  const push = (): void => {
    channel.send(
      JSON.stringify({
        type: "response.item.create",
        item: { type: "message", role: "user", content: [{ type: "input_text", text: speak }] },
      }),
    );
    channel.send(JSON.stringify({ type: "response.create" }));
    bumpHangup();
  };
  if (channel.readyState === "open") push();
  else channel.addEventListener("open", push, { once: true });

  ceilingTimer = setTimeout(stop, MAX_NARRATION_MS);

  const connected = await Promise.race([
    audioArrived.then(() => true),
    new Promise<false>((resolve) => {
      setTimeout(() => resolve(false), CONNECT_TIMEOUT_MS);
    }),
  ]);
  if (!connected) {
    stop();
    throw new LiveVoiceUnavailable("no audio from the live session");
  }

  return { stream: inbound, finished, stop };
}

/**
 * A silent outbound audio track, so the session has something to negotiate
 * against without opening the microphone.
 *
 * Returns null where Web Audio is unavailable; the caller then falls back to
 * a bare transceiver.
 */
function silentTrack(): MediaStreamTrack | null {
  const Ctor =
    typeof AudioContext !== "undefined"
      ? AudioContext
      : (globalThis as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctor) return null;
  try {
    const context = new Ctor();
    const destination = context.createMediaStreamDestination();
    // An oscillator at zero gain: a running source, emitting nothing.
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    gain.gain.value = 0;
    oscillator.connect(gain).connect(destination);
    oscillator.start();
    return destination.stream.getAudioTracks()[0] ?? null;
  } catch {
    return null;
  }
}

/** One finished utterance, from whichever side said it. */
export interface Utterance {
  /** Who spoke: the reader at the microphone, or the model. */
  readonly who: "reader" | "voice";
  readonly text: string;
}

/**
 * How long a transcript may go quiet before it counts as a finished
 * utterance. Deltas arrive as words are said, so a gap is a pause; short
 * enough to record promptly, long enough not to split a sentence in two.
 */
const UTTERANCE_GAP_MS = 1500;

/** A two-way spoken conversation with the live model. */
export interface LiveConversation {
  /** The model's voice, for attaching to an audio element. */
  readonly stream: MediaStream;
  /** Resolves when the session ends, however it ends. */
  readonly closed: Promise<void>;
  /** Seconds of wall clock billed so far. */
  readonly elapsedS: () => number;
  /** Hang up now. Safe to call repeatedly. */
  readonly stop: () => void;
  /**
   * Resolve once the voice is not mid-sentence, so hanging up does not cut it
   * off. With `expectSpeech`, wait for it to start first: an appended result
   * is spoken shortly after it lands, not at once.
   */
  readonly untilQuiet: (expectSpeech?: boolean) => Promise<void>;
  /**
   * Return a delegation's result for the voice to say in its own words
   * (`session.commentary.append`).
   */
  readonly commentary: (delegationId: string, content: string) => void;
  /** Give the voice something to know without saying it (`session.thinking.append`). */
  readonly thinking: (content: string) => void;
}

/** The pause in the voice's words that marks a sentence as finished. */
const QUIET_GAP_MS = 1200;

/** Never hold a hang-up longer than this waiting for the voice to stop. */
const QUIET_MAX_MS = 4000;

/** How long to wait for the voice to start saying an appended result. */
const QUIET_FIRST_WORD_MS = 3000;

/** The hang-up ceiling when the voice still has something to say. */
const QUIET_EXPECT_MAX_MS = 10_000;

/** How much of the reader's recent speech a delegation carries as its question. */
const MAX_DELEGATED_CHARS = 1000;

/** Each append takes at most 500 tokens; characters are a safe proxy. */
const MAX_APPEND_CHARS = 1800;

/**
 * Open a two-way conversation: it hears the microphone and answers aloud.
 *
 * Unlike narration this has no natural end, so nothing here closes it on a
 * silence. It bills by wall clock for as long as it is open -- roughly three
 * dollars an hour, whether anyone is talking or not -- so the only thing that
 * ends it is the reader, and the caller is expected to show them the meter.
 *
 * The model answers natively from a briefing built from the session's
 * companion ledger. What that does not cover it delegates: GPT-Live's client
 * delegation raises `session.delegation.created`, the caller answers through
 * `commentary`, and the voice says the result.
 *
 * @param sessionId - Conversation whose ledger briefs the model.
 * @throws LiveVoiceUnavailable when the microphone or the session is refused.
 */
export async function openLiveConversation(
  sessionId: string | null,
  options: {
    onUtterance?: (utterance: Utterance) => void;
    /**
     * The voice delegated. `asked` is what the reader said since the last
     * delegation, since the event itself carries no task text.
     */
    onDelegation?: (delegationId: string, asked: string) => void;
    /** Either side was heard: a transcript delta came in for them. */
    onSpeech?: (who: "reader" | "voice") => void;
  } = {},
): Promise<LiveConversation> {
  if (typeof RTCPeerConnection === "undefined") {
    throw new LiveVoiceUnavailable("this browser has no WebRTC");
  }

  let mic: MediaStream;
  try {
    mic = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (error) {
    // Refusing the microphone is a choice, not a fault: report it plainly so
    // the caller can say so rather than looking broken.
    throw new LiveVoiceUnavailable(`microphone unavailable: ${String(error)}`);
  }

  const pc = new RTCPeerConnection();
  const startedAt = Date.now();
  let closed = false;
  let settleClosed: () => void = () => {};
  const closedPromise = new Promise<void>((resolve) => {
    settleClosed = resolve;
  });

  const channel = pc.createDataChannel("oai-events");
  const inbound = new MediaStream();
  let idleTimer: ReturnType<typeof setTimeout> | undefined;

  /** Speech from either side means the conversation is alive. */
  const touch = (): void => {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(stop, CONVERSATION_IDLE_MS);
  };

  // Buffer each side's transcript and flush it on a pause. Nothing else
  // records this conversation: audio goes browser-to-OpenAI directly, so if
  // it is not captured here it is gone the moment it is said.
  const buffers: Record<
    "reader" | "voice",
    { text: string; timer?: ReturnType<typeof setTimeout> }
  > = {
    reader: { text: "" },
    voice: { text: "" },
  };

  const collect = (who: "reader" | "voice", delta: string): void => {
    const buffer = buffers[who];
    buffer.text += delta;
    clearTimeout(buffer.timer);
    buffer.timer = setTimeout(() => {
      const said = buffer.text.trim();
      buffer.text = "";
      if (said) options.onUtterance?.({ who, text: said });
    }, UTTERANCE_GAP_MS);
  };

  const flushAll = (): void => {
    for (const who of ["reader", "voice"] as const) {
      const buffer = buffers[who];
      clearTimeout(buffer.timer);
      const said = buffer.text.trim();
      buffer.text = "";
      if (said) options.onUtterance?.({ who, text: said });
    }
  };

  const stop = (): void => {
    if (closed) return;
    closed = true;
    clearTimeout(idleTimer);
    try {
      if (channel.readyState === "open") {
        channel.send(JSON.stringify({ type: "session.close" }));
      }
    } catch {
      // Already gone; closing the connection below is what stops the meter.
    }
    // Whatever was mid-sentence is still worth recording.
    flushAll();
    // Release the microphone, or the browser keeps showing it as in use.
    for (const track of mic.getTracks()) track.stop();
    try {
      pc.close();
    } catch {
      // Nothing left to close.
    }
    settleClosed();
  };

  pc.addEventListener("track", (event) => {
    for (const track of event.streams[0]?.getTracks() ?? [event.track]) {
      inbound.addTrack(track);
    }
  });
  pc.addEventListener("connectionstatechange", () => {
    if (pc.connectionState === "failed" || pc.connectionState === "closed") stop();
  });
  /** Set while waiting for the voice to finish what it is saying. */
  let voiceSpeaking: (() => void) | undefined;
  // What the reader has said since the voice last delegated. A delegation
  // carries no task text, so this is the question it is about.
  let readerSinceDelegation = "";
  let eventCount = 0;

  channel.addEventListener("message", (event) => {
    let payload: {
      type?: string;
      delta?: string;
      delegation?: { id?: string };
      error?: unknown;
    };
    try {
      payload = JSON.parse(String(event.data));
    } catch {
      return;
    }
    if (payload.type === "session.input_transcript.delta") {
      touch();
      if (payload.delta?.trim()) options.onSpeech?.("reader");
      readerSinceDelegation = (readerSinceDelegation + (payload.delta ?? "")).slice(
        -MAX_DELEGATED_CHARS,
      );
      collect("reader", payload.delta ?? "");
    } else if (payload.type === "session.output_transcript.delta") {
      touch();
      voiceSpeaking?.();
      if (payload.delta?.trim()) options.onSpeech?.("voice");
      collect("voice", payload.delta ?? "");
    } else if (payload.type === "session.delegation.created" && payload.delegation?.id) {
      // The voice could not answer and handed the question to this application.
      touch();
      const asked = readerSinceDelegation.trim();
      readerSinceDelegation = "";
      options.onDelegation?.(payload.delegation.id, asked);
    } else if (payload.type === "error") {
      console.warn("live session error", payload.error);
    } else if (payload.type === "session.closed") {
      stop();
    }
  });

  for (const track of mic.getTracks()) pc.addTrack(track, mic);
  // The clock starts at connect, so a session nobody ever speaks into hangs
  // up too rather than billing until the reader notices it is open.
  touch();
  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);

  try {
    const response = await authenticatedFetch(OFFER_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sdp: pc.localDescription?.sdp ?? offer.sdp,
        mode: "converse",
        session_id: sessionId,
      }),
    });
    if (!response.ok) {
      throw new LiveVoiceUnavailable(`live session refused: ${response.status}`);
    }
    const answer = (await response.json()) as OfferResponse;
    await pc.setRemoteDescription({ type: "answer", sdp: answer.sdp });
  } catch (error) {
    stop();
    throw error instanceof LiveVoiceUnavailable ? error : new LiveVoiceUnavailable(String(error));
  }

  /** Send one context append, the documented way to feed a running session. */
  const append = (type: string, delegationId: string | null, content: string): void => {
    if (closed || channel.readyState !== "open") return;
    eventCount += 1;
    channel.send(
      JSON.stringify({
        type,
        event_id: `omnigent_${eventCount}`,
        delegation_id: delegationId,
        content: content.slice(0, MAX_APPEND_CHARS),
      }),
    );
  };

  const untilQuiet = (expectSpeech = false): Promise<void> =>
    new Promise<void>((resolve) => {
      if (closed) {
        resolve();
        return;
      }
      let gap: ReturnType<typeof setTimeout> | undefined;
      const done = (): void => {
        clearTimeout(gap);
        clearTimeout(ceiling);
        voiceSpeaking = undefined;
        resolve();
      };
      const ceiling = setTimeout(done, expectSpeech ? QUIET_EXPECT_MAX_MS : QUIET_MAX_MS);
      // Each word from the voice pushes the finish back; a pause ends it.
      voiceSpeaking = () => {
        clearTimeout(gap);
        gap = setTimeout(done, QUIET_GAP_MS);
      };
      gap = setTimeout(done, expectSpeech ? QUIET_FIRST_WORD_MS : QUIET_GAP_MS);
      void closedPromise.then(done);
    });

  return {
    stream: inbound,
    closed: closedPromise,
    elapsedS: () => (Date.now() - startedAt) / 1000,
    stop,
    untilQuiet,
    commentary: (delegationId, content) =>
      append("session.commentary.append", delegationId, content),
    thinking: (content) => append("session.thinking.append", null, content),
  };
}
