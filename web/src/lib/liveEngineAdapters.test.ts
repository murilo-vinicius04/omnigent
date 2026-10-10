import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const openLiveConversation = vi.fn();
const startGeminiLive = vi.fn();

vi.mock("./liveVoice", () => ({
  openLiveConversation: (...args: unknown[]) => openLiveConversation(...args),
  LiveVoiceUnavailable: class extends Error {},
}));

vi.mock("./geminiLive", () => ({
  startGeminiLive: (...args: unknown[]) => startGeminiLive(...args),
}));

import type { GeminiLiveEvent } from "./geminiLive";
import {
  SEND_SETTLE_MAX_MS,
  SEND_SETTLE_MS,
  gptEngineAdapter,
  geminiEngineAdapter,
  getLiveEngineAdapter,
  unmuteEngineAdapter,
  USD_PER_MINUTE,
} from "./liveEngineAdapters";

if (typeof globalThis.MediaStream === "undefined") {
  (globalThis as unknown as { MediaStream: unknown }).MediaStream = function MediaStream() {};
}

function fakeGptConversation() {
  let settle: () => void = () => {};
  const closed = new Promise<void>((resolve) => {
    settle = resolve;
  });
  return {
    stream: new MediaStream(),
    closed,
    elapsedS: () => 42,
    stop: vi.fn(() => settle()),
    untilQuiet: vi.fn(async (_expectSpeech?: boolean) => {}),
    commentary: vi.fn((_id: string, _content: string) => {}),
    thinking: vi.fn(),
  };
}

function fakeGeminiSession() {
  let settleDrain: () => void = () => {};
  const drainPromise = new Promise<void>((resolve) => {
    settleDrain = resolve;
  });
  return {
    stop: vi.fn(),
    sendToolResponse: vi.fn(),
    untilPlaybackDrained: vi.fn(() => drainPromise),
    drain: () => settleDrain(),
  };
}

describe("liveEngineAdapters", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("adapter selection", () => {
    it("returns gptEngineAdapter for gpt and geminiEngineAdapter for gemini", () => {
      expect(getLiveEngineAdapter("gpt")).toBe(gptEngineAdapter);
      expect(getLiveEngineAdapter("gemini")).toBe(geminiEngineAdapter);
      expect(getLiveEngineAdapter("unmute")).toBe(unmuteEngineAdapter);
    });
  });

  describe("unmuteEngineAdapter", () => {
    it("runs the Gemini call flow through the Unmute relay, under its own name", async () => {
      vi.useFakeTimers();
      let opts: {
        endpoint?: string;
        onEvent?: (ev: GeminiLiveEvent) => void;
        onStateChange?: (state: { state: string }) => void;
      } = {};
      const session = fakeGeminiSession();
      startGeminiLive.mockImplementation((o: typeof opts) => {
        opts = o;
        return Promise.resolve(session);
      });
      const onDelegation = vi.fn();
      const onNotice = vi.fn();
      const handle = await unmuteEngineAdapter.open("session_u", {
        onUtterance: vi.fn(),
        onDelegation,
        onNotice,
      });

      expect(handle.engine).toBe("unmute");
      expect(opts.endpoint).toBe("/v1/live/unmute/ws");
      // Same rule as Gemini: the first handoff only buys the question.
      opts.onEvent?.({ type: "toolCall", calls: [{ id: "c1", name: "ask_claude" }] });
      expect(onDelegation).not.toHaveBeenCalled();
      opts.onEvent?.({ type: "toolCall", calls: [{ id: "c2", name: "ask_claude" }] });
      await vi.advanceTimersByTimeAsync(SEND_SETTLE_MS);
      expect(onDelegation).toHaveBeenCalledTimes(1);
      opts.onStateChange?.({ state: "not-hearing" });
      expect(onNotice).toHaveBeenLastCalledWith(
        "Unmute isn't hearing you — nothing you said came through",
        "warn",
      );

      // A chosen voice rides on the socket URL of the next call.
      window.localStorage.setItem("omnigent:unmute-voice", "ex03-happy");
      await unmuteEngineAdapter.open("session_u", { onUtterance: vi.fn(), onDelegation: vi.fn() });
      window.localStorage.removeItem("omnigent:unmute-voice");
      expect(opts.endpoint).toBe("/v1/live/unmute/ws?voice=ex03-happy");
      vi.useRealTimers();
    });
  });

  describe("gptEngineAdapter", () => {
    it("honours the LiveEngineHandle interface with fake transport", async () => {
      const gptLive = fakeGptConversation();
      openLiveConversation.mockResolvedValue(gptLive);

      const onUtterance = vi.fn();
      const onDelegation = vi.fn();
      const handle = await gptEngineAdapter.open("session_1", { onUtterance, onDelegation });

      expect(handle.engine).toBe("gpt");
      expect(handle.costPerMinuteUsd).toBe(USD_PER_MINUTE);
      expect(handle.audioElement).toBeInstanceOf(HTMLAudioElement);
      expect(handle.audioElement?.dataset.summaryAudio).toBe("conversation");
      expect(handle.elapsedS()).toBe(42);

      // speakBack maps to commentary
      handle.speakBack("del_1", "companion answer");
      expect(gptLive.commentary).toHaveBeenCalledWith("del_1", "companion answer");

      // untilQuiet maps to untilQuiet(true)
      await handle.untilQuiet();
      expect(gptLive.untilQuiet).toHaveBeenCalledWith(true);

      // stop cleans up audio and calls stop on underlying
      handle.stop();
      expect(gptLive.stop).toHaveBeenCalled();
    });
  });

  describe("geminiEngineAdapter", () => {
    let capturedOpts: {
      onEvent?: (ev: GeminiLiveEvent) => void;
      onStateChange?: (state: {
        state: string;
        kind?: string;
        code?: number;
        reason?: string;
      }) => void;
    };

    let currentSession: ReturnType<typeof fakeGeminiSession>;

    beforeEach(() => {
      capturedOpts = {};
      currentSession = fakeGeminiSession();
      startGeminiLive.mockImplementation((opts: typeof capturedOpts) => {
        capturedOpts = opts;
        return Promise.resolve(currentSession);
      });
    });

    it("honours the LiveEngineHandle interface with fake transport", async () => {
      const onUtterance = vi.fn();
      const onDelegation = vi.fn();
      const handle = await geminiEngineAdapter.open("session_2", { onUtterance, onDelegation });

      expect(handle.engine).toBe("gemini");
      expect(handle.costPerMinuteUsd).toBeNull();
      expect(handle.audioElement).toBeNull();
      expect(typeof handle.elapsedS()).toBe("number");
    });

    it("asks before the first handoff, and sends on the second call", async () => {
      vi.useFakeTimers();
      const onUtterance = vi.fn();
      const onDelegation = vi.fn();
      await geminiEngineAdapter.open("session_2", { onUtterance, onDelegation });

      capturedOpts.onEvent?.({ type: "inputTranscript", text: "Can you check the docs?" });

      // The reported failure: "can you check the documentation" was read as the
      // go-ahead and sent mid-discussion, ending the call.
      capturedOpts.onEvent?.({ type: "toolCall", calls: [{ id: "c1", name: "ask_claude" }] });
      expect(onDelegation).not.toHaveBeenCalled();
      expect(currentSession.sendToolResponse).toHaveBeenCalledWith([
        expect.objectContaining({
          id: "c1",
          name: "ask_claude",
          response: expect.objectContaining({
            output: expect.stringContaining("ask them first"),
          }),
        }),
      ]);

      // They say yes, the model asks again: now it goes, with the words that
      // prompted it still attached.
      capturedOpts.onEvent?.({ type: "toolCall", calls: [{ id: "c2", name: "ask_claude" }] });
      await vi.advanceTimersByTimeAsync(SEND_SETTLE_MS);
      expect(onDelegation).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(SEND_SETTLE_MS);
      expect(onDelegation).toHaveBeenCalledWith("c2", "Can you check the docs?");
      vi.useRealTimers();
    });

    it("sends at once when the voice already asked and the reader said yes", async () => {
      vi.useFakeTimers();
      const onDelegation = vi.fn();
      await geminiEngineAdapter.open("session_2", { onUtterance: vi.fn(), onDelegation });
      const say = (text: string) => {
        capturedOpts.onEvent?.({ type: "outputTranscript", text });
        capturedOpts.onEvent?.({ type: "turnComplete" });
      };

      capturedOpts.onEvent?.({ type: "inputTranscript", text: "Check my disk usage." });
      say("Got it. Want me to send that to Claude?");
      capturedOpts.onEvent?.({ type: "inputTranscript", text: "Yes." });
      // An acknowledgement in between is not a new question.
      say("Okay, sending it now.");
      capturedOpts.onEvent?.({ type: "toolCall", calls: [{ id: "c1", name: "ask_claude" }] });

      await vi.advanceTimersByTimeAsync(SEND_SETTLE_MS);

      expect(onDelegation).toHaveBeenCalledTimes(1);
      expect(currentSession.sendToolResponse).not.toHaveBeenCalled();
      vi.useRealTimers();
    });

    it("sends at once when the reader asked for it themselves", async () => {
      vi.useFakeTimers();
      // 10-07: "can you ask Claude about that?" drew "do you want me to send
      // that to Claude?", and the page then made it ask a second time.
      const onDelegation = vi.fn();
      await geminiEngineAdapter.open("session_2", { onUtterance: vi.fn(), onDelegation });

      capturedOpts.onEvent?.({
        type: "inputTranscript",
        text: "Yes, so can you ask Claude about that?",
      });
      capturedOpts.onEvent?.({ type: "toolCall", calls: [{ id: "c1", name: "ask_claude" }] });

      await vi.advanceTimersByTimeAsync(SEND_SETTLE_MS);

      expect(onDelegation).toHaveBeenCalledTimes(1);
      expect(currentSession.sendToolResponse).not.toHaveBeenCalled();
      vi.useRealTimers();
    });

    it("takes a yes to an offer to send that was cut off mid-question", async () => {
      vi.useFakeTimers();
      const onDelegation = vi.fn();
      await geminiEngineAdapter.open("session_2", { onUtterance: vi.fn(), onDelegation });

      capturedOpts.onEvent?.({ type: "inputTranscript", text: "Look into the voice freezes." });
      capturedOpts.onEvent?.({
        type: "outputTranscript",
        text: "Do you want me to send that to Claude now, covering both",
      });
      capturedOpts.onEvent?.({ type: "interrupted" });
      capturedOpts.onEvent?.({ type: "inputTranscript", text: "Yes." });
      capturedOpts.onEvent?.({ type: "toolCall", calls: [{ id: "c1", name: "ask_claude" }] });

      await vi.advanceTimersByTimeAsync(SEND_SETTLE_MS);

      expect(onDelegation).toHaveBeenCalledTimes(1);
      expect(currentSession.sendToolResponse).not.toHaveBeenCalled();
      vi.useRealTimers();
    });

    it("still asks when the reader turned the question down or it moved on", async () => {
      const onDelegation = vi.fn();
      await geminiEngineAdapter.open("session_2", { onUtterance: vi.fn(), onDelegation });
      const say = (text: string) => {
        capturedOpts.onEvent?.({ type: "outputTranscript", text });
        capturedOpts.onEvent?.({ type: "turnComplete" });
      };

      say("Should I send that to Claude?");
      capturedOpts.onEvent?.({ type: "inputTranscript", text: "No, not yet, okay?" });
      capturedOpts.onEvent?.({ type: "toolCall", calls: [{ id: "c1", name: "ask_claude" }] });
      expect(onDelegation).not.toHaveBeenCalled();
    });

    it("forgets the question once the voice asks something else", async () => {
      const onDelegation = vi.fn();
      await geminiEngineAdapter.open("session_3", { onUtterance: vi.fn(), onDelegation });
      const say = (text: string) => {
        capturedOpts.onEvent?.({ type: "outputTranscript", text });
        capturedOpts.onEvent?.({ type: "turnComplete" });
      };

      say("Do you want me to send that to Claude?");
      say("Actually, which folder did you mean?");
      capturedOpts.onEvent?.({ type: "inputTranscript", text: "Yeah, the home one." });
      capturedOpts.onEvent?.({ type: "toolCall", calls: [{ id: "c1", name: "ask_claude" }] });
      expect(onDelegation).not.toHaveBeenCalled();
    });

    it("buffers transcript fragments and flushes whole utterances", async () => {
      const onUtterance = vi.fn();
      const onDelegation = vi.fn();
      await geminiEngineAdapter.open("session_2", { onUtterance, onDelegation });

      // Reader speaking in fragments
      capturedOpts.onEvent?.({ type: "inputTranscript", text: "Hello" });
      capturedOpts.onEvent?.({ type: "inputTranscript", text: " world" });
      expect(onUtterance).not.toHaveBeenCalled();

      // Output transcript arriving flushes reader buffer
      capturedOpts.onEvent?.({ type: "outputTranscript", text: "Hi" });
      expect(onUtterance).toHaveBeenCalledTimes(1);
      expect(onUtterance).toHaveBeenNthCalledWith(1, { who: "reader", text: "Hello world" });

      // Voice speaking in fragments
      capturedOpts.onEvent?.({ type: "outputTranscript", text: " there" });
      expect(onUtterance).toHaveBeenCalledTimes(1);

      // Turn complete flushes voice buffer
      capturedOpts.onEvent?.({ type: "turnComplete" });
      expect(onUtterance).toHaveBeenCalledTimes(2);
      expect(onUtterance).toHaveBeenNthCalledWith(2, { who: "voice", text: "Hi there" });
    });

    it("says who is heard, so a reading under the call knows when to stop", async () => {
      const onSpeech = vi.fn();
      await geminiEngineAdapter.open("session_2", {
        onUtterance: vi.fn(),
        onDelegation: vi.fn(),
        onSpeech,
      });

      // Blank fragments are not anyone talking.
      capturedOpts.onEvent?.({ type: "inputTranscript", text: " " });
      expect(onSpeech).not.toHaveBeenCalled();

      capturedOpts.onEvent?.({ type: "inputTranscript", text: "Wait" });
      expect(onSpeech).toHaveBeenLastCalledWith("reader");
      capturedOpts.onEvent?.({ type: "audio", mimeType: "audio/pcm", data: "AAAA" });
      expect(onSpeech).toHaveBeenLastCalledWith("voice");
    });

    it("flushes pending buffers on close and reports error", async () => {
      const onUtterance = vi.fn();
      const onError = vi.fn();
      const handle = await geminiEngineAdapter.open("session_2", {
        onUtterance,
        onDelegation: vi.fn(),
        onError,
      });

      capturedOpts.onEvent?.({ type: "inputTranscript", text: "What is this?" });
      capturedOpts.onEvent?.({ type: "outputTranscript", text: "It is an engine." });
      capturedOpts.onEvent?.({ type: "inputTranscript", text: "And that?" });
      // "What is this?" flushed when outputTranscript arrived.
      expect(onUtterance).toHaveBeenCalledTimes(1);

      capturedOpts.onStateChange?.({
        state: "closed",
        kind: "error",
        code: 1011,
        reason: "server exploded",
      });

      // Voice flushed first, then Reader
      expect(onUtterance).toHaveBeenCalledTimes(3);
      expect(onUtterance).toHaveBeenNthCalledWith(2, { who: "voice", text: "It is an engine." });
      expect(onUtterance).toHaveBeenNthCalledWith(3, { who: "reader", text: "And that?" });
      expect(onError).toHaveBeenCalledWith("server exploded");

      // Stop after close does not double-flush
      handle.stop();
      expect(onUtterance).toHaveBeenCalledTimes(3);
    });

    it("untilQuiet does not resolve on turnComplete while audio is scheduled, and resolves on drain", async () => {
      const handle = await geminiEngineAdapter.open("session_2", {
        onUtterance: vi.fn(),
        onDelegation: vi.fn(),
      });

      handle.speakBack("call_9", "spoken back text");
      expect(currentSession.sendToolResponse).toHaveBeenCalledWith([
        {
          id: "call_9",
          name: "ask_claude",
          response: { output: "spoken back text" },
        },
      ]);

      let quietResolved = false;
      const quietPromise = handle.untilQuiet().then(() => {
        quietResolved = true;
      });

      expect(quietResolved).toBe(false);
      // turnComplete arrives while audio is still draining
      capturedOpts.onEvent?.({ type: "turnComplete" });
      await new Promise<void>((r) => {
        setTimeout(r, 10);
      });
      expect(quietResolved).toBe(false);

      // Playback drain completes
      currentSession.drain();
      await quietPromise;
      expect(quietResolved).toBe(true);
    });

    it("untilQuiet is capped if playback drain never completes", async () => {
      vi.useFakeTimers();
      try {
        const handle = await geminiEngineAdapter.open("session_2", {
          onUtterance: vi.fn(),
          onDelegation: vi.fn(),
        });

        let quietResolved = false;
        void handle.untilQuiet(5000).then(() => {
          quietResolved = true;
        });

        capturedOpts.onEvent?.({ type: "turnComplete" });
        await vi.advanceTimersByTimeAsync(4000);
        expect(quietResolved).toBe(false);

        await vi.advanceTimersByTimeAsync(1000);
        expect(quietResolved).toBe(true);
      } finally {
        vi.useRealTimers();
      }
    });

    it("surfaces non-error notice on policy close distinguishing idle and session cap", async () => {
      const onNotice = vi.fn();
      await geminiEngineAdapter.open("session_2", {
        onUtterance: vi.fn(),
        onDelegation: vi.fn(),
        onNotice,
      });

      capturedOpts.onStateChange?.({
        state: "closed",
        kind: "policy",
        code: 1008,
        reason: "gemini live client keepalive timed out",
      });

      expect(onNotice).toHaveBeenCalledWith("Live conversation ended: no audio for 60s");

      onNotice.mockClear();
      await geminiEngineAdapter.open("session_3", {
        onUtterance: vi.fn(),
        onDelegation: vi.fn(),
        onNotice,
      });

      capturedOpts.onStateChange?.({
        state: "closed",
        kind: "policy",
        code: 1008,
        reason: "gemini live session cap reached",
      });

      expect(onNotice).toHaveBeenCalledWith("Live conversation ended: session cap reached");
    });

    it("tells the reader when Google stops answering, without reopening", async () => {
      const onNotice = vi.fn();
      const onError = vi.fn();
      await geminiEngineAdapter.open("session_stall", {
        onUtterance: vi.fn(),
        onDelegation: vi.fn(),
        onNotice,
        onError,
      });
      const opens = startGeminiLive.mock.calls.length;

      capturedOpts.onStateChange?.({
        state: "closed",
        kind: "error",
        code: 4000,
        reason: "gemini stopped responding",
      });
      await Promise.resolve();

      // Reopening would lose everything the model was holding in its head.
      expect(startGeminiLive.mock.calls.length).toBe(opens);
      expect(onError).toHaveBeenCalledWith(
        "Gemini stopped answering — start the conversation again",
      );
    });

    it("handles toolCall ask_claude by invoking onDelegation", async () => {
      vi.useFakeTimers();
      const onDelegation = vi.fn();
      await geminiEngineAdapter.open("session_2", {
        onUtterance: vi.fn(),
        onDelegation,
      });

      // The first ask_claude only buys the question; prime it so this test
      // still exercises the send.
      capturedOpts.onEvent?.({ type: "toolCall", calls: [{ id: "prime", name: "ask_claude" }] });
      capturedOpts.onEvent?.({
        type: "toolCall",
        calls: [
          {
            id: "call_a",
            name: "ask_claude",
            args: { question: "how does this work?" },
          },
        ],
      });

      await vi.advanceTimersByTimeAsync(SEND_SETTLE_MS);

      expect(onDelegation).toHaveBeenCalledWith("call_a", "how does this work?");
      vi.useRealTimers();
    });

    it("delegates the reader's own words, not the model's paraphrase", async () => {
      vi.useFakeTimers();
      const onDelegation = vi.fn();
      await geminiEngineAdapter.open("session_2", {
        onUtterance: vi.fn(),
        onDelegation,
      });

      capturedOpts.onEvent?.({ type: "inputTranscript", text: "I want the Grok pill," });
      capturedOpts.onEvent?.({ type: "inputTranscript", text: " with the history." });
      capturedOpts.onEvent?.({ type: "outputTranscript", text: "Got it." });
      capturedOpts.onEvent?.({ type: "turnComplete" });
      // "Can you ask Claude?" is the reader's own yes, so the first call sends.
      capturedOpts.onEvent?.({ type: "inputTranscript", text: " Can you ask Claude?" });
      capturedOpts.onEvent?.({
        type: "toolCall",
        calls: [{ id: "call_v", name: "ask_claude", args: { question: "Build a UI feature" } }],
      });

      await vi.advanceTimersByTimeAsync(SEND_SETTLE_MS);

      expect(onDelegation).toHaveBeenCalledWith(
        "call_v",
        "I want the Grok pill, with the history. Can you ask Claude?",
      );

      // The next delegation carries only what was said after this one.
      capturedOpts.onEvent?.({ type: "inputTranscript", text: "And the Codex one." });
      capturedOpts.onEvent?.({
        type: "toolCall",
        calls: [{ id: "call_w", name: "ask_claude", args: { question: "Codex" } }],
      });
      await vi.advanceTimersByTimeAsync(SEND_SETTLE_MS);
      expect(onDelegation).toHaveBeenLastCalledWith("call_w", "And the Codex one.");
      vi.useRealTimers();
    });

    it("uses readerBuf fallback when args.question is empty", async () => {
      vi.useFakeTimers();
      const onDelegation = vi.fn();
      await geminiEngineAdapter.open("session_2", {
        onUtterance: vi.fn(),
        onDelegation,
      });

      // The first ask_claude only buys the question; prime it so this test
      // still exercises the send.
      capturedOpts.onEvent?.({ type: "toolCall", calls: [{ id: "prime", name: "ask_claude" }] });
      capturedOpts.onEvent?.({ type: "inputTranscript", text: "what is the status?" });
      capturedOpts.onEvent?.({
        type: "toolCall",
        calls: [
          {
            id: "call_c",
            name: "ask_claude",
            args: {},
          },
        ],
      });

      await vi.advanceTimersByTimeAsync(SEND_SETTLE_MS);

      expect(onDelegation).toHaveBeenCalledWith("call_c", "what is the status?");
      vi.useRealTimers();
    });

    it("rejects unknown tool calls directly without onDelegation", async () => {
      const onDelegation = vi.fn();

      await geminiEngineAdapter.open("session_2", {
        onUtterance: vi.fn(),
        onDelegation,
      });

      capturedOpts.onEvent?.({
        type: "toolCall",
        calls: [
          {
            id: "call_b",
            name: "unknown_tool",
            args: {},
          },
        ],
      });

      expect(onDelegation).not.toHaveBeenCalled();
      expect(currentSession.sendToolResponse).toHaveBeenCalledWith([
        {
          id: "call_b",
          name: "unknown_tool",
          response: { output: "Tool 'unknown_tool' is not available." },
        },
      ]);
    });
  });

  describe("waiting for the reader before a send, and looking things up", () => {
    let opts: { onEvent?: (ev: GeminiLiveEvent) => void } = {};
    let session: ReturnType<typeof fakeGeminiSession>;

    beforeEach(() => {
      vi.useFakeTimers();
      session = fakeGeminiSession();
      startGeminiLive.mockImplementation((o: typeof opts) => {
        opts = o;
        return Promise.resolve(session);
      });
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it("keeps the words said after asking for the send", async () => {
      const onDelegation = vi.fn();
      await geminiEngineAdapter.open("s_trail", { onUtterance: vi.fn(), onDelegation });
      // 10-09: "send this to Claude, and also tell him ..." lost the second half.
      opts.onEvent?.({ type: "inputTranscript", text: "Send this to Claude" });
      opts.onEvent?.({ type: "toolCall", calls: [{ id: "c1", name: "ask_claude" }] });
      await vi.advanceTimersByTimeAsync(SEND_SETTLE_MS - 500);
      opts.onEvent?.({ type: "inputTranscript", text: ", and also tell him the voice" });
      await vi.advanceTimersByTimeAsync(SEND_SETTLE_MS - 500);
      opts.onEvent?.({ type: "inputTranscript", text: " claims commands it never ran." });
      // Still talking, so nothing has gone yet.
      await vi.advanceTimersByTimeAsync(SEND_SETTLE_MS - 500);
      expect(onDelegation).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(600);
      expect(onDelegation).toHaveBeenCalledWith(
        "c1",
        "Send this to Claude, and also tell him the voice claims commands it never ran.",
      );
    });

    it("does not wait forever on a reader who never stops", async () => {
      const onDelegation = vi.fn();
      await geminiEngineAdapter.open("s_noisy", { onUtterance: vi.fn(), onDelegation });
      opts.onEvent?.({ type: "inputTranscript", text: "Send it to Claude" });
      opts.onEvent?.({ type: "toolCall", calls: [{ id: "c1", name: "ask_claude" }] });
      // A word every second, for longer than the cap.
      const timer = setInterval(() => {
        opts.onEvent?.({ type: "inputTranscript", text: " and" });
      }, 1000);
      await vi.advanceTimersByTimeAsync(SEND_SETTLE_MAX_MS);
      clearInterval(timer);
      await vi.advanceTimersByTimeAsync(100);
      expect(onDelegation).toHaveBeenCalledTimes(1);
    });

    it("hands check_machine to the companion and answers under its own name", async () => {
      const onCheck = vi.fn();
      const onDelegation = vi.fn();
      const handle = await geminiEngineAdapter.open("s_check", {
        onUtterance: vi.fn(),
        onDelegation,
        onCheck,
      });
      opts.onEvent?.({ type: "inputTranscript", text: "how full is the disk?" });
      opts.onEvent?.({
        type: "toolCall",
        calls: [{ id: "k1", name: "check_machine", args: { question: "disk usage" } }],
      });
      // No confirmation question and no send: looking does not end the call.
      expect(onCheck).toHaveBeenCalledWith("k1", "disk usage");
      expect(session.sendToolResponse).not.toHaveBeenCalled();
      handle.speakBack("k1", "Looked it up: 97% used.");
      expect(session.sendToolResponse).toHaveBeenCalledWith([
        { id: "k1", name: "check_machine", response: { output: "Looked it up: 97% used." } },
      ]);
      await vi.advanceTimersByTimeAsync(SEND_SETTLE_MAX_MS);
      expect(onDelegation).not.toHaveBeenCalled();
      expect(session.stop).not.toHaveBeenCalled();
    });

    it("leaves claude_status to the relay, which answers it itself", async () => {
      await geminiEngineAdapter.open("s_status", { onUtterance: vi.fn(), onDelegation: vi.fn() });
      opts.onEvent?.({ type: "toolCall", calls: [{ id: "w1", name: "claude_status" }] });
      expect(session.sendToolResponse).not.toHaveBeenCalled();
    });

    it("in the hub conversation, leaves every tool to the relay and keeps the call", async () => {
      const onDelegation = vi.fn();
      const onCheck = vi.fn();
      await geminiEngineAdapter.open("s_hub", { onUtterance: vi.fn(), onDelegation, onCheck });
      opts.onEvent?.({ type: "session", model: "gemini-3.8-live", toolsBlock: false, hub: true });
      opts.onEvent?.({ type: "inputTranscript", text: "manda a brasa rodar os testes" });
      opts.onEvent?.({
        type: "toolCall",
        calls: [
          { id: "h1", name: "ask_claude", args: { session: "brasa", message: "run the tests" } },
          { id: "h2", name: "check_machine", args: { session: "brasa", question: "disk" } },
        ],
      });
      await vi.advanceTimersByTimeAsync(SEND_SETTLE_MAX_MS);
      expect(session.sendToolResponse).not.toHaveBeenCalled();
      expect(onCheck).not.toHaveBeenCalled();
      expect(onDelegation).not.toHaveBeenCalled();
      expect(session.stop).not.toHaveBeenCalled();
    });

    it("refuses check_machine on a page that cannot answer it", async () => {
      await geminiEngineAdapter.open("s_nocheck", { onUtterance: vi.fn(), onDelegation: vi.fn() });
      opts.onEvent?.({ type: "toolCall", calls: [{ id: "k2", name: "check_machine" }] });
      expect(session.sendToolResponse).toHaveBeenCalledWith([
        expect.objectContaining({ id: "k2", name: "check_machine" }),
      ]);
    });
  });
});
