// Persisted, per-device preference for read-aloud playback of assistant
// spoken summaries.
//
// When this opt-in preference is on and an assistant message arrives carrying
// a spoken_summary part, the summary is spoken aloud via the browser Web Speech API
// (window.speechSynthesis). It's a device-local playback preference — no account
// or session state changes — so it lives in localStorage like other `*Preferences`
// helpers.

export const SPOKEN_SUMMARY_PLAYBACK_STORAGE_KEY = "omnigent:spoken-summary-playback";

export const DEFAULT_SPOKEN_SUMMARY_PLAYBACK = false;

/**
 * Read the persisted "speak responses" preference. Returns the default (off)
 * when nothing is stored, on a server render (no `window`), or when the stored
 * value is malformed — never throws, so a corrupt entry can't break the app.
 */
export function readSpokenSummaryPlayback(): boolean {
  if (typeof window === "undefined") return DEFAULT_SPOKEN_SUMMARY_PLAYBACK;
  try {
    const raw = window.localStorage.getItem(SPOKEN_SUMMARY_PLAYBACK_STORAGE_KEY);
    if (raw === null) return DEFAULT_SPOKEN_SUMMARY_PLAYBACK;
    return raw === "true";
  } catch {
    return DEFAULT_SPOKEN_SUMMARY_PLAYBACK;
  }
}

/**
 * Persist the "speak responses" preference. Swallows quota/access errors so a
 * failed write can't break the app.
 */
export function writeSpokenSummaryPlayback(value: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(SPOKEN_SUMMARY_PLAYBACK_STORAGE_KEY, value ? "true" : "false");
  } catch {
    // localStorage quota or access errors shouldn't break the app.
  }
}
