// The open spoken conversation, if there is one.
//
// At most one exists at a time: two would be two voices talking over each
// other, and two meters running. It bills by wall clock for as long as it is
// open, so the only thing that closes it is the reader — nothing here times
// out, and nothing reopens it on their behalf.

import { create } from "zustand";
import { delegateSpoken, noteCompanion, prewarmCompanion } from "./companionApi";
import { claimSpeechChannel, setConversationSpeaking } from "./speechPlayback";
import type { LiveVoiceEngine } from "./liveVoiceEngine";
import {
  getLiveEngineAdapter,
  USD_PER_MINUTE,
  type LiveEngineHandle,
} from "./liveEngineAdapters";

export { USD_PER_MINUTE };

/** Returned to the voice when a delegated question has gone to Claude. */
const HANDED_OFF =
  "I've sent that to Claude. Its answer will show up in the chat, so I'm ending the call now.";

/** Returned to the voice when a question needed Claude but could not be sent. */
const NOT_SENT =
  "I couldn't send that to Claude from here, so it will need to be typed in the chat.";

/** Returned to the voice when the companion does not answer within the timeout. */
const NOT_READY =
  "The answer is not ready yet. Please let the user know briefly and offer to send the question to Claude.";

const DELEGATION_TIMEOUT_MS = 15_000;

/** Shown while a delegated question is being routed, when the call is quiet. */
const WORKING_ON_IT = "Working on what you asked…";

/** Shown when that routing ran out of time, so the silence is not a mystery. */
const NOTICE_NOT_READY = "That took too long to route — ask again or type it in the chat.";

interface ConversationStoreState {
  /** Session whose conversation is open, or null when none is. */
  sessionId: string | null;
  /** Which engine is running the conversation, or null when none is. */
  engine: LiveVoiceEngine | null;
  /** What was last handed to Claude, so the UI can say so. */
  handedOff: string | null;
  /** True while the handshake is in flight, so the control can say so. */
  connecting: boolean;
  /** Seconds the open session has been billed, ticking while it runs. */
  elapsedS: number;
  /** Why the last attempt failed, for the control to show. */
  error: string | null;
  /** Non-error notice (e.g. policy close: idle / session cap), for the control to show. */
  notice: string | null;
  start: (sessionId: string, agentId?: string | null) => Promise<void>;
  stop: () => void;
}

let activeHandle: LiveEngineHandle | null = null;
let ticker: ReturnType<typeof setInterval> | undefined;

/** Tear down everything owned here. Safe to call when nothing is open. */
function teardown(set: (partial: Partial<ConversationStoreState>) => void): void {
  try {
    setConversationSpeaking(false);
  } catch {
    // Mocked in tests without setConversationSpeaking
  }
  clearInterval(ticker);
  ticker = undefined;
  const handle = activeHandle;
  activeHandle = null;
  handle?.stop();
  set({ sessionId: null, engine: null, connecting: false, elapsedS: 0 });
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
  engine: null,
  handedOff: null,
  connecting: false,
  elapsedS: 0,
  error: null,
  notice: null,

  start: async (sessionId: string, agentId: string | null = null) => {
    // Opening a second would be two voices and two meters.
    if (get().sessionId || get().connecting) return;
    set({ connecting: true, error: null, notice: null, handedOff: null });
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
     * The voice model could not answer and delegated. The delegation names no
     * task, so the question is what the reader just said. The companion answers
     * it and the voice says that back; or Claude gets it, the voice says so,
     * and the call ends so the meter does not run through Claude's turn.
     */
    const answerDelegation = async (delegationId: string, asked: string): Promise<void> => {
      const question = asked || lastReader;
      if (!question || handedOff) return;

      // The wait that follows is silent on the call, so say so on screen: a
      // quiet voice for several seconds otherwise reads as a dead connection.
      set({ notice: WORKING_ON_IT });

      let timer: ReturnType<typeof setTimeout> | undefined;
      const timeoutPromise = new Promise<"timeout">((resolve) => {
        timer = setTimeout(() => resolve("timeout"), DELEGATION_TIMEOUT_MS);
      });

      let result: Awaited<ReturnType<typeof delegateSpoken>> | "timeout";
      try {
        result = await Promise.race([
          delegateSpoken(sessionId, question),
          timeoutPromise,
        ]);
      } finally {
        if (timer) clearTimeout(timer);
      }

      const current = activeHandle;
      if (!current || handedOff) return;

      if (result === "timeout") {
        set({ notice: NOTICE_NOT_READY });
        current.speakBack(delegationId, NOT_READY);
        return;
      }

      const decision = result;
      if (!decision.forward && decision.answer) {
        set({ notice: null });
        current.speakBack(delegationId, decision.answer);
        return;
      }
      handedOff = true;
      const text = decision.english ?? question;
      if (!(await handToClaude(text, agentId))) {
        // Hanging up without sending would lose the request entirely.
        handedOff = false;
        set({ notice: NOT_SENT });
        current.speakBack(delegationId, NOT_SENT);
        return;
      }
      set({ handedOff: text, notice: null });
      current.speakBack(delegationId, HANDED_OFF);
      // Long enough for the whole goodbye: hanging up early clips it mid-word.
      await current.untilQuiet(20_000);
      get().stop();
    };

    const adapter = getLiveEngineAdapter();
    let handle: LiveEngineHandle;

    try {
      handle = await adapter.open(sessionId, {
        // Written to the ledger so the companion remembers it, the panel shows
        // it, and the next conversation opens knowing what the last one said.
        onUtterance: ({ who, text }) => {
          void noteCompanion(sessionId, who === "reader" ? "question" : "answer", text);
          if (who === "reader") lastReader = text;
        },
        onDelegation: (delegationId, asked) => void answerDelegation(delegationId, asked),
        onError: (error) => {
          set({ error });
        },
        onNotice: (notice) => {
          set({ notice });
        },
      });
    } catch (error) {
      set({ connecting: false, error: String(error) });
      return;
    }

    // The reader may have pressed stop during the handshake.
    if (!get().connecting) {
      handle.stop();
      return;
    }

    activeHandle = handle;
    claimSpeechChannel(handle.audioElement, sessionId);
    try {
      setConversationSpeaking(true, () => get().stop());
    } catch {
      // Mocked in tests without setConversationSpeaking
    }

    if (handle.audioElement) {
      void handle.audioElement.play().catch(() => {
        // Without playback there is nothing to hear, so stop rather than bill.
        teardown(set);
        set({ error: "the browser would not play the conversation" });
      });
    }

    ticker = setInterval(() => set({ elapsedS: handle.elapsedS() }), 1000);
    set({ sessionId, engine: handle.engine, connecting: false, elapsedS: 0 });

    void handle.closed.then(() => {
      if (activeHandle === handle) teardown(set);
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
