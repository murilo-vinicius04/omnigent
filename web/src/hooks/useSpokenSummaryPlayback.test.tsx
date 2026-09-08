import { cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Bubble, RenderItem } from "@/lib/renderItems";
import type { ActiveResponse } from "@/store/types";
import * as speechPlayback from "@/lib/speechPlayback";
import {
  BrowserSpeechEngine,
  clearInMemorySpokenTracking,
  isMessageSpoken,
  markMessageSpoken,
  MAX_PERSISTED_SPOKEN_IDS,
  resetSpeechEngine,
  resetSpokenMessageTracking,
  setSpeechEngine,
  type SpeechEngine,
  SPOKEN_MESSAGES_SESSION_STORAGE_KEY,
  useSpeechPlaybackStore,
} from "@/lib/speechPlayback";
import { writeSpokenSummaryPlayback } from "@/lib/spokenSummaryPlaybackPreferences";
import { SpokenSummaryPlaybackControl } from "@/components/chat/SpokenSummaryPlaybackControl";
import { isToolStreaming, useSpokenSummaryPlayback } from "./useSpokenSummaryPlayback";

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

function makeAssistantBubbleWithItems(
  responseId: string,
  items: RenderItem[],
  lifecycle: ActiveResponse["state"] = "completed",
): Bubble {
  return {
    kind: "assistant",
    responseId,
    stableId: responseId,
    lifecycle,
    error: null,
    items,
  };
}

describe("useSpokenSummaryPlayback", () => {
  let mockEngine: MockSpeechEngine;
  const originalSpeakLiveSummary = useSpeechPlaybackStore.getState().speakLiveSummary;

  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    resetSpokenMessageTracking();
    useSpeechPlaybackStore.setState({
      isSpeaking: false,
      speakingItemId: null,
      speakLiveSummary: originalSpeakLiveSummary,
    });
    mockEngine = new MockSpeechEngine();
    setSpeechEngine(mockEngine);
  });

  afterEach(() => {
    cleanup();
    resetSpeechEngine();
    useSpeechPlaybackStore.setState({ speakLiveSummary: originalSpeakLiveSummary });
    vi.restoreAllMocks();
  });

  it("mounts with [], rerenders with full populated history array, asserts speak was NOT called", () => {
    writeSpokenSummaryPlayback(true);
    const { rerender } = renderHook(
      ({ bubbles }) => useSpokenSummaryPlayback(bubbles),
      { initialProps: { bubbles: [] as Bubble[] } },
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

    rerender({ bubbles: historyBubbles });

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
      ({ bubbles }) => useSpokenSummaryPlayback(bubbles),
      { initialProps: { bubbles: sessionABubbles } },
    );

    expect(mockEngine.speak).not.toHaveBeenCalled();

    // Switch to Session B (history swap) where Session B's activeResponse was left completed in store
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

    rerender({ bubbles: sessionBBubbles });

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
      ({ bubbles }) => useSpokenSummaryPlayback(bubbles),
      { initialProps: { bubbles: initialBubbles } },
    );

    // 1. Streaming arrives: non-final, itemId null, lifecycle streaming
    const streamingBubbles: Bubble[] = [
      ...initialBubbles,
      makeAssistantBubble("resp_live", null, undefined, false, "streaming"),
    ];
    rerender({ bubbles: streamingBubbles });
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
    rerender({ bubbles: finalBeforeReconcile });

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
    rerender({ bubbles: reconciledBubbles });

    // Assert speak was called exactly ONCE
    expect(mockEngine.speak).toHaveBeenCalledTimes(1);
  });

  it("does NOT speak when toggle is OFF even when a new live message arrives", () => {
    writeSpokenSummaryPlayback(false);
    const initialBubbles: Bubble[] = [];

    const { rerender } = renderHook(
      ({ bubbles }) => useSpokenSummaryPlayback(bubbles),
      { initialProps: { bubbles: initialBubbles } },
    );

    const streamingBubbles: Bubble[] = [
      makeAssistantBubble("resp_1", null, undefined, false, "streaming"),
    ];
    rerender({ bubbles: streamingBubbles });

    const finalBubbles: Bubble[] = [
      makeAssistantBubble(
        "resp_1",
        "item_1",
        { text: "Live summary text", lang: "en-US" },
        true,
        "completed",
      ),
    ];
    rerender({ bubbles: finalBubbles });

    expect(mockEngine.speak).not.toHaveBeenCalled();
    expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(false);
  });

  it("does NOT speak when the live assistant message has no spoken summary", () => {
    writeSpokenSummaryPlayback(true);
    const initialBubbles: Bubble[] = [];

    const { rerender } = renderHook(
      ({ bubbles }) => useSpokenSummaryPlayback(bubbles),
      { initialProps: { bubbles: initialBubbles } },
    );

    const streamingBubbles: Bubble[] = [
      makeAssistantBubble("resp_1", null, undefined, false, "streaming"),
    ];
    rerender({ bubbles: streamingBubbles });

    const finalBubbles: Bubble[] = [
      makeAssistantBubble("resp_1", "item_1", undefined, true, "completed"),
    ];
    rerender({ bubbles: finalBubbles });

    expect(mockEngine.speak).not.toHaveBeenCalled();
  });

  it("does NOT speak when the live assistant message is not final", () => {
    writeSpokenSummaryPlayback(true);
    const initialBubbles: Bubble[] = [];

    const { rerender } = renderHook(
      ({ bubbles }) => useSpokenSummaryPlayback(bubbles),
      { initialProps: { bubbles: initialBubbles } },
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
    rerender({ bubbles: streamingBubbles });

    expect(mockEngine.speak).not.toHaveBeenCalled();
  });

  it("does NOT speak when spoken summary text is empty string or whitespace", () => {
    writeSpokenSummaryPlayback(true);
    const { rerender } = renderHook(
      ({ bubbles }) => useSpokenSummaryPlayback(bubbles),
      { initialProps: { bubbles: [] as Bubble[] } },
    );

    const streamingBubbles: Bubble[] = [
      makeAssistantBubble("resp_1", null, undefined, false, "streaming"),
    ];
    rerender({ bubbles: streamingBubbles });

    const finalBubbles: Bubble[] = [
      makeAssistantBubble("resp_1", "item_1", { text: "   ", lang: "en-US" }, true, "completed"),
    ];
    rerender({ bubbles: finalBubbles });

    expect(mockEngine.speak).not.toHaveBeenCalled();
  });

  it("cancels in-flight speech when a new summary arrives", () => {
    writeSpokenSummaryPlayback(true);
    const { rerender } = renderHook(
      ({ bubbles }) => useSpokenSummaryPlayback(bubbles),
      { initialProps: { bubbles: [] as Bubble[] } },
    );

    // Turn 1 streams and completes
    const bubbles1Streaming: Bubble[] = [
      makeAssistantBubble("resp_1", null, undefined, false, "streaming"),
    ];
    rerender({ bubbles: bubbles1Streaming });

    const bubbles1Done: Bubble[] = [
      makeAssistantBubble(
        "resp_1",
        "item_1",
        { text: "First summary", lang: "pt-BR" },
        true,
        "completed",
      ),
    ];
    rerender({ bubbles: bubbles1Done });

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
    rerender({ bubbles: bubbles2Streaming });

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
    rerender({ bubbles: bubbles2Done });

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

  it("plays spoken summary exactly once for a tool-using turn arriving incrementally", () => {
    writeSpokenSummaryPlayback(true);

    const { rerender } = renderHook(
      ({ bubbles }) => useSpokenSummaryPlayback(bubbles),
      { initialProps: { bubbles: [] as Bubble[] } },
    );

    // Step 1: Preliminary text arrives (final: true, no summary, turn streaming)
    const step1Items: RenderItem[] = [
      {
        kind: "text",
        itemId: "txt_prelim",
        text: "I'll check the files...",
        final: true,
      },
    ];
    rerender({
      bubbles: [makeAssistantBubbleWithItems("resp_tools", step1Items, "streaming")],
    });
    expect(mockEngine.speak).not.toHaveBeenCalled();

    // Step 2: Tool call item arrives (tool running)
    const step2Items: RenderItem[] = [
      ...step1Items,
      {
        kind: "tool",
        itemId: "tool_1",
        execution: {
          callId: "call_1",
          name: "list_files",
          arguments: {},
          argsSummary: "",
          agentName: "test",
          executedBy: "server",
          output: null,
        },
        output: null,
        state: "input-available",
        startedAt: 100,
        duration: undefined,
      },
    ];
    rerender({
      bubbles: [makeAssistantBubbleWithItems("resp_tools", step2Items, "streaming")],
    });
    expect(mockEngine.speak).not.toHaveBeenCalled();

    // Step 3: Tool result arrives (tool finished, turn still streaming)
    const step3Items: RenderItem[] = [
      step1Items[0]!,
      {
        kind: "tool",
        itemId: "tool_1",
        execution: {
          callId: "call_1",
          name: "list_files",
          arguments: {},
          argsSummary: "",
          agentName: "test",
          executedBy: "server",
          output: '["file1.ts"]',
        },
        output: '["file1.ts"]',
        state: "output-available",
        startedAt: 100,
        duration: 50,
      },
    ];
    rerender({
      bubbles: [makeAssistantBubbleWithItems("resp_tools", step3Items, "streaming")],
    });
    expect(mockEngine.speak).not.toHaveBeenCalled();

    // Step 4: Final answer arrives WITH spoken summary, turn completes
    const step4Items: RenderItem[] = [
      ...step3Items,
      {
        kind: "text",
        itemId: "txt_final",
        text: "Here is what I found in the files.",
        final: true,
        spokenSummary: {
          text: "I found the requested files.",
          lang: "en-US",
        },
      },
    ];
    rerender({
      bubbles: [makeAssistantBubbleWithItems("resp_tools", step4Items, "completed")],
    });

    expect(mockEngine.speak).toHaveBeenCalledTimes(1);
    expect(mockEngine.speak).toHaveBeenCalledWith(
      "I found the requested files.",
      "en-US",
      expect.any(Function),
      expect.any(Function),
    );
    expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(true);
    expect(useSpeechPlaybackStore.getState().speakingItemId).toBe("resp_tools");
  });

  it("plays spoken summary exactly once for a tool turn when store reports NON-streaming lifecycle in the tool gap", () => {
    writeSpokenSummaryPlayback(true);

    const { rerender } = renderHook(
      ({ bubbles, activeResponseId }) => useSpokenSummaryPlayback(bubbles, activeResponseId),
      {
        initialProps: {
          bubbles: [] as Bubble[],
          activeResponseId: "resp_tool_gap" as string | null,
        },
      },
    );

    // Step 1: Preliminary text arrives (final: true, no summary, turn streaming)
    const step1Items: RenderItem[] = [
      {
        kind: "text",
        itemId: "txt_prelim",
        text: "Checking data...",
        final: true,
      },
    ];
    rerender({
      bubbles: [makeAssistantBubbleWithItems("resp_tool_gap", step1Items, "streaming")],
      activeResponseId: "resp_tool_gap",
    });
    expect(mockEngine.speak).not.toHaveBeenCalled();

    // Step 2: Tool call item arrives (tool running: state = input-available)
    const step2Items: RenderItem[] = [
      ...step1Items,
      {
        kind: "tool",
        itemId: "tool_1",
        execution: {
          callId: "call_1",
          name: "fetch_data",
          arguments: {},
          argsSummary: "",
          agentName: "test",
          executedBy: "server",
          output: null,
        },
        output: null,
        state: "input-available",
        startedAt: 100,
        duration: undefined,
      },
    ];
    rerender({
      bubbles: [makeAssistantBubbleWithItems("resp_tool_gap", step2Items, "streaming")],
      activeResponseId: "resp_tool_gap",
    });
    expect(mockEngine.speak).not.toHaveBeenCalled();

    // Step 3: Tool result arrives (state = output-available), BUT store reports a NON-streaming lifecycle ("completed")
    // in the gap before the final assistant message arrives.
    const step3Items: RenderItem[] = [
      step1Items[0]!,
      {
        kind: "tool",
        itemId: "tool_1",
        execution: {
          callId: "call_1",
          name: "fetch_data",
          arguments: {},
          argsSummary: "",
          agentName: "test",
          executedBy: "server",
          output: '{"status":"ok"}',
        },
        output: '{"status":"ok"}',
        state: "output-available",
        startedAt: 100,
        duration: 50,
      },
    ];
    rerender({
      bubbles: [makeAssistantBubbleWithItems("resp_tool_gap", step3Items, "completed")],
      activeResponseId: "resp_tool_gap",
    });
    // Critical assertion: summary must NOT be marked spoken early in the gap while response is still active
    expect(mockEngine.speak).not.toHaveBeenCalled();
    expect(isMessageSpoken("resp_tool_gap")).toBe(false);

    // Step 4: Final message arrives with spoken summary, turn completes
    const step4Items: RenderItem[] = [
      ...step3Items,
      {
        kind: "text",
        itemId: "txt_final",
        text: "Data was successfully fetched.",
        final: true,
        spokenSummary: {
          text: "Data was fetched successfully.",
          lang: "en-US",
        },
      },
    ];
    rerender({
      bubbles: [makeAssistantBubbleWithItems("resp_tool_gap", step4Items, "completed")],
      activeResponseId: "resp_tool_gap",
    });

    // Proves summary speaks exactly once and was not silenced by early marking
    expect(mockEngine.speak).toHaveBeenCalledTimes(1);
    expect(mockEngine.speak).toHaveBeenCalledWith(
      "Data was fetched successfully.",
      "en-US",
      expect.any(Function),
      expect.any(Function),
    );
    expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(true);
    expect(useSpeechPlaybackStore.getState().speakingItemId).toBe("resp_tool_gap");
  });

  it("skips bubbles with empty-string responseId (isolates hook guard from speechPlayback guards)", () => {
    writeSpokenSummaryPlayback(true);

    const speakLiveSummarySpy = vi.fn();
    useSpeechPlaybackStore.setState({ speakLiveSummary: speakLiveSummarySpy });
    const markMessagesSpokenSpy = vi.spyOn(speechPlayback, "markMessagesSpoken");

    const { rerender } = renderHook(
      ({ bubbles, activeResponseId }) => useSpokenSummaryPlayback(bubbles, activeResponseId),
      { initialProps: { bubbles: [] as Bubble[], activeResponseId: "" as string | null } },
    );

    // Stream arrives with empty string responseId
    const emptyRidStreaming: Bubble[] = [
      makeAssistantBubble("", null, undefined, false, "streaming"),
    ];
    rerender({ bubbles: emptyRidStreaming, activeResponseId: "" });

    // Empty responseId bubble finalizes with a summary
    const emptyRidCompleted: Bubble[] = [
      makeAssistantBubble(
        "",
        "item_1",
        { text: "Should never speak", lang: "en-US" },
        true,
        "completed",
      ),
    ];
    rerender({ bubbles: emptyRidCompleted, activeResponseId: null });

    // The hook guard `if (!responseId) continue` must prevent delegating to playback or marking.
    // Isolating via store and module spies ensures downstream guards in speechPlayback
    // (if (!itemId) return false / if (!id) return) do not mask a regression if the hook guard is reverted.
    expect(speakLiveSummarySpy).not.toHaveBeenCalled();
    expect(markMessagesSpokenSpy).not.toHaveBeenCalled();
    expect(mockEngine.speak).not.toHaveBeenCalled();
  });

  it("marks an active turn spoken and never speaks it when demoted by a newer assistant turn (!isLatestTurn)", () => {
    writeSpokenSummaryPlayback(true);

    const { rerender } = renderHook(
      ({ bubbles, activeResponseId }) => useSpokenSummaryPlayback(bubbles, activeResponseId),
      {
        initialProps: {
          bubbles: [] as Bubble[],
          activeResponseId: "resp_1" as string | null,
        },
      },
    );

    // 1. Turn 1 observed live streaming
    rerender({
      bubbles: [makeAssistantBubble("resp_1", null, undefined, false, "streaming")],
      activeResponseId: "resp_1",
    });
    expect(mockEngine.speak).not.toHaveBeenCalled();

    // 2. Turn 1 completes without a summary while still active in store
    rerender({
      bubbles: [makeAssistantBubble("resp_1", "item_1", undefined, true, "completed")],
      activeResponseId: "resp_1",
    });
    // Deferred from marking while active
    expect(isMessageSpoken("resp_1")).toBe(false);
    expect(mockEngine.speak).not.toHaveBeenCalled();

    // 3. A newer assistant turn arrives (resp_2), demoting resp_1 via !isLatestTurn
    // (even if store still holds resp_1 as activeResponseId, demotion alone forces marking)
    rerender({
      bubbles: [
        makeAssistantBubble("resp_1", "item_1", undefined, true, "completed"),
        makeAssistantBubble("resp_2", null, undefined, false, "streaming"),
      ],
      activeResponseId: "resp_1",
    });

    // resp_1 was demoted: unmarked state exits and turn becomes marked spoken
    expect(isMessageSpoken("resp_1")).toBe(true);
    expect(mockEngine.speak).not.toHaveBeenCalled();

    // 4. If resp_1 is rebuilt carrying a summary, it must NOT speak
    rerender({
      bubbles: [
        makeAssistantBubble(
          "resp_1",
          "item_1",
          { text: "Late summary for demoted turn", lang: "en-US" },
          true,
          "completed",
        ),
        makeAssistantBubble("resp_2", null, undefined, false, "streaming"),
      ],
      activeResponseId: "resp_1",
    });

    expect(mockEngine.speak).not.toHaveBeenCalled();
  });

  it("marks a turn spoken when retired from active store and asserts silence when rebuilt with a summary", () => {
    writeSpokenSummaryPlayback(true);

    const { rerender } = renderHook(
      ({ bubbles, activeResponseId }) => useSpokenSummaryPlayback(bubbles, activeResponseId),
      {
        initialProps: {
          bubbles: [] as Bubble[],
          activeResponseId: "resp_1" as string | null,
        },
      },
    );

    // 1. Turn 1 observed live streaming
    rerender({
      bubbles: [makeAssistantBubble("resp_1", null, undefined, false, "streaming")],
      activeResponseId: "resp_1",
    });
    expect(mockEngine.speak).not.toHaveBeenCalled();

    // 2. Client gets response.completed but misses output_item.done carrying summary (SSE reconnect gap).
    // Lifecycle is completed with no streaming items, no summary, but response is still active in store.
    rerender({
      bubbles: [makeAssistantBubble("resp_1", "item_1", undefined, true, "completed")],
      activeResponseId: "resp_1",
    });
    // Left unmarked while still active
    expect(isMessageSpoken("resp_1")).toBe(false);
    expect(mockEngine.speak).not.toHaveBeenCalled();

    // 3. Response is retired: store sets activeResponse to null (no longer active)
    rerender({
      bubbles: [makeAssistantBubble("resp_1", "item_1", undefined, true, "completed")],
      activeResponseId: null,
    });
    // The unmarked window exits: resp_1 must now be marked spoken
    expect(isMessageSpoken("resp_1")).toBe(true);
    expect(mockEngine.speak).not.toHaveBeenCalled();

    // 4. Minutes later bubbles are rebuilt from server data carrying the persisted summary.
    // Must assert SILENCE: never replay a turn the user finished reading long ago.
    rerender({
      bubbles: [
        makeAssistantBubble(
          "resp_1",
          "item_1",
          { text: "Late arriving summary from server", lang: "en-US" },
          true,
          "completed",
        ),
      ],
      activeResponseId: null,
    });

    expect(mockEngine.speak).not.toHaveBeenCalled();
    expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(false);
  });

  it("batches multiple turn marks into a single sessionStorage setItem persist (eliminates write churn)", () => {
    writeSpokenSummaryPlayback(true);
    sessionStorage.clear();
    resetSpokenMessageTracking();

    const setItemSpy = vi.spyOn(Storage.prototype, "setItem");

    const historyBubbles: Bubble[] = [];
    for (let i = 0; i < 250; i++) {
      historyBubbles.push(
        makeAssistantBubble(`resp_bulk_${i}`, `item_${i}`, undefined, true, "completed"),
      );
    }

    renderHook(() => useSpokenSummaryPlayback(historyBubbles, null));

    // A single setItem write occurred for all 250 marked turns instead of ~250 sequential writes
    expect(setItemSpy).toHaveBeenCalledTimes(1);

    const storedRaw = sessionStorage.getItem(SPOKEN_MESSAGES_SESSION_STORAGE_KEY);
    expect(storedRaw).not.toBeNull();
    const storedIds = JSON.parse(storedRaw!);
    expect(storedIds.length).toBe(MAX_PERSISTED_SPOKEN_IDS);
    // FIFO eviction retained newest 200 IDs (resp_bulk_50 .. resp_bulk_249)
    expect(storedIds[0]).toBe("resp_bulk_50");
    expect(storedIds[storedIds.length - 1]).toBe("resp_bulk_249");
    expect(storedIds).not.toContain("resp_bulk_0");
    expect(storedIds).not.toContain("resp_bulk_49");
  });

  it("does NOT re-speak when bubbles re-render without new messages", () => {
    writeSpokenSummaryPlayback(true);
    const initialBubbles: Bubble[] = [];

    const { rerender } = renderHook(
      ({ bubbles }) => useSpokenSummaryPlayback(bubbles),
      { initialProps: { bubbles: initialBubbles } },
    );

    const streamingBubbles: Bubble[] = [
      makeAssistantBubble("resp_1", null, undefined, false, "streaming"),
    ];
    rerender({ bubbles: streamingBubbles });

    const finalBubbles: Bubble[] = [
      makeAssistantBubble(
        "resp_1",
        "item_1",
        { text: "Summary 1", lang: "en-US" },
        true,
        "completed",
      ),
    ];
    rerender({ bubbles: finalBubbles });
    expect(mockEngine.speak).toHaveBeenCalledTimes(1);

    // Re-render with identical messages (new array instance)
    rerender({ bubbles: [...finalBubbles] });
    expect(mockEngine.speak).toHaveBeenCalledTimes(1);
  });

  it("does NOT speak prepended historical messages when pagination loads older history", () => {
    writeSpokenSummaryPlayback(true);
    const currentBubbles: Bubble[] = [
      makeAssistantBubble(
        "resp_2",
        "item_2",
        { text: "Tail message", lang: "en-US" },
        true,
        "completed",
      ),
    ];

    const { rerender } = renderHook(
      ({ bubbles }) => useSpokenSummaryPlayback(bubbles),
      { initialProps: { bubbles: currentBubbles } },
    );

    // Prepend older history at the beginning of transcript (pagination load)
    const prependedBubbles: Bubble[] = [
      makeAssistantBubble(
        "resp_1",
        "item_1",
        { text: "Older message", lang: "en-US" },
        true,
        "completed",
      ),
      ...currentBubbles,
    ];

    rerender({ bubbles: prependedBubbles });

    expect(mockEngine.speak).not.toHaveBeenCalled();
  });

  it("does not replay audio on hard reload mid-stream (sessionStorage persistence)", () => {
    writeSpokenSummaryPlayback(true);

    // 1. Live stream starts in tab
    const initialStreamBubbles: Bubble[] = [
      makeAssistantBubble("resp_reload", null, undefined, false, "streaming"),
    ];
    const { unmount } = renderHook(
      ({ bubbles }) => useSpokenSummaryPlayback(bubbles),
      {
        initialProps: {
          bubbles: initialStreamBubbles,
        },
      },
    );

    // Summary arrives and speech starts playing live
    const spoken = useSpeechPlaybackStore
      .getState()
      .speakLiveSummary("resp_reload", "Summary before reload", "en-US");
    expect(spoken).toBe(true);
    expect(mockEngine.speak).toHaveBeenCalledTimes(1);

    // 2. Hard reload mid-stream: component unmounts, speech stops, in-memory state is wiped,
    // but sessionStorage persists in the same tab.
    unmount();
    clearInMemorySpokenTracking();
    useSpeechPlaybackStore.setState({ isSpeaking: false, speakingItemId: null });

    // 3. Tab reconnects to the still-running session
    const { rerender: reloadedRerender } = renderHook(
      ({ bubbles }) => useSpokenSummaryPlayback(bubbles),
      {
        initialProps: {
          bubbles: initialStreamBubbles,
        },
      },
    );

    // 4. Turn finalizes after reload
    const completedBubbles: Bubble[] = [
      makeAssistantBubble(
        "resp_reload",
        "item_1",
        { text: "Summary before reload", lang: "en-US" },
        true,
        "completed",
      ),
    ];
    reloadedRerender({ bubbles: completedBubbles });

    // Assert speech is NOT played again from the top
    expect(mockEngine.speak).toHaveBeenCalledTimes(1);
  });

  describe("sessionStorage persistence and resilience", () => {
    it("falls back to in-memory-only tracking when sessionStorage throws (never permanently silences)", () => {
      const getItemSpy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
        throw new Error("Storage access restricted");
      });
      const setItemSpy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
        throw new Error("Storage quota exceeded");
      });

      try {
        clearInMemorySpokenTracking();
        // Storage throws: must NOT return true (permanent silence) for an unseen id
        expect(isMessageSpoken("unseen_turn")).toBe(false);

        // Marking as spoken succeeds in-memory
        markMessageSpoken("unseen_turn");
        expect(isMessageSpoken("unseen_turn")).toBe(true);
      } finally {
        getItemSpy.mockRestore();
        setItemSpy.mockRestore();
      }
    });

    it("self-heals corrupted sessionStorage (invalid JSON or non-array) by removing key", () => {
      // 1. Corrupt JSON
      sessionStorage.setItem(SPOKEN_MESSAGES_SESSION_STORAGE_KEY, "not{valid:json");
      clearInMemorySpokenTracking();
      expect(isMessageSpoken("any_id")).toBe(false);
      expect(sessionStorage.getItem(SPOKEN_MESSAGES_SESSION_STORAGE_KEY)).toBeNull();

      // 2. Non-array JSON value
      sessionStorage.setItem(
        SPOKEN_MESSAGES_SESSION_STORAGE_KEY,
        JSON.stringify({ not: "an array" }),
      );
      clearInMemorySpokenTracking();
      expect(isMessageSpoken("any_id")).toBe(false);
      expect(sessionStorage.getItem(SPOKEN_MESSAGES_SESSION_STORAGE_KEY)).toBeNull();
    });

    it("caps persisted list at 200 IDs FIFO in insertion order", () => {
      clearInMemorySpokenTracking();
      sessionStorage.clear();

      for (let i = 0; i < 250; i++) {
        markMessageSpoken(`id_${i}`);
      }

      const raw = sessionStorage.getItem(SPOKEN_MESSAGES_SESSION_STORAGE_KEY);
      expect(raw).not.toBeNull();
      const stored = JSON.parse(raw!);
      expect(Array.isArray(stored)).toBe(true);
      expect(stored.length).toBe(MAX_PERSISTED_SPOKEN_IDS);
      expect(stored.length).toBe(200);
      // FIFO: oldest 50 (id_0..id_49) were dropped; contains id_50..id_249
      expect(stored[0]).toBe("id_50");
      expect(stored[stored.length - 1]).toBe("id_249");
      expect(stored).not.toContain("id_0");
      expect(stored).not.toContain("id_49");
    });
  });

  describe("isToolStreaming", () => {
    it("returns true only for input-available and false for settled states", () => {
      expect(isToolStreaming("input-available")).toBe(true);
      expect(isToolStreaming("output-available")).toBe(false);
      expect(isToolStreaming("output-error")).toBe(false);
      expect(isToolStreaming("cancelled")).toBe(false);
      expect(isToolStreaming("no-output")).toBe(false);
    });
  });

  it("toggling 'Speak responses' OFF stops any in-flight utterance", () => {
    writeSpokenSummaryPlayback(true);
    useSpeechPlaybackStore.setState({ isSpeaking: true, speakingItemId: "resp_in_flight" });

    // Exercise decoupled SpokenSummaryPlaybackControl component toggle handler
    render(<SpokenSummaryPlaybackControl />);

    const toggle = screen.getByTestId("spoken-summary-playback-toggle");
    expect(toggle).toBeInTheDocument();
    expect(toggle).toHaveAttribute("data-state", "checked");

    fireEvent.click(toggle);

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
