// Per-session language for the spoken summary.
//
// Unlike narration on/off, which only decides whether this device plays the
// audio, the language decides what the SERVER writes and speaks — so it lives
// in the session's labels rather than in browser storage, and a change has to
// reach the server before the next turn's summary is generated.
//
// `resolve_spoken_summary_settings_async` reads the pair together and only
// consults the label branch when `spoken_summary_enabled` is present, so both
// keys are always written even though only the language is being changed.

import { updateSession } from "./sessionsApi";

/** Languages the summary can be written and spoken in. */
export const SUMMARY_LANGUAGES = ["pt-BR", "en-US"] as const;
export type SummaryLanguage = (typeof SUMMARY_LANGUAGES)[number];

/** Short label for the toggle face, e.g. `"PT"`. */
export function shortLanguageLabel(lang: SummaryLanguage): string {
  return lang.slice(0, 2).toUpperCase();
}

/**
 * Read the session's configured language from its labels.
 *
 * @param labels The session's guardrails labels, if loaded.
 * @returns The configured language, or `null` when the session has none and
 *   the project or server default applies.
 */
export function readSummaryLanguage(
  labels: Record<string, string> | undefined,
): SummaryLanguage | null {
  const raw = labels?.["spoken_summary_language"]?.trim();
  return (SUMMARY_LANGUAGES as readonly string[]).includes(raw ?? "")
    ? (raw as SummaryLanguage)
    : null;
}

/** The other language, for a two-state toggle. */
export function otherLanguage(lang: SummaryLanguage): SummaryLanguage {
  return lang === "pt-BR" ? "en-US" : "pt-BR";
}

/**
 * Persist the session's summary language.
 *
 * Writes `spoken_summary_enabled` alongside it: the server reads the label
 * pair as one unit, so a language written on its own is silently ignored.
 *
 * @param sessionId The session to update.
 * @param lang The language to write.
 */
export async function writeSummaryLanguage(
  sessionId: string,
  lang: SummaryLanguage,
): Promise<void> {
  await updateSession(sessionId, {
    labels: { spoken_summary_enabled: "true", spoken_summary_language: lang },
    silent: true,
  });
}
