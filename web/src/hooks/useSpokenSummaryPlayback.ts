import { useEffect, useRef } from "react";
import type { Bubble } from "@/lib/renderItems";
import { markMessageSpoken, useSpeechPlaybackStore } from "@/lib/speechPlayback";

/**
 * Hook to coordinate read-aloud playback of newly-arrived live assistant messages
 * carrying a spoken_summary part.
 *
 * Guarantees:
 * - On initial mount, records all existing messages so history/page refresh never replays audio.
 * - Speeds newly-arrived live assistant messages when the playback preference is ON.
 * - Cancels in-flight speech when a new summary arrives, on component unmount, or on navigation.
 * - Tracks spoken message IDs to prevent duplicate playback across re-renders or SSE reconnects.
 */
export function useSpokenSummaryPlayback(bubbles: Bubble[]): void {
  const isInitialMountRef = useRef(true);
  const knownMessageIdsRef = useRef<Set<string>>(new Set());
  const speakLiveSummary = useSpeechPlaybackStore((s) => s.speakLiveSummary);
  const stop = useSpeechPlaybackStore((s) => s.stop);

  useEffect(() => {
    // Initial mount: record all currently present message IDs so history/reloads never play audio.
    if (isInitialMountRef.current) {
      for (const bubble of bubbles) {
        if (bubble.kind === "assistant") {
          for (const item of bubble.items) {
            if (item.kind === "text") {
              const id = item.itemId || bubble.responseId;
              knownMessageIdsRef.current.add(id);
              markMessageSpoken(id);
            }
          }
        }
      }
      isInitialMountRef.current = false;
      return;
    }

    // Find the latest assistant bubble in the transcript — live messages only arrive at the tail.
    let lastAssistantIdx = -1;
    for (let i = bubbles.length - 1; i >= 0; i--) {
      if (bubbles[i]?.kind === "assistant") {
        lastAssistantIdx = i;
        break;
      }
    }

    for (let i = 0; i < bubbles.length; i++) {
      const bubble = bubbles[i];
      if (bubble?.kind === "assistant") {
        const isLatestTurn = i === lastAssistantIdx;
        for (const item of bubble.items) {
          if (item.kind === "text") {
            const id = item.itemId || bubble.responseId;
            if (!knownMessageIdsRef.current.has(id)) {
              knownMessageIdsRef.current.add(id);
              // Only live messages at the active edge are spoken.
              // Historical messages prepended via pagination or history load are recorded but not spoken.
              if (isLatestTurn && item.final && item.spokenSummary) {
                speakLiveSummary(id, item.spokenSummary.text, item.spokenSummary.lang);
              } else {
                markMessageSpoken(id);
              }
            }
          }
        }
      }
    }
  }, [bubbles, speakLiveSummary]);

  // Cancel in-flight speech when the component unmounts or user navigates away.
  useEffect(() => {
    return () => {
      stop();
    };
  }, [stop]);
}
