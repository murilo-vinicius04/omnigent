// The open spoken conversation, if there is one.
//
// At most one exists at a time: two would be two voices talking over each
// other, and two meters running. It bills by wall clock for as long as it is
// open, so the only thing that closes it is the reader — nothing here times
// out, and nothing reopens it on their behalf.

import { create } from "zustand";
import { openLiveConversation, type LiveConversation } from "./liveVoice";
import { delegateSpoken, noteCompanion, prewarmCompanion } from "./companionApi";
import { claimSpeechChannel } from "./speechPlayback";

/** Billed rate, mirrored from the server so the meter can be shown. */
export const USD_PER_MINUTE = 0.05;

/** Returned to the voice when a delegated question has gone to Claude. */
const HANDED_OFF =
  "I've sent that to Claude. Its answer will show up in the chat, so I'm ending the call now.";

/** Returned to the voice when a question needed Claude but could not be sent. */
const NOT_SENT =
  "I couldn't send that to Claude from here, so it will need to be typed in the chat.";

interface ConversationStoreState {
  /** Session whose conversation is open, or null when none is. */
  sessionId: string | null;
  /** What was last handed to Claude, so the UI can say so. */
  handedOff: string | null;
  /** True while the handshake is in flight, so the control can say so. */
  connecting: boolean;
  /** Seconds the open session has been billed, ticking while it runs. */
  elapsedS: number;
  /** Why the last attempt failed, for the control to show. */
  error: string | null;
  start: (sessionId: string, agentId?: string | null) => Promise<void>;
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

/**
 * Hand something the reader said to Claude, as if they had typed it.
 *
 * Deliberately the same path the composer uses: the message appears in the
 * conversation, the turn starts, and everything downstream -- the summary,
 * the narration -- behaves exactly as it would have.
 */
async function handToClaude(text: string, agentId: string | null): Promise<boolean> {
  if (!agentId) return false;
  try {
    const { useChatStore } = await import("@/store/chatStore");
    // Already decided by the companion; the typed-message pass could answer it instead.
    await useChatStore.getState().send(text, agentId, undefined, { forceClaude: true });
    return true;
  } catch {
    return false;
  }
}

export const useLiveConversationStore = create<ConversationStoreState>((set, get) => ({
  sessionId: null,
  handedOff: null,
  connecting: false,
  elapsedS: 0,
  error: null,

  start: async (sessionId: string, agentId: string | null = null) => {
    // Opening a second would be two voices and two meters.
    if (get().sessionId || get().connecting) return;
    set({ connecting: true, error: null, handedOff: null });
    // A delegation waits on the companion, and a cold one can spend most of
    // the time the voice is holding the conversation. Warming it is free.
    void prewarmCompanion(sessionId).catch(() => {
      // Delegation still works against a cold companion, only slower.
    });

    let handedOff = false;
    // The reader's last finished sentence, for a delegation that arrives before
    // the transcript of the words it is about.
    let lastReader = "";

    /**
     * GPT-Live could not answer and delegated. The delegation names no task, so
     * the question is what the reader just said. The companion answers it and
     * the voice says that back; or Claude gets it, the voice says so, and the
     * call ends so the meter does not run through Claude's turn.
     */
    const answerDelegation = async (delegationId: string, asked: string): Promise<void> => {
      const question = asked || lastReader;
      if (!question || handedOff) return;
      const decision = await delegateSpoken(sessionId, question);
      const current = active;
      if (!current || handedOff) return;
      if (!decision.forward && decision.answer) {
        current.commentary(delegationId, decision.answer);
        return;
      }
      handedOff = true;
      const text = decision.english ?? question;
      if (!(await handToClaude(text, agentId))) {
        // Hanging up without sending would lose the request entirely.
        handedOff = false;
        current.commentary(delegationId, NOT_SENT);
        return;
      }
      set({ handedOff: text });
      current.commentary(delegationId, HANDED_OFF);
      await current.untilQuiet(true);
      get().stop();
    };

    let live: LiveConversation;
    try {
      live = await openLiveConversation(sessionId, {
        // Nothing else records this: the audio never touches our server. Written
        // to the ledger so the companion remembers it, the panel shows it, and
        // the next conversation opens knowing what the last one said.
        onUtterance: ({ who, text }) => {
          void noteCompanion(sessionId, who === "reader" ? "question" : "answer", text);
          if (who === "reader") lastReader = text;
        },
        onDelegation: (delegationId, asked) => void answerDelegation(delegationId, asked),
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
