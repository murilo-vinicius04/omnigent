import { useEffect, useRef } from "react";
import type { Bubble } from "@/lib/renderItems";
import { isMessageSpoken, markMessageSpoken, useSpeechPlaybackStore } from "@/lib/speechPlayback";

/**
 * Hook to coordinate read-aloud playback of newly-arrived live assistant messages
 * carrying a spoken_summary part.
 *
 * Guarantees:
 * - Requires positive evidence this client observed the turn arrive live (either seen in a
 *   non-final/streaming state, or matching the activeResponse id).
 * - Historical turns (e.g. loaded history after mount with [], page refresh, or in-SPA session
 *   switches) are never spoken.
 * - Uses a single stable identity (responseId) across streaming, reconcile, and skim-line controls
 *   so mid-turn itemId stamping never causes duplicate playback.
 * - Cancels in-flight speech when a new summary arrives, on session switch, or on unmount.
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
        const isStreamingLifecycle =
          bubble.lifecycle === "streaming" || (bubble.lifecycle as string) === "running";
        const hasNonFinalItem = bubble.items.some((it) => it.kind === "text" && it.final === false);
        const isActiveResponse = Boolean(
          activeResponseId && activeResponseId === bubble.responseId,
        );

        if (isStreamingLifecycle || hasNonFinalItem || isActiveResponse) {
          observedLiveResponseIdsRef.current.add(bubble.responseId);
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
        const isLatestTurn = i === lastAssistantIdx;
        const wasObservedLive = observedLiveResponseIdsRef.current.has(responseId);

        const textItem = bubble.items.find((it) => it.kind === "text");
        const isFinal = textItem ? textItem.final !== false : bubble.lifecycle !== "streaming";
        const summary = textItem?.spokenSummary;
        const hasValidSummary = Boolean(summary && summary.text.trim().length > 0);

        if (!isMessageSpoken(responseId)) {
          // Speak ONLY if:
          // - We observed positive evidence that this client watched the turn arrive live
          // - It is the latest assistant turn at the transcript tail
          // - It is finalized and has a valid non-empty summary
          if (wasObservedLive && isLatestTurn && isFinal && hasValidSummary) {
            speakLiveSummary(responseId, summary!.text, summary!.lang);
          } else if (isFinal) {
            // Settled history or non-tail turn: index as spoken so it is never replayed later.
            markMessageSpoken(responseId);
          }
        }
      }
    }
  }, [bubbles, activeResponseId, speakLiveSummary, stop]);

  // Cancel in-flight speech when the component unmounts or user navigates away.
  useEffect(() => {
    return () => {
      stop();
    };
  }, [stop]);
}
