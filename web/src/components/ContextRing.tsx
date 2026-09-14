// The composer's context ring, and the auto-compaction control behind it.
//
// The ring shows how full the context window is. Clicking it opens the one
// setting that governs what happens when it fills: the share at which Omnigent
// asks the session to write its context down and then compacts it.
//
// It also carries the state the server publishes while compacting. That state
// exists for one reason: a compaction happens inside a turn, but the number on
// the ring is only remeasured when the NEXT turn ends — so without a word from
// the server, a successful compaction looks exactly like a failed one.

import { useEffect, useState } from "react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Input } from "@/components/ui/input";
import { useSessionLabels } from "@/hooks/useSessionLabels";
import {
  autoCompactState,
  autoCompactStateText,
  clampThreshold,
  MAX_THRESHOLD_PCT,
  MIN_THRESHOLD_PCT,
  THRESHOLD_LABEL,
  thresholdPct,
} from "@/lib/autoCompact";
import { updateSession } from "@/lib/sessionsApi";
import { cn } from "@/lib/utils";

/** Circumference of the progress ring (r=5.5). */
const RING_CIRCUMFERENCE = 2 * Math.PI * 5.5;

/**
 * Circular progress ring showing how much context window is used, with the
 * used percentage beside it and the auto-compaction setting behind it.
 *
 * @param contextWindow - The model's window size in tokens.
 * @param tokensUsed - Tokens used by the last completed response.
 * @param conversationId - The session, for reading and writing its threshold;
 *   null renders the ring without the control.
 */
export function ContextRing({
  contextWindow,
  tokensUsed,
  conversationId = null,
}: {
  contextWindow: number;
  tokensUsed: number;
  conversationId?: string | null;
}) {
  const pct = Math.min(tokensUsed / contextWindow, 1);
  // Arc, %, label, and tooltip all encode context USED: a fresh session
  // shows an empty ring at 0% and the ring fills as context is consumed.
  const usedArc = pct * RING_CIRCUMFERENCE;
  const usedPct = Math.round(pct * 100);

  const labels = useSessionLabels(conversationId);
  const threshold = thresholdPct(labels);
  const state = autoCompactState(labels);
  const stateText = autoCompactStateText(state);

  const color =
    pct > 0.8 ? "text-destructive" : pct > 0.6 ? "text-warning" : "text-muted-foreground";

  const ring = (
    <span className={cn("flex items-center gap-1.5", color)}>
      <svg viewBox="0 0 16 16" width="16" height="16" fill="none" aria-hidden="true">
        {/* Track */}
        <circle cx="8" cy="8" r="5.5" stroke="currentColor" strokeWidth="2" opacity="0.2" />
        {/* Used arc — skipped at 0, where round linecaps would still paint a dot. */}
        {usedArc > 0 && (
          <circle
            cx="8"
            cy="8"
            r="5.5"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeDasharray={`${usedArc} ${RING_CIRCUMFERENCE}`}
            transform="rotate(-90 8 8)"
          />
        )}
        {/* Where compaction kicks in, so the setting is visible without opening it. */}
        {conversationId && (
          <line
            x1="8"
            y1="1"
            x2="8"
            y2="3.2"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            opacity="0.55"
            transform={`rotate(${(threshold / 100) * 360} 8 8)`}
          />
        )}
      </svg>
      <span className="text-sm tabular-nums" aria-hidden="true">
        {usedPct}%
      </span>
      {state === "writing-notes" && (
        <span className="text-sm" aria-hidden="true">
          ✍
        </span>
      )}
    </span>
  );

  if (!conversationId) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span aria-label={`${usedPct}% of context used`}>{ring}</span>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-44 text-center text-sm">
          <p className="tabular-nums">{usedPct}% of context used.</p>
        </TooltipContent>
      </Tooltip>
    );
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          data-testid="context-ring-trigger"
          aria-label={`${usedPct}% of context used`}
          title={stateText ?? `${usedPct}% of context used. Compacts at ${threshold}%.`}
          className="rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
        >
          {ring}
        </button>
      </PopoverTrigger>
      <PopoverContent side="top" align="end" className="w-72 space-y-3 text-sm">
        <div>
          <p className="font-medium text-foreground">Context</p>
          <p className="tabular-nums text-muted-foreground">
            {tokensUsed.toLocaleString()} of {contextWindow.toLocaleString()} tokens ({usedPct}%).
          </p>
        </div>
        {stateText && (
          <p data-testid="context-ring-state" className="text-foreground">
            {stateText}
          </p>
        )}
        <ThresholdControl conversationId={conversationId} threshold={threshold} />
      </PopoverContent>
    </Popover>
  );
}

/**
 * The share of the window at which this session compacts.
 *
 * Saved on blur and on Enter rather than per keystroke: typing "4" on the way
 * to "45" would otherwise land a threshold of 10 (the clamp) and compact the
 * session immediately.
 *
 * @param conversationId - The session whose label is written.
 * @param threshold - The threshold currently in effect.
 */
function ThresholdControl({
  conversationId,
  threshold,
}: {
  conversationId: string;
  threshold: number;
}) {
  const [draft, setDraft] = useState(String(threshold));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Follow the session's own value when it changes elsewhere (another tab, or
  // a first load that arrived after this opened), except mid-edit.
  useEffect(() => {
    if (!saving) setDraft(String(threshold));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threshold, conversationId]);

  const save = async () => {
    const wanted = Number.parseInt(draft, 10);
    if (!Number.isFinite(wanted)) {
      setDraft(String(threshold));
      setError(null);
      return;
    }
    const next = clampThreshold(wanted);
    setDraft(String(next));
    if (next === threshold) return;
    setSaving(true);
    setError(null);
    try {
      await updateSession(conversationId, {
        labels: { [THRESHOLD_LABEL]: String(next) },
        silent: true,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setDraft(String(threshold));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-1.5">
      <label htmlFor="autocompact-pct" className="block text-foreground">
        Compact at
      </label>
      <div className="flex items-center gap-2">
        <Input
          id="autocompact-pct"
          data-testid="autocompact-pct"
          type="number"
          min={MIN_THRESHOLD_PCT}
          max={MAX_THRESHOLD_PCT}
          step={5}
          value={draft}
          disabled={saving}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => void save()}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void save();
            }
          }}
          className="h-8 w-20 tabular-nums"
        />
        <span className="text-muted-foreground">% of the window</span>
      </div>
      <p className="text-sm text-muted-foreground">
        Past this, the session is asked to write its context down, and the turn that answers is
        compacted. Between {MIN_THRESHOLD_PCT} and {MAX_THRESHOLD_PCT}.
      </p>
      {error && (
        <p data-testid="autocompact-pct-error" className="text-destructive">
          Could not save: {error}
        </p>
      )}
    </div>
  );
}
