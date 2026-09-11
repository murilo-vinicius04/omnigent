import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { useChatStore } from "@/store/chatStore";
import { getSession } from "@/lib/sessionsApi";
import {
  otherLanguage,
  readSummaryLanguage,
  shortLanguageLabel,
  writeSummaryLanguage,
  type SummaryLanguage,
} from "@/lib/sessionSummaryLanguage";

export interface ComposerSummaryLanguageButtonProps {
  disabled?: boolean;
  /** Language to assume before the session's labels have loaded. */
  fallback?: SummaryLanguage;
}

/**
 * Switches the summary's language for this session, next to the narrate toggle.
 *
 * Which language you want read back is a property of the conversation, not of
 * the device or the project — the same reader wants Portuguese in one session
 * and English in the next. It sits beside narration because they are the same
 * decision made twice: whether to be read to, and in what.
 */
export function ComposerSummaryLanguageButton({
  disabled,
  fallback = "pt-BR",
}: ComposerSummaryLanguageButtonProps) {
  const sessionId = useChatStore((s) => s.conversationId);
  const [lang, setLang] = useState<SummaryLanguage>(fallback);
  const [saving, setSaving] = useState(false);

  // Labels are the source of truth, so the face is seeded from the server
  // rather than from a local guess that could disagree with what gets spoken.
  useEffect(() => {
    let cancelled = false;
    if (!sessionId) return;
    void getSession(sessionId)
      .then((s) => {
        const stored = readSummaryLanguage(s.labels);
        if (!cancelled && stored) setLang(stored);
      })
      .catch(() => {
        // An unreadable session leaves the fallback showing; the click still works.
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const toggle = async () => {
    if (!sessionId || saving) return;
    const next = otherLanguage(lang);
    setLang(next); // optimistic: the control should feel immediate
    setSaving(true);
    try {
      await writeSummaryLanguage(sessionId, next);
    } catch {
      setLang(lang); // the server refused it, so stop claiming it changed
    } finally {
      setSaving(false);
    }
  };

  return (
    <Button
      type="button"
      size="icon"
      variant="ghost"
      className="size-9 md:size-8 text-[11px] font-medium tabular-nums"
      disabled={disabled || !sessionId || saving}
      onClick={() => void toggle()}
      title={`Summaries in ${lang} — click for ${otherLanguage(lang)}`}
      data-testid="composer-summary-language"
      componentId="chat.composer.summaryLanguage"
    >
      {shortLanguageLabel(lang)}
      <span className="sr-only">
        Summary language is {lang}. Switch to {otherLanguage(lang)}.
      </span>
    </Button>
  );
}
