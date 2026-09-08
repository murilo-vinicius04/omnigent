import { useEffect, useRef } from "react";
import type { Bubble, RenderItem, ToolState } from "@/lib/renderItems";
import { isMessageSpoken, markMessagesSpoken, useSpeechPlaybackStore } from "@/lib/speechPlayback";
import type { ActiveResponse } from "@/store/types";

/**
 * How long after a turn finalizes a summary may still arrive and be spoken.
 *
 * The server generates the summary after the turn completes, capped at 4s
 * (`OMNIGENT_SPOKEN_SUMMARY_TIMEOUT_S`), so a real one lands within seconds or never.
 * Raise this to match if that timeout is raised past this window.
 */
const LIVE_SUMMARY_WINDOW_MS = 15_000;

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
 * - Live-window bound: a turn whose response has gone stale is indexed as spoken rather than
 *   played, so a rebuild surfacing a summary minutes later (history refetch, pagination, SSE
 *   reconnect gap) can never replay a turn the user already read. `activeResponse` is not
 *   cleared on completion, so liveness comes from its state/completedAt, not its id.
 */
export function useSpokenSummaryPlayback(
  bubbles: Bubble[],
  activeResponse?: ActiveResponse | null,
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
        // A completed response stays in the store until the next send, so identity alone never
        // goes false while the user reads. Stale is a disqualifier, not a liveness requirement:
        // suppressing without positive evidence would silence turns that have no active response.
        const isResponseStale = Boolean(
          activeResponse &&
          // Another response is live, or this one finalized too long ago for a real summary.
          (activeResponse.responseId !== responseId ||
            (activeResponse.state !== "streaming" &&
              activeResponse.completedAt !== undefined &&
              Date.now() - activeResponse.completedAt > LIVE_SUMMARY_WINDOW_MS)),
        );

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
          // - The response has not gone stale. This gate belongs here, not only on the marking
          //   branch: the branches are exclusive, so a marking-only guard is unreachable exactly
          //   when the speak conditions hold — the replay case.
          if (wasObservedLive && isLatestTurn && isFinal && hasValidSummary && !isResponseStale) {
            speakLiveSummary(responseId, summary!.text, summary!.lang);
          } else if (isFinal && (!wasObservedLive || !isLatestTurn || isResponseStale)) {
            // Settled history, non-tail turn, or a response past its live window:
            // queue for single-persist batch marking so it is never replayed later.
            toMarkSpoken.add(responseId);
          }
        }
      }
    }

    if (toMarkSpoken.size > 0) {
      markMessagesSpoken(Array.from(toMarkSpoken));
    }
  }, [bubbles, activeResponse, speakLiveSummary, stop]);

  // Cancel in-flight speech when the component unmounts or user navigates away.
  useEffect(() => {
    return () => {
      stop();
    };
  }, [stop]);
}
