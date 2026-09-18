// Adapters for the live voice engines (GPT Live over WebRTC and Gemini Live over WebSocket).
// Each adapter normalizes its transport into a shared LiveEngineHandle interface
// so the live conversation store can run a single flow.

import { openLiveConversation, type LiveConversation } from "./liveVoice";
import { startGeminiLive, type GeminiLiveSession, type GeminiLiveEvent } from "./geminiLive";
import { getLiveVoiceEngine, type LiveVoiceEngine } from "./liveVoiceEngine";

/** Billed rate for gpt-live, mirrored from the server so the meter can be shown. */
export const USD_PER_MINUTE = 0.05;

/** How much of the reader's own speech a Gemini delegation carries to Claude. */
const MAX_DELEGATED_CHARS = 4000;

/** Silent-session reopens before the conversation is handed back to the reader. */
const MAX_RECONNECTS = 2;

export interface LiveEngineCallbacks {
  /** Emitted when a complete utterance is spoken by either the reader or the voice. */
  onUtterance: (utterance: { who: "reader" | "voice"; text: string }) => void;
  /** Emitted when the voice model delegates a question. */
  onDelegation: (id: string, question: string) => void;
  /** Emitted on a fatal or unexpected session error. */
  onError?: (error: string) => void;
  /** Emitted on a deliberate policy or lifecycle notice (e.g. idle timeout, session cap). */
  onNotice?: (notice: string) => void;
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
    };
  },
};

/** Gemini Live adapter: relayed WebSocket connection with Gemini Live API. */
export const geminiEngineAdapter: LiveEngineAdapter = {
  engine: "gemini",
  open: async (sessionId: string, callbacks: LiveEngineCallbacks): Promise<LiveEngineHandle> => {
    let readerBuf = "";
    let voiceBuf = "";
    let lastReader = "";
    // Everything the reader said since the last delegation, as transcribed.
    let readerSinceDelegation = "";
    let waitForTurnCompleteResolve: (() => void) | null = null;
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

    const flushVoice = (): void => {
      const text = voiceBuf.trim();
      voiceBuf = "";
      if (text) {
        callbacks.onUtterance({ who: "voice", text });
      }
    };

    let started: GeminiLiveSession;

    const closeSession = (errorReason?: string | null): void => {
      if (closed) return;
      closed = true;
      flushVoice();
      flushReader();
      if (waitForTurnCompleteResolve) {
        waitForTurnCompleteResolve();
        waitForTurnCompleteResolve = null;
      }
      if (errorReason) {
        callbacks.onError?.(errorReason);
      }
      started?.stop();
      settleClosed();
    };

    const handleEvent = (ev: GeminiLiveEvent): void => {
      if (ev.type === "inputTranscript") {
        readerBuf += ev.text;
        lastReader = readerBuf.trim();
        readerSinceDelegation = (readerSinceDelegation + ev.text).slice(-MAX_DELEGATED_CHARS);
      } else if (ev.type === "outputTranscript") {
        if (readerBuf) {
          flushReader();
        }
        voiceBuf += ev.text;
      } else if (ev.type === "interrupted" || ev.type === "turnComplete") {
        flushVoice();
        if (ev.type === "turnComplete" && waitForTurnCompleteResolve) {
          waitForTurnCompleteResolve();
          waitForTurnCompleteResolve = null;
        }
      } else if (ev.type === "toolCall") {
        for (const call of ev.calls) {
          if (call.name !== "ask_claude") {
            started.sendToolResponse([
              {
                id: call.id,
                name: call.name,
                response: { output: `Tool '${call.name}' is not available.` },
              },
            ]);
          } else {
            // Claude gets the reader's own words, as GPT Live sends them. The
            // model's `question` is its paraphrase, used only with no transcript.
            const spoken = readerSinceDelegation.trim();
            readerSinceDelegation = "";
            const asked = typeof call.args?.question === "string" ? call.args.question.trim() : "";
            callbacks.onDelegation(call.id ?? "", spoken || asked || lastReader);
          }
        }
      }
    };

    // Google's live endpoint goes quiet mid-conversation often enough that
    // ending the call would be the wrong answer: reopen and keep going. The
    // reader loses what the model was holding in its head, not the session.
    let reconnects = 0;
    let userStopped = false;

    const connect = async (): Promise<void> => {
      started = await startGeminiLive({
        sessionId,
        onEvent: handleEvent,
        onStateChange: (state) => {
          if (state.state === "closed") {
            if (
              state.reason === "gemini stopped responding" &&
              !userStopped &&
              !closed &&
              reconnects < MAX_RECONNECTS
            ) {
              reconnects += 1;
              callbacks.onNotice?.(
                `Gemini went quiet — reconnecting (${reconnects}/${MAX_RECONNECTS})`,
              );
              void connect().catch(() => {
                closeSession("Gemini stopped answering — start the conversation again");
              });
              return;
            }
            if (state.kind === "error") {
              closeSession(
                state.reason === "gemini stopped responding"
                  ? "Gemini stopped answering — start the conversation again"
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
    };

    await connect();

    const openedAt = Date.now();

    return {
      engine: "gemini",
      stop: () => {
        userStopped = true;
        closeSession(null);
      },
      elapsedS: () => Math.round((Date.now() - openedAt) / 1000),
      speakBack: (id: string, text: string) => {
        started.sendToolResponse([
          {
            id: id || undefined,
            name: "ask_claude",
            response: { output: text },
          },
        ]);
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
    };
  },
};

/** Select the live engine adapter by engine name, defaulting to the stored preference. */
export function getLiveEngineAdapter(engine?: LiveVoiceEngine): LiveEngineAdapter {
  const chosen = engine ?? getLiveVoiceEngine();
  return chosen === "gemini" ? geminiEngineAdapter : gptEngineAdapter;
}
