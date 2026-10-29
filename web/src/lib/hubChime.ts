/**
 * Discrete notification sound/chime for the Hub.
 *
 * Provides a subtle, pleasant audio chime when a Hub task finishes or
 * when a task needs user attention/response, without requiring voice narration.
 */

import { useEffect } from "react";
import { create } from "zustand";
import { hubLastId, hubUpdates } from "./hubApi";

export const HUB_CHIME_ENABLED_KEY = "omnigent:hub-chime-enabled";
export const HUB_CHIME_VOLUME_KEY = "omnigent:hub-chime-volume";

export const DEFAULT_CHIME_VOLUME = 0.6;
const CHIME_GAIN_SCALE = 0.6;
const HUB_NEWS_WAIT_S = 25;
const HUB_NEWS_RETRY_MS = 3_000;

export function readHubChimeEnabled(): boolean {
  if (typeof window === "undefined" || !window.localStorage) return false;
  return window.localStorage.getItem(HUB_CHIME_ENABLED_KEY) === "true";
}

export function writeHubChimeEnabled(enabled: boolean): void {
  if (typeof window === "undefined" || !window.localStorage) return;
  window.localStorage.setItem(HUB_CHIME_ENABLED_KEY, enabled ? "true" : "false");
}

export function readHubChimeVolume(): number {
  if (typeof window === "undefined" || !window.localStorage) return DEFAULT_CHIME_VOLUME;
  const raw = window.localStorage.getItem(HUB_CHIME_VOLUME_KEY);
  if (raw === null) return DEFAULT_CHIME_VOLUME;
  const num = Number.parseFloat(raw);
  return Number.isFinite(num) ? Math.max(0, Math.min(1, num)) : DEFAULT_CHIME_VOLUME;
}

export function writeHubChimeVolume(volume: number): void {
  if (typeof window === "undefined" || !window.localStorage) return;
  const clamped = Math.max(0, Math.min(1, volume));
  window.localStorage.setItem(HUB_CHIME_VOLUME_KEY, clamped.toString());
}

export type HubChimeKind = "finished" | "attention" | "preview";

let sharedAudioCtx: AudioContext | null = null;

function getAudioContext(): AudioContext | null {
  if (typeof window === "undefined") return null;
  const AudioCtx =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioCtx) return null;
  if (!sharedAudioCtx || sharedAudioCtx.state === "closed") {
    try {
      sharedAudioCtx = new AudioCtx();
    } catch {
      return null;
    }
  }
  return sharedAudioCtx;
}

/**
 * Play a subtle sinusoidal chime using the Web Audio API.
 *
 * Finished: warm two-tone chime (D5 -> A5) with smooth exponential decay.
 * Attention: attentive three-tone chime (A5 -> E5 -> G5) indicating a response is needed.
 */
export function playHubChimeSound(
  kind: HubChimeKind = "finished",
  volume: number = DEFAULT_CHIME_VOLUME,
): boolean {
  const ctx = getAudioContext();
  if (!ctx) return false;

  if (ctx.state === "suspended") {
    // A suspended clock stands still; schedule the tones only once it runs.
    void ctx.resume().then(
      () => {
        if (ctx.state === "running") scheduleChime(ctx, kind, volume);
      },
      () => {},
    );
    return true;
  }

  scheduleChime(ctx, kind, volume);
  return true;
}

/** Wake the shared audio context on the first click/key so later chimes can sound. */
export function unlockHubChimeAudio(): () => void {
  if (typeof window === "undefined") return () => {};
  const wake = (): void => {
    const ctx = getAudioContext();
    if (ctx?.state === "suspended") void ctx.resume().catch(() => {});
  };
  const events = ["pointerdown", "keydown"] as const;
  for (const e of events) window.addEventListener(e, wake, { passive: true });
  wake();
  return () => {
    for (const e of events) window.removeEventListener(e, wake);
  };
}

function scheduleChime(ctx: AudioContext, kind: HubChimeKind, volume: number): void {
  const now = ctx.currentTime;
  const masterGain = ctx.createGain();
  const effectiveVolume = Math.max(0, Math.min(1, volume));
  masterGain.gain.setValueAtTime(effectiveVolume * CHIME_GAIN_SCALE, now);
  masterGain.connect(ctx.destination);

  // Tone definitions: [frequencyHz, startOffsetSec, durationSec]
  const tones: [number, number, number][] =
    kind === "attention"
      ? [
          [880.0, 0.0, 0.18], // A5
          [659.25, 0.09, 0.22], // E5
          [783.99, 0.19, 0.32], // G5
        ]
      : [
          [587.33, 0.0, 0.28], // D5
          [880.0, 0.12, 0.38], // A5
        ];

  for (const [freq, startOffset, duration] of tones) {
    const osc = ctx.createOscillator();
    const noteGain = ctx.createGain();

    osc.type = "sine";
    osc.frequency.setValueAtTime(freq, now + startOffset);

    // Smooth attack and natural exponential decay
    const t0 = now + startOffset;
    const tAttack = t0 + 0.015;
    const tEnd = t0 + duration;

    noteGain.gain.setValueAtTime(0.0001, t0);
    noteGain.gain.exponentialRampToValueAtTime(1.0, tAttack);
    noteGain.gain.exponentialRampToValueAtTime(0.0001, tEnd);

    osc.connect(noteGain);
    noteGain.connect(masterGain);

    osc.start(t0);
    osc.stop(tEnd);

    osc.onended = () => {
      osc.disconnect();
      noteGain.disconnect();
    };
  }
}

export interface HubChimeStoreState {
  enabled: boolean;
  volume: number;
  setEnabled: (enabled: boolean) => void;
  setVolume: (volume: number) => void;
  playChime: (kind?: HubChimeKind) => void;
}

export const useHubChimeStore = create<HubChimeStoreState>((set, get) => ({
  enabled: readHubChimeEnabled(),
  volume: readHubChimeVolume(),

  setEnabled: (enabled: boolean) => {
    writeHubChimeEnabled(enabled);
    set({ enabled });
  },

  setVolume: (volume: number) => {
    const clamped = Math.max(0, Math.min(1, volume));
    writeHubChimeVolume(clamped);
    set({ volume: clamped });
  },

  playChime: (kind: HubChimeKind = "finished") => {
    const state = get();
    // Allow previewing even when disabled so users can test the sound
    if (!state.enabled && kind !== "preview") return;
    playHubChimeSound(kind, state.volume);
  },
}));

/**
 * Start listening for Hub news in the background and play a subtle chime
 * when a task finishes or requires attention.
 *
 * @param onChime Optional callback for testing when a chime is triggered.
 * @returns An unsubscribe function to stop listening.
 */
export function startHubChimeListener(
  onChime?: (kind: "finished" | "attention", text: string) => void,
): () => void {
  let active = true;
  let after: number | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const step = async (): Promise<void> => {
    if (!active) return;
    if (!useHubChimeStore.getState().enabled) {
      // Recheck periodically while disabled so we pick up state changes
      if (active) timer = setTimeout(() => void step(), 1000);
      return;
    }

    let retryMs = 0;
    try {
      if (after === null) {
        after = await hubLastId();
      } else {
        const { updates, lastId } = await hubUpdates(after, HUB_NEWS_WAIT_S);
        after = lastId;
        if (!active) return;

        for (const update of updates) {
          const kind = update.kind === "attention" ? "attention" : "finished";
          playHubChimeSound(kind, useHubChimeStore.getState().volume);
          onChime?.(kind, update.text);
        }
      }
    } catch {
      retryMs = HUB_NEWS_RETRY_MS;
    }

    if (active) {
      timer = setTimeout(() => void step(), retryMs);
    }
  };

  void step();

  return () => {
    active = false;
    if (timer) clearTimeout(timer);
  };
}

/**
 * Hook to run the background news listener while the chime option is enabled.
 * Mounts in the app shell so it listens across the entire app.
 */
export function useHubChimeListener(): void {
  const enabled = useHubChimeStore((s) => s.enabled);
  useEffect(() => {
    if (!enabled) return;
    const stopListening = startHubChimeListener();
    const stopUnlock = unlockHubChimeAudio();
    return () => {
      stopListening();
      stopUnlock();
    };
  }, [enabled]);
}
