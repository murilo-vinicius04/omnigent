// Which live voice engine a spoken conversation runs on: OpenAI's gpt-live
// (WebRTC, with agent handoff), Gemini Live (relayed WS, self-contained), or a
// local Kyutai Unmute stack that the server relays in Gemini Live's format.
// A plain localStorage choice so it survives reloads without a server round
// trip; every failure mode falls back to the established GPT path.

import { hostFetch } from "./host";

export type LiveVoiceEngine = "gpt" | "gemini" | "unmute";
export type GeminiAvailability = "configured" | "unconfigured" | "unknown";

const STORAGE_KEY = "omnigent:live-voice-engine";
const UNMUTE_VOICE_KEY = "omnigent:unmute-voice";

/** A voice the local Unmute stack offers, as the server lists it. */
export interface UnmuteVoice {
  id: string;
  label: string;
}

/** The chosen engine; anything unreadable or unknown means GPT. */
export function getLiveVoiceEngine(): LiveVoiceEngine {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored === "gemini" || stored === "unmute" ? stored : "gpt";
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
  return fetchAvailability("/v1/live/gemini/availability");
}

/** The chosen Unmute voice id, or null for the server's default. */
export function getUnmuteVoice(): string | null {
  try {
    return localStorage.getItem(UNMUTE_VOICE_KEY) || null;
  } catch {
    return null;
  }
}

/** Persist the Unmute voice. Storage refusals are non-fatal. */
export function setUnmuteVoice(voice: string): void {
  try {
    localStorage.setItem(UNMUTE_VOICE_KEY, voice);
  } catch {
    // The choice then lasts only until the page reloads.
  }
}

/** The Unmute relay socket path, carrying the chosen voice. */
export function unmuteEndpoint(): string {
  const voice = getUnmuteVoice();
  return voice ? `/v1/live/unmute/ws?voice=${encodeURIComponent(voice)}` : "/v1/live/unmute/ws";
}

/** Whether the local Unmute stack is up, and the voices it offers. */
export async function fetchUnmuteLive(): Promise<{
  availability: GeminiAvailability;
  voices: UnmuteVoice[];
  defaultVoice: string | null;
}> {
  try {
    const res = await hostFetch("/v1/live/unmute/availability");
    if (!res.ok) return { availability: "unknown", voices: [], defaultVoice: null };
    const data = (await res.json()) as {
      configured?: unknown;
      voices?: unknown;
      default?: unknown;
    };
    const voices = Array.isArray(data.voices)
      ? data.voices.filter(
          (v): v is UnmuteVoice =>
            Boolean(v) && typeof v.id === "string" && typeof v.label === "string",
        )
      : [];
    return {
      availability: data.configured === true ? "configured" : "unconfigured",
      voices,
      defaultVoice: typeof data.default === "string" ? data.default : null,
    };
  } catch {
    return { availability: "unknown", voices: [], defaultVoice: null };
  }
}

async function fetchAvailability(path: string): Promise<GeminiAvailability> {
  try {
    const res = await hostFetch(path);
    if (!res.ok) return "unknown";
    const data = (await res.json()) as { configured?: unknown };
    return data.configured === true ? "configured" : "unconfigured";
  } catch {
    return "unknown";
  }
}
