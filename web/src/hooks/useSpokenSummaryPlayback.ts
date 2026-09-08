import { useEffect, useRef } from "react";
import type { Bubble, RenderItem, ToolState } from "@/lib/renderItems";
import { isMessageSpoken, markMessageSpoken, useSpeechPlaybackStore } from "@/lib/speechPlayback";

/**
 * Compile-time exhaustive check for ToolState.
 * Returns true if the tool execution is currently in-progress / streaming.
 */
export function isToolStreaming(state: ToolState): boolean {
  switch (state) {
    case "input-available":
      return true;
    case "output-available":
    case "output-error":
    case "cancelled":
    case "no-output":
      return false;
    default: {
      const exhaustiveCheck: never = state;
      return exhaustiveCheck;
    }
  }
}

/**
 * Hook to coordinate read-aloud playback of newly-arrived live assistant messages
 * carrying a spoken_summary part.
 *
 * Guarantees:
 * - Requires positive evidence this client observed the turn arrive live (seen in a
 *   non-final/streaming state).
 * - Historical turns (e.g. loaded history after mount with [], page refresh, or in-SPA session
 *   switches) are never spoken.
 * - Uses a single stable identity (responseId) across streaming, reconcile, and skim-line controls
 *   so mid-turn itemId stamping never causes duplicate playback.
 * - Cancels in-flight speech when a new summary arrives, on session switch, or on unmount.
 * - Falsy responseId turns are never spoken and never marked spoken.
 */
export function useSpokenSummaryPlayback(bubbles: Bubble[]): void {
  // Set of responseIds that were positively observed streaming live in this client session.
  const observedLiveResponseIdsRef = useRef<Set<string>>(new Set());
  const speakLiveSummary = useSpeechPlaybackStore((s) => s.speakLiveSummary);
  const stop = useSpeechPlaybackStore((s) => s.stop);

  useEffect(() => {
    // If audio is playing from a turn no longer in the transcript (e.g. session switch), stop it.
    const currentSpeakingId = useSpeechPlaybackStore.getState().speakingItemId;
    if (
      currentSpeakingId &&
      !bubbles.some((b) => b.kind === "assistant" && b.responseId === currentSpeakingId)
    ) {
      stop();
    }

    // 1. Positive evidence collection: observe live/streaming turns.
    for (const bubble of bubbles) {
      if (bubble.kind === "assistant") {
        const responseId = bubble.responseId;
        if (!responseId) {
          continue;
        }

        const isStreamingLifecycle =
          bubble.lifecycle === "streaming" || (bubble.lifecycle as string) === "running";
        const hasStreamingItem = bubble.items.some(
          (it) =>
            (it.kind === "text" && it.final === false) ||
            (it.kind === "tool" && isToolStreaming(it.state)),
        );

        if (isStreamingLifecycle || hasStreamingItem) {
          observedLiveResponseIdsRef.current.add(responseId);
        }
      }
    }

    // 2. Find the latest assistant bubble in the transcript — live speech only triggers at the active tail.
    let lastAssistantIdx = -1;
    for (let i = bubbles.length - 1; i >= 0; i--) {
      if (bubbles[i]?.kind === "assistant") {
        lastAssistantIdx = i;
        break;
      }
    }

    // 3. Process turns with stable responseId identity.
    for (let i = 0; i < bubbles.length; i++) {
      const bubble = bubbles[i];
      if (bubble?.kind === "assistant") {
        const responseId = bubble.responseId;
        if (!responseId) {
          continue;
        }

        const isLatestTurn = i === lastAssistantIdx;
        const wasObservedLive = observedLiveResponseIdsRef.current.has(responseId);

        // Select the text item that actually carries the spoken summary (the LAST/final one), not the first.
        const textItems = bubble.items.filter(
          (it): it is Extract<RenderItem, { kind: "text" }> => it.kind === "text",
        );
        const summaryItem = textItems
          .slice()
          .reverse()
          .find((it) => Boolean(it.spokenSummary && it.spokenSummary.text.trim().length > 0));
        const textItem = summaryItem ?? textItems[textItems.length - 1];

        // Do NOT mark a turn spoken while bubble.lifecycle === "streaming" or while any item is still streaming.
        // Both conditions must hold for the turn to be considered final:
        // bubble.lifecycle !== "streaming" AND no item is still streaming.
        const isBubbleStreaming =
          bubble.lifecycle === "streaming" || (bubble.lifecycle as string) === "running";
        const hasStreamingItem = bubble.items.some(
          (it) =>
            (it.kind === "text" && it.final === false) ||
            (it.kind === "tool" && isToolStreaming(it.state)),
        );
        const isFinal = !isBubbleStreaming && !hasStreamingItem;

        const summary = textItem?.spokenSummary;
        const hasValidSummary = Boolean(summary && summary.text.trim().length > 0);

        if (!isMessageSpoken(responseId)) {
          // Speak ONLY if:
          // - We observed positive evidence that this client watched the turn arrive live
          // - It is the latest assistant turn at the transcript tail
          // - It is finalized and has a valid non-empty summary
          if (wasObservedLive && isLatestTurn && isFinal && hasValidSummary) {
            speakLiveSummary(responseId, summary!.text, summary!.lang);
          } else if (isFinal && (!wasObservedLive || !isLatestTurn)) {
            // Settled history or non-tail turn: index as spoken so it is never replayed later.
            markMessageSpoken(responseId);
          }
        }
      }
    }
  }, [bubbles, speakLiveSummary, stop]);

  // Cancel in-flight speech when the component unmounts or user navigates away.
  useEffect(() => {
    return () => {
      stop();
    };
  }, [stop]);
}
