// Which voice reads a session aloud: the local model, or OpenAI's live one.
//
// Per session rather than global, for the same reason the volume is: the
// reader is in one conversation at a time and chooses there. It sits beside
// the volume because the two answer neighbouring questions -- whether to
// speak, and in whose voice.
//
// Local is the default, and stays the default. It runs on the reader's own
// GPU for nothing; the live voice is faster and better but bills by the
// second of wall clock, so switching it on is a decision to spend, and that
// decision is never made on the reader's behalf.

import { create } from "zustand";

const KEY_PREFIX = "omnigent:voice-backend:";

/** The voices a session can be read in. */
export type VoiceBackend = "local" | "live";

/** Local synthesis: free, on-device, and slower to start. */
export const DEFAULT_BACKEND: VoiceBackend = "local";

function storageKey(sessionId: string): string {
  return `${KEY_PREFIX}${sessionId}`;
}

function parse(raw: string | null): VoiceBackend {
  return raw === "live" ? "live" : DEFAULT_BACKEND;
}

/**
 * Read a session's chosen voice.
 *
 * Never throws, and an unreadable store reads as local: failing towards the
 * free voice means a broken preference costs nothing, where failing towards
 * the paid one would quietly spend money.
 */
export function readSessionBackend(sessionId: string | null): VoiceBackend {
  if (!sessionId || typeof window === "undefined") return DEFAULT_BACKEND;
  try {
    return parse(window.localStorage.getItem(storageKey(sessionId)));
  } catch {
    return DEFAULT_BACKEND;
  }
}

/** Persist a session's chosen voice. Swallows quota/access errors. */
export function writeSessionBackend(sessionId: string, value: VoiceBackend): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(storageKey(sessionId), value);
  } catch {
    // Degrade gracefully: the session stays on whatever it is using now.
  }
}

interface BackendStoreState {
  /** Session id -> chosen voice, for sessions touched this page load. */
  choices: Record<string, VoiceBackend>;
  get: (sessionId: string | null) => VoiceBackend;
  set: (sessionId: string, value: VoiceBackend) => void;
}

/**
 * The voice for the session being viewed, so controls re-render on a change.
 *
 * The store is the read path rather than `readSessionBackend` directly: a
 * component reading storage would not re-render when another control changes
 * the choice.
 */
export const useVoiceBackendStore = create<BackendStoreState>((set, get) => ({
  choices: {},
  get: (sessionId) => {
    if (!sessionId) return DEFAULT_BACKEND;
    const known = get().choices[sessionId];
    return known === undefined ? readSessionBackend(sessionId) : known;
  },
  set: (sessionId, value) => {
    writeSessionBackend(sessionId, value);
    set((s) => ({ choices: { ...s.choices, [sessionId]: value } }));
  },
}));

/** The voice to use for audio starting now, in the given session. */
export function currentVoiceBackend(sessionId: string | null): VoiceBackend {
  return useVoiceBackendStore.getState().get(sessionId);
}
