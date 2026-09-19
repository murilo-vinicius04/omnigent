// Which live voice engine a spoken conversation runs on: OpenAI's gpt-live
// (WebRTC, with agent handoff) or Gemini Live (relayed WS, self-contained).
// A plain localStorage choice so it survives reloads without a server round
// trip; every failure mode falls back to the established GPT path.

import { hostFetch } from "./host";

export type LiveVoiceEngine = "gpt" | "gemini";
export type GeminiAvailability = "configured" | "unconfigured" | "unknown";

const STORAGE_KEY = "omnigent:live-voice-engine";

/** The chosen engine; anything unreadable or unknown means GPT. */
export function getLiveVoiceEngine(): LiveVoiceEngine {
  try {
    return localStorage.getItem(STORAGE_KEY) === "gemini" ? "gemini" : "gpt";
  } catch {
    return "gpt";
  }
}

/** Persist the choice. Storage refusals (private mode, quota) are non-fatal. */
export function setLiveVoiceEngine(engine: LiveVoiceEngine): void {
  try {
    localStorage.setItem(STORAGE_KEY, engine);
  } catch {
    // The choice then lasts only until the page reloads.
  }
}

/** Whether the server has a Gemini key configured (GET /v1/live/gemini/availability). */
export async function fetchGeminiLiveAvailability(): Promise<GeminiAvailability> {
  try {
    const res = await hostFetch("/v1/live/gemini/availability");
    if (!res.ok) return "unknown";
    const data = (await res.json()) as { configured?: unknown };
    return data.configured === true ? "configured" : "unconfigured";
  } catch {
    return "unknown";
  }
}
