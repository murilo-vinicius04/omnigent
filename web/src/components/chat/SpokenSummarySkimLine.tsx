import { useCallback } from "react";
import { SquareIcon, Volume2Icon } from "lucide-react";
import { useSpeechPlaybackStore } from "@/lib/speechPlayback";

export interface SpokenSummarySkimLineProps {
  summary: {
    text: string;
    lang: string;
  };
  /** Stable identifier for playback tracking (e.g. responseId). */
  id?: string;
  /** @deprecated use `id` */
  itemId?: string | null;
}

/**
 * Compact, visually secondary skim-line rendered above the assistant message.
 * Keeps the full response completely visible and untouched, while offering
 * quick visual skimming and an immediate stop/play affordance for read-aloud speech.
 */
export function SpokenSummarySkimLine({ summary, id, itemId }: SpokenSummarySkimLineProps) {
  const speakingItemId = useSpeechPlaybackStore((s) => s.speakingItemId);
  const playManual = useSpeechPlaybackStore((s) => s.playManual);
  const stop = useSpeechPlaybackStore((s) => s.stop);

  const effectiveId = id || itemId || "";
  const isSpeaking = Boolean(effectiveId && speakingItemId === effectiveId);

  const handleToggle = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      if (!effectiveId) return;
      if (isSpeaking) {
        stop();
      } else {
        playManual(effectiveId, summary.text, summary.lang);
      }
    },
    [isSpeaking, stop, playManual, effectiveId, summary.text, summary.lang],
  );

  return (
    <div
      data-testid="spoken-summary-skim-line"
      className="mb-2 flex items-center gap-2 rounded-md bg-muted/40 px-2.5 py-1.5 text-xs text-muted-foreground"
    >
      {effectiveId && (
        <button
          type="button"
          onClick={handleToggle}
          data-testid={isSpeaking ? "spoken-summary-stop-button" : "spoken-summary-play-button"}
          aria-label={isSpeaking ? "Stop read-aloud summary" : "Play spoken summary"}
          className="inline-flex shrink-0 items-center gap-1 rounded p-0.5 hover:text-foreground focus-visible:outline-none"
        >
          {isSpeaking ? (
            <>
              <SquareIcon className="size-3 fill-current text-primary" aria-hidden="true" />
              <span className="font-medium text-primary">Stop</span>
            </>
          ) : (
            <Volume2Icon className="size-3.5" aria-hidden="true" />
          )}
        </button>
      )}
      <span data-testid="spoken-summary-text" className="line-clamp-2 italic">
        {summary.text}
      </span>
    </div>
  );
}
