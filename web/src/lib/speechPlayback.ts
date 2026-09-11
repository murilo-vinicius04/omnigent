import { create } from "zustand";
import { isNarrationEnabled } from "./sessionNarrationPreferences";
import { currentNarrationVolume } from "./sessionNarrationVolume";

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

interface SpeechPlaybackStoreState {
  isSpeaking: boolean;
  speakingItemId: string | null;
  speakLiveSummary: (
    itemId: string,
    text: string,
    lang?: string,
    audioUrl?: string,
    sessionId?: string | null,
  ) => boolean;
  playManual: (itemId: string) => void;
  stop: () => void;
}

/** The server-audio element currently playing, so `stop` can silence it. */
let activeAudio: HTMLAudioElement | null = null;

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

/** Whose summary is being read right now, so a newer one from the SAME
 * conversation can replace it while another conversation's waits. */
let speakingSessionId: string | null = null;

/**
 * How long a summary may wait before it is no longer worth hearing. Bounds
 * the case where playback never reports an end and the queue sits for hours.
 */
const QUEUE_MAX_WAIT_MS = 5 * 60_000;

/** Drop everything waiting (the reader stopped playback, or switched off). */
export function clearSpeechQueue(): void {
  speechQueue.length = 0;
}

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
 */
export function claimSpeechChannel(el: HTMLAudioElement | null, sessionId?: string | null): void {
  getSpeechEngine().stop();
  if (el) el.volume = currentNarrationVolume(sessionId ?? null);
  if (activeAudio && activeAudio !== el) {
    activeAudio.pause();
    activeAudio.currentTime = 0;
  }
  // Any other summary player already on the page, whoever started it.
  if (typeof document !== "undefined") {
    for (const other of document.querySelectorAll("audio[data-summary-audio]")) {
      if (other !== el && other instanceof HTMLAudioElement && !other.paused) {
        other.pause();
        other.currentTime = 0;
      }
    }
  }
  activeAudio = el;
}

/**
 * Play one summary now from its own recording.
 *
 * Every path ends by advancing the queue, so one that fails silently does not
 * strand the summaries waiting behind it.
 */
function startSummaryPlayback(
  item: QueuedSummary,
  set: (partial: Partial<SpeechPlaybackStoreState>) => void,
  get: () => SpeechPlaybackStoreState,
): boolean {
  const { itemId, audioUrl, sessionId } = item;
  speakingSessionId = sessionId ?? null;
  if (audioUrl) {
    const el = new Audio(audioUrl);
    set({ isSpeaking: true, speakingItemId: itemId });
    const clear = () => {
      if (get().speakingItemId === itemId) set({ isSpeaking: false, speakingItemId: null });
      playNextQueued(set, get);
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
    el.addEventListener("ended", clear);
    el.addEventListener("error", giveUp);
    claimSpeechChannel(el, sessionId);
    // A rejected play() here is usually the browser's autoplay policy, not a
    // broken file. Falling back would answer a blocked good recording with
    // the robotic voice the generated one exists to replace, so stay silent
    // and let the reader press play.
    void el.play().catch(clear);
    return true;
  }

  return false; // nothing to play without a recording
}

/** Start the next waiting summary, if the channel just went quiet. */
function playNextQueued(
  set: (partial: Partial<SpeechPlaybackStoreState>) => void,
  get: () => SpeechPlaybackStoreState,
): void {
  if (get().isSpeaking) return;
  let next = speechQueue.shift();
  while (next && next.queuedAt !== undefined && Date.now() - next.queuedAt > QUEUE_MAX_WAIT_MS) {
    next = speechQueue.shift(); // too late to be worth reading out
  }
  if (next) startSummaryPlayback(next, set, get);
}

export const useSpeechPlaybackStore = create<SpeechPlaybackStoreState>((set, get) => ({
  isSpeaking: false,
  speakingItemId: null,

  speakLiveSummary: (
    itemId: string,
    text: string,
    lang?: string,
    audioUrl?: string,
    sessionId?: string | null,
  ) => {
    // Hard requirement: do NOT autoplay anything when narration is OFF. The
    // session's own switch decides; the device default only fills in for a
    // session the reader has not set either way.
    if (!isNarrationEnabled(sessionId ?? null)) return false;
    // Never treat empty/falsy ID as a valid speaking identity
    if (!itemId) return false;
    // Hard requirement: speak ONLY on newly-arrived live messages.
    if (isMessageSpoken(itemId)) return false;
    // The summary ships before its recording, so "no audio yet" means wait,
    // not speak. Reading it in the host's robotic voice is exactly what the
    // generated one exists to replace -- and marking it spoken here would bury
    // the real recording when it lands seconds later.
    if (!audioUrl) return false;
    markMessageSpoken(itemId);

    // Another conversation is being read: wait rather than talk over it. A
    // newer summary from the same conversation still replaces the one playing
    // -- it supersedes it, and hearing the stale one finish helps nobody.
    const otherConversationSpeaking =
      get().isSpeaking && Boolean(sessionId) && speakingSessionId !== (sessionId ?? null);
    if (otherConversationSpeaking) {
      speechQueue.push({ itemId, text, lang, audioUrl, sessionId, queuedAt: Date.now() });
      if (speechQueue.length > QUEUE_MAX) speechQueue.shift();
      return true;
    }
    return startSummaryPlayback({ itemId, text, lang, audioUrl, sessionId }, set, get);
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
    speakingSessionId = null;
    if (activeAudio) {
      activeAudio.pause();
      activeAudio = null;
    }
    const engine = getSpeechEngine();
    engine.stop();
    set({ isSpeaking: false, speakingItemId: null });
  },
}));
