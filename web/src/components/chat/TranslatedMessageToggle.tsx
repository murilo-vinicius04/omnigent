import { useState } from "react";
import { ChevronRightIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export interface TranslatedMessageToggleProps {
  /** The English the answering model actually received. */
  translated: string;
}

/**
 * Reveals the English a message was translated into before dispatch.
 *
 * The bubble shows what the reader wrote; this shows what the model read.
 * Mirrors the assistant side, where the rewrite is shown and the model's own
 * words sit behind a toggle — in both directions the reader's language is the
 * default and English is one click away.
 */
export function TranslatedMessageToggle({ translated }: TranslatedMessageToggleProps) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-1 flex flex-col items-end" data-testid="translated-message">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        data-testid="translated-message-toggle"
        aria-expanded={open}
        className="inline-flex items-center gap-0.5 rounded text-xs text-muted-foreground hover:text-foreground focus-visible:outline-none"
      >
        <ChevronRightIcon
          className={cn("size-3 transition-transform", open && "rotate-90")}
          aria-hidden="true"
        />
        {open ? "Hide English" : "Show English"}
      </button>
      {open && (
        <div
          data-testid="translated-message-text"
          className="mt-1 whitespace-pre-wrap border-r-2 border-border pr-3 text-left text-sm text-muted-foreground"
        >
          {translated}
        </div>
      )}
    </div>
  );
}
