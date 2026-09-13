// The open spoken conversation, if there is one.
//
// At most one exists at a time: two would be two voices talking over each
// other, and two meters running. It bills by wall clock for as long as it is
// open, so the only thing that closes it is the reader — nothing here times
// out, and nothing reopens it on their behalf.

import { create } from "zustand";
import { openLiveConversation, type LiveConversation } from "./liveVoice";
import { noteCompanion, routeSpoken } from "./companionApi";
import { claimSpeechChannel } from "./speechPlayback";

/** Billed rate, mirrored from the server so the meter can be shown. */
export const USD_PER_MINUTE = 0.05;

/**
 * How long the reader must stop talking before what they said is routed.
 *
 * Longer than the ledger's own flush, deliberately. A transcript settles
 * after a short pause, which is right for recording but too eager for
 * deciding: a breath in the middle of a sentence would send half a thought
 * to Claude. Fragments that arrive inside this window are joined instead.
 */
const ROUTE_SETTLE_MS = 2500;

/** Told to the voice once something has been sent, since it cannot know. */
const HANDOFF_ANNOUNCEMENT =
  "[From the app, not from them: what they just said has been sent to Claude, " +
  "and this call is ending. Tell them so in one short sentence, in the language " +
  "they are speaking, and say nothing else.]";

/** Told to the voice when it held off on something that stays with it. */
const KEPT_ANNOUNCEMENT =
  "[From the app, not from them: that was not sent to Claude, it is yours. " +
  "Answer them now from the notes you were given, in a sentence or two. If " +
  "the notes do not cover it, say so plainly.]";

/** A reply that holds the floor instead of answering. */
const DEFERRAL =
  /\b(one sec|just a sec|hold on|let'?s see|let me (see|check)|let you know|as soon as i hear|i'?ll check|um segundo|deixa eu ver)\b/i;

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
    // Already routed as speech; the typed-message pass could answer it instead.
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

    // What the reader has said since they last stopped talking. Routed as
    // one thought once they stay quiet, so a mid-sentence breath does not
    // send half of it.
    let pending = "";
    let settle: ReturnType<typeof setTimeout> | undefined;
    let handedOff = false;
    // What the voice has said since the reader started this thought.
    let voiceReply = "";

    const decide = async (): Promise<void> => {
      const said = pending.trim();
      pending = "";
      if (!said || handedOff) return;
      const routing = await routeSpoken(sessionId, said);
      if (handedOff) return;
      if (!routing.forward) {
        // A voice that held off is waiting to be told; left alone it goes quiet
        // and the idle timer hangs up on a question it could have answered.
        if (DEFERRAL.test(voiceReply)) await active?.announce(KEPT_ANNOUNCEMENT);
        return;
      }
      // Claude is needed. Send it exactly as typing it would, then hang up:
      // the meter must not run through however long the turn takes.
      handedOff = true;
      const sent = await handToClaude(routing.english ?? said, agentId);
      if (!sent) {
        handedOff = false;
        return;
      }
      set({ handedOff: routing.english ?? said });
      // The voice cannot know this on its own, so it is told before hanging up.
      await active?.announce(HANDOFF_ANNOUNCEMENT);
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
          if (who === "voice") {
            voiceReply = voiceReply ? `${voiceReply} ${text}` : text;
            return;
          }
          if (handedOff) return;
          // A new thought: whatever the voice said before belongs to the last one.
          if (!pending) voiceReply = "";
          pending = pending ? `${pending} ${text}` : text;
          clearTimeout(settle);
          settle = setTimeout(() => void decide(), ROUTE_SETTLE_MS);
        },
        // Still talking: a thought already waiting is not finished yet.
        onReaderSpeaking: () => {
          if (!pending || handedOff) return;
          clearTimeout(settle);
          settle = setTimeout(() => void decide(), ROUTE_SETTLE_MS);
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
