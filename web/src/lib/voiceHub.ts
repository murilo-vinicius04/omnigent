/**
 * The voice hub: one Gemini call that reaches every project, not one chat.
 *
 * The relay (`/v1/live/gemini/ws?mode=hub`) briefs it on every project and
 * runs its tools itself — reading a project's live status, sending a message
 * into a project's session, read-only checks — so this side only plays and
 * records audio and shows what the hub is doing. Sending never ends the call.
 */
import { create } from "zustand";

import { startGeminiLive, type GeminiLiveEvent, type GeminiLiveSession } from "@/lib/geminiLive";
import { useLiveConversationStore } from "@/lib/liveConversation";

export const HUB_ENDPOINT = "/v1/live/gemini/ws?mode=hub";

/** What the hub is doing right now, as the status pill shows it. */
export interface HubActivity {
  tool: string;
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
  const where = project || "a project";
  if (tool === "project_status") {
    return project === "all" ? "Looking at every project" : `Looking at ${where}`;
  }
  if (tool === "ask_claude") return `Sending to ${where}'s Claude`;
  if (tool === "check_machine") return `Checking on ${where} (read-only)`;
  return `${tool} — ${where}`;
}

let session: GeminiLiveSession | null = null;
let clearTimer: ReturnType<typeof setTimeout> | undefined;

export const useVoiceHubStore = create<VoiceHubState>((set, get) => {
  const finish = (notice: string | null, tone: "info" | "warn" = "info") => {
    session = null;
    if (clearTimer) clearTimeout(clearTimer);
    set({ active: false, connecting: false, activity: null, notice, noticeTone: tone });
  };

  const onEvent = (ev: GeminiLiveEvent) => {
    if (ev.type === "toolCall") {
      const call = ev.calls[0];
      if (!call) return;
      const project = typeof call.args?.project === "string" ? call.args.project : "";
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
      try {
        session = await startGeminiLive({
          endpoint: HUB_ENDPOINT,
          onEvent,
          onStateChange: (st) => {
            if (st.state === "ready") set({ notice: "Listening — every project" });
            else if (st.state === "waiting") set({ notice: "Still thinking…" });
            else if (st.state === "nudged") {
              set({ notice: "Gemini went quiet — asking it to answer", noticeTone: "warn" });
            } else if (st.state === "not-hearing") {
              set({ notice: "Gemini isn't hearing you — check the mic", noticeTone: "warn" });
            } else if (st.state === "clear") set({ notice: null, noticeTone: "info" });
            else if (st.state === "closed") {
              if (!session) return;
              finish(
                st.kind === "error"
                  ? st.reason === "gemini stopped responding"
                    ? "Gemini stopped answering — start the hub again"
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
      finish(null);
    },
  };
});
