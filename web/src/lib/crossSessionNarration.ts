// Narration that follows the reader, not the open conversation.
//
// The in-conversation hook (`useSpokenSummaryPlayback`) only ever sees the
// session on screen, so a turn that finishes while the reader is reading
// something else is never spoken — and switching away mid-turn silences it for
// good. This listens to the session-updates socket the sidebar already runs,
// which pushes a row for EVERY watched session, and speaks a summary whichever
// conversation is open.
//
// The two paths share `speakLiveSummary`, so they also share its narration
// switch, its spoken index (a summary is spoken once, by whichever path gets
// there first) and its single-stream channel claim.

import { fetchSessionItemsPage } from "@/lib/sessionsApi";
import { sessionUpdatesSocket, type SessionUpdatesFrame } from "@/lib/sessionUpdatesSocket";
import { spokenSummaryFromMessageContent } from "@/lib/blockStream";
import { isMessageSpoken, useSpeechPlaybackStore } from "@/lib/speechPlayback";

/**
 * How late a summary may still be spoken, measured from the server's own
 * stamp on it.
 *
 * A reconnect replays a snapshot of every watched session, and a laptop
 * waking up replays hours of them at once. Without a bound the reader would
 * be read a backlog. Wide enough to cover a turn that finished while they
 * were in another conversation, since that is the case this exists for.
 */
export const NARRATE_WITHIN_MS = 5 * 60_000;

/** Items scanned per finished turn. The summary lands last, so this is slack. */
const LOOKBACK_ITEMS = 8;

/** Statuses that mean the turn is over and its summary may have landed. */
const FINISHED: ReadonlySet<string> = new Set(["idle", "failed"]);

interface WireItem {
  id: string;
  status?: string;
  updated_at?: number | string;
}

/** Sessions already examined at their current revision, so frames are cheap. */
const seenRevision = new Map<string, string>();
/** Sessions with a fetch in flight, so a burst of frames starts one lookup. */
const inFlight = new Set<string>();

/** Forget every session's last-seen revision (test seam / sign-out). */
export function resetCrossSessionNarration(): void {
  seenRevision.clear();
  inFlight.clear();
}

function revisionOf(item: WireItem): string {
  return `${item.status ?? ""}@${item.updated_at ?? ""}`;
}

/**
 * Look for a fresh, unspoken summary on *sessionId* and speak it.
 *
 * @param sessionId The session whose turn just finished.
 * @param now Clock seam for tests.
 */
export async function narrateLatestSummary(
  sessionId: string,
  now: () => number = Date.now,
): Promise<void> {
  if (inFlight.has(sessionId)) return;
  inFlight.add(sessionId);
  try {
    // The summary lands moments after the turn goes idle, so a second update
    // usually arrives while this lookup is still running. Re-run for it rather
    // than dropping it: its revision is already recorded, so nothing else will.
    // Sequential on purpose: each lookup must see the result of the one
    // before it, so running them together would race.
    let handled: string;
    do {
      handled = seenRevision.get(sessionId) ?? "";
      // eslint-disable-next-line no-await-in-loop
      if (await speakIfSummaryReady(sessionId, now)) return;
    } while (handled !== (seenRevision.get(sessionId) ?? ""));
  } finally {
    inFlight.delete(sessionId);
  }
}

/**
 * One lookup: speak the newest fresh, unspoken summary if there is one.
 *
 * @returns True when a summary was handed to playback (or was already spoken),
 *   so the caller can stop looking.
 */
async function speakIfSummaryReady(sessionId: string, now: () => number): Promise<boolean> {
  try {
    const page = await fetchSessionItemsPage(sessionId, { limit: LOOKBACK_ITEMS });
    // Chronological: the newest summary is the last one that carries text.
    for (let i = page.items.length - 1; i >= 0; i -= 1) {
      const item = page.items[i] as {
        response_id?: string;
        created_at?: number;
        data?: { content?: unknown };
        content?: unknown;
      };
      const summary = spokenSummaryFromMessageContent(item?.data?.content ?? item?.content);
      if (!summary) continue;
      const responseId = item.response_id;
      if (!responseId || isMessageSpoken(responseId)) return true;
      const ageMs = item.created_at === undefined ? 0 : now() - item.created_at * 1000;
      if (ageMs > NARRATE_WITHIN_MS) return true;
      const audioUrl = summary.audioFileId
        ? `/v1/sessions/${encodeURIComponent(sessionId)}/resources/files/${encodeURIComponent(
            summary.audioFileId,
          )}/content`
        : undefined;
      // Speaks only when narration is on for this session, and marks it spoken
      // so the in-conversation path will not repeat it. A summary whose
      // recording is still being made is not spoken and not marked: the next
      // revision carries the audio, and that is the one worth hearing.
      const spoke = useSpeechPlaybackStore
        .getState()
        .speakLiveSummary(responseId, summary.text, summary.lang, audioUrl, sessionId);
      return spoke || Boolean(audioUrl);
    }
  } catch {
    // A failed lookup costs this one summary, never the stream.
    return true;
  }
  return false; // no summary yet: a later revision may carry one
}

/**
 * Start narrating finished turns from every watched session.
 *
 * @param now Clock seam for tests.
 * @returns An unsubscribe function.
 */
export function startCrossSessionNarration(now: () => number = Date.now): () => void {
  return sessionUpdatesSocket.subscribe((frame: SessionUpdatesFrame) => {
    // Only live changes: a snapshot is the state of the world on connect, and
    // speaking it would read the backlog aloud on every reconnect.
    if (frame.type !== "changed") return;
    for (const raw of frame.items) {
      const item = raw as WireItem;
      if (!item?.id) continue;
      const revision = revisionOf(item);
      if (seenRevision.get(item.id) === revision) continue;
      seenRevision.set(item.id, revision);
      if (!item.status || !FINISHED.has(item.status)) continue;
      void narrateLatestSummary(item.id, now);
    }
  });
}
