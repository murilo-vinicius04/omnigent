import { cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Bubble } from "@/lib/renderItems";
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
  itemId: string,
  spokenSummary?: { text: string; lang: string },
  final = true,
): Bubble {
  return {
    kind: "assistant",
    responseId,
    stableId: responseId,
    lifecycle: "completed",
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

  it("does NOT speak on initial mount even if toggle is ON (history load / refresh guarantee)", () => {
    writeSpokenSummaryPlayback(true);
    const initialBubbles: Bubble[] = [
      makeAssistantBubble("resp_1", "item_1", { text: "Historical summary", lang: "pt-BR" }),
      makeAssistantBubble("resp_2", "item_2", {
        text: "Another historical summary",
        lang: "en-US",
      }),
    ];

    renderHook(() => useSpokenSummaryPlayback(initialBubbles));

    expect(mockEngine.speak).not.toHaveBeenCalled();
    expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(false);
  });

  it("speaks when toggle is ON and a new live assistant message arrives at the tail", () => {
    writeSpokenSummaryPlayback(true);
    const initialBubbles: Bubble[] = [
      makeAssistantBubble("resp_1", "item_1", { text: "Historical summary", lang: "pt-BR" }),
    ];

    const { rerender } = renderHook(({ bubbles }) => useSpokenSummaryPlayback(bubbles), {
      initialProps: { bubbles: initialBubbles },
    });

    expect(mockEngine.speak).not.toHaveBeenCalled();

    // New live message arrives
    const newBubbles: Bubble[] = [
      ...initialBubbles,
      makeAssistantBubble("resp_2", "item_2", { text: "Live summary text", lang: "pt-BR" }),
    ];

    rerender({ bubbles: newBubbles });

    expect(mockEngine.speak).toHaveBeenCalledTimes(1);
    expect(mockEngine.speak).toHaveBeenCalledWith(
      "Live summary text",
      "pt-BR",
      expect.any(Function),
      expect.any(Function),
    );
    expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(true);
    expect(useSpeechPlaybackStore.getState().speakingItemId).toBe("item_2");
  });

  it("does NOT speak when toggle is OFF even when a new live message arrives", () => {
    writeSpokenSummaryPlayback(false);
    const initialBubbles: Bubble[] = [];

    const { rerender } = renderHook(({ bubbles }) => useSpokenSummaryPlayback(bubbles), {
      initialProps: { bubbles: initialBubbles },
    });

    const newBubbles: Bubble[] = [
      makeAssistantBubble("resp_1", "item_1", { text: "Live summary text", lang: "en-US" }),
    ];

    rerender({ bubbles: newBubbles });

    expect(mockEngine.speak).not.toHaveBeenCalled();
    expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(false);
  });

  it("does NOT speak when the live assistant message has no spoken summary", () => {
    writeSpokenSummaryPlayback(true);
    const initialBubbles: Bubble[] = [];

    const { rerender } = renderHook(({ bubbles }) => useSpokenSummaryPlayback(bubbles), {
      initialProps: { bubbles: initialBubbles },
    });

    const newBubbles: Bubble[] = [makeAssistantBubble("resp_1", "item_1", undefined)];

    rerender({ bubbles: newBubbles });

    expect(mockEngine.speak).not.toHaveBeenCalled();
  });

  it("does NOT speak when the live assistant message is not final", () => {
    writeSpokenSummaryPlayback(true);
    const initialBubbles: Bubble[] = [];

    const { rerender } = renderHook(({ bubbles }) => useSpokenSummaryPlayback(bubbles), {
      initialProps: { bubbles: initialBubbles },
    });

    const newBubbles: Bubble[] = [
      makeAssistantBubble("resp_1", "item_1", { text: "Streaming...", lang: "en-US" }, false),
    ];

    rerender({ bubbles: newBubbles });

    expect(mockEngine.speak).not.toHaveBeenCalled();
  });

  it("does NOT re-speak when bubbles re-render without new messages", () => {
    writeSpokenSummaryPlayback(true);
    const initialBubbles: Bubble[] = [];

    const { rerender } = renderHook(({ bubbles }) => useSpokenSummaryPlayback(bubbles), {
      initialProps: { bubbles: initialBubbles },
    });

    const newBubbles: Bubble[] = [
      makeAssistantBubble("resp_1", "item_1", { text: "Summary 1", lang: "en-US" }),
    ];

    rerender({ bubbles: newBubbles });
    expect(mockEngine.speak).toHaveBeenCalledTimes(1);

    // Re-render with identical messages
    rerender({ bubbles: [...newBubbles] });
    expect(mockEngine.speak).toHaveBeenCalledTimes(1);
  });

  it("does NOT speak prepended historical messages when pagination loads older history", () => {
    writeSpokenSummaryPlayback(true);
    const currentBubbles: Bubble[] = [
      makeAssistantBubble("resp_2", "item_2", { text: "Tail message", lang: "en-US" }),
    ];

    const { rerender } = renderHook(({ bubbles }) => useSpokenSummaryPlayback(bubbles), {
      initialProps: { bubbles: currentBubbles },
    });

    // Prepend older history at the beginning of transcript
    const prependedBubbles: Bubble[] = [
      makeAssistantBubble("resp_1", "item_1", { text: "Older message", lang: "en-US" }),
      ...currentBubbles,
    ];

    rerender({ bubbles: prependedBubbles });

    expect(mockEngine.speak).not.toHaveBeenCalled();
  });

  it("cancels in-flight speech when a new summary arrives", () => {
    writeSpokenSummaryPlayback(true);
    const initialBubbles: Bubble[] = [];

    const { rerender } = renderHook(({ bubbles }) => useSpokenSummaryPlayback(bubbles), {
      initialProps: { bubbles: initialBubbles },
    });

    // Message 1 arrives and starts speaking
    const bubblesWithFirst: Bubble[] = [
      makeAssistantBubble("resp_1", "item_1", { text: "First summary", lang: "pt-BR" }),
    ];
    rerender({ bubbles: bubblesWithFirst });

    expect(mockEngine.stop).toHaveBeenCalled();
    expect(mockEngine.speak).toHaveBeenCalledWith(
      "First summary",
      "pt-BR",
      expect.any(Function),
      expect.any(Function),
    );

    mockEngine.stop.mockClear();

    // Message 2 arrives while Message 1 was speaking
    const bubblesWithSecond: Bubble[] = [
      ...bubblesWithFirst,
      makeAssistantBubble("resp_2", "item_2", { text: "Second summary", lang: "pt-BR" }),
    ];
    rerender({ bubbles: bubblesWithSecond });

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
});
