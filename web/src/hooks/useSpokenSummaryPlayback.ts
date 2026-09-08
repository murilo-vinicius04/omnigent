import { useEffect, useRef } from "react";
import type { Bubble, RenderItem, ToolState } from "@/lib/renderItems";
import {
  isMessageSpoken,
  markMessagesSpoken,
  useSpeechPlaybackStore,
} from "@/lib/speechPlayback";

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
 * - Active turn deferral: turns that finish without a summary are only deferred from marking
 *   while the store still considers the response active. Once retired (or demoted by a newer turn),
 *   they are indexed as spoken so late-arriving rebuilds never replay past turns.
 */
export function useSpokenSummaryPlayback(
  bubbles: Bubble[],
  activeResponseId?: string | null,
): void {
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
    const toMarkSpoken = new Set<string>();

    for (let i = 0; i < bubbles.length; i++) {
      const bubble = bubbles[i];
      if (bubble?.kind === "assistant") {
        const responseId = bubble.responseId;
        if (!responseId) {
          continue;
        }

        const isLatestTurn = i === lastAssistantIdx;
        const wasObservedLive = observedLiveResponseIdsRef.current.has(responseId);
        const isStillActive = Boolean(activeResponseId && activeResponseId === responseId);

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
        // Known deliberate bound: A tool stuck in 'input-available' (e.g. hung tool or pending approval)
        // holds isFinal false indefinitely, so the turn never speaks and never gets marked.
        // This deliberate bias to silence prevents premature playback.
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

        if (!isMessageSpoken(responseId) && !toMarkSpoken.has(responseId)) {
          // Speak ONLY if:
          // - We observed positive evidence that this client watched the turn arrive live
          // - It is the latest assistant turn at the transcript tail
          // - It is finalized and has a valid non-empty summary
          if (wasObservedLive && isLatestTurn && isFinal && hasValidSummary) {
            speakLiveSummary(responseId, summary!.text, summary!.lang);
          } else if (isFinal && (!wasObservedLive || !isLatestTurn || !isStillActive)) {
            // Settled history, non-tail turn, or retired active response without a summary:
            // queue for single-persist batch marking so it is never replayed later.
            toMarkSpoken.add(responseId);
          }
        }
      }
    }

    if (toMarkSpoken.size > 0) {
      markMessagesSpoken(Array.from(toMarkSpoken));
    }
  }, [bubbles, activeResponseId, speakLiveSummary, stop]);

  // Cancel in-flight speech when the component unmounts or user navigates away.
  useEffect(() => {
    return () => {
      stop();
    };
  }, [stop]);
}
