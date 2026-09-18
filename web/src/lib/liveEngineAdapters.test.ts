import { beforeEach, describe, expect, it, vi } from "vitest";

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
  gptEngineAdapter,
  geminiEngineAdapter,
  getLiveEngineAdapter,
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
      const onDelegation = vi.fn();
      await geminiEngineAdapter.open("session_2", {
        onUtterance: vi.fn(),
        onDelegation,
      });

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

      expect(onDelegation).toHaveBeenCalledWith("call_a", "how does this work?");
    });

    it("delegates the reader's own words, not the model's paraphrase", async () => {
      const onDelegation = vi.fn();
      await geminiEngineAdapter.open("session_2", {
        onUtterance: vi.fn(),
        onDelegation,
      });

      capturedOpts.onEvent?.({ type: "inputTranscript", text: "I want the Grok pill," });
      capturedOpts.onEvent?.({ type: "inputTranscript", text: " with the history." });
      capturedOpts.onEvent?.({ type: "outputTranscript", text: "Got it." });
      capturedOpts.onEvent?.({ type: "turnComplete" });
      capturedOpts.onEvent?.({ type: "inputTranscript", text: " Can you ask Claude?" });
      capturedOpts.onEvent?.({
        type: "toolCall",
        calls: [{ id: "call_v", name: "ask_claude", args: { question: "Build a UI feature" } }],
      });

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
      expect(onDelegation).toHaveBeenLastCalledWith("call_w", "And the Codex one.");
    });

    it("uses readerBuf fallback when args.question is empty", async () => {
      const onDelegation = vi.fn();
      await geminiEngineAdapter.open("session_2", {
        onUtterance: vi.fn(),
        onDelegation,
      });

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

      expect(onDelegation).toHaveBeenCalledWith("call_c", "what is the status?");
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
});
