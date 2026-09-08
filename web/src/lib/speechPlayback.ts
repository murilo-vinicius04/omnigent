import { create } from "zustand";
import { readSpokenSummaryPlayback } from "./spokenSummaryPlaybackPreferences";

/**
 * Engine interface for text-to-speech playback.
 * Browser speechSynthesis is the default implementation, but alternative
 * engines could be plugged in via setSpeechEngine without altering call sites.
 */
export interface SpeechEngine {
  isSupported: () => boolean;
  speak: (text: string, lang?: string, onEnd?: () => void, onError?: () => void) => void;
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

  speak(text: string, lang?: string, onEnd?: () => void, onError?: () => void): void {
    if (!this.isSupported()) return;
    try {
      window.speechSynthesis.cancel();
      const utterance = new window.SpeechSynthesisUtterance(text);
      if (lang) {
        utterance.lang = lang;
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
      const valid = parsed.filter(
        (x): x is string => typeof x === "string" && x.length > 0,
      );
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
  speakLiveSummary: (itemId: string, text: string, lang?: string) => boolean;
  playManual: (itemId: string, text: string, lang?: string) => void;
  stop: () => void;
}

export const useSpeechPlaybackStore = create<SpeechPlaybackStoreState>((set, get) => ({
  isSpeaking: false,
  speakingItemId: null,

  speakLiveSummary: (itemId: string, text: string, lang?: string) => {
    // Hard requirement: do NOT autoplay anything when toggle is OFF.
    if (!readSpokenSummaryPlayback()) return false;
    // Never treat empty/falsy ID as a valid speaking identity
    if (!itemId) return false;
    // Hard requirement: speak ONLY on newly-arrived live messages.
    if (isMessageSpoken(itemId)) return false;
    markMessageSpoken(itemId);

    const engine = getSpeechEngine();
    if (!engine.isSupported()) return false;

    // Hard requirement: cancel in-flight speech when a new summary arrives.
    engine.stop();

    set({ isSpeaking: true, speakingItemId: itemId });

    engine.speak(
      text,
      lang,
      () => {
        if (get().speakingItemId === itemId) {
          set({ isSpeaking: false, speakingItemId: null });
        }
      },
      () => {
        if (get().speakingItemId === itemId) {
          set({ isSpeaking: false, speakingItemId: null });
        }
      },
    );

    return true;
  },

  playManual: (itemId: string, text: string, lang?: string) => {
    if (!itemId) return;
    const { speakingItemId, stop } = get();
    if (speakingItemId === itemId) {
      stop();
      return;
    }

    const engine = getSpeechEngine();
    if (!engine.isSupported()) return;

    engine.stop();
    set({ isSpeaking: true, speakingItemId: itemId });

    engine.speak(
      text,
      lang,
      () => {
        if (get().speakingItemId === itemId) {
          set({ isSpeaking: false, speakingItemId: null });
        }
      },
      () => {
        if (get().speakingItemId === itemId) {
          set({ isSpeaking: false, speakingItemId: null });
        }
      },
    );
  },

  stop: () => {
    const engine = getSpeechEngine();
    engine.stop();
    set({ isSpeaking: false, speakingItemId: null });
  },
}));
