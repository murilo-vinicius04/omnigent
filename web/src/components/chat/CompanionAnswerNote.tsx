import { useState } from "react";
import { MessagesSquareIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useChatStore } from "@/store/chatStore";

export interface CompanionAnswerNoteProps {
  /** What the reader originally typed, re-sent if they want Claude after all. */
  asked: string;
}

/**
 * Marks a reply as the companion's, with one click to ask Claude anyway.
 *
 * The companion answers messages it judged Claude was not needed for, and
 * those replies land in the same transcript as Claude's. Saying who answered
 * is not decoration: the companion cannot see the code, so a reader who
 * mistakes its reply for Claude's is being misled by the UI rather than by
 * the model. The escape hatch matters for the same reason — the judgement is
 * a guess, and the reader must be able to overrule it without retyping.
 */
export function CompanionAnswerNote({ asked }: CompanionAnswerNoteProps) {
  const send = useChatStore((s) => s.send);
  const agentId = useChatStore((s) => s.boundAgentId);
  const [sent, setSent] = useState(false);

  const askClaude = () => {
    if (!asked || !agentId || sent) return;
    setSent(true);
    // forceClaude bypasses routing, or the companion would simply answer
    // the same question a second time.
    void send(asked, agentId, undefined, { forceClaude: true }).catch(() => setSent(false));
  };

  return (
    <div className="mt-1.5 flex items-center gap-2 text-xs text-muted-foreground">
      <MessagesSquareIcon className="size-3.5 shrink-0" data-icon-size="14" />
      <span>Answered by the companion — Claude never saw this.</span>
      {asked !== "" && (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-6 px-2 text-xs"
          disabled={sent || !agentId}
          onClick={askClaude}
          componentId="chat.companion.ask_claude_anyway"
        >
          {sent ? "Sent to Claude" : "Ask Claude anyway"}
        </Button>
      )}
    </div>
  );
}
