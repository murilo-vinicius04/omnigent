import { cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Bubble } from "@/lib/renderItems";
import type { ActiveResponse } from "@/store/types";
import {
  BrowserSpeechEngine,
  resetSpeechEngine,
  resetSpokenMessageTracking,
  setSpeechEngine,
  type SpeechEngine,
  useSpeechPlaybackStore,
} from "@/lib/speechPlayback";
import { writeSpokenSummaryPlayback } from "@/lib/spokenSummaryPlaybackPreferences";
import { useSpokenSummaryPlayback } from "./useSpokenSummaryPlayback";

class MockSpeechEngine implements SpeechEngine {
  isSupported = vi.fn().mockReturnValue(true);
  speak = vi.fn((_text: string, _lang?: string, _onEnd?: () => void, _onError?: () => void) => {});
  stop = vi.fn();
  isSpeaking = vi.fn().mockReturnValue(false);
}

function makeAssistantBubble(
  responseId: string,
  itemId: string | null = null,
  spokenSummary?: { text: string; lang: string },
  final = true,
  lifecycle: ActiveResponse["state"] = "completed",
): Bubble {
  return {
    kind: "assistant",
    responseId,
    stableId: responseId,
    lifecycle,
    error: null,
    items: [
      {
        kind: "text",
        itemId,
        text: "Here is the full response text that should never be hidden.",
        final,
        spokenSummary,
      },
    ],
  };
}

describe("useSpokenSummaryPlayback", () => {
  let mockEngine: MockSpeechEngine;

  beforeEach(() => {
    localStorage.clear();
    resetSpokenMessageTracking();
    useSpeechPlaybackStore.setState({ isSpeaking: false, speakingItemId: null });
    mockEngine = new MockSpeechEngine();
    setSpeechEngine(mockEngine);
  });

  afterEach(() => {
    cleanup();
    resetSpeechEngine();
    vi.restoreAllMocks();
  });

  it("mounts with [], rerenders with full populated history array, asserts speak was NOT called", () => {
    writeSpokenSummaryPlayback(true);
    const { rerender } = renderHook(
      ({ bubbles, activeResponseId }) => useSpokenSummaryPlayback(bubbles, activeResponseId),
      { initialProps: { bubbles: [] as Bubble[], activeResponseId: null as string | null } },
    );

    expect(mockEngine.speak).not.toHaveBeenCalled();

    const historyBubbles: Bubble[] = [
      makeAssistantBubble(
        "resp_h1",
        "item_h1",
        { text: "Historical summary 1", lang: "pt-BR" },
        true,
        "completed",
      ),
      makeAssistantBubble(
        "resp_h2",
        "item_h2",
        { text: "Historical summary 2", lang: "en-US" },
        true,
        "completed",
      ),
    ];

    rerender({ bubbles: historyBubbles, activeResponseId: null });

    expect(mockEngine.speak).not.toHaveBeenCalled();
    expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(false);
  });

  it("asserts session-switch (history swap) does not speak", () => {
    writeSpokenSummaryPlayback(true);
    const sessionABubbles: Bubble[] = [
      makeAssistantBubble(
        "resp_a1",
        "item_a1",
        { text: "Session A summary", lang: "en-US" },
        true,
        "completed",
      ),
    ];

    const { rerender } = renderHook(
      ({ bubbles, activeResponseId }) => useSpokenSummaryPlayback(bubbles, activeResponseId),
      { initialProps: { bubbles: sessionABubbles, activeResponseId: null as string | null } },
    );

    expect(mockEngine.speak).not.toHaveBeenCalled();

    // Switch to Session B (history swap)
    const sessionBBubbles: Bubble[] = [
      makeAssistantBubble(
        "resp_b1",
        "item_b1",
        { text: "Session B summary 1", lang: "pt-BR" },
        true,
        "completed",
      ),
      makeAssistantBubble(
        "resp_b2",
        "item_b2",
        { text: "Session B summary 2", lang: "pt-BR" },
        true,
        "completed",
      ),
    ];

    rerender({ bubbles: sessionBBubbles, activeResponseId: null });

    expect(mockEngine.speak).not.toHaveBeenCalled();
    expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(false);
  });

  it("simulates the real streaming sequence non-final -> final WITH an itemId stamped on reconcile, and asserts speak is called exactly ONCE", () => {
    writeSpokenSummaryPlayback(true);
    const initialBubbles: Bubble[] = [
      makeAssistantBubble(
        "resp_h1",
        "item_h1",
        { text: "Historical summary", lang: "pt-BR" },
        true,
        "completed",
      ),
    ];

    const { rerender } = renderHook(
      ({ bubbles, activeResponseId }) => useSpokenSummaryPlayback(bubbles, activeResponseId),
      { initialProps: { bubbles: initialBubbles, activeResponseId: null as string | null } },
    );

    // 1. Streaming arrives: non-final, itemId null, lifecycle streaming
    const streamingBubbles: Bubble[] = [
      ...initialBubbles,
      makeAssistantBubble("resp_live", null, undefined, false, "streaming"),
    ];
    rerender({ bubbles: streamingBubbles, activeResponseId: "resp_live" });
    expect(mockEngine.speak).not.toHaveBeenCalled();

    // 2. Turn completes: final true, spokenSummary attached, itemId still null (before reconcile)
    const finalBeforeReconcile: Bubble[] = [
      ...initialBubbles,
      makeAssistantBubble(
        "resp_live",
        null,
        { text: "Live spoken summary", lang: "pt-BR" },
        true,
        "completed",
      ),
    ];
    rerender({ bubbles: finalBeforeReconcile, activeResponseId: null });

    expect(mockEngine.speak).toHaveBeenCalledTimes(1);
    expect(mockEngine.speak).toHaveBeenCalledWith(
      "Live spoken summary",
      "pt-BR",
      expect.any(Function),
      expect.any(Function),
    );
    expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(true);
    expect(useSpeechPlaybackStore.getState().speakingItemId).toBe("resp_live");

    // 3. chatStore reconciles output_item.done: itemId stamped on the item
    const reconciledBubbles: Bubble[] = [
      ...initialBubbles,
      makeAssistantBubble(
        "resp_live",
        "msg_server_stamped_id",
        { text: "Live spoken summary", lang: "pt-BR" },
        true,
        "completed",
      ),
    ];
    rerender({ bubbles: reconciledBubbles, activeResponseId: null });

    // Assert speak was called exactly ONCE
    expect(mockEngine.speak).toHaveBeenCalledTimes(1);
  });

  it("does NOT speak when toggle is OFF even when a new live message arrives", () => {
    writeSpokenSummaryPlayback(false);
    const initialBubbles: Bubble[] = [];

    const { rerender } = renderHook(
      ({ bubbles, activeResponseId }) => useSpokenSummaryPlayback(bubbles, activeResponseId),
      { initialProps: { bubbles: initialBubbles, activeResponseId: "resp_1" as string | null } },
    );

    const streamingBubbles: Bubble[] = [
      makeAssistantBubble("resp_1", null, undefined, false, "streaming"),
    ];
    rerender({ bubbles: streamingBubbles, activeResponseId: "resp_1" });

    const finalBubbles: Bubble[] = [
      makeAssistantBubble(
        "resp_1",
        "item_1",
        { text: "Live summary text", lang: "en-US" },
        true,
        "completed",
      ),
    ];
    rerender({ bubbles: finalBubbles, activeResponseId: null });

    expect(mockEngine.speak).not.toHaveBeenCalled();
    expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(false);
  });

  it("does NOT speak when the live assistant message has no spoken summary", () => {
    writeSpokenSummaryPlayback(true);
    const initialBubbles: Bubble[] = [];

    const { rerender } = renderHook(
      ({ bubbles, activeResponseId }) => useSpokenSummaryPlayback(bubbles, activeResponseId),
      { initialProps: { bubbles: initialBubbles, activeResponseId: "resp_1" as string | null } },
    );

    const streamingBubbles: Bubble[] = [
      makeAssistantBubble("resp_1", null, undefined, false, "streaming"),
    ];
    rerender({ bubbles: streamingBubbles, activeResponseId: "resp_1" });

    const finalBubbles: Bubble[] = [
      makeAssistantBubble("resp_1", "item_1", undefined, true, "completed"),
    ];
    rerender({ bubbles: finalBubbles, activeResponseId: null });

    expect(mockEngine.speak).not.toHaveBeenCalled();
  });

  it("does NOT speak when the live assistant message is not final", () => {
    writeSpokenSummaryPlayback(true);
    const initialBubbles: Bubble[] = [];

    const { rerender } = renderHook(
      ({ bubbles, activeResponseId }) => useSpokenSummaryPlayback(bubbles, activeResponseId),
      { initialProps: { bubbles: initialBubbles, activeResponseId: "resp_1" as string | null } },
    );

    const streamingBubbles: Bubble[] = [
      makeAssistantBubble(
        "resp_1",
        null,
        { text: "Streaming...", lang: "en-US" },
        false,
        "streaming",
      ),
    ];
    rerender({ bubbles: streamingBubbles, activeResponseId: "resp_1" });

    expect(mockEngine.speak).not.toHaveBeenCalled();
  });

  it("does NOT speak when spoken summary text is empty string or whitespace", () => {
    writeSpokenSummaryPlayback(true);
    const { rerender } = renderHook(
      ({ bubbles, activeResponseId }) => useSpokenSummaryPlayback(bubbles, activeResponseId),
      { initialProps: { bubbles: [] as Bubble[], activeResponseId: "resp_1" as string | null } },
    );

    const streamingBubbles: Bubble[] = [
      makeAssistantBubble("resp_1", null, undefined, false, "streaming"),
    ];
    rerender({ bubbles: streamingBubbles, activeResponseId: "resp_1" });

    const finalBubbles: Bubble[] = [
      makeAssistantBubble("resp_1", "item_1", { text: "   ", lang: "en-US" }, true, "completed"),
    ];
    rerender({ bubbles: finalBubbles, activeResponseId: null });

    expect(mockEngine.speak).not.toHaveBeenCalled();
  });

  it("cancels in-flight speech when a new summary arrives", () => {
    writeSpokenSummaryPlayback(true);
    const { rerender } = renderHook(
      ({ bubbles, activeResponseId }) => useSpokenSummaryPlayback(bubbles, activeResponseId),
      { initialProps: { bubbles: [] as Bubble[], activeResponseId: "resp_1" as string | null } },
    );

    // Turn 1 streams and completes
    const bubbles1Streaming: Bubble[] = [
      makeAssistantBubble("resp_1", null, undefined, false, "streaming"),
    ];
    rerender({ bubbles: bubbles1Streaming, activeResponseId: "resp_1" });

    const bubbles1Done: Bubble[] = [
      makeAssistantBubble(
        "resp_1",
        "item_1",
        { text: "First summary", lang: "pt-BR" },
        true,
        "completed",
      ),
    ];
    rerender({ bubbles: bubbles1Done, activeResponseId: null });

    expect(mockEngine.stop).toHaveBeenCalled();
    expect(mockEngine.speak).toHaveBeenCalledWith(
      "First summary",
      "pt-BR",
      expect.any(Function),
      expect.any(Function),
    );

    mockEngine.stop.mockClear();

    // Turn 2 streams and completes while Turn 1 was speaking
    const bubbles2Streaming: Bubble[] = [
      ...bubbles1Done,
      makeAssistantBubble("resp_2", null, undefined, false, "streaming"),
    ];
    rerender({ bubbles: bubbles2Streaming, activeResponseId: "resp_2" });

    const bubbles2Done: Bubble[] = [
      ...bubbles1Done,
      makeAssistantBubble(
        "resp_2",
        "item_2",
        { text: "Second summary", lang: "pt-BR" },
        true,
        "completed",
      ),
    ];
    rerender({ bubbles: bubbles2Done, activeResponseId: null });

    // In-flight audio must be cancelled
    expect(mockEngine.stop).toHaveBeenCalledTimes(1);
    expect(mockEngine.speak).toHaveBeenCalledWith(
      "Second summary",
      "pt-BR",
      expect.any(Function),
      expect.any(Function),
    );
  });

  it("cancels in-flight speech on component unmount", () => {
    writeSpokenSummaryPlayback(true);
    const { unmount } = renderHook(() => useSpokenSummaryPlayback([]));

    unmount();

    expect(mockEngine.stop).toHaveBeenCalled();
  });

  it("toggling 'Speak responses' OFF stops any in-flight utterance", () => {
    writeSpokenSummaryPlayback(true);
    useSpeechPlaybackStore.setState({ isSpeaking: true, speakingItemId: "resp_in_flight" });

    // When user toggles off, settings control calls writeSpokenSummaryPlayback(false) and store.stop()
    writeSpokenSummaryPlayback(false);
    useSpeechPlaybackStore.getState().stop();

    expect(mockEngine.stop).toHaveBeenCalled();
    expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(false);
    expect(useSpeechPlaybackStore.getState().speakingItemId).toBeNull();
  });
});

describe("BrowserSpeechEngine", () => {
  it("uses window.speechSynthesis and sets utterance.lang to part.lang", () => {
    const cancelMock = vi.fn();
    const speakMock = vi.fn();

    class FakeUtterance {
      text: string;
      lang = "";
      constructor(text: string) {
        this.text = text;
      }
    }

    vi.stubGlobal("speechSynthesis", {
      cancel: cancelMock,
      speak: speakMock,
      speaking: false,
    });
    vi.stubGlobal("SpeechSynthesisUtterance", FakeUtterance);

    const engine = new BrowserSpeechEngine();
    expect(engine.isSupported()).toBe(true);

    engine.speak("Olá mundo", "pt-BR");

    expect(cancelMock).toHaveBeenCalledTimes(1);
    expect(speakMock).toHaveBeenCalledTimes(1);
    const spoken = speakMock.mock.calls[0]?.[0] as FakeUtterance;
    expect(spoken).toBeDefined();
    expect(spoken.text).toBe("Olá mundo");
    expect(spoken.lang).toBe("pt-BR");
  });

  it("degrades silently and never throws in a browser without window.speechSynthesis", () => {
    vi.stubGlobal("speechSynthesis", undefined);
    vi.stubGlobal("SpeechSynthesisUtterance", undefined);

    const engine = new BrowserSpeechEngine();
    expect(engine.isSupported()).toBe(false);
    expect(engine.isSpeaking()).toBe(false);
    expect(() => engine.speak("Test text", "en-US")).not.toThrow();
    expect(() => engine.stop()).not.toThrow();
  });
});
