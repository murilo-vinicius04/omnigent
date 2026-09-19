// Which live engine the spoken conversation runs on, chosen in the composer.
// The button's click routes into the live-conversation store, which branches
// on the localStorage engine: gpt keeps openLiveConversation (WebRTC), gemini
// calls startGeminiLive. Both transports are mocked here; the store is real.

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ComposerMicButton } from "./ComposerMicButton";
import { useVoiceBackendStore } from "@/lib/sessionVoiceBackend";

const openLiveConversation = vi.fn();
const startGeminiLive = vi.fn();

vi.mock("./liveVoice", () => ({
  openLiveConversation: (...args: unknown[]) => openLiveConversation(...args),
  LiveVoiceUnavailable: class extends Error {},
}));
vi.mock("@/lib/liveVoice", () => ({
  openLiveConversation: (...args: unknown[]) => openLiveConversation(...args),
  LiveVoiceUnavailable: class extends Error {},
}));
vi.mock("@/lib/geminiLive", () => ({
  startGeminiLive: (...args: unknown[]) => startGeminiLive(...args),
}));
vi.mock("@/lib/companionApi", () => ({
  noteCompanion: vi.fn(async () => {}),
  delegateSpoken: vi.fn(async () => ({ forward: false, english: null, answer: null })),
  prewarmCompanion: vi.fn(async () => {}),
}));
const send = vi.fn(async (_text: string, _agentId: string) => {});
vi.mock("@/store/chatStore", () => ({
  useChatStore: { getState: () => ({ send }) },
}));

vi.mock("@/lib/speechPlayback", () => ({ claimSpeechChannel: vi.fn() }));

import type { GeminiLiveEvent } from "@/lib/geminiLive";
import { useLiveConversationStore } from "@/lib/liveConversation";
import { ComposerLiveMeter } from "./ComposerLiveMeter";

if (typeof globalThis.MediaStream === "undefined") {
  (globalThis as unknown as { MediaStream: unknown }).MediaStream = function MediaStream() {};
}

/** A GPT-path conversation stand-in with the surface the store touches. */
function fakeConversation() {
  let settle: () => void = () => {};
  const closed = new Promise<void>((resolve) => {
    settle = resolve;
  });
  return {
    stream: new MediaStream(),
    closed,
    elapsedS: () => 12,
    stop: vi.fn(() => {
      settle();
    }),
    untilQuiet: vi.fn(async () => {}),
    commentary: vi.fn(),
    end: () => settle(),
  };
}

/** A Gemini-path session stand-in; captures the callbacks for the test. */
function fakeGeminiSession() {
  return {
    stop: vi.fn(),
    sendToolResponse: vi.fn(),
    untilPlaybackDrained: vi.fn(() => Promise.resolve()),
  };
}

let geminiCallbacks: {
  onEvent?: (ev: GeminiLiveEvent) => void;
  onStateChange?: (state: { state: string; kind?: string; code?: number; reason?: string }) => void;
};

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  // The button renders nothing with no recognizer and no server dictation;
  // a minimal ctor keeps it mounted so the live-voice path is reachable.
  class FakeRecognition {
    continuous = false;
    interimResults = false;
    lang = "";
    start() {}
    stop() {}
    addEventListener() {}
    removeEventListener() {}
  }
  vi.stubGlobal("SpeechRecognition", FakeRecognition);
  geminiCallbacks = {};
  startGeminiLive.mockImplementation((opts: typeof geminiCallbacks) => {
    geminiCallbacks = opts;
    return Promise.resolve(fakeGeminiSession());
  });
  openLiveConversation.mockResolvedValue(fakeConversation());
  useLiveConversationStore.setState({
    sessionId: null,
    engine: null,
    connecting: false,
    elapsedS: 0,
    error: null,
  });
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useVoiceBackendStore.setState({ choices: {} });
  useLiveConversationStore.setState({
    sessionId: null,
    engine: null,
    connecting: false,
    elapsedS: 0,
    error: null,
  });
  window.localStorage.clear();
  vi.restoreAllMocks();
});

function renderLiveMic() {
  useVoiceBackendStore.getState().set("conv_a", "live");
  return render(<ComposerMicButton onTranscript={vi.fn()} sessionId="conv_a" agentId="agent_1" />);
}

async function clickMic() {
  await act(async () => {
    fireEvent.click(screen.getByRole("button"));
  });
}

describe("ComposerMicButton live engine routing", () => {
  it("with engine gemini, the mic starts Gemini Live, not the GPT client", async () => {
    window.localStorage.setItem("omnigent:live-voice-engine", "gemini");
    renderLiveMic();
    await clickMic();

    expect(startGeminiLive).toHaveBeenCalledTimes(1);
    expect(startGeminiLive).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: "conv_a" }),
    );
    expect(openLiveConversation).not.toHaveBeenCalled();
    expect(useLiveConversationStore.getState().sessionId).toBe("conv_a");
    expect(useLiveConversationStore.getState().engine).toBe("gemini");
  });

  it("with engine gpt (default), the mic starts the GPT client, not Gemini", async () => {
    renderLiveMic();
    await clickMic();

    expect(openLiveConversation).toHaveBeenCalledTimes(1);
    expect(openLiveConversation.mock.calls[0]?.[0]).toBe("conv_a");
    expect(startGeminiLive).not.toHaveBeenCalled();
    expect(useLiveConversationStore.getState().sessionId).toBe("conv_a");
    expect(useLiveConversationStore.getState().engine).toBe("gpt");
  });

  it("claims the speech channel with null element on Gemini start", async () => {
    window.localStorage.setItem("omnigent:live-voice-engine", "gemini");
    renderLiveMic();
    await clickMic();

    const { claimSpeechChannel } = await import("@/lib/speechPlayback");
    expect(claimSpeechChannel).toHaveBeenCalledWith(null, "conv_a");
    expect(claimSpeechChannel).toHaveBeenCalledTimes(1);
  });

  it("buffers transcript fragments and flushes whole utterances to the companion ledger", async () => {
    window.localStorage.setItem("omnigent:live-voice-engine", "gemini");
    renderLiveMic();
    await clickMic();

    const { noteCompanion } = await import("@/lib/companionApi");
    vi.mocked(noteCompanion).mockClear();

    await act(async () => {
      geminiCallbacks.onEvent?.({ type: "inputTranscript", text: "what" });
    });
    expect(noteCompanion).toHaveBeenCalledTimes(0);

    await act(async () => {
      geminiCallbacks.onEvent?.({ type: "inputTranscript", text: " changed?" });
    });
    expect(noteCompanion).toHaveBeenCalledTimes(0);

    await act(async () => {
      geminiCallbacks.onEvent?.({ type: "outputTranscript", text: "the" });
    });
    expect(noteCompanion).toHaveBeenCalledTimes(1);
    expect(noteCompanion).toHaveBeenNthCalledWith(1, "conv_a", "question", "what changed?");

    await act(async () => {
      geminiCallbacks.onEvent?.({ type: "outputTranscript", text: " meter" });
    });
    expect(noteCompanion).toHaveBeenCalledTimes(1);

    await act(async () => {
      geminiCallbacks.onEvent?.({ type: "turnComplete" });
    });
    expect(noteCompanion).toHaveBeenCalledTimes(2);
    expect(noteCompanion).toHaveBeenNthCalledWith(1, "conv_a", "question", "what changed?");
    expect(noteCompanion).toHaveBeenNthCalledWith(2, "conv_a", "answer", "the meter");
  });

  it("a close with pending buffers flushes them once", async () => {
    window.localStorage.setItem("omnigent:live-voice-engine", "gemini");
    renderLiveMic();
    await clickMic();

    const { noteCompanion } = await import("@/lib/companionApi");
    vi.mocked(noteCompanion).mockClear();

    await act(async () => {
      geminiCallbacks.onEvent?.({ type: "inputTranscript", text: "what changed?" });
      geminiCallbacks.onEvent?.({ type: "outputTranscript", text: "the meter" });
      geminiCallbacks.onEvent?.({ type: "inputTranscript", text: "pending question" });
    });
    // "what changed?" flushed when outputTranscript arrived.
    // voiceBuf has "the meter", readerBuf has "pending question".
    expect(noteCompanion).toHaveBeenCalledTimes(1);
    expect(noteCompanion).toHaveBeenNthCalledWith(1, "conv_a", "question", "what changed?");

    await act(async () => {
      geminiCallbacks.onStateChange?.({ state: "closed", kind: "normal", code: 1000 });
    });

    // voiceBuf flushed as answer, then readerBuf as question
    expect(noteCompanion).toHaveBeenCalledTimes(3);
    expect(noteCompanion).toHaveBeenNthCalledWith(2, "conv_a", "answer", "the meter");
    expect(noteCompanion).toHaveBeenNthCalledWith(3, "conv_a", "question", "pending question");

    // Second teardown/stop does not flush again
    useLiveConversationStore.getState().stop();
    expect(noteCompanion).toHaveBeenCalledTimes(3);
  });

  it("a policy close ends the session as a normal hang-up, with no error and surfaces notice", async () => {
    window.localStorage.setItem("omnigent:live-voice-engine", "gemini");
    renderLiveMic();
    await clickMic();

    await act(async () => {
      geminiCallbacks.onStateChange?.({
        state: "closed",
        kind: "policy",
        code: 1008,
        reason: "gemini live client keepalive timed out",
      });
    });
    const state = useLiveConversationStore.getState();
    expect(state.sessionId).toBeNull();
    expect(state.error).toBeNull();
    expect(state.notice).toBe("Live conversation ended: no audio for 60s");
  });

  it("an error close surfaces the reason in the store's error field", async () => {
    window.localStorage.setItem("omnigent:live-voice-engine", "gemini");
    renderLiveMic();
    await clickMic();

    await act(async () => {
      geminiCallbacks.onStateChange?.({ state: "closed", kind: "error", code: 1011, reason: "boom" });
    });
    expect(useLiveConversationStore.getState().error).toBe("boom");
    expect(useLiveConversationStore.getState().sessionId).toBeNull();
  });

  it("pressing the mic again stops the Gemini session", async () => {
    window.localStorage.setItem("omnigent:live-voice-engine", "gemini");
    renderLiveMic();
    await clickMic();
    const session = await startGeminiLive.mock.results[0]?.value;
    await clickMic();

    expect(session.stop).toHaveBeenCalled();
    expect(useLiveConversationStore.getState().sessionId).toBeNull();
  });

  it("a failed Gemini handshake reports the error like the GPT path", async () => {
    window.localStorage.setItem("omnigent:live-voice-engine", "gemini");
    startGeminiLive.mockRejectedValue(new Error("microphone unavailable"));
    renderLiveMic();
    await clickMic();

    const state = useLiveConversationStore.getState();
    expect(state.sessionId).toBeNull();
    expect(state.connecting).toBe(false);
    expect(state.error).toContain("microphone unavailable");
  });

  it("companion-answers case: delegateSpoken called once, sendToolResponse carries answer, session not stopped", async () => {
    const { delegateSpoken } = await import("@/lib/companionApi");
    vi.mocked(delegateSpoken).mockResolvedValueOnce({
      forward: false,
      english: null,
      answer: "The painted stencils run four to one.",
    });
    window.localStorage.setItem("omnigent:live-voice-engine", "gemini");
    renderLiveMic();
    await clickMic();
    const session = await startGeminiLive.mock.results[0]?.value;

    await act(async () => {
      geminiCallbacks.onEvent?.({
        type: "toolCall",
        calls: [
          {
            id: "call_1",
            name: "ask_claude",
            args: { question: "did we identify which link is which?" },
          },
        ],
      });
    });

    expect(delegateSpoken).toHaveBeenCalledWith("conv_a", "did we identify which link is which?");
    expect(session.sendToolResponse).toHaveBeenCalledWith([
      {
        id: "call_1",
        name: "ask_claude",
        response: { output: "The painted stencils run four to one." },
      },
    ]);
    expect(send).not.toHaveBeenCalled();
    expect(session.stop).not.toHaveBeenCalled();
    expect(useLiveConversationStore.getState().sessionId).toBe("conv_a");
  });

  it("forward case: handToClaude called with forwarded text, sendToolResponse carries HANDED_OFF, store handedOff set, and session stops after turnComplete", async () => {
    const { delegateSpoken } = await import("@/lib/companionApi");
    vi.mocked(delegateSpoken).mockResolvedValueOnce({
      forward: true,
      english: "Which caliper reading belongs to link three?",
      answer: null,
    });
    window.localStorage.setItem("omnigent:live-voice-engine", "gemini");
    renderLiveMic();
    await clickMic();
    const session = await startGeminiLive.mock.results[0]?.value;

    await act(async () => {
      geminiCallbacks.onEvent?.({
        type: "toolCall",
        calls: [
          {
            id: "call_2",
            name: "ask_claude",
            args: { question: "which caliper reading is link three" },
          },
        ],
      });
    });

    expect(send).toHaveBeenCalledWith(
      "Which caliper reading belongs to link three?",
      "agent_1",
      undefined,
      { forceClaude: true },
    );
    expect(session.sendToolResponse).toHaveBeenCalledWith([
      {
        id: "call_2",
        name: "ask_claude",
        response: {
          output:
            "I've sent that to Claude. Its answer will show up in the chat, so I'm ending the call now.",
        },
      },
    ]);
    expect(useLiveConversationStore.getState().handedOff).toBe(
      "Which caliper reading belongs to link three?",
    );

    // Call has not ended yet (waiting for voice turn to finish)
    expect(session.stop).not.toHaveBeenCalled();

    // Turn completes -> session stops
    await act(async () => {
      geminiCallbacks.onEvent?.({ type: "turnComplete" });
    });

    expect(session.stop).toHaveBeenCalled();
    expect(useLiveConversationStore.getState().sessionId).toBeNull();
  });

  it("forward case with silent model: stops via the goodbye cap when no turnComplete arrives", async () => {
    vi.useFakeTimers();
    try {
      const { delegateSpoken } = await import("@/lib/companionApi");
      vi.mocked(delegateSpoken).mockResolvedValueOnce({
        forward: true,
        english: "What is next?",
        answer: null,
      });
      window.localStorage.setItem("omnigent:live-voice-engine", "gemini");
      renderLiveMic();
      await clickMic();
      const session = await startGeminiLive.mock.results[0]?.value;

      await act(async () => {
        geminiCallbacks.onEvent?.({
          type: "toolCall",
          calls: [
            {
              id: "call_3",
              name: "ask_claude",
              args: { question: "what is next" },
            },
          ],
        });
      });

      expect(session.sendToolResponse).toHaveBeenCalled();
      expect(session.stop).not.toHaveBeenCalled();

      // Still speaking its goodbye at the old 10s cap.
      await act(async () => {
        vi.advanceTimersByTime(10000);
      });
      expect(session.stop).not.toHaveBeenCalled();

      // The 20s cap ends it.
      await act(async () => {
        vi.advanceTimersByTime(10000);
      });

      expect(session.stop).toHaveBeenCalled();
      expect(useLiveConversationStore.getState().sessionId).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("send-failure case: NOT_SENT response sent and session stays open", async () => {
    const { delegateSpoken } = await import("@/lib/companionApi");
    vi.mocked(delegateSpoken).mockResolvedValueOnce({
      forward: true,
      english: "Run the tests",
      answer: null,
    });
    send.mockRejectedValueOnce(new Error("network error"));

    window.localStorage.setItem("omnigent:live-voice-engine", "gemini");
    renderLiveMic();
    await clickMic();
    const session = await startGeminiLive.mock.results[0]?.value;

    await act(async () => {
      geminiCallbacks.onEvent?.({
        type: "toolCall",
        calls: [
          {
            id: "call_4",
            name: "ask_claude",
            args: { question: "run the tests" },
          },
        ],
      });
    });

    expect(session.sendToolResponse).toHaveBeenCalledWith([
      {
        id: "call_4",
        name: "ask_claude",
        response: {
          output:
            "I couldn't send that to Claude from here, so it will need to be typed in the chat.",
        },
      },
    ]);
    expect(session.stop).not.toHaveBeenCalled();
    expect(useLiveConversationStore.getState().sessionId).toBe("conv_a");
  });

  it("unknown tool name: response sent saying not available and nothing throws", async () => {
    window.localStorage.setItem("omnigent:live-voice-engine", "gemini");
    renderLiveMic();
    await clickMic();
    const session = await startGeminiLive.mock.results[0]?.value;

    await act(async () => {
      geminiCallbacks.onEvent?.({
        type: "toolCall",
        calls: [
          {
            id: "call_5",
            name: "some_unknown_tool",
            args: {},
          },
        ],
      });
    });

    expect(session.sendToolResponse).toHaveBeenCalledWith([
      {
        id: "call_5",
        name: "some_unknown_tool",
        response: {
          output: "Tool 'some_unknown_tool' is not available.",
        },
      },
    ]);
    expect(session.stop).not.toHaveBeenCalled();
    expect(useLiveConversationStore.getState().sessionId).toBe("conv_a");
  });
});

describe("ComposerLiveMeter engine display", () => {
  it("shows cost in $ when engine is gpt", () => {
    useLiveConversationStore.setState({
      sessionId: "conv_a",
      engine: "gpt",
      connecting: false,
      elapsedS: 60,
    });
    render(<ComposerLiveMeter />);
    const meter = screen.getByTestId("composer-live-meter");
    expect(meter).toHaveTextContent("1:00");
    expect(meter).toHaveTextContent("$0.050");
  });

  it("shows clock only and no $ when engine is gemini", () => {
    useLiveConversationStore.setState({
      sessionId: "conv_a",
      engine: "gemini",
      connecting: false,
      elapsedS: 60,
    });
    render(<ComposerLiveMeter />);
    const meter = screen.getByTestId("composer-live-meter");
    expect(meter).toHaveTextContent("1:00");
    expect(meter).not.toHaveTextContent("$");
  });
});
