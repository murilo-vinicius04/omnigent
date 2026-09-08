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

/** Set of message IDs that have already been spoken or marked as historical. */
const spokenMessageIds = new Set<string>();

export function markMessageSpoken(id: string): void {
  spokenMessageIds.add(id);
}

export function isMessageSpoken(id: string): boolean {
  return spokenMessageIds.has(id);
}

export function resetSpokenMessageTracking(): void {
  spokenMessageIds.clear();
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
