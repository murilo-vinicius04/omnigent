// Adapters for the live voice engines (GPT Live over WebRTC; Gemini Live and the
// local Unmute stack over a server relay that speaks Gemini Live's frames).
// Each adapter normalizes its transport into a shared LiveEngineHandle interface
// so the live conversation store can run a single flow.

import { describeHubTool } from "./hubTools";
import { openLiveConversation, type LiveConversation } from "./liveVoice";
import { startGeminiLive, type GeminiLiveSession, type GeminiLiveEvent } from "./geminiLive";
import { getLiveVoiceEngine, unmuteEndpoint, type LiveVoiceEngine } from "./liveVoiceEngine";
import { conversationVolume, useVolumeStore } from "./sessionNarrationVolume";

/** Billed rate for gpt-live, mirrored from the server so the meter can be shown. */
export const USD_PER_MINUTE = 0.05;

/** How much of the reader's own speech a Gemini delegation carries to Claude. */
const MAX_DELEGATED_CHARS = 4000;

/**
 * Quiet the reader must leave before a send goes out. The voice decides to send
 * the moment it hears "send it to Claude", usually mid-sentence, and the words
 * after it were being dropped; the send now waits until they stop talking.
 */
export const SEND_SETTLE_MS = 1800;
/** Longest a send waits for the reader to stop, so a noisy room cannot hold it. */
export const SEND_SETTLE_MAX_MS = 10_000;

/** Returned to the voice the first time it reaches for Claude, instead of sending. */
/** Tools the server relay answers itself (omnigent/server/voice_hub.py). */
const SERVER_TOOLS = new Set(["claude_status"]);

const CONFIRM_BEFORE_SENDING =
  "Not sent yet. Sending ends the call, so ask them first, in one short " +
  "question, whether to send this to Claude. Call ask_claude again only if " +
  "they say yes; if they say no or keep talking, carry on the conversation.";

/** The voice's last words asked whether to send something to Claude. */
const ASKED_TO_SEND = /\b(claude|cloud|clod)\b[^?]*\?\s*$/i;
/** The voice was cut off while offering to send: no question mark survives. */
const OFFERED_TO_SEND = /\b(claude|cloud|clod|send)\b/i;
/** The reader asked for it themselves ("ask Claude", "send it"): that is the yes. */
const ASKED_FOR_SEND =
  /\b(ask|tell|send|forward|pass)\b[^.?!]{0,40}\b(claude|cloud|clod)\b|\bsend (it|that|this)\b/i;
/** A reply that agrees; one that opens with a refusal does not. */
const SAID_YES = /\b(yes|yeah|yep|yup|sure|ok|okay|go ahead|do it|send it)\b/i;
const SAID_NO = /^\W*(no|nope|not|wait|don't|do not)\b/i;

/** "warn" when the call is not working as the reader expects, else "info". */
export type LiveNoticeTone = "info" | "warn";

/** Shown while the voice hears nothing: the reader is talking to themselves. */
export const notHearingNotice = (voice: string): string =>
  `${voice} isn't hearing you — nothing you said came through`;
/** Shown while a reply's audio arrives slower than it plays. */
export const laggingNotice = (voice: string): string => `${voice}'s audio is lagging — expect gaps`;
export const NOT_HEARING_NOTICE = notHearingNotice("Gemini");
export const LAGGING_NOTICE = laggingNotice("Gemini");

export interface LiveEngineCallbacks {
  /** Emitted when a complete utterance is spoken by either the reader or the voice. */
  onUtterance: (utterance: { who: "reader" | "voice"; text: string }) => void;
  /** Emitted when the voice model delegates a question. */
  onDelegation: (id: string, question: string) => void;
  /**
   * Emitted when the voice asks the companion to look something up on the
   * machine (read-only). Answer it with `speakBack`; the call stays open.
   * Engines without the tool never emit it.
   */
  onCheck?: (id: string, question: string) => void;
  /** Emitted whenever either side is heard: the reader's words, or the voice's. */
  onSpeech?: (who: "reader" | "voice") => void;
  /** Emitted on a fatal or unexpected session error. */
  onError?: (error: string) => void;
  /** Emitted on a deliberate policy or lifecycle notice (e.g. idle timeout, session cap). */
  onNotice?: (notice: string | null, tone?: LiveNoticeTone) => void;
  /**
   * Emitted when the relay itself sent the request to a project (the hub's
   * call): the call should end once the voice has said so.
   */
  onRelaySent?: (project: string) => void;
}

export interface LiveEngineHandle {
  /** Which engine is running the conversation. */
  readonly engine: LiveVoiceEngine;
  /** Hang up the conversation. Safe to call repeatedly. */
  readonly stop: () => void;
  /** Elapsed duration in seconds since the conversation opened. */
  readonly elapsedS: () => number;
  /** Return an answer or handoff notification back to the voice model. */
  readonly speakBack: (id: string, text: string) => void;
  /** Wait until the voice has finished speaking (with optional timeout cap). */
  readonly untilQuiet: (capMs?: number) => Promise<void>;
  /** The HTML audio element holding the live stream (GPT), or null (Gemini). */
  readonly audioElement: HTMLAudioElement | null;
  /** Cost per minute in USD, or null if unbilled / billed differently. */
  readonly costPerMinuteUsd: number | null;
  /** Resolves when the conversation has closed. */
  readonly closed: Promise<void>;
  /** Whether this call is the hub's voice, reaching every session. */
  readonly isHub: () => boolean;
  /**
   * Have the voice tell them news unprompted (a session finished). Only for an
   * engine whose news the page relays itself; the relays tell theirs directly.
   */
  readonly announce?: (text: string) => void;
}

export interface LiveEngineAdapter {
  readonly engine: LiveVoiceEngine;
  open: (sessionId: string, callbacks: LiveEngineCallbacks) => Promise<LiveEngineHandle>;
}

/** GPT Live adapter: WebRTC connection with OpenAI Realtime API. */
export const gptEngineAdapter: LiveEngineAdapter = {
  engine: "gpt",
  open: async (sessionId: string, callbacks: LiveEngineCallbacks): Promise<LiveEngineHandle> => {
    let live: LiveConversation;
    live = await openLiveConversation(sessionId, {
      onUtterance: callbacks.onUtterance,
      onDelegation: callbacks.onDelegation,
      onSpeech: callbacks.onSpeech,
    });

    const audio = new Audio();
    audio.srcObject = live.stream;
    audio.dataset.summaryAudio = "conversation";

    return {
      engine: "gpt",
      stop: () => {
        live.stop();
        if (audio) {
          audio.pause();
          audio.srcObject = null;
        }
      },
      elapsedS: () => live.elapsedS(),
      speakBack: (id: string, text: string) => {
        live.commentary(id, text);
      },
      untilQuiet: async () => {
        await live.untilQuiet(true);
      },
      audioElement: audio,
      costPerMinuteUsd: USD_PER_MINUTE,
      closed: live.closed,
      isHub: () => live.hub,
      announce: (text: string) => live.announce(text),
    };
  },
};

/**
 * Adapter for a voice relayed in Gemini Live's wire format: Gemini itself, or
 * the local Unmute stack, whose server relay translates to the same frames.
 */
function relayedEngineAdapter(
  engine: "gemini" | "unmute",
  endpoint: () => string,
  voice: string,
): LiveEngineAdapter {
  return {
    engine,
    open: async (sessionId: string, callbacks: LiveEngineCallbacks): Promise<LiveEngineHandle> => {
      let readerBuf = "";
      let voiceBuf = "";
      let lastReader = "";
      // The voice asked to send and has not asked anything else since, and
      // what the reader has said since that question.
      let askedToSend = false;
      let readerSinceAsk = "";
      // Everything the reader said since the last delegation, as transcribed.
      let readerSinceDelegation = "";
      // Whether the reader has been asked about a handoff yet this call.
      let handoffOffered = false;
      let waitForTurnCompleteResolve: (() => void) | null = null;
      // Which tool each pending call id belongs to, so its answer goes back
      // under the right name.
      const toolNames = new Map<string, string>();
      // A send the reader agreed to, waiting for them to finish talking.
      let pendingSend: {
        id: string;
        asked: string;
        deadline: number;
        timer: ReturnType<typeof setTimeout> | null;
      } | null = null;
      let closed = false;
      let settleClosed: () => void = () => {};
      const closedPromise = new Promise<void>((resolve) => {
        settleClosed = resolve;
      });

      const flushReader = (): void => {
        const text = readerBuf.trim();
        readerBuf = "";
        if (text) {
          lastReader = text;
          callbacks.onUtterance({ who: "reader", text });
        }
      };

      const flushVoice = (cutOff = false): void => {
        const text = voiceBuf.trim();
        voiceBuf = "";
        if (text) {
          if (ASKED_TO_SEND.test(text) || (cutOff && OFFERED_TO_SEND.test(text))) {
            askedToSend = true;
            readerSinceAsk = "";
          } else if (text.endsWith("?")) {
            askedToSend = false;
          }
          callbacks.onUtterance({ who: "voice", text });
        }
      };

      // The voice already asked to send and the reader said yes: asking again
      // is the double question, so the send goes through.
      // Or the reader asked for the send in their own words: asking them back is
      // the same double question.
      const alreadyAgreed = (): boolean => {
        if (ASKED_FOR_SEND.test(readerSinceDelegation)) return true;
        const reply = readerSinceAsk.trim();
        return askedToSend && SAID_YES.test(reply) && !SAID_NO.test(reply);
      };

      let started: GeminiLiveSession;

      // Send now: everything they said since the last send, including what
      // they kept saying after asking for it.
      const fireSend = (): void => {
        const pending = pendingSend;
        if (!pending) return;
        pendingSend = null;
        if (pending.timer) clearTimeout(pending.timer);
        flushReader();
        const spoken = readerSinceDelegation.trim();
        readerSinceDelegation = "";
        callbacks.onDelegation(pending.id, spoken || pending.asked || lastReader);
      };

      // (Re)start the quiet timer; every new word from the reader pushes it back.
      const settleSend = (): void => {
        if (!pendingSend) return;
        if (pendingSend.timer) clearTimeout(pendingSend.timer);
        const wait = Math.max(0, Math.min(SEND_SETTLE_MS, pendingSend.deadline - Date.now()));
        pendingSend.timer = setTimeout(fireSend, wait);
      };

      const closeSession = (errorReason?: string | null): void => {
        if (closed) return;
        closed = true;
        if (pendingSend?.timer) clearTimeout(pendingSend.timer);
        pendingSend = null;
        flushVoice();
        flushReader();
        if (waitForTurnCompleteResolve) {
          waitForTurnCompleteResolve();
          waitForTurnCompleteResolve = null;
        }
        if (errorReason) {
          callbacks.onError?.(errorReason);
        }
        started?.stop(errorReason || "page hung up");
        settleClosed();
      };

      let relayAnswersTools = false;
      const handleEvent = (ev: GeminiLiveEvent): void => {
        if (ev.type === "inputTranscript") {
          if (ev.text.trim()) callbacks.onSpeech?.("reader");
          readerBuf += ev.text;
          readerSinceAsk += ev.text;
          lastReader = readerBuf.trim();
          readerSinceDelegation = (readerSinceDelegation + ev.text).slice(-MAX_DELEGATED_CHARS);
          if (pendingSend && ev.text.trim()) settleSend();
        } else if (ev.type === "outputTranscript") {
          if (ev.text.trim()) callbacks.onSpeech?.("voice");
          if (readerBuf) {
            flushReader();
          }
          voiceBuf += ev.text;
        } else if (ev.type === "audio") {
          callbacks.onSpeech?.("voice");
        } else if (ev.type === "interrupted" || ev.type === "turnComplete") {
          flushVoice(ev.type === "interrupted");
          if (ev.type === "turnComplete" && waitForTurnCompleteResolve) {
            waitForTurnCompleteResolve();
            waitForTurnCompleteResolve = null;
          }
        } else if (ev.type === "session") {
          // In the hub conversation the relay runs every tool itself.
          relayAnswersTools = ev.hub;
        } else if (ev.type === "toolDone") {
          if (relayAnswersTools) {
            callbacks.onNotice?.(null);
            // The words that asked for the send are in the transcript before
            // it: otherwise they read as said after it, and went to the hub a
            // second time when the call ended (19:07 and 19:12).
            if (ev.sent) flushReader();
            if (ev.sent) callbacks.onRelaySent?.(ev.project);
          }
        } else if (ev.type === "toolCall") {
          // A tool call ends the reader's turn: what they said led to it.
          if (relayAnswersTools) flushReader();
          for (const call of ev.calls) {
            // The relay answers these itself; a reply from here would be a second one.
            if (relayAnswersTools) {
              // Said on screen while it runs: a check can take most of a minute.
              const named = call.args?.session ?? call.args?.project;
              const project = typeof named === "string" ? named : "";
              callbacks.onNotice?.(`${describeHubTool(call.name, project)}…`, "info");
              continue;
            }
            if (SERVER_TOOLS.has(call.name)) continue;
            if (call.id) toolNames.set(call.id, call.name);
            if (call.name === "check_machine" && callbacks.onCheck) {
              const asked =
                typeof call.args?.question === "string" ? call.args.question.trim() : "";
              callbacks.onCheck(call.id ?? "", asked || lastReader);
              continue;
            }
            if (call.name !== "ask_claude") {
              started.sendToolResponse([
                {
                  id: call.id,
                  name: call.name,
                  response: { output: `Tool '${call.name}' is not available.` },
                },
              ]);
            } else if (!handoffOffered && !alreadyAgreed()) {
              // Sending ends the call, so it is the reader's decision. Asking for
              // that in the prompt was not enough: told "can you check the
              // documentation", the model read the request itself as the yes and
              // sent mid-discussion. So a first call it did not ask about buys the
              // question instead. The transcript is deliberately left intact,
              // because the send that follows still needs it.
              handoffOffered = true;
              started.sendToolResponse([
                {
                  id: call.id,
                  name: call.name,
                  response: { output: CONFIRM_BEFORE_SENDING },
                },
              ]);
            } else {
              // Only a call's first hand-off is ever questioned.
              handoffOffered = true;
              // Claude gets the reader's own words, as GPT Live sends them. The
              // model's `question` is its paraphrase, used only with no transcript.
              // The send waits until they stop talking: the voice calls the tool
              // on "send it to Claude", and what they say next belongs in it.
              const asked =
                typeof call.args?.question === "string" ? call.args.question.trim() : "";
              if (pendingSend) fireSend();
              pendingSend = {
                id: call.id ?? "",
                asked,
                deadline: Date.now() + SEND_SETTLE_MAX_MS,
                timer: null,
              };
              settleSend();
            }
          }
        }
      };

      started = await startGeminiLive({
        sessionId,
        endpoint: endpoint(),
        volume: conversationVolume(sessionId),
        onEvent: handleEvent,
        onStateChange: (state) => {
          if (state.state === "ready") {
            // The socket opened earlier; this is the first moment the model can
            // actually hear, so it is the one worth telling the reader about.
            callbacks.onNotice?.("Listening — go ahead");
            return;
          }
          if (state.state === "waiting") {
            callbacks.onNotice?.("Still thinking…");
            return;
          }
          if (state.state === "nudged") {
            callbacks.onNotice?.(`${voice} went quiet — asking it to answer`, "warn");
            return;
          }
          if (state.state === "not-hearing") {
            callbacks.onNotice?.(notHearingNotice(voice), "warn");
            return;
          }
          if (state.state === "lagging") {
            callbacks.onNotice?.(laggingNotice(voice), "warn");
            return;
          }
          if (state.state === "clear") {
            callbacks.onNotice?.(null);
            return;
          }
          if (state.state === "closed") {
            if (state.kind === "error") {
              closeSession(
                state.reason === "gemini stopped responding"
                  ? `${voice} stopped answering — start the conversation again`
                  : state.reason || "the conversation ended early",
              );
            } else if (state.kind === "policy" || state.code === 1008) {
              const reasonLower = (state.reason || "").toLowerCase();
              let notice: string;
              if (reasonLower.includes("cap")) {
                notice = "Live conversation ended: session cap reached";
              } else if (
                reasonLower.includes("keepalive") ||
                reasonLower.includes("idle") ||
                reasonLower.includes("silent") ||
                reasonLower.includes("time")
              ) {
                notice = "Live conversation ended: no audio for 60s";
              } else {
                notice = state.reason
                  ? `Live conversation ended: ${state.reason}`
                  : "Live conversation ended";
              }
              callbacks.onNotice?.(notice);
              closeSession(null);
            } else {
              closeSession(null);
            }
          }
        },
      });

      const openedAt = Date.now();
      // The slider moves the call too, as it does a reading mid-sentence.
      const unsubscribeVolume = useVolumeStore.subscribe(() =>
        started.setVolume(conversationVolume(sessionId)),
      );
      void closedPromise.then(unsubscribeVolume);

      return {
        engine,
        stop: () => {
          closeSession(null);
        },
        elapsedS: () => Math.round((Date.now() - openedAt) / 1000),
        speakBack: (id: string, text: string) => {
          started.sendToolResponse([
            {
              id: id || undefined,
              name: toolNames.get(id) ?? "ask_claude",
              response: { output: text },
            },
          ]);
          toolNames.delete(id);
        },
        untilQuiet: (capMs = 10_000) =>
          new Promise<void>((resolve) => {
            if (closed) {
              resolve();
              return;
            }
            let timer: ReturnType<typeof setTimeout> | null = null;
            let settled = false;

            const finish = () => {
              if (settled) return;
              settled = true;
              if (timer) {
                clearTimeout(timer);
                timer = null;
              }
              if (waitForTurnCompleteResolve === onTurnComplete) {
                waitForTurnCompleteResolve = null;
              }
              resolve();
            };

            timer = setTimeout(finish, capMs);

            const onTurnComplete = () => {
              if (timer) clearTimeout(timer);
              timer = setTimeout(finish, capMs);
              void started?.untilPlaybackDrained().then(finish);
            };

            waitForTurnCompleteResolve = onTurnComplete;
          }),
        audioElement: null,
        costPerMinuteUsd: null,
        closed: closedPromise,
        isHub: () => relayAnswersTools,
      };
    },
  };
}

/** Gemini Live adapter: relayed WebSocket connection with Gemini Live API. */
export const geminiEngineAdapter = relayedEngineAdapter(
  "gemini",
  () => "/v1/live/gemini/ws",
  "Gemini",
);

/** Local Kyutai Unmute: speech on this machine's GPU, Omnigent's model as the brain. */
export const unmuteEngineAdapter = relayedEngineAdapter("unmute", unmuteEndpoint, "Unmute");

/** Select the live engine adapter by engine name, defaulting to the stored preference. */
export function getLiveEngineAdapter(engine?: LiveVoiceEngine): LiveEngineAdapter {
  const chosen = engine ?? getLiveVoiceEngine();
  if (chosen === "gemini") return geminiEngineAdapter;
  if (chosen === "unmute") return unmuteEngineAdapter;
  return gptEngineAdapter;
}
