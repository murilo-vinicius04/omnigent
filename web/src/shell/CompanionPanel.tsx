import { useCallback, useEffect, useRef, useState } from "react";
import { getCompanionState, type CompanionEntry, type CompanionState } from "@/lib/companionApi";
import { cn } from "@/lib/utils";

export interface CompanionPanelProps {
  conversationId: string;
}

/** How each ledger entry is introduced. */
const LABEL: Record<CompanionEntry["kind"], string> = {
  activity: "You asked Claude",
  summary: "Claude said",
  question: "You said",
  answer: "It answered",
  note: "Note",
};

const ACCENT: Record<CompanionEntry["kind"], string> = {
  activity: "border-l-amber-500/70",
  summary: "border-l-emerald-500/70",
  question: "border-l-sky-500/70",
  answer: "border-l-violet-500/70",
  note: "border-l-border",
};

/** How often the ledger is re-read while the panel is open. */
const POLL_MS = 4000;

/**
 * What the companion knows about this session.
 *
 * The companion sees every message before Claude does and answers the ones
 * that do not need Claude. That makes "what is it deciding from?" a real
 * question, and this panel is the answer: the whole ledger, in order, which
 * is the companion's entire memory rather than a summary of it.
 *
 * Read-only by design. The composer is how you talk to it — a second input
 * here would be a second chat, which is exactly what this replaced.
 */
export function CompanionPanel({ conversationId }: CompanionPanelProps) {
  const [state, setState] = useState<CompanionState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const atBottomRef = useRef(true);

  const refresh = useCallback(async () => {
    try {
      setState(await getCompanionState(conversationId));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [conversationId]);

  // Polled rather than pushed: the ledger changes only when a turn happens,
  // and a session event stream of its own would be a lot of machinery for a
  // panel most readers keep closed.
  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  // Follow the tail, but only while the reader is already there — yanking
  // the view back down while they read older context is worse than stale.
  useEffect(() => {
    const el = listRef.current;
    if (el && atBottomRef.current) el.scrollTop = el.scrollHeight;
  }, [state]);

  const entries = state?.context ?? [];
  const firstPending = entries.length - (state?.pendingNotes ?? 0);

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="companion-panel">
      <div className="flex items-center justify-between gap-2 border-b px-3 py-2">
        <span className="text-ui font-medium">What the companion knows</span>
        <span className="text-xs text-muted-foreground">
          {state?.warmSince ? "warm" : state?.running ? "starting" : "cold"}
        </span>
      </div>

      <div
        ref={listRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
        className="min-h-0 flex-1 overflow-y-auto px-3 py-2"
      >
        {error !== null ? (
          <p className="py-6 text-center text-sm text-destructive" role="alert">
            {error}
          </p>
        ) : entries.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            Nothing yet. It learns what you send and the spoken summaries of what Claude
            answers — never the transcript or the code.
          </p>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {entries.map((entry, i) => (
              <li
                key={entry.id}
                className={cn(
                  "rounded-md border-l-2 bg-muted/40 px-2 py-1.5",
                  ACCENT[entry.kind],
                  state?.pendingNotes && i >= firstPending && "opacity-60",
                )}
              >
                <span className="mr-1.5 text-[11px] font-medium text-muted-foreground">
                  {LABEL[entry.kind]}
                </span>
                <span className="text-sm whitespace-pre-wrap">{entry.text}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
