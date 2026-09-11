// Per-session narration volume.
//
// Like the narrate toggle and unlike the summary language, this is purely a
// playback property: it never reaches the server, because how loud you want to
// be read to is about the room you are in, not about the conversation. Stored
// per session for the same reason narration is — headphones now, speakers next
// window.

import { create } from "zustand";

const KEY_PREFIX = "omnigent:narrate-volume:";

/** Full volume, used when a session has no stored choice. */
export const DEFAULT_VOLUME = 1;

function storageKey(sessionId: string): string {
  return `${KEY_PREFIX}${sessionId}`;
}

function clamp(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_VOLUME;
  return Math.min(1, Math.max(0, value));
}

/**
 * Read the stored volume for a session.
 *
 * Never throws: a blocked or corrupt store reads as full volume rather than
 * silencing narration, which would look identical to the feature being broken.
 */
export function readSessionVolume(sessionId: string | null): number {
  if (!sessionId || typeof window === "undefined") return DEFAULT_VOLUME;
  try {
    const raw = window.localStorage.getItem(storageKey(sessionId));
    return raw === null ? DEFAULT_VOLUME : clamp(Number.parseFloat(raw));
  } catch {
    return DEFAULT_VOLUME;
  }
}

/** Persist a session's volume. Swallows quota/access errors. */
export function writeSessionVolume(sessionId: string, value: number): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(storageKey(sessionId), String(clamp(value)));
  } catch {
    // Degrade gracefully
  }
}

interface VolumeStoreState {
  /** Session id -> chosen volume, for sessions touched this page load. */
  levels: Record<string, number>;
  get: (sessionId: string | null) => number;
  set: (sessionId: string, value: number) => void;
}

/**
 * Volume for the session being viewed, so controls re-render on a change.
 *
 * The store is the read path rather than `readSessionVolume` directly: a
 * component reading storage would not re-render when another control changes
 * the level.
 */
export const useVolumeStore = create<VolumeStoreState>((set, get) => ({
  levels: {},
  get: (sessionId) => {
    if (!sessionId) return DEFAULT_VOLUME;
    const known = get().levels[sessionId];
    return known === undefined ? readSessionVolume(sessionId) : known;
  },
  set: (sessionId, value) => {
    const level = clamp(value);
    writeSessionVolume(sessionId, level);
    set((s) => ({ levels: { ...s.levels, [sessionId]: level } }));
  },
}));

/** The level to apply to audio starting now, for the session being viewed. */
export function currentNarrationVolume(sessionId: string | null): number {
  return useVolumeStore.getState().get(sessionId);
}
