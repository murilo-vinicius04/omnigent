// Per-session read-aloud preference.
//
// "Speak responses" in Settings is a device-wide default. This is the switch
// inside the conversation: narration is something a reader wants for this
// session and not the next one -- headphones on now, silence in the next
// window -- so the session value wins wherever one has been set, and the
// device default only decides how a session starts.

import { create } from "zustand";
import {
  readSpokenSummaryPlayback,
  writeSpokenSummaryPlayback,
} from "./spokenSummaryPlaybackPreferences";

const KEY_PREFIX = "omnigent:narrate-session:";

function storageKey(sessionId: string): string {
  return `${KEY_PREFIX}${sessionId}`;
}

/**
 * Read the stored per-session choice, or `null` when the session has none.
 *
 * Never throws: a blocked or corrupt store reads as "no choice made", which
 * falls back to the device default rather than silencing narration outright.
 */
export function readSessionNarration(sessionId: string | null): boolean | null {
  if (!sessionId || typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(storageKey(sessionId));
    if (raw === null) return null;
    return raw === "true";
  } catch {
    return null;
  }
}

/** Persist this session's choice. Swallows quota/access errors. */
export function writeSessionNarration(sessionId: string, value: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(storageKey(sessionId), value ? "true" : "false");
  } catch {
    // Degrade to in-memory for this tab.
  }
}

/**
 * Whether summaries should be read aloud in *this* session.
 *
 * :param sessionId: The conversation being read, or `null` before one loads.
 * :returns: The session's own choice when it has one, else the device default.
 */
export function isNarrationEnabled(sessionId: string | null): boolean {
  const own = readSessionNarration(sessionId);
  return own === null ? readSpokenSummaryPlayback() : own;
}

interface NarrationStoreState {
  /** Session id → choice, mirrored from storage so the UI re-renders on change. */
  overrides: Record<string, boolean>;
  setEnabled: (sessionId: string, value: boolean) => void;
  isEnabled: (sessionId: string | null) => boolean;
}

export const useNarrationStore = create<NarrationStoreState>((set, get) => ({
  overrides: {},

  setEnabled: (sessionId: string, value: boolean) => {
    writeSessionNarration(sessionId, value);
    // Turning it on means "read to me", not "read to me only here": every
    // session the reader has not decided about follows. Turning it off stays
    // local, so one noisy conversation can be silenced without ending
    // narration everywhere (Settings still owns the device-wide switch).
    if (value) writeSpokenSummaryPlayback(true);
    set({ overrides: { ...get().overrides, [sessionId]: value } });
  },

  isEnabled: (sessionId: string | null) => {
    if (!sessionId) return readSpokenSummaryPlayback();
    const cached = get().overrides[sessionId];
    if (cached !== undefined) return cached;
    return isNarrationEnabled(sessionId);
  },
}));
