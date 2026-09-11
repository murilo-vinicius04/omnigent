import { useEffect, useRef } from "react";
import { useChatStore } from "@/store/chatStore";
import type { Bubble, RenderItem, ToolState } from "@/lib/renderItems";
import { isMessageSpoken, markMessagesSpoken, useSpeechPlaybackStore } from "@/lib/speechPlayback";
import type { ActiveResponse } from "@/store/types";

/**
 * How long after a turn finalizes a summary may still arrive and be spoken.
 *
 * Has to cover both post-turn steps: the `agy` rewrite (capped at 45s) and the voice
 * synthesis after it. Still bounded, so a rebuild minutes later never replays a turn
 * the reader already finished.
 */
const LIVE_SUMMARY_WINDOW_MS = 120_000;

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
  const sessionId = useChatStore((s) => s.conversationId);
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
        // The reader has moved on to another turn, so this one's summary is no longer the
        // answer in front of them, whenever it arrives.
        const anotherResponseIsLive = Boolean(
          activeResponse && activeResponse.responseId !== responseId,
        );
        // This turn finalized too long ago for a summary to still be its live answer --
        // unless the summary itself says otherwise, settled below against its server stamp.
        const responseAgedOut = Boolean(
          activeResponse &&
          activeResponse.responseId === responseId &&
          activeResponse.state !== "streaming" &&
          activeResponse.completedAt !== undefined &&
          Date.now() - activeResponse.completedAt > LIVE_SUMMARY_WINDOW_MS,
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
          // A summary is appended as its own item long after the turn it
          // describes, so its own server stamp says whether it just arrived.
          // That is the signal that survives a reload: `wasObservedLive` is
          // evidence this client watched the turn stream, which a client that
          // loaded the page after the turn ended can never have, leaving a
          // freshly-arrived summary unspoken. Either is enough; the spoken
          // index and the live window still bound replay.
          const summaryAgeS =
            summary && textItem?.createdAtS !== undefined
              ? Date.now() / 1000 - textItem.createdAtS
              : undefined;
          const isFreshSummary =
            summaryAgeS !== undefined &&
            summaryAgeS >= 0 &&
            summaryAgeS * 1000 < LIVE_SUMMARY_WINDOW_MS;
          // A summary that just landed is this turn's live answer however long it took to
          // build, so its own stamp settles the age question the response's cannot.
          const isResponseStale = anotherResponseIsLive || (responseAgedOut && !isFreshSummary);

          if (
            (wasObservedLive || isFreshSummary) &&
            isLatestTurn &&
            isFinal &&
            hasValidSummary &&
            !isResponseStale
          ) {
            // Prefer the server-synthesized audio; the host engine is the fallback.
            const audioUrl =
              summary!.audioFileId && sessionId
                ? `/v1/sessions/${encodeURIComponent(sessionId)}/resources/files/${encodeURIComponent(summary!.audioFileId)}/content`
                : undefined;
            speakLiveSummary(responseId, summary!.text, summary!.lang, audioUrl, sessionId);
          } else if (
            isFinal &&
            ((!wasObservedLive && !isFreshSummary) || !isLatestTurn || isResponseStale)
          ) {
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
  }, [bubbles, activeResponse, sessionId, speakLiveSummary, stop]);

  // Cancel in-flight speech when the component unmounts or user navigates away.
  useEffect(() => {
    return () => {
      stop();
    };
  }, [stop]);
}
