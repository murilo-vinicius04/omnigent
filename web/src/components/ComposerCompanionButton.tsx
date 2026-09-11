import { useCallback, useEffect, useRef, useState } from "react";
import { MessagesSquareIcon, SendIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import {
  askCompanion,
  getCompanionState,
  prewarmCompanion,
  type CompanionEntry,
  type CompanionState,
} from "@/lib/companionApi";
import { useChatStore } from "@/store/chatStore";

export interface ComposerCompanionButtonProps {
  disabled?: boolean;
}

/** How each ledger entry is introduced in the panel. */
const LABEL: Record<CompanionEntry["kind"], string> = {
  activity: "Claude is",
  summary: "Claude said",
  question: "You asked",
  answer: "It said",
  note: "Note",
};

const ACCENT: Record<CompanionEntry["kind"], string> = {
  activity: "border-l-amber-500/70",
  summary: "border-l-emerald-500/70",
  question: "border-l-sky-500/70",
  answer: "border-l-violet-500/70",
  note: "border-l-border",
};

/**
 * Talk to the session's companion without interrupting Claude.
 *
 * The companion is a warm Gemini process that has been following along
 * from the side: it hears what you asked Claude and the spoken summaries
 * of what Claude answered, and nothing else — not the transcript, not the
 * code. That thinness is the point (it is a colleague who has been
 * half-listening, not a second engineer), and it is also why the panel
 * shows the ledger above the reply: "what does it actually know?" should
 * be answerable by looking, not by trusting this docstring.
 *
 * Opening the panel prewarms the process, so the first question costs
 * ~1s instead of ~3.5s while the reader is still reading the ledger.
 */
export function ComposerCompanionButton({ disabled }: ComposerCompanionButtonProps) {
  const sessionId = useChatStore((s) => s.conversationId);
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<CompanionState | null>(null);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  const refresh = useCallback(async () => {
    if (!sessionId) return;
    try {
      setState(await getCompanionState(sessionId));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [sessionId]);

  useEffect(() => {
    if (!open || !sessionId) return;
    void refresh();
    // Warming is slow enough to notice and useless to wait for, so it runs
    // unawaited; a failure surfaces on the first question instead. Its reply
    // is deliberately discarded and the ledger re-read instead: two writers
    // racing on one piece of state is how a panel ends up showing an older
    // ledger than the one it already had.
    void prewarmCompanion(sessionId)
      .then(() => refresh())
      .catch(() => undefined);
  }, [open, sessionId, refresh]);

  // Keep the newest exchange in view the way a chat does.
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [state]);

  const ask = async () => {
    const text = question.trim();
    if (!text || !sessionId || asking) return;
    setQuestion("");
    setAsking(true);
    setError(null);
    try {
      setState((await askCompanion(sessionId, text)).state);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      void refresh();
    } finally {
      setAsking(false);
    }
  };

  const entries = state?.context ?? [];
  // The tail the process has not been told yet: it rides the next question.
  const firstPending = entries.length - (state?.pendingNotes ?? 0);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="size-9 md:size-8"
          disabled={disabled || !sessionId}
          title="Talk to the companion about this session"
          data-testid="composer-companion"
          componentId="chat.composer.companion"
        >
          <MessagesSquareIcon className="size-4" data-icon-size="16" />
          <span className="sr-only">Talk to the companion about this session</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[min(30rem,calc(100vw-2rem))] p-0">
        <div className="flex items-center justify-between gap-2 border-b px-3 py-2">
          <span className="text-sm font-medium">Companion</span>
          <span className="text-xs text-muted-foreground">
            {state?.warmSince ? "warm" : state?.running ? "starting" : "cold"}
            {state?.model ? ` · ${state.model}` : ""}
          </span>
        </div>

        <div ref={listRef} className="max-h-[22rem] overflow-y-auto px-3 py-2">
          {entries.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              It has not heard anything yet. It learns what you ask Claude and the spoken
              summaries of what Claude answers.
            </p>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {entries.map((entry, i) => (
                <li
                  key={entry.id}
                  className={`rounded-md border-l-2 bg-muted/40 px-2 py-1.5 ${ACCENT[entry.kind]} ${
                    state?.pendingNotes && i >= firstPending ? "opacity-60" : ""
                  }`}
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

        {error !== null && (
          <p className="border-t px-3 py-2 text-xs text-destructive" role="alert">
            {error}
          </p>
        )}

        <div className="flex items-center gap-1.5 border-t p-2">
          <Input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void ask();
              }
            }}
            placeholder="Ask it about what's going on…"
            disabled={asking}
            aria-label="Ask the companion"
            data-testid="companion-input"
          />
          <Button
            type="button"
            size="icon"
            className="size-9 shrink-0 md:size-8"
            disabled={asking || question.trim() === ""}
            onClick={() => void ask()}
            title="Ask"
            componentId="chat.composer.companion.ask"
          >
            {asking ? (
              <Spinner className="size-4" />
            ) : (
              <SendIcon className="size-4" data-icon-size="16" />
            )}
            <span className="sr-only">Ask</span>
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
