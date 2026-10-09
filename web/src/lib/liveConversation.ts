// The open spoken conversation, if there is one.
//
// At most one exists at a time: two would be two voices talking over each
// other, and two meters running. It bills by wall clock for as long as it is
// open, so the only thing that closes it is the reader — nothing here times
// out, and nothing reopens it on their behalf.

import { create } from "zustand";
import {
  checkSpoken,
  delegateSpoken,
  getCompanionState,
  noteCompanion,
  prewarmCompanion,
  type CheckedCommand,
  type CompanionActivity,
} from "./companionApi";
import { claimSpeechChannel, isNarrating, setConversationSpeaking } from "./speechPlayback";
import type { LiveVoiceEngine } from "./liveVoiceEngine";
import { conversationVolume } from "./sessionNarrationVolume";
import { getLiveEngineAdapter, USD_PER_MINUTE, type LiveEngineHandle } from "./liveEngineAdapters";

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

/** How often the companion's live state is read while it is looking something up. */
const CHECK_POLL_MS = 700;

/** Reader silence the call waits for before hanging up after a send. */
const HANGUP_QUIET_MS = 1500;
/** Longest the call stays open after a send waiting for that silence. */
const HANGUP_QUIET_CAP_MS = 12_000;

/** Prefix for what the reader said after the send, forwarded as a second message. */
const FOLLOW_UP_PREFIX = "Also, said right after that on the same call:";

/** Short sign-offs that are not worth a second message to Claude. */
const SIGN_OFF =
  /^\W*(ok|okay|ok then|thanks|thank you|thx|bye|goodbye|cool|great|right|yes|yeah|no|sim|não|valeu|obrigad[oa]|beleza|tchau|falou|show|isso|tá|ta|blz)\W*$/i;

/** Whether what was said after a send carries anything for Claude. */
export function worthFollowUp(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed || SIGN_OFF.test(trimmed)) return false;
  return trimmed.split(/\s+/).length >= 3;
}

/** What the voice is told after a check, so it can say the result itself. */
export function checkReply(result: {
  answer: string | null;
  needsClaude: string | null;
  commands: CheckedCommand[];
}): string {
  const ran = result.commands.map((c) => c.command);
  const how = ran.length
    ? `Looked it up by running (read-only): ${ran.join("; ")}.`
    : "Nothing was run.";
  if (result.answer) {
    return `${how} Result to tell them, in your own words: ${result.answer}`;
  }
  return (
    `${how} It could not be found out by looking (${result.needsClaude ?? "unknown"}). ` +
    "Nothing on the machine was changed. Tell them plainly, and offer once to send it to Claude."
  );
}

/** The last thing the companion looked up during the call, for the UI. */
export interface LastCheck {
  question: string;
  commands: string[];
  answer: string | null;
  needsClaude: string | null;
  at: number;
}

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
  /** "warn" for a notice that means the call is not working as expected. */
  noticeTone: "info" | "warn";
  /** What the companion is doing right now during a check, or null. */
  companion: CompanionActivity | null;
  /** The last check of this call, so the reader can see what really ran. */
  lastCheck: LastCheck | null;
  start: (sessionId: string, agentId?: string | null) => Promise<void>;
  stop: () => void;
}

let activeHandle: LiveEngineHandle | null = null;
let ticker: ReturnType<typeof setInterval> | undefined;

/** Whether a summary is being read aloud, false where playback is mocked out. */
function narrationPlaying(): boolean {
  try {
    return isNarrating();
  } catch {
    // Mocked in tests without isNarrating
    return false;
  }
}

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
  set({ sessionId: null, engine: null, connecting: false, elapsedS: 0, companion: null });
}

/**
 * Hand something the reader said to Claude, as if they had typed it.
 *
 * Deliberately the same path the composer uses: the message appears in the
 * conversation, the turn starts, and everything downstream -- the summary,
 * the narration -- behaves exactly as it would have.
 */
/** Newest-first budget for the discussion carried into the handoff. */
const MAX_CONTEXT_CHARS = 6000;

/**
 * Compose the handoff message: the question, then the call it came out of.
 *
 * The delegated question is one restated sentence. On its own it strands
 * Claude -- a three-minute discussion becomes "I want this", with nothing
 * saying what "this" is. Both sides of the call are on screen already; they
 * belong in the message too.
 *
 * :param question: The companion's restatement of what the reader asked.
 * :param transcript: The call so far, oldest first.
 * :returns: The text to send, unchanged when there is no discussion to add.
 */
export function composeHandoff(
  question: string,
  transcript: readonly { who: "reader" | "voice"; text: string }[],
): string {
  const lines: string[] = [];
  let budget = MAX_CONTEXT_CHARS;
  // Oldest lines are the ones to drop, so fill from the end and reverse.
  for (let i = transcript.length - 1; i >= 0; i -= 1) {
    const { who, text } = transcript[i];
    const line = `${who === "reader" ? "Me" : "Voice"}: ${text.trim()}`;
    if (!text.trim()) continue;
    if (line.length > budget) break;
    budget -= line.length + 1;
    lines.push(line);
  }
  if (lines.length === 0) return question;
  lines.reverse();
  return `${question}\n\n---\nContext — the voice conversation this came out of:\n\n${lines.join("\n")}`;
}

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
  noticeTone: "info",
  companion: null,
  lastCheck: null,

  start: async (sessionId: string, agentId: string | null = null) => {
    // Opening a second would be two voices and two meters.
    if (get().sessionId || get().connecting) return;
    set({
      connecting: true,
      error: null,
      notice: null,
      noticeTone: "info",
      handedOff: null,
      companion: null,
      lastCheck: null,
    });
    // A delegation waits on the companion, and a cold one can spend most of
    // the time the voice is holding the conversation. Warming it is free.
    void prewarmCompanion(sessionId).catch(() => {
      // Delegation still works against a cold companion, only slower.
    });

    // Pressed mid-reading: the reading carries on under the open mic until the
    // reader talks over it, the way they would interrupt a person.
    const overNarration = narrationPlaying();
    // Set once either side is heard; until then the reading keeps the channel.
    let heard = false;
    let takeOver: (() => void) | null = null;

    let handedOff = false;
    // The reader's last finished sentence, for a delegation that arrives before
    // the transcript of the words it is about.
    let lastReader = "";
    // Both sides of the call, oldest first: the context a handoff carries.
    const transcript: { who: "reader" | "voice"; text: string }[] = [];
    // When the reader was last heard, so the call never hangs up on them.
    let readerHeardAt = 0;

    /** Resolve once the reader has been quiet for `quietMs`, or after `capMs`. */
    const readerQuiet = (quietMs: number, capMs: number): Promise<void> =>
      new Promise<void>((resolve) => {
        const giveUp = Date.now() + capMs;
        const tick = () => {
          const now = Date.now();
          if (now - readerHeardAt >= quietMs || now >= giveUp || !activeHandle) {
            resolve();
            return;
          }
          setTimeout(tick, 200);
        };
        tick();
      });

    /**
     * The voice asked the companion to look something up. It runs read-only
     * commands on the server; while it does, its live state is shown, so the
     * reader sees what is actually running. The call stays open.
     */
    const answerCheck = async (callId: string, asked: string): Promise<void> => {
      const question = asked || lastReader;
      const current = activeHandle;
      if (!current) return;
      if (!question) {
        current.speakBack(
          callId,
          "Nothing was asked, so nothing was run. Ask what they want to know.",
        );
        return;
      }
      set({ companion: { state: "thinking", question, command: null, since: Date.now() / 1000 } });
      let polling = true;
      const poll = (): void => {
        if (!polling) return;
        void getCompanionState(sessionId)
          .then((state) => {
            if (polling && state.activity) set({ companion: state.activity });
          })
          .catch(() => {
            // The status line is a courtesy; the check carries on.
          })
          .finally(() => {
            if (polling) setTimeout(poll, CHECK_POLL_MS);
          });
      };
      poll();
      let result: Awaited<ReturnType<typeof checkSpoken>>;
      try {
        result = await checkSpoken(sessionId, question);
      } finally {
        polling = false;
        set({ companion: null });
      }
      const ranList = result.commands.map((c) => c.command).join("; ");
      set({
        notice: ranList ? `Companion ran (read-only): ${ranList}` : "Companion ran nothing",
        noticeTone: "info",
        lastCheck: {
          question,
          commands: result.commands.map((c) => c.command),
          answer: result.answer,
          needsClaude: result.needsClaude,
          at: Date.now(),
        },
      });
      if (activeHandle !== current) return;
      current.speakBack(callId, checkReply(result));
    };

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
      set({ notice: WORKING_ON_IT, noticeTone: "info" });

      let timer: ReturnType<typeof setTimeout> | undefined;
      const timeoutPromise = new Promise<"timeout">((resolve) => {
        timer = setTimeout(() => resolve("timeout"), DELEGATION_TIMEOUT_MS);
      });

      let result: Awaited<ReturnType<typeof delegateSpoken>> | "timeout";
      try {
        result = await Promise.race([delegateSpoken(sessionId, question), timeoutPromise]);
      } finally {
        if (timer) clearTimeout(timer);
      }

      const current = activeHandle;
      if (!current || handedOff) return;

      if (result === "timeout") {
        set({ notice: NOTICE_NOT_READY, noticeTone: "info" });
        current.speakBack(delegationId, NOT_READY);
        return;
      }

      const decision = result;
      if (!decision.forward && decision.answer) {
        set({ notice: null, noticeTone: "info" });
        current.speakBack(delegationId, decision.answer);
        return;
      }
      handedOff = true;
      const text = composeHandoff(decision.english ?? question, transcript);
      if (!(await handToClaude(text, agentId))) {
        // Hanging up without sending would lose the request entirely.
        handedOff = false;
        set({ notice: NOT_SENT, noticeTone: "info" });
        current.speakBack(delegationId, NOT_SENT);
        return;
      }
      set({ handedOff: text, notice: null });
      const sentAt = transcript.length;
      current.speakBack(delegationId, HANDED_OFF);
      // Long enough for the whole goodbye: hanging up early clips it mid-word.
      await current.untilQuiet(20_000);
      // And never while they are still talking: hanging up mid-sentence lost
      // whatever they added after asking for the send.
      await readerQuiet(HANGUP_QUIET_MS, HANGUP_QUIET_CAP_MS);
      get().stop();
      // Stopping flushes the last words. Anything they said after the send
      // still reaches Claude, as a second message.
      const after = transcript
        .slice(sentAt)
        .filter((line) => line.who === "reader")
        .map((line) => line.text.trim())
        .filter(Boolean)
        .join(" ");
      if (worthFollowUp(after)) {
        void handToClaude(`${FOLLOW_UP_PREFIX} ${after}`, agentId);
      }
    };

    const adapter = getLiveEngineAdapter();
    let handle: LiveEngineHandle;

    try {
      handle = await adapter.open(sessionId, {
        // Written to the ledger so the companion remembers it, the panel shows
        // it, and the next conversation opens knowing what the last one said.
        onUtterance: ({ who, text }) => {
          void noteCompanion(sessionId, who === "reader" ? "question" : "answer", text);
          transcript.push({ who, text });
          if (who === "reader") lastReader = text;
        },
        onDelegation: (delegationId, asked) => void answerDelegation(delegationId, asked),
        onCheck: (callId, asked) => void answerCheck(callId, asked),
        onSpeech: (who) => {
          if (who === "reader") readerHeardAt = Date.now();
          heard = true;
          const claim = takeOver;
          takeOver = null;
          claim?.();
        },
        onError: (error) => {
          set({ error });
        },
        onNotice: (notice, tone) => {
          set({ notice, noticeTone: tone ?? "info" });
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
    const claim = (): void => {
      claimSpeechChannel(handle.audioElement, sessionId);
      // The narration level, except that a muted session's call still plays.
      if (handle.audioElement) handle.audioElement.volume = conversationVolume(sessionId);
    };
    // Either side speaking hands the channel over: the reader interrupting,
    // or the voice starting to answer, which must not play over the reading.
    if (overNarration && !heard && narrationPlaying()) {
      takeOver = () => {
        claim();
        // A reading that connected after the call paused its audio on the way in.
        const el = handle.audioElement;
        if (el?.paused && activeHandle === handle) void el.play().catch(() => {});
      };
    } else {
      claim();
    }
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
