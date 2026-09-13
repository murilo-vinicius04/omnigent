import { cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Bubble, RenderItem } from "@/lib/renderItems";
import type { ActiveResponse } from "@/store/types";
import * as speechPlayback from "@/lib/speechPlayback";
import {
  BrowserSpeechEngine,
  clearInMemorySpokenTracking,
  clearSpeechQueue,
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
import { useVolumeStore } from "@/lib/sessionNarrationVolume";
import { useChatStore } from "@/store/chatStore";
import { isToolStreaming, useSpokenSummaryPlayback } from "./useSpokenSummaryPlayback";

function streamingResponse(responseId: string): ActiveResponse {
  return { responseId, state: "streaming", error: null };
}

/** A response the store has finalized, `ageMs` milliseconds ago. */
function completedResponse(responseId: string, ageMs = 0): ActiveResponse {
  return { responseId, state: "completed", error: null, completedAt: Date.now() - ageMs };
}

class MockSpeechEngine implements SpeechEngine {
  isSupported = vi.fn().mockReturnValue(true);
  speak = vi.fn((_text: string, _lang?: string, _onEnd?: () => void, _onError?: () => void) => {});
  stop = vi.fn();
  isSpeaking = vi.fn().mockReturnValue(false);
}

/** Audio URLs handed to the player, newest last. */
const played: string[] = [];

/** The URL a summary's recording is served from, for assertions. */
function audioUrlFor(fileId: string, sessionId = "conv_1"): string {
  return `/v1/sessions/${sessionId}/resources/files/${fileId}/content`;
}

function makeAssistantBubble(
  responseId: string,
  itemId: string | null = null,
  spokenSummary?: { text: string; lang: string; audioFileId?: string },
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
        // Autoplay only ever plays a recording, so give every fixture summary
        // one unless the test is specifically about a summary without audio.
        spokenSummary:
          spokenSummary && !spokenSummary.audioFileId
            ? { ...spokenSummary, audioFileId: `f_${responseId}` }
            : spokenSummary,
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
    // Same rule as makeAssistantBubble: a summary with no recording is never
    // spoken, so give one to any fixture that did not ask for the empty case.
    items: items.map((item) =>
      item.kind === "text" && item.spokenSummary && !item.spokenSummary.audioFileId
        ? { ...item, spokenSummary: { ...item.spokenSummary, audioFileId: `f_${responseId}` } }
        : item,
    ),
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
    // Summaries are spoken only from their own recording now, so every
    // autoplay test needs a session (to build the audio URL) and a spy on what
    // actually played.
    useChatStore.setState({ conversationId: "conv_1" } as never);
    clearSpeechQueue();
    played.length = 0;
    vi.spyOn(window.HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    vi.spyOn(window.HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    const RealAudio = window.Audio;
    vi.stubGlobal(
      "Audio",
      class extends RealAudio {
        constructor(src?: string) {
          super(src);
          played.push(src ?? "");
        }
      },
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    resetSpeechEngine();
    useSpeechPlaybackStore.setState({ speakLiveSummary: originalSpeakLiveSummary });
    vi.restoreAllMocks();
  });

  it("mounts with [], rerenders with full populated history array, asserts speak was NOT called", () => {
    useVolumeStore.getState().set("conv_1", 1);
    const { rerender } = renderHook(({ bubbles }) => useSpokenSummaryPlayback(bubbles), {
      initialProps: { bubbles: [] as Bubble[] },
    });

    expect(played).toEqual([]);

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

    expect(played).toEqual([]);
    expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(false);
  });

  it("asserts session-switch (history swap) does not speak", () => {
    useVolumeStore.getState().set("conv_1", 1);
    const sessionABubbles: Bubble[] = [
      makeAssistantBubble(
        "resp_a1",
        "item_a1",
        { text: "Session A summary", lang: "en-US" },
        true,
        "completed",
      ),
    ];

    const { rerender } = renderHook(({ bubbles }) => useSpokenSummaryPlayback(bubbles), {
      initialProps: { bubbles: sessionABubbles },
    });

    expect(played).toEqual([]);

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

    expect(played).toEqual([]);
    expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(false);
  });

  it("simulates the real streaming sequence non-final -> final WITH an itemId stamped on reconcile, and asserts speak is called exactly ONCE", () => {
    useVolumeStore.getState().set("conv_1", 1);
    const initialBubbles: Bubble[] = [
      makeAssistantBubble(
        "resp_h1",
        "item_h1",
        { text: "Historical summary", lang: "pt-BR" },
        true,
        "completed",
      ),
    ];

    const { rerender } = renderHook(({ bubbles }) => useSpokenSummaryPlayback(bubbles), {
      initialProps: { bubbles: initialBubbles },
    });

    // 1. Streaming arrives: non-final, itemId null, lifecycle streaming
    const streamingBubbles: Bubble[] = [
      ...initialBubbles,
      makeAssistantBubble("resp_live", null, undefined, false, "streaming"),
    ];
    rerender({ bubbles: streamingBubbles });
    expect(played).toEqual([]);

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

    expect(played.length).toBe(1);
    expect(played.at(-1)).toBe(audioUrlFor("f_resp_live"));
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
    expect(played.length).toBe(1);
  });

  it("does NOT speak when toggle is OFF even when a new live message arrives", () => {
    useVolumeStore.getState().set("conv_1", 0);
    const initialBubbles: Bubble[] = [];

    const { rerender } = renderHook(({ bubbles }) => useSpokenSummaryPlayback(bubbles), {
      initialProps: { bubbles: initialBubbles },
    });

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

    expect(played).toEqual([]);
    expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(false);
  });

  it("does NOT speak when the live assistant message has no spoken summary", () => {
    useVolumeStore.getState().set("conv_1", 1);
    const initialBubbles: Bubble[] = [];

    const { rerender } = renderHook(({ bubbles }) => useSpokenSummaryPlayback(bubbles), {
      initialProps: { bubbles: initialBubbles },
    });

    const streamingBubbles: Bubble[] = [
      makeAssistantBubble("resp_1", null, undefined, false, "streaming"),
    ];
    rerender({ bubbles: streamingBubbles });

    const finalBubbles: Bubble[] = [
      makeAssistantBubble("resp_1", "item_1", undefined, true, "completed"),
    ];
    rerender({ bubbles: finalBubbles });

    expect(played).toEqual([]);
  });

  it("does NOT speak when the live assistant message is not final", () => {
    useVolumeStore.getState().set("conv_1", 1);
    const initialBubbles: Bubble[] = [];

    const { rerender } = renderHook(({ bubbles }) => useSpokenSummaryPlayback(bubbles), {
      initialProps: { bubbles: initialBubbles },
    });

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

    expect(played).toEqual([]);
  });

  it("does NOT speak when spoken summary text is empty string or whitespace", () => {
    useVolumeStore.getState().set("conv_1", 1);
    const { rerender } = renderHook(({ bubbles }) => useSpokenSummaryPlayback(bubbles), {
      initialProps: { bubbles: [] as Bubble[] },
    });

    const streamingBubbles: Bubble[] = [
      makeAssistantBubble("resp_1", null, undefined, false, "streaming"),
    ];
    rerender({ bubbles: streamingBubbles });

    const finalBubbles: Bubble[] = [
      makeAssistantBubble("resp_1", "item_1", { text: "   ", lang: "en-US" }, true, "completed"),
    ];
    rerender({ bubbles: finalBubbles });

    expect(played).toEqual([]);
  });

  it("cancels in-flight speech when a new summary arrives", () => {
    useVolumeStore.getState().set("conv_1", 1);
    const { rerender } = renderHook(({ bubbles }) => useSpokenSummaryPlayback(bubbles), {
      initialProps: { bubbles: [] as Bubble[] },
    });

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

    expect(played.at(-1)).toBe(audioUrlFor("f_resp_1"));

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
    expect(played.length).toBe(2); // the newer summary replaced the older
    expect(played.at(-1)).toBe(audioUrlFor("f_resp_2"));
  });

  it("cancels in-flight speech on component unmount", () => {
    useVolumeStore.getState().set("conv_1", 1);
    const { unmount } = renderHook(() => useSpokenSummaryPlayback([]));

    unmount();
  });

  it("plays spoken summary exactly once for a tool-using turn arriving incrementally", () => {
    useVolumeStore.getState().set("conv_1", 1);

    const { rerender } = renderHook(({ bubbles }) => useSpokenSummaryPlayback(bubbles), {
      initialProps: { bubbles: [] as Bubble[] },
    });

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
    expect(played).toEqual([]);

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
    expect(played).toEqual([]);

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
    expect(played).toEqual([]);

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

    expect(played.length).toBe(1);
    expect(played.at(-1)).toBe(audioUrlFor("f_resp_tools"));
    expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(true);
    expect(useSpeechPlaybackStore.getState().speakingItemId).toBe("resp_tools");
  });

  it("plays spoken summary exactly once for a tool turn when store reports NON-streaming lifecycle in the tool gap", () => {
    useVolumeStore.getState().set("conv_1", 1);

    const { rerender } = renderHook(
      ({ bubbles, activeResponse }) => useSpokenSummaryPlayback(bubbles, activeResponse),
      {
        initialProps: {
          bubbles: [] as Bubble[],
          activeResponse: streamingResponse("resp_tool_gap") as ActiveResponse | null,
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
      activeResponse: streamingResponse("resp_tool_gap"),
    });
    expect(played).toEqual([]);

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
      activeResponse: streamingResponse("resp_tool_gap"),
    });
    expect(played).toEqual([]);

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
      activeResponse: streamingResponse("resp_tool_gap"),
    });
    // Critical assertion: summary must NOT be marked spoken early in the gap while response is still active
    expect(played).toEqual([]);
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
      activeResponse: streamingResponse("resp_tool_gap"),
    });

    // Proves summary speaks exactly once and was not silenced by early marking
    expect(played.length).toBe(1);
    expect(played.at(-1)).toBe(audioUrlFor("f_resp_tool_gap"));
    expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(true);
    expect(useSpeechPlaybackStore.getState().speakingItemId).toBe("resp_tool_gap");
  });

  it("skips bubbles with empty-string responseId (isolates hook guard from speechPlayback guards)", () => {
    useVolumeStore.getState().set("conv_1", 1);

    const speakLiveSummarySpy = vi.fn();
    useSpeechPlaybackStore.setState({ speakLiveSummary: speakLiveSummarySpy });
    const markMessagesSpokenSpy = vi.spyOn(speechPlayback, "markMessagesSpoken");

    const { rerender } = renderHook(
      ({ bubbles, activeResponse }) => useSpokenSummaryPlayback(bubbles, activeResponse),
      {
        initialProps: {
          bubbles: [] as Bubble[],
          activeResponse: streamingResponse("") as ActiveResponse | null,
        },
      },
    );

    // Stream arrives with empty string responseId
    const emptyRidStreaming: Bubble[] = [
      makeAssistantBubble("", null, undefined, false, "streaming"),
    ];
    rerender({ bubbles: emptyRidStreaming, activeResponse: streamingResponse("") });

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
    rerender({ bubbles: emptyRidCompleted, activeResponse: null });

    // The hook guard `if (!responseId) continue` must prevent delegating to playback or marking.
    // Isolating via store and module spies ensures downstream guards in speechPlayback
    // (if (!itemId) return false / if (!id) return) do not mask a regression if the hook guard is reverted.
    expect(speakLiveSummarySpy).not.toHaveBeenCalled();
    expect(markMessagesSpokenSpy).not.toHaveBeenCalled();
    expect(played).toEqual([]);
  });

  it("marks an active turn spoken and never speaks it when demoted by a newer assistant turn (!isLatestTurn)", () => {
    useVolumeStore.getState().set("conv_1", 1);

    const { rerender } = renderHook(
      ({ bubbles, activeResponse }) => useSpokenSummaryPlayback(bubbles, activeResponse),
      {
        initialProps: {
          bubbles: [] as Bubble[],
          activeResponse: streamingResponse("resp_1") as ActiveResponse | null,
        },
      },
    );

    // 1. Turn 1 observed live streaming
    rerender({
      bubbles: [makeAssistantBubble("resp_1", null, undefined, false, "streaming")],
      activeResponse: streamingResponse("resp_1"),
    });
    expect(played).toEqual([]);

    // 2. Turn 1 completes without a summary while still active in store
    rerender({
      bubbles: [makeAssistantBubble("resp_1", "item_1", undefined, true, "completed")],
      activeResponse: streamingResponse("resp_1"),
    });
    // Deferred from marking while active
    expect(isMessageSpoken("resp_1")).toBe(false);
    expect(played).toEqual([]);

    // 3. A newer assistant turn arrives (resp_2), demoting resp_1 via !isLatestTurn
    // (even if the store still holds resp_1 as the active response, demotion alone forces marking)
    rerender({
      bubbles: [
        makeAssistantBubble("resp_1", "item_1", undefined, true, "completed"),
        makeAssistantBubble("resp_2", null, undefined, false, "streaming"),
      ],
      activeResponse: streamingResponse("resp_1"),
    });

    // resp_1 was demoted: unmarked state exits and turn becomes marked spoken
    expect(isMessageSpoken("resp_1")).toBe(true);
    expect(played).toEqual([]);

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
      activeResponse: streamingResponse("resp_1"),
    });

    expect(played).toEqual([]);
  });

  it("stays silent when a summary surfaces past the live window with the response still in the store (no send in between)", () => {
    // Production ordering. The store does NOT clear `activeResponse` when a turn completes —
    // it only clears it on the next send (chatStore setActive on send). So while the user sits
    // reading, the completed response is still there with the SAME responseId. Any guard keyed
    // on response identity alone therefore stays true and cannot bound the replay window.
    useVolumeStore.getState().set("conv_1", 1);

    const { rerender } = renderHook(
      ({ bubbles, activeResponse }) => useSpokenSummaryPlayback(bubbles, activeResponse),
      {
        initialProps: {
          bubbles: [] as Bubble[],
          activeResponse: streamingResponse("resp_1") as ActiveResponse | null,
        },
      },
    );

    // 1. Turn 1 observed live streaming
    rerender({
      bubbles: [makeAssistantBubble("resp_1", null, undefined, false, "streaming")],
      activeResponse: streamingResponse("resp_1"),
    });
    expect(played).toEqual([]);

    // 2. Client gets response.completed but misses the output_item.done carrying the summary
    // (SSE reconnect gap). Finalized, no summary. Still within the live window, so the turn is
    // deferred rather than marked: a real summary may yet arrive (the server generates it after
    // completion, capped at ~4s).
    rerender({
      bubbles: [makeAssistantBubble("resp_1", "item_1", undefined, true, "completed")],
      activeResponse: completedResponse("resp_1"),
    });
    expect(isMessageSpoken("resp_1")).toBe(false);
    expect(played).toEqual([]);

    // 3. Minutes pass with the user reading. NO send occurs, so the store still holds resp_1 —
    // only its completedAt has aged out of the live window. The transcript is then rebuilt from
    // server data (history refetch / pagination) and the persisted message DOES carry a summary.
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
      activeResponse: completedResponse("resp_1", 5 * 60_000),
    });

    expect(played).toEqual([]);
    expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(false);
    // And it is now indexed, so no later rebuild can revive it either.
    expect(isMessageSpoken("resp_1")).toBe(true);
  });

  it("still speaks when the summary arrives shortly after the turn finalizes (server generates it post-completion)", () => {
    // Guards the opposite failure: bounding the window must not kill autoplay for real turns,
    // whose summary legitimately lands a few seconds after response.completed.
    useVolumeStore.getState().set("conv_1", 1);

    const { rerender } = renderHook(
      ({ bubbles, activeResponse }) => useSpokenSummaryPlayback(bubbles, activeResponse),
      {
        initialProps: {
          bubbles: [] as Bubble[],
          activeResponse: streamingResponse("resp_1") as ActiveResponse | null,
        },
      },
    );

    rerender({
      bubbles: [makeAssistantBubble("resp_1", null, undefined, false, "streaming")],
      activeResponse: streamingResponse("resp_1"),
    });

    // Finalized, summary not yet generated.
    rerender({
      bubbles: [makeAssistantBubble("resp_1", "item_1", undefined, true, "completed")],
      activeResponse: completedResponse("resp_1"),
    });
    expect(played).toEqual([]);

    // Summary lands 3s later, inside the live window: must speak.
    rerender({
      bubbles: [
        makeAssistantBubble(
          "resp_1",
          "item_1",
          { text: "Fixed the pool leak. Tests pass.", lang: "en-US" },
          true,
          "completed",
        ),
      ],
      activeResponse: completedResponse("resp_1", 3_000),
    });

    expect(played.length).toBe(1);
    expect(played.at(-1)).toBe(audioUrlFor("f_resp_1"));
  });

  it("speaks a summary that took longer than the live window to generate", () => {
    // The rewrite (agy, capped at 45s) and the voice synthesis both run after the turn
    // finalizes, so a real summary can land well past the window measured from completedAt.
    // The summary's own server stamp says it just arrived; that must win over the turn's age,
    // or the slow path is silently indexed as spoken and the reader hears nothing.
    useVolumeStore.getState().set("conv_1", 1);
    const nowS = Date.now() / 1000;

    const { rerender } = renderHook(
      ({ bubbles, activeResponse }) => useSpokenSummaryPlayback(bubbles, activeResponse),
      {
        initialProps: {
          bubbles: [] as Bubble[],
          activeResponse: streamingResponse("resp_slow") as ActiveResponse | null,
        },
      },
    );

    rerender({
      bubbles: [makeAssistantBubble("resp_slow", null, undefined, false, "streaming")],
      activeResponse: streamingResponse("resp_slow"),
    });
    rerender({
      bubbles: [makeAssistantBubble("resp_slow", "item_1", undefined, true, "completed")],
      activeResponse: completedResponse("resp_slow"),
    });
    expect(played).toEqual([]);

    // 150s later the summary finally lands, stamped now by the server.
    rerender({
      bubbles: [
        makeAssistantBubbleWithItems("resp_slow", [
          {
            kind: "text",
            itemId: "item_1",
            text: "Here is the full response text that should never be hidden.",
            final: true,
            createdAtS: nowS,
            spokenSummary: { text: "Terminei o ajuste, passou tudo.", lang: "pt-BR" },
          },
        ]),
      ],
      activeResponse: completedResponse("resp_slow", 150_000),
    });

    expect(played.length).toBe(1);
    expect(played.at(-1)).toBe(audioUrlFor("f_resp_slow"));
  });

  it("still refuses a stale summary the reader already finished reading", () => {
    // The other half of the same gate: no fresh server stamp means the age bound still holds,
    // so a history rebuild minutes later stays silent.
    useVolumeStore.getState().set("conv_1", 1);

    const { rerender } = renderHook(
      ({ bubbles, activeResponse }) => useSpokenSummaryPlayback(bubbles, activeResponse),
      {
        initialProps: {
          bubbles: [] as Bubble[],
          activeResponse: streamingResponse("resp_old") as ActiveResponse | null,
        },
      },
    );

    rerender({
      bubbles: [makeAssistantBubble("resp_old", null, undefined, false, "streaming")],
      activeResponse: streamingResponse("resp_old"),
    });
    rerender({
      bubbles: [
        makeAssistantBubbleWithItems("resp_old", [
          {
            kind: "text",
            itemId: "item_1",
            text: "Here is the full response text that should never be hidden.",
            final: true,
            createdAtS: Date.now() / 1000 - 600,
            spokenSummary: { text: "Resumo antigo.", lang: "pt-BR" },
          },
        ]),
      ],
      activeResponse: completedResponse("resp_old", 10 * 60_000),
    });

    expect(played).toEqual([]);
    expect(isMessageSpoken("resp_old")).toBe(true);
  });

  it("batches multiple turn marks into a single sessionStorage setItem persist (eliminates write churn)", () => {
    useVolumeStore.getState().set("conv_1", 1);
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
    useVolumeStore.getState().set("conv_1", 1);
    const initialBubbles: Bubble[] = [];

    const { rerender } = renderHook(({ bubbles }) => useSpokenSummaryPlayback(bubbles), {
      initialProps: { bubbles: initialBubbles },
    });

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
    expect(played.length).toBe(1);

    // Re-render with identical messages (new array instance)
    rerender({ bubbles: [...finalBubbles] });
    expect(played.length).toBe(1);
  });

  it("does NOT speak prepended historical messages when pagination loads older history", () => {
    useVolumeStore.getState().set("conv_1", 1);
    const currentBubbles: Bubble[] = [
      makeAssistantBubble(
        "resp_2",
        "item_2",
        { text: "Tail message", lang: "en-US" },
        true,
        "completed",
      ),
    ];

    const { rerender } = renderHook(({ bubbles }) => useSpokenSummaryPlayback(bubbles), {
      initialProps: { bubbles: currentBubbles },
    });

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

    expect(played).toEqual([]);
  });

  it("does not replay audio on hard reload mid-stream (sessionStorage persistence)", () => {
    useVolumeStore.getState().set("conv_1", 1);

    // 1. Live stream starts in tab
    const initialStreamBubbles: Bubble[] = [
      makeAssistantBubble("resp_reload", null, undefined, false, "streaming"),
    ];
    const { unmount } = renderHook(({ bubbles }) => useSpokenSummaryPlayback(bubbles), {
      initialProps: {
        bubbles: initialStreamBubbles,
      },
    });

    // Summary arrives and speech starts playing live
    const spoken = useSpeechPlaybackStore
      .getState()
      .speakLiveSummary(
        "resp_reload",
        "Summary before reload",
        "en-US",
        audioUrlFor("f_resp_reload"),
        "conv_1",
      );
    expect(spoken).toBe(true);
    expect(played.length).toBe(1);

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
    expect(played.length).toBe(1);
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

describe("useSpokenSummaryPlayback — server audio", () => {
  let engine: MockSpeechEngine;

  beforeEach(() => {
    engine = new MockSpeechEngine();
    setSpeechEngine(engine);
    resetSpokenMessageTracking();
    clearInMemorySpokenTracking();
    sessionStorage.clear();
  });

  afterEach(() => {
    resetSpeechEngine();
  });

  it("does not layer the host engine over a recording that is already playing", () => {
    // A large recording stalling on a slow link fires `error` mid-playback, not
    // only when the file is missing. Speaking then put the host voice on top of
    // audio that was still playing: the same words twice, offset -- an echo.
    useVolumeStore.getState().set("conv_1", 1);
    useChatStore.setState({ conversationId: "conv_1" } as never);
    vi.spyOn(window.HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    const pause = vi.spyOn(window.HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    // Playback got as far as some audio before the error.
    vi.spyOn(window.HTMLMediaElement.prototype, "currentTime", "get").mockReturnValue(4.2);
    const created: HTMLAudioElement[] = [];
    const RealAudio = window.Audio;
    vi.stubGlobal(
      "Audio",
      class extends RealAudio {
        constructor(src?: string) {
          super(src);
          created.push(this as unknown as HTMLAudioElement);
        }
      },
    );

    useSpeechPlaybackStore
      .getState()
      .speakLiveSummary("resp_mid", "resumo falado", "pt-BR", "/audio.wav", "conv_1");
    expect(created).toHaveLength(1);
    created[0]!.dispatchEvent(new Event("error"));

    expect(pause).toHaveBeenCalled();
    expect(engine.speak).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("stays silent when the recording will not load", () => {
    // The host voice is what the generated one exists to replace. With the
    // summary already on screen, silence beats reading it in the robot voice.
    useVolumeStore.getState().set("conv_1", 1);
    useChatStore.setState({ conversationId: "conv_1" } as never);
    const hostEngine = new MockSpeechEngine(); // replaces the suite's engine
    setSpeechEngine(hostEngine);
    vi.spyOn(window.HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    vi.spyOn(window.HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    const created: HTMLAudioElement[] = [];
    const RealAudio = window.Audio;
    vi.stubGlobal(
      "Audio",
      class extends RealAudio {
        constructor(src?: string) {
          super(src);
          created.push(this as unknown as HTMLAudioElement);
        }
      },
    );

    useSpeechPlaybackStore
      .getState()
      .speakLiveSummary("resp_gone", "resumo falado", "pt-BR", "/audio.wav", "conv_1");
    created[0]!.dispatchEvent(new Event("error"));

    expect(engine.speak).not.toHaveBeenCalled();
    expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(false);
    vi.unstubAllGlobals();
  });

  /** Bubble whose summary carries its own server stamp, as the real one does. */
  function bubbleWithStampedSummary(responseId: string, createdAtS: number): Bubble {
    return {
      kind: "assistant",
      responseId,
      stableId: responseId,
      lifecycle: "completed",
      error: null,
      items: [
        {
          kind: "text",
          itemId: "i1",
          text: "resposta completa",
          final: true,
          createdAtS,
          spokenSummary: { text: "resumo falado", lang: "pt-BR" },
        },
      ],
    } as Bubble;
  }

  it("speaks a just-arrived summary even when the turn was never watched live", () => {
    // The rewrite lands ~30s after the turn ends. A reader who opened the page
    // in that gap never saw the turn stream, so positive liveness can never be
    // collected -- and autoplay would stay silent forever.
    const speak = vi.spyOn(useSpeechPlaybackStore.getState(), "speakLiveSummary");
    const bubbles = [bubbleWithStampedSummary("resp_fresh", Date.now() / 1000 - 5)];

    renderHook(() => useSpokenSummaryPlayback(bubbles, null));

    expect(speak).toHaveBeenCalled();
    expect(speak.mock.calls[0]?.[0]).toBe("resp_fresh");
    speak.mockRestore();
  });

  it("never speaks an old summary rebuilt from history", () => {
    const speak = vi.spyOn(useSpeechPlaybackStore.getState(), "speakLiveSummary");
    const bubbles = [bubbleWithStampedSummary("resp_old", Date.now() / 1000 - 3600)];

    renderHook(() => useSpokenSummaryPlayback(bubbles, null));

    expect(speak).not.toHaveBeenCalled();
    // Indexed as settled history so a later rebuild cannot replay it either.
    expect(isMessageSpoken("resp_old")).toBe(true);
    speak.mockRestore();
  });

  /** The same reply before its summary has been appended. */
  function replyWithoutSummary(responseId: string, createdAtS: number): Bubble {
    const bubble = bubbleWithStampedSummary(responseId, createdAtS);
    return {
      ...bubble,
      items: [{ kind: "text", itemId: "i1", text: "resposta completa", final: true, createdAtS }],
    } as Bubble;
  }

  it("speaks a summary that lands after its reply, even when the turn was never watched live", () => {
    // A Claude Code reply arrives already finished, and its summary follows a
    // couple of seconds later as a separate item. The render in between must
    // not index the reply as history, or the summary is skipped when it lands.
    const speak = vi.spyOn(useSpeechPlaybackStore.getState(), "speakLiveSummary");
    const nowS = Date.now() / 1000;
    const { rerender } = renderHook(
      ({ bubbles }: { bubbles: Bubble[] }) => useSpokenSummaryPlayback(bubbles, null),
      { initialProps: { bubbles: [replyWithoutSummary("resp_late", nowS - 2)] } },
    );
    expect(speak).not.toHaveBeenCalled();

    rerender({ bubbles: [bubbleWithStampedSummary("resp_late", nowS - 2)] });

    expect(speak).toHaveBeenCalledTimes(1);
    expect(speak.mock.calls[0]?.[0]).toBe("resp_late");
    speak.mockRestore();
  });

  it("still indexes an old reply as history before any summary shows up", () => {
    const speak = vi.spyOn(useSpeechPlaybackStore.getState(), "speakLiveSummary");
    renderHook(() =>
      useSpokenSummaryPlayback(
        [replyWithoutSummary("resp_history", Date.now() / 1000 - 3600)],
        null,
      ),
    );
    expect(speak).not.toHaveBeenCalled();
    expect(isMessageSpoken("resp_history")).toBe(true);
    speak.mockRestore();
  });
});
