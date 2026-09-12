// The open spoken conversation, if there is one.
//
// At most one exists at a time: two would be two voices talking over each
// other, and two meters running. It bills by wall clock for as long as it is
// open, so the only thing that closes it is the reader — nothing here times
// out, and nothing reopens it on their behalf.

import { create } from "zustand";
import { openLiveConversation, type LiveConversation } from "./liveVoice";
import { noteCompanion } from "./companionApi";
import { claimSpeechChannel } from "./speechPlayback";

/** Billed rate, mirrored from the server so the meter can be shown. */
export const USD_PER_MINUTE = 0.05;

interface ConversationStoreState {
  /** Session whose conversation is open, or null when none is. */
  sessionId: string | null;
  /** True while the handshake is in flight, so the control can say so. */
  connecting: boolean;
  /** Seconds the open session has been billed, ticking while it runs. */
  elapsedS: number;
  /** Why the last attempt failed, for the control to show. */
  error: string | null;
  start: (sessionId: string) => Promise<void>;
  stop: () => void;
}

let active: LiveConversation | null = null;
let audio: HTMLAudioElement | null = null;
let ticker: ReturnType<typeof setInterval> | undefined;

/** Tear down everything owned here. Safe to call when nothing is open. */
function teardown(set: (partial: Partial<ConversationStoreState>) => void): void {
  clearInterval(ticker);
  ticker = undefined;
  active?.stop();
  active = null;
  if (audio) {
    audio.pause();
    audio.srcObject = null;
    audio = null;
  }
  set({ sessionId: null, connecting: false, elapsedS: 0 });
}

export const useLiveConversationStore = create<ConversationStoreState>((set, get) => ({
  sessionId: null,
  connecting: false,
  elapsedS: 0,
  error: null,

  start: async (sessionId: string) => {
    // Opening a second would be two voices and two meters.
    if (get().sessionId || get().connecting) return;
    set({ connecting: true, error: null });
    let live: LiveConversation;
    try {
      live = await openLiveConversation(sessionId, {
        // Nothing else records this: the audio never touches our server. Written
        // to the ledger so the companion remembers it, the panel shows it, and
        // the next conversation opens knowing what the last one said.
        onUtterance: ({ who, text }) => {
          void noteCompanion(sessionId, who === "reader" ? "question" : "answer", text);
        },
      });
    } catch (error) {
      set({ connecting: false, error: String(error) });
      return;
    }
    // The reader may have pressed stop during the handshake.
    if (!get().connecting) {
      live.stop();
      return;
    }
    active = live;
    audio = new Audio();
    audio.srcObject = live.stream;
    audio.dataset.summaryAudio = "conversation";
    // Sole owner of the channel: a summary reading itself over the top of a
    // conversation is two voices at once, and the conversation wins.
    claimSpeechChannel(audio, sessionId);
    void audio.play().catch(() => {
      // Without playback there is nothing to hear, so stop rather than bill.
      teardown(set);
      set({ error: "the browser would not play the conversation" });
    });
    ticker = setInterval(() => set({ elapsedS: live.elapsedS() }), 1000);
    set({ sessionId, connecting: false, elapsedS: 0 });
    void live.closed.then(() => {
      if (active === live) teardown(set);
    });
  },

  stop: () => {
    teardown(set);
  },
}));

/** Whether a conversation is open for this session. */
export function isConversationOpen(sessionId: string | null): boolean {
  const state = useLiveConversationStore.getState();
  return Boolean(sessionId) && state.sessionId === sessionId;
}

/** What the open session has cost so far, in USD. */
export function conversationCostUsd(elapsedS: number): number {
  return (elapsedS / 60) * USD_PER_MINUTE;
}
