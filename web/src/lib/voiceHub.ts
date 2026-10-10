/**
 * The voice hub: one call that reaches every session, the hub chat's voice,
 * on whichever live engine is chosen.
 *
 * Gemini and Unmute: their relay (`?mode=hub`) briefs the call on every recent
 * session and runs its tools itself — reading a session's live status, sending
 * it a message, read-only checks — so this side only plays and records audio
 * and shows what the hub is doing. GPT-Live talks to OpenAI from the browser,
 * so the hub conversation's own call carries it. Sends and finished reports
 * land in the hub chat either way. Sending never ends the call.
 */
import { create } from "zustand";

import { startGeminiLive, type GeminiLiveEvent, type GeminiLiveSession } from "@/lib/geminiLive";
import { openHub } from "@/lib/hubApi";
import { useLiveConversationStore } from "@/lib/liveConversation";
import { getLiveVoiceEngine, unmuteEndpoint, type LiveVoiceEngine } from "@/lib/liveVoiceEngine";

export const HUB_ENDPOINT = "/v1/live/gemini/ws?mode=hub";

/** The relay a voice hub call opens, for an engine relayed in Gemini's frames. */
export function hubEndpoint(engine: "gemini" | "unmute"): string {
  if (engine === "gemini") return HUB_ENDPOINT;
  const base = unmuteEndpoint();
  return `${base}${base.includes("?") ? "&" : "?"}mode=hub`;
}

const VOICE_NAMES: Record<LiveVoiceEngine, string> = {
  gpt: "GPT Live",
  gemini: "Gemini",
  unmute: "Unmute",
};

/** What the hub is doing right now, as the status pill shows it. */
export interface HubActivity {
  tool: string;
  /** The session as the call named it. */
  project: string;
  state: "running" | "done";
  /** The finished tool's result, shortened; empty while running. */
  result: string;
}

interface VoiceHubState {
  active: boolean;
  connecting: boolean;
  notice: string | null;
  noticeTone: "info" | "warn";
  activity: HubActivity | null;
  start: () => Promise<void>;
  stop: () => void;
}

/** How long a finished tool stays on the pill. */
const DONE_SHOWN_MS = 6_000;

/** The pill's words for a running hub tool. */
export function describeHubTool(tool: string, project: string): string {
  const where = project || "a session";
  if (tool === "session_status" || tool === "project_status") {
    return project === "all" ? "Looking at every session" : `Looking at ${where}`;
  }
  if (tool === "ask_claude") return `Sending to ${where}'s Claude`;
  if (tool === "check_machine") return `Checking on ${where} (read-only)`;
  return `${tool} — ${where}`;
}

let session: GeminiLiveSession | null = null;
let clearTimer: ReturnType<typeof setTimeout> | undefined;
/** The hub conversation whose GPT-Live call this hub is riding, while it is. */
let gptHubId: string | null = null;
let unwatchGpt: (() => void) | null = null;

export const useVoiceHubStore = create<VoiceHubState>((set, get) => {
  const finish = (notice: string | null, tone: "info" | "warn" = "info") => {
    session = null;
    gptHubId = null;
    unwatchGpt?.();
    unwatchGpt = null;
    if (clearTimer) clearTimeout(clearTimer);
    set({ active: false, connecting: false, activity: null, notice, noticeTone: tone });
  };

  const onEvent = (ev: GeminiLiveEvent) => {
    if (ev.type === "toolCall") {
      const call = ev.calls[0];
      if (!call) return;
      const named = call.args?.session ?? call.args?.project;
      const project = typeof named === "string" ? named : "";
      if (clearTimer) clearTimeout(clearTimer);
      set({ activity: { tool: call.name, project, state: "running", result: "" } });
    } else if (ev.type === "toolDone") {
      set({
        activity: { tool: ev.name, project: ev.project, state: "done", result: ev.output },
      });
      if (clearTimer) clearTimeout(clearTimer);
      clearTimer = setTimeout(() => set({ activity: null }), DONE_SHOWN_MS);
    }
  };

  /** GPT-Live: open the hub conversation's own call, and follow it. */
  const startGptHub = async (voice: string): Promise<void> => {
    let hubId: string;
    try {
      hubId = await openHub();
    } catch (err) {
      finish(`Hub could not start: ${err instanceof Error ? err.message : String(err)}`, "warn");
      return;
    }
    const live = useLiveConversationStore;
    await live.getState().start(hubId, null, { fromVoiceHub: true });
    const opened = live.getState();
    if (opened.sessionId !== hubId) {
      finish(`Hub could not start: ${opened.error ?? `${voice} did not connect`}`, "warn");
      return;
    }
    gptHubId = hubId;
    set({ active: true, connecting: false, notice: "Listening — every session" });
    unwatchGpt = live.subscribe((state) => {
      if (gptHubId !== hubId) return;
      if (state.sessionId !== hubId) {
        finish(state.error ? `Hub ended: ${state.error}` : null, state.error ? "warn" : "info");
        return;
      }
      set({ notice: state.notice ?? "Listening — every session", noticeTone: state.noticeTone });
    });
  };

  return {
    active: false,
    connecting: false,
    notice: null,
    noticeTone: "info",
    activity: null,

    start: async () => {
      if (get().active || get().connecting) return;
      // Two open mics would be two voices answering at once.
      useLiveConversationStore.getState().stop();
      set({ connecting: true, notice: null, noticeTone: "info", activity: null });
      const engine = getLiveVoiceEngine();
      const voice = VOICE_NAMES[engine];
      if (engine === "gpt") {
        await startGptHub(voice);
        return;
      }
      try {
        session = await startGeminiLive({
          endpoint: hubEndpoint(engine),
          onEvent,
          onStateChange: (st) => {
            if (st.state === "ready") set({ notice: "Listening — every session" });
            else if (st.state === "waiting") set({ notice: "Still thinking…" });
            else if (st.state === "nudged") {
              set({ notice: `${voice} went quiet — asking it to answer`, noticeTone: "warn" });
            } else if (st.state === "not-hearing") {
              set({ notice: `${voice} isn't hearing you — check the mic`, noticeTone: "warn" });
            } else if (st.state === "clear") set({ notice: null, noticeTone: "info" });
            else if (st.state === "closed") {
              if (!session) return;
              finish(
                st.kind === "error"
                  ? st.reason === "gemini stopped responding"
                    ? `${voice} stopped answering — start the hub again`
                    : `Hub ended: ${st.reason || "connection lost"}`
                  : null,
                st.kind === "error" ? "warn" : "info",
              );
            }
          },
        });
        set({ active: true, connecting: false });
      } catch (err) {
        finish(`Hub could not start: ${err instanceof Error ? err.message : String(err)}`, "warn");
      }
    },

    stop: () => {
      const open = session;
      session = null;
      open?.stop("page hung up");
      if (gptHubId && useLiveConversationStore.getState().sessionId === gptHubId) {
        useLiveConversationStore.getState().stop();
      }
      finish(null);
    },
  };
});
