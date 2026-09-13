// Autoplay decisions, reported to the server log.
//
// Whether a summary is read aloud is decided entirely in the browser, and the
// server never learns why one stayed silent. Each decision is posted so the
// server log can answer "why didn't that play?" without guessing.

import { authenticatedFetch } from "./identity";

/** Which part of the page made the decision. */
export type NarrationPath = "in-session" | "cross-session" | "playback";

export interface NarrationDecision {
  path: NarrationPath;
  /** What happened, e.g. "too-old" or "handed-to-playback". */
  decision: string;
  sessionId?: string | null;
  itemId?: string | null;
  /** Short context for the log line. */
  detail?: string;
}

const DECISION_URL = "/v1/narration/decision";

/** How many distinct decisions to remember, so a re-render cannot flood the log. */
const MAX_REMEMBERED = 500;

const reported = new Set<string>();

/** Forget what has been reported (tests). */
export function resetNarrationLog(): void {
  reported.clear();
}

/**
 * Post one decision to the server log.
 *
 * Never throws and never waits: a lost report costs one log line, never
 * playback. The same decision about the same item is reported once, because
 * the hooks that decide re-run on every render.
 */
export function reportNarration(entry: NarrationDecision): void {
  const key = [entry.path, entry.sessionId ?? "", entry.itemId ?? "", entry.decision].join("|");
  if (reported.has(key)) return;
  reported.add(key);
  if (reported.size > MAX_REMEMBERED) {
    const oldest = reported.values().next().value;
    if (oldest !== undefined) reported.delete(oldest);
  }
  void (async () => {
    try {
      await authenticatedFetch(DECISION_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: entry.path,
          decision: entry.decision,
          session_id: entry.sessionId ?? null,
          item_id: entry.itemId ?? null,
          detail: entry.detail ?? null,
        }),
      });
    } catch {
      // Diagnostics only: nothing to recover.
    }
  })();
}
