// Auto-compaction, as the composer's context ring shows it.
//
// The server decides when to compact (`omnigent/server/auto_compact.py`); this
// is the reader's half: the threshold they can move, and the state the server
// publishes so a compaction isn't silent. Both travel as session labels, which
// the session-updates socket already pushes, so nothing new is polled.
//
// The constants are duplicated from the Python module deliberately — a label
// name is a wire contract, and the test below pins them to it.

/** Session label holding the per-session threshold, as a percentage string. */
export const THRESHOLD_LABEL = "omnigent.autocompact_pct";

/** Session label holding what compaction is doing right now. */
export const STATE_LABEL = "omnigent.autocompact_state";

/** Share of the context window at which the write-up is asked for. */
export const DEFAULT_THRESHOLD_PCT = 60;

/** Below this the session would compact constantly. */
export const MIN_THRESHOLD_PCT = 10;

/** Above this Claude Code's own compaction gets there first. */
export const MAX_THRESHOLD_PCT = 95;

/** What compaction is doing, for the badge beside the ring. */
export type AutoCompactState = "writing-notes" | "compacted" | null;

/**
 * Read the auto-compaction threshold from a session's labels.
 *
 * Mirrors `auto_compact.threshold_pct`: an unset, malformed or out-of-range
 * value is a mistake rather than a preference, so it clamps instead of
 * refusing.
 *
 * @param labels - The session's labels, or undefined before they load.
 * @returns The threshold percentage.
 */
export function thresholdPct(labels: Record<string, string> | undefined | null): number {
  const raw = labels?.[THRESHOLD_LABEL]?.trim();
  if (!raw) return DEFAULT_THRESHOLD_PCT;
  const wanted = Number.parseInt(raw, 10);
  if (!Number.isFinite(wanted)) return DEFAULT_THRESHOLD_PCT;
  return clampThreshold(wanted);
}

/**
 * Hold a threshold inside the range the server accepts.
 *
 * @param wanted - The requested percentage.
 * @returns The nearest allowed percentage.
 */
export function clampThreshold(wanted: number): number {
  return Math.max(MIN_THRESHOLD_PCT, Math.min(MAX_THRESHOLD_PCT, Math.round(wanted)));
}

/**
 * Read what compaction is doing from a session's labels.
 *
 * @param labels - The session's labels, or undefined before they load.
 * @returns The state, or null when compaction has nothing to say.
 */
export function autoCompactState(
  labels: Record<string, string> | undefined | null,
): AutoCompactState {
  const raw = labels?.[STATE_LABEL]?.trim();
  if (raw === "writing-notes" || raw === "compacted") return raw;
  return null;
}

/**
 * The sentence shown beside the ring for each state.
 *
 * "compacted" exists because of a real confusion: the compaction happens
 * inside a turn, but the percentage next to it is only remeasured when the
 * NEXT turn ends — so the old number sits there looking like a failure.
 *
 * @param state - The published state.
 * @returns The sentence, or null when there is nothing to say.
 */
export function autoCompactStateText(state: AutoCompactState): string | null {
  if (state === "writing-notes") return "Writing the context down, then compacting…";
  if (state === "compacted")
    return "Compacted. The size below is from before it — it updates when the next turn ends.";
  return null;
}
