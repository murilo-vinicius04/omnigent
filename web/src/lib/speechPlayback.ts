import { create } from "zustand";
import { narrateViaLive, type LiveNarration } from "./liveVoice";
import { narrateViaGeminiLive } from "./geminiNarrator";
import { getLiveVoiceEngine, unmuteEndpoint } from "./liveVoiceEngine";
import { currentNarrationVolume, isNarrationEnabled } from "./sessionNarrationVolume";
import { currentVoiceBackend } from "./sessionVoiceBackend";
import { reportNarration } from "./narrationLog";

/**
 * Engine interface for text-to-speech playback.
 * Browser speechSynthesis is the default implementation, but alternative
 * engines could be plugged in via setSpeechEngine without altering call sites.
 */
export interface SpeechEngine {
  isSupported: () => boolean;
  speak: (
    text: string,
    lang?: string,
    onEnd?: () => void,
    onError?: () => void,
    /** 0..1; omitted means the engine's own default. */
    volume?: number,
  ) => void;
  stop: () => void;
  isSpeaking: () => boolean;
}

/**
 * Default TTS engine using the browser's Web Speech API (window.speechSynthesis).
 * Zero dependencies, Apache-2.0 compatible, free.
 */
export class BrowserSpeechEngine implements SpeechEngine {
  isSupported(): boolean {
    return (
      typeof window !== "undefined" &&
      "speechSynthesis" in window &&
      typeof window.SpeechSynthesisUtterance !== "undefined"
    );
  }

  speak(
    text: string,
    lang?: string,
    onEnd?: () => void,
    onError?: () => void,
    volume?: number,
  ): void {
    if (!this.isSupported()) return;
    try {
      window.speechSynthesis.cancel();
      const utterance = new window.SpeechSynthesisUtterance(text);
      if (lang) {
        utterance.lang = lang;
      }
      if (volume !== undefined) {
        utterance.volume = Math.min(1, Math.max(0, volume));
      }
      if (onEnd) {
        utterance.onend = () => onEnd();
      }
      if (onError) {
        utterance.onerror = () => onError();
      }
      window.speechSynthesis.speak(utterance);
    } catch {
      // Degrade silently if speech synthesis fails.
    }
  }

  stop(): void {
    if (!this.isSupported()) return;
    try {
      window.speechSynthesis.cancel();
    } catch {
      // Degrade silently.
    }
  }

  isSpeaking(): boolean {
    return this.isSupported() && Boolean(window.speechSynthesis.speaking);
  }
}

let activeSpeechEngine: SpeechEngine = new BrowserSpeechEngine();

export function getSpeechEngine(): SpeechEngine {
  return activeSpeechEngine;
}

export function setSpeechEngine(engine: SpeechEngine): void {
  activeSpeechEngine = engine;
}

export function resetSpeechEngine(): void {
  activeSpeechEngine = new BrowserSpeechEngine();
}

export const SPOKEN_MESSAGES_SESSION_STORAGE_KEY = "omnigent:spoken-summary:spoken-ids";
export const MAX_PERSISTED_SPOKEN_IDS = 200;

/** Unbounded in-memory set of all spoken or historical message IDs. */
const spokenMessageIds = new Set<string>();

/** In-memory FIFO array of IDs persisted in sessionStorage (capped at MAX_PERSISTED_SPOKEN_IDS). */
let persistedIdsCache: string[] | null = null;
let persistedIdsSetCache: Set<string> | null = null;

function getSessionStorage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage ?? null;
  } catch {
    return null;
  }
}

/**
 * Loads the persisted spoken IDs from sessionStorage into memory caches.
 *
 * Deliberate bias and trade-off:
 * On storage read errors (e.g. SecurityError, disabled cookies/storage) or corrupted data,
 * this function intentionally initializes and returns an empty cache, causing `isMessageSpoken`
 * to answer 'not spoken'.
 *
 * Why this trade-off:
 * The opposite choice (returning 'spoken' on error) would permanently silence spoken summaries
 * for any user in private browsing or environments where sessionStorage is blocked.
 * The narrow consequence of biasing to 'not spoken' is that corrupt/blocked sessionStorage
 * combined with a hard reload mid-stream can restart a partially-heard summary.
 * Permanent silence is a strictly worse user failure than a rare mid-stream restart on hard reload,
 * so biasing to 'not spoken' is the correct and intentional decision.
 */
function loadSessionStorageCache(): { ids: string[]; set: Set<string> } {
  if (persistedIdsCache !== null && persistedIdsSetCache !== null) {
    return { ids: persistedIdsCache, set: persistedIdsSetCache };
  }

  persistedIdsCache = [];
  persistedIdsSetCache = new Set();

  const storage = getSessionStorage();
  if (!storage) {
    return { ids: persistedIdsCache, set: persistedIdsSetCache };
  }

  try {
    const raw = storage.getItem(SPOKEN_MESSAGES_SESSION_STORAGE_KEY);
    if (!raw) {
      return { ids: persistedIdsCache, set: persistedIdsSetCache };
    }

    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) {
      const valid = parsed.filter((x): x is string => typeof x === "string" && x.length > 0);
      // Keep at most MAX_PERSISTED_SPOKEN_IDS (FIFO, most recent at tail)
      const capped = valid.slice(-MAX_PERSISTED_SPOKEN_IDS);
      persistedIdsCache = capped;
      persistedIdsSetCache = new Set(capped);
      for (const id of capped) {
        spokenMessageIds.add(id);
      }
    } else {
      // Self-heal corruption: stored value is not an array, remove it once so next load starts clean
      try {
        storage.removeItem(SPOKEN_MESSAGES_SESSION_STORAGE_KEY);
      } catch {
        // Degrade gracefully
      }
    }
  } catch {
    // Self-heal corruption: parse error or storage error, remove item once
    try {
      storage.removeItem(SPOKEN_MESSAGES_SESSION_STORAGE_KEY);
    } catch {
      // Degrade gracefully
    }
  }

  return { ids: persistedIdsCache, set: persistedIdsSetCache };
}

function writeSessionStorageIds(newIds: string[]): void {
  const validIds: string[] = [];
  for (const id of newIds) {
    if (id && typeof id === "string") {
      validIds.push(id);
    }
  }
  if (validIds.length === 0) return;

  const { ids, set } = loadSessionStorageCache();
  let changed = false;

  for (const id of validIds) {
    if (!set.has(id)) {
      ids.push(id);
      set.add(id);
      changed = true;
    }
  }

  if (!changed) return;

  // Bound persisted list to MAX_PERSISTED_SPOKEN_IDS (FIFO, oldest evicted from head)
  if (ids.length > MAX_PERSISTED_SPOKEN_IDS) {
    const excess = ids.length - MAX_PERSISTED_SPOKEN_IDS;
    const dropped = ids.splice(0, excess);
    for (const d of dropped) {
      set.delete(d);
    }
  }

  const storage = getSessionStorage();
  if (!storage) return;

  try {
    storage.setItem(SPOKEN_MESSAGES_SESSION_STORAGE_KEY, JSON.stringify(ids));
  } catch {
    // Degrade gracefully to in-memory only tracking
  }
}

/**
 * Mark a single message/turn as spoken or settled history.
 */
export function markMessageSpoken(id: string): void {
  if (!id) return;
  spokenMessageIds.add(id);
  writeSessionStorageIds([id]);
}

/**
 * Forget that *id* was spoken, so it can be played later.
 *
 * Marking happens before playback starts, which is right for "already read"
 * but wrong when playback never happened: the browser's autoplay policy can
 * reject `play()` outright, and a summary burned that way would stay silent
 * for good.
 */
export function unmarkMessageSpoken(id: string): void {
  if (!id) return;
  spokenMessageIds.delete(id);
  const { ids } = loadSessionStorageCache();
  const kept = ids.filter((stored) => stored !== id);
  persistedIdsCache = kept;
  persistedIdsSetCache = new Set(kept);
  const storage = getSessionStorage();
  if (!storage) return;
  try {
    storage.setItem(SPOKEN_MESSAGES_SESSION_STORAGE_KEY, JSON.stringify(kept));
  } catch {
    // Degrade gracefully
  }
}

/**
 * Batch mark multiple messages/turns as spoken or settled history.
 * Performs a single sessionStorage persist across all marked IDs to prevent write churn and array thrashing.
 */
export function markMessagesSpoken(ids: string[]): void {
  const valid: string[] = [];
  for (const id of ids) {
    if (id) {
      spokenMessageIds.add(id);
      valid.push(id);
    }
  }
  if (valid.length > 0) {
    writeSessionStorageIds(valid);
  }
}

export function isMessageSpoken(id: string): boolean {
  if (!id) return false;
  if (spokenMessageIds.has(id)) {
    return true;
  }
  const { set } = loadSessionStorageCache();
  if (set.has(id)) {
    spokenMessageIds.add(id);
    return true;
  }
  return false;
}

export function resetSpokenMessageTracking(): void {
  spokenMessageIds.clear();
  persistedIdsCache = null;
  persistedIdsSetCache = null;
  const storage = getSessionStorage();
  if (storage) {
    try {
      storage.removeItem(SPOKEN_MESSAGES_SESSION_STORAGE_KEY);
    } catch {
      // Degrade gracefully
    }
  }
}

/** Testing helper: simulates a tab reload where in-memory state is wiped but sessionStorage persists. */
export function clearInMemorySpokenTracking(): void {
  spokenMessageIds.clear();
  persistedIdsCache = null;
  persistedIdsSetCache = null;
}

export interface SpeakOptions {
  /**
   * Drop it rather than wait while a voice call is open: the hub's messages,
   * which a hub call says itself.
   */
  dropDuringCall?: boolean;
  /**
   * Wait for the summary being read, even one from this conversation, rather
   * than replace it: the hub's messages are separate news, not revisions.
   */
  waitTurn?: boolean;
}

interface SpeechPlaybackStoreState {
  isSpeaking: boolean;
  speakingItemId: string | null;
  /** Whose summary is being read right now, so a newer one from the SAME
   * conversation can replace it while another conversation's waits. */
  speakingSessionId: string | null;
  speakLiveSummary: (
    itemId: string,
    text: string,
    lang?: string,
    audioUrl?: string,
    sessionId?: string | null,
    options?: SpeakOptions,
  ) => boolean;
  playManual: (itemId: string) => void;
  /**
   * Speak one summary because the reader pressed play.
   *
   * Same choice of voice as autoplay, but none of its gates: a deliberate
   * press is not subject to having-been-spoken, the live window, or waiting
   * behind another conversation's queue.
   */
  speakNow: (
    itemId: string,
    text: string,
    lang?: string,
    audioUrl?: string,
    sessionId?: string | null,
  ) => boolean;
  stop: () => void;
}

/** The server-audio element currently playing, so `stop` can silence it. */
let activeAudio: HTMLAudioElement | null = null;

/**
 * The live session currently speaking, if any.
 *
 * Tracked separately from `activeAudio` because stopping it is not pausing an
 * element: the session bills by wall clock until it is hung up, so silencing
 * one without closing it would keep spending on audio nobody hears.
 */
let activeLive: LiveNarration | null = null;

/** Hang up any live session, whether or not it is still speaking. */
function closeActiveLive(): void {
  const live = activeLive;
  activeLive = null;
  live?.stop();
}

/**
 * A recording paused for an open live call, resumed when the call ends.
 *
 * A live-streamed narration cannot wait here: stopping it mid-stream cannot
 * resume from the same offset, so it is hung up like before and only
 * recordings are ever parked.
 */
let pausedForCall: HTMLAudioElement | null = null;

/**
 * Open a live voice call automatically when a narration starts.
 *
 * Default on. The guards stay strict regardless: it fires only when the
 * microphone permission is already granted (it never triggers a permission
 * prompt by itself) and no call is open. An explicit stored `false` opts out.
 */
export const LIVE_AUTO_OPEN_KEY = "omnigent:live-voice-auto-open";

export function readLiveAutoOpen(): boolean {
  if (typeof window === "undefined" || !window.localStorage) return true;
  try {
    return window.localStorage.getItem(LIVE_AUTO_OPEN_KEY) !== "false";
  } catch {
    return true;
  }
}

export function writeLiveAutoOpen(enabled: boolean): void {
  if (typeof window === "undefined" || !window.localStorage) return;
  try {
    window.localStorage.setItem(LIVE_AUTO_OPEN_KEY, enabled ? "true" : "false");
  } catch {
    // The choice then lasts only until the page reloads.
  }
}

/** A summary waiting for the one before it to finish. */
interface QueuedSummary {
  itemId: string;
  text: string;
  lang?: string;
  audioUrl?: string;
  sessionId?: string | null;
  /** When it joined the queue, so a stalled playback cannot strand it. */
  queuedAt?: number;
}

/**
 * Summaries waiting their turn.
 *
 * Two conversations can finish at the same moment, and playing both is two
 * voices at once. The second waits instead. Bounded: past this the reader is
 * being read a backlog, and the newest summaries are the ones worth keeping.
 */
const speechQueue: QueuedSummary[] = [];
const QUEUE_MAX = 5;

/**
 * How long a summary may wait before it is no longer worth hearing. Bounds
 * the case where playback never reports an end and the queue sits for hours.
 * After a long call, an old summary is noise, and the text is already on screen.
 */
const QUEUE_MAX_WAIT_MS = 10 * 60_000;

/** Drop everything waiting (the reader stopped playback, or switched off). */
export function clearSpeechQueue(): void {
  speechQueue.length = 0;
}

let liveConversationOpen = false;
let hangUpLiveConversation: (() => void) | null = null;

/**
 * Register whether a live voice conversation is active.
 *
 * While a call is open, spoken summaries wait in speechQueue instead of playing over
 * or silencing the call. When the call ends, queued summaries are drained and played.
 */
export function setConversationSpeaking(open: boolean, hangUp?: (() => void) | null): void {
  liveConversationOpen = open;
  hangUpLiveConversation = open ? (hangUp ?? null) : null;
  if (!open) {
    // A recording paused for the call resumes where it stopped. The queue
    // waits behind it: isSpeaking is still set for the resumed item, so the
    // next playNextQueued returns early until it finishes.
    const resume = pausedForCall;
    pausedForCall = null;
    if (resume) {
      activeAudio = resume;
      void resume.play().catch(() => {
        if (activeAudio === resume) activeAudio = null;
        playNextQueued(useSpeechPlaybackStore.setState, useSpeechPlaybackStore.getState);
      });
    }
    playNextQueued(useSpeechPlaybackStore.setState, useSpeechPlaybackStore.getState);
  }
}

/** Alias for setConversationSpeaking */
export function registerLiveConversation(open: boolean, hangUp?: (() => void) | null): void {
  setConversationSpeaking(open, hangUp);
}

/**
 * What happens to whatever is already speaking when *el* takes the channel.
 *
 * - `replace`: today's behavior. Everything stops or rewinds; the new sound
 *   is the only thing left speaking. Used when one narration supersedes
 *   another.
 * - `pause`: the current recording pauses in place and resumes where it
 *   stopped when the live call ends. Used when a live conversation (an open
 *   microphone) takes the channel: killing the narration mid-sentence strands
 *   it, while playing over the call lets the voice hear itself.
 * - `drop`: like `pause`, but the paused recording is discarded instead of
 *   resumed. Used when the call itself was opened *because of* that
 *   narration: replaying it after hangup would repeat what the call was about.
 */
export type SpeechTakeover = "replace" | "pause" | "drop";

/**
 * Take sole ownership of the speech channel for *el*.
 *
 * Summary audio reaches the page from two places -- autoplay, which owns its
 * own element, and the read-aloud control, which owns the one in the bubble --
 * and either can start while the other is mid-sentence. That plays the same
 * words twice, offset, which is heard as an echo. Rather than have each caller
 * remember to silence the other, every start comes through here: the host
 * engine stops, every other summary element pauses and rewinds, and *el* is
 * left as the only thing that can be speaking.
 *
 * @param el The element about to play, or `null` to silence everything.
 * @param sessionId Optional session identifier.
 * @param onConflict What to do with audio already playing: `replace` kills
 * it, `pause` parks a recording to resume after the call, `drop` parks
 * nothing and clears the speaking flags so the queue is not wedged.
 */
export function claimSpeechChannel(
  el: HTMLAudioElement | null,
  sessionId?: string | null,
  onConflict: SpeechTakeover = "replace",
): void {
  getSpeechEngine().stop();
  closeActiveLive();
  if (el) el.volume = currentNarrationVolume(sessionId ?? null);
  if (activeAudio && activeAudio !== el) {
    if (onConflict === "replace") {
      activeAudio.pause();
      activeAudio.currentTime = 0;
    } else {
      // Pause in place: the offset survives, so a resume continues the
      // sentence instead of restarting it. Only one element ever waits.
      activeAudio.pause();
      pausedForCall = onConflict === "pause" ? activeAudio : null;
      if (onConflict === "drop") {
        activeAudio = null;
        useSpeechPlaybackStore.setState({ isSpeaking: false, speakingItemId: null });
      }
    }
  }
  // Any other summary player already on the page, whoever started it. The
  // element parked for the call keeps its offset: rewinding it here would
  // defeat the resume.
  if (typeof document !== "undefined") {
    for (const other of document.querySelectorAll("audio[data-summary-audio]")) {
      if (
        other !== el &&
        other !== pausedForCall &&
        other instanceof HTMLAudioElement &&
        !other.paused
      ) {
        other.pause();
        other.currentTime = 0;
      }
    }
  }
  activeAudio = el;
}

/** Whether a summary is being read aloud right now, or its voice is connecting. */
export function isNarrating(): boolean {
  return useSpeechPlaybackStore.getState().isSpeaking;
}

/**
 * Play one summary now, in the voice its session has chosen.
 *
 * Every path ends by advancing the queue, so one that fails silently does not
 * strand the summaries waiting behind it.
 */
function startSummaryPlayback(
  item: QueuedSummary,
  set: (partial: Partial<SpeechPlaybackStoreState>) => void,
  get: () => SpeechPlaybackStoreState,
  // Manual play is the reader asking to listen, not to talk: no auto-open.
  autoOpen = true,
): boolean {
  const { text, sessionId } = item;
  set({ speakingSessionId: sessionId ?? null });

  // A session on the live voice is read by the live voice and nothing else.
  // The local recording is never its fallback: the reader chose live, and
  // hearing the local voice there is the wrong voice, not a rescue.
  if (currentVoiceBackend(sessionId ?? null) === "live") {
    if (!text.trim()) return false;
    startLivePlayback(item, set, get, autoOpen);
    return true;
  }

  return playRecording(item, set, get, autoOpen);
}

/**
 * Play one summary from the recording the server synthesized for it.
 *
 * Only ever reached on the local voice; the live voice never plays recordings.
 */
function playRecording(
  item: QueuedSummary,
  set: (partial: Partial<SpeechPlaybackStoreState>) => void,
  get: () => SpeechPlaybackStoreState,
  autoOpen: boolean,
): boolean {
  const { itemId, audioUrl, sessionId } = item;
  if (audioUrl) {
    const el = new Audio(audioUrl);
    set({ isSpeaking: true, speakingItemId: itemId });
    const clearSpeaking = () => {
      if (get().speakingItemId === itemId) set({ isSpeaking: false, speakingItemId: null });
    };
    const drain = () => playNextQueued(set, get);
    const clear = () => {
      clearSpeaking();
      drain();
    };
    // A session keeps only its newest recordings, so audio can be absent for
    // a summary that still names one. That is a real media error, and the
    // host engine is better than silence.
    const giveUp = () => {
      // A recording that will not load stays silent: the host voice is what
      // the generated one exists to replace, and hearing it is worse than
      // reading the summary that is already on screen.
      el.pause();
      activeAudio = null;
      clear();
    };
    // A natural end means the reader heard the whole thing, so this is the
    // moment for hands-free answering: open the call for a reply before
    // moving on to the next queued summary. An error end means nothing was
    // heard, so it just drains like before.
    el.addEventListener("ended", () => {
      if (get().speakingItemId !== itemId) {
        drain();
        return;
      }
      clearSpeaking();
      void (async () => {
        if (autoOpen && (await maybeAutoOpenLiveCall(sessionId ?? null, itemId))) return;
        drain();
      })();
    });
    el.addEventListener("error", giveUp);
    claimSpeechChannel(el, sessionId);
    // A rejected play() here is usually the browser's autoplay policy, not a
    // broken file. Forget it was spoken so pressing play still works, and so a
    // later summary is not silenced by a queue entry that never ran.
    void el.play().catch(() => {
      unmarkMessageSpoken(itemId);
      clear();
    });
    return true;
  }

  return false; // nothing to play without a recording
}

/**
 * Speak one summary through a live session.
 *
 * Starts asynchronously: the handshake takes a moment, and the queue must
 * not stall behind it. A session that cannot open leaves the summary unread
 * but unmarked, so pressing play tries again; the local recording is never
 * played instead.
 */
function startLivePlayback(
  item: QueuedSummary,
  set: (partial: Partial<SpeechPlaybackStoreState>) => void,
  get: () => SpeechPlaybackStoreState,
  autoOpen: boolean,
): void {
  const { itemId, text, sessionId, lang } = item;
  set({ isSpeaking: true, speakingItemId: itemId });

  const clear = (): void => {
    if (get().speakingItemId === itemId) set({ isSpeaking: false, speakingItemId: null });
    playNextQueued(set, get);
  };

  const engine = getLiveVoiceEngine();
  // The voice speaks the chat's language selector; the summary's own
  // language stands in when the chat has none picked.
  const voiceLanguage = { sessionId: sessionId || undefined, lang: lang || undefined };
  const narrate =
    engine === "gemini"
      ? (summary: string) => narrateViaGeminiLive(summary, voiceLanguage)
      : engine === "unmute"
        ? (summary: string) =>
            narrateViaGeminiLive(summary, { endpoint: unmuteEndpoint(), ...voiceLanguage })
        : (summary: string) => narrateViaLive(summary, voiceLanguage);

  void narrate(text)
    .then((live) => {
      // Between the handshake starting and finishing, the reader may have
      // stopped playback or moved on. Hang up rather than talk over them.
      if (get().speakingItemId !== itemId) {
        live.stop();
        return;
      }
      const el = new Audio();
      el.srcObject = live.stream;
      el.dataset.summaryAudio = "live";
      claimSpeechChannel(el, sessionId);
      activeLive = live;
      // play() resolving is not proof anyone heard anything: autoplay can
      // accept the element and still stay silent. Only a real `playing`
      // event counts the narration as heard for the hands-free handoff below.
      let audible = false;
      el.addEventListener(
        "playing",
        () => {
          audible = true;
        },
        { once: true },
      );
      void el.play().catch((reason: unknown) => {
        // Autoplay refused the stream. Forget it was spoken so the play
        // button still works, and stop billing for audio nobody hears.
        //
        // Say so out loud: silently un-marking looks identical to the
        // feature never having run, and a refused WebRTC stream is a browser
        // policy decision the reader can act on, not a bug in the session.
        console.warn(
          "[omnigent] the browser refused to autoplay the live voice; " +
            "press play on the summary to hear it",
          reason,
        );
        unmarkMessageSpoken(itemId);
        live.stop();
      });
      void live.finished.then(() => {
        if (activeLive === live) activeLive = null;
        el.pause();
        el.srcObject = null;
        if (activeAudio === el) activeAudio = null;
        // Hang up: a spoken-out narration still holds its upstream session,
        // which counts against the live quota and crowds out conversation
        // sessions until the server's runaway cap kills it half an hour later.
        live.stop();
        if (get().speakingItemId !== itemId) {
          playNextQueued(set, get);
          return;
        }
        // Natural end after audible playback: the reader heard it all, so
        // this is the hands-free moment. A finished session that never
        // played (autoplay refusal, stillborn stream) heard nothing: drain.
        set({ isSpeaking: false, speakingItemId: null });
        void (async () => {
          if (autoOpen && audible && (await maybeAutoOpenLiveCall(sessionId ?? null, itemId)))
            return;
          playNextQueued(set, get);
        })();
      });
    })
    .catch(() => {
      // No live session could be opened. The local recording is never the
      // fallback on the live voice; forget it was spoken so play tries again.
      reportNarration({ path: "playback", decision: "live-voice-failed", sessionId, itemId });
      if (get().speakingItemId !== itemId) return;
      unmarkMessageSpoken(itemId);
      clear();
    });
}

/**
 * Open a live voice call because a narration was heard in full, when the
 * reader asked for that behavior.
 *
 * Hands-free answering: the message has been read aloud, so the microphone
 * opens for the reply instead of making them press anything. Fires only on a
 * natural end of audible playback -- never when playback failed, errored, or
 * was refused -- and only when every guard holds: the opt-in is on, the
 * narration belongs to a session, the microphone permission is already
 * granted (this never triggers a permission prompt by itself), and no call
 * is already open. Returns whether a call was opened, so the caller knows
 * whether to drain the queue. Failures are silent by design: the reader can
 * still open the call by hand.
 */
async function maybeAutoOpenLiveCall(sessionId: string | null, itemId: string): Promise<boolean> {
  if (!sessionId || !readLiveAutoOpen()) return false;
  let granted: boolean;
  try {
    const status = await navigator.permissions?.query({
      name: "microphone" as PermissionName,
    });
    granted = status?.state === "granted";
  } catch {
    return false;
  }
  if (!granted) {
    reportNarration({ path: "playback", decision: "auto-open-skipped-no-mic", sessionId, itemId });
    return false;
  }
  const { useLiveConversationStore } = await import("./liveConversation");
  const { conversationRegistry } = await import("../store/conversationRegistry");
  const conversation = useLiveConversationStore.getState();
  if (conversation.sessionId || conversation.connecting) return false;
  reportNarration({ path: "playback", decision: "auto-open-call", sessionId, itemId });
  // The narration names the session, and the session knows its agent: the
  // opened call can hand questions to Claude exactly like a hand-opened one.
  // Without a bound agent this stays null and delegation falls back to saying
  // so out loud instead of failing silently.
  const agentId = conversationRegistry.peek(sessionId)?.getState().boundAgentId ?? null;
  // The triggering narration is dropped rather than resumed after hangup:
  // replaying it would repeat what the call was opened to discuss.
  await conversation.start(sessionId, agentId, { narration: "drop" });
  return true;
}

/** Start the next waiting summary, if the channel just went quiet. */
function playNextQueued(
  set: (partial: Partial<SpeechPlaybackStoreState>) => void,
  get: () => SpeechPlaybackStoreState,
): void {
  if (get().isSpeaking || liveConversationOpen) return;
  let next = speechQueue.shift();
  while (next && next.queuedAt !== undefined && Date.now() - next.queuedAt > QUEUE_MAX_WAIT_MS) {
    next = speechQueue.shift(); // after a long call, an old summary is noise, and the text is already on screen
  }
  if (next) startSummaryPlayback(next, set, get);
}

export const useSpeechPlaybackStore = create<SpeechPlaybackStoreState>((set, get) => ({
  isSpeaking: false,
  speakingItemId: null,
  speakingSessionId: null,

  speakLiveSummary: (
    itemId: string,
    text: string,
    lang?: string,
    audioUrl?: string,
    sessionId?: string | null,
    options?: SpeakOptions,
  ) => {
    // Hard requirement: do NOT autoplay anything into a muted session. The
    // volume control is the switch: zero means the reader wants quiet here.
    if (!isNarrationEnabled(sessionId ?? null)) {
      reportNarration({ path: "playback", decision: "skipped-muted", sessionId, itemId });
      return false;
    }
    // Never treat empty/falsy ID as a valid speaking identity
    if (!itemId) return false;
    // Hard requirement: speak ONLY on newly-arrived live messages.
    if (isMessageSpoken(itemId)) {
      reportNarration({ path: "playback", decision: "skipped-already-spoken", sessionId, itemId });
      return false;
    }
    // The summary ships before its recording, so "no audio yet" means wait,
    // not speak. Reading it in the host's robotic voice is exactly what the
    // generated one exists to replace -- and marking it spoken here would bury
    // the real recording when it lands seconds later.
    //
    // The live voice is the exception: it reads the text itself and never
    // needs a recording. Making it wait for one would hand back the very
    // delay it exists to remove.
    const live = currentVoiceBackend(sessionId ?? null) === "live" && Boolean(text.trim());
    if (!audioUrl && !live) return false;
    markMessageSpoken(itemId);

    if (liveConversationOpen && options?.dropDuringCall) {
      reportNarration({ path: "playback", decision: "dropped-during-call", sessionId, itemId });
      return true;
    }
    // A live conversation is open: wait rather than talk over it or silence the call.
    // The call must never sit silent while billing, and summaries wait until the call ends.
    if (liveConversationOpen) {
      speechQueue.push({ itemId, text, lang, audioUrl, sessionId, queuedAt: Date.now() });
      if (speechQueue.length > QUEUE_MAX) speechQueue.shift();
      return true;
    }

    // Another conversation is being read: wait rather than talk over it. A
    // newer summary from the same conversation still replaces the one playing
    // -- it supersedes it, and hearing the stale one finish helps nobody.
    const otherConversationSpeaking =
      get().isSpeaking && Boolean(sessionId) && get().speakingSessionId !== (sessionId ?? null);
    if (otherConversationSpeaking || (options?.waitTurn && get().isSpeaking)) {
      speechQueue.push({ itemId, text, lang, audioUrl, sessionId, queuedAt: Date.now() });
      if (speechQueue.length > QUEUE_MAX) speechQueue.shift();
      return true;
    }
    return startSummaryPlayback({ itemId, text, lang, audioUrl, sessionId }, set, get);
  },

  speakNow: (itemId, text, lang, audioUrl, sessionId) => {
    if (!itemId) return false;
    // The reader pressing play during a call means they want the summary, not the call.
    // Hang up the open call first: a silent call that still bills is the thing we are avoiding.
    if (liveConversationOpen && hangUpLiveConversation) {
      hangUpLiveConversation();
    }
    clearSpeechQueue();
    closeActiveLive();
    markMessageSpoken(itemId);
    // Manual play is listening, not a handoff: never auto-open from it.
    return startSummaryPlayback({ itemId, text, lang, audioUrl, sessionId }, set, get, false);
  },

  playManual: (itemId: string) => {
    // Kept so the control can stop what is playing. Summaries are only ever
    // spoken from their own recording now, so there is nothing to start here.
    if (!itemId) return;
    if (get().speakingItemId === itemId) get().stop();
  },

  stop: () => {
    // The reader asked for quiet: drop what is waiting rather than starting it.
    clearSpeechQueue();
    // A parked recording dies with the quiet request instead of resuming later.
    pausedForCall = null;
    // Pausing a live session would silence it while it kept billing.
    closeActiveLive();
    if (activeAudio) {
      activeAudio.pause();
      activeAudio = null;
    }
    const engine = getSpeechEngine();
    engine.stop();
    set({ isSpeaking: false, speakingItemId: null, speakingSessionId: null });
  },
}));
