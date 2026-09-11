import { authenticatedFetch } from "./identity";
import { ApiError } from "./sessionsApi";

/** One thing the companion knows, as the server stores it. */
interface CompanionEntryWire {
  id: number;
  kind: "activity" | "summary" | "question" | "answer" | "note";
  text: string;
  at: number;
}

interface CompanionStateWire {
  session_id: string;
  model: string;
  running: boolean;
  idle_s: number;
  warm_since: number | null;
  pending_notes: number;
  context: CompanionEntryWire[];
}

interface CompanionAskWire {
  answer: string;
  state: CompanionStateWire;
}

/** One entry in the companion's ledger. */
export interface CompanionEntry {
  /** Position in the session's ledger, from 1. Stable across reads. */
  id: number;
  kind: CompanionEntryWire["kind"];
  text: string;
  /** Unix seconds when the companion learned it. */
  at: number;
}

/**
 * What the companion knows for one session.
 *
 * The ledger is the companion's memory — its warm `agy` process is only a
 * cache of it — so this is the whole truth about what it can answer from,
 * and the panel renders it directly rather than keeping its own copy.
 */
export interface CompanionState {
  sessionId: string;
  model: string;
  /** Whether a warm process is held right now. */
  running: boolean;
  /** Seconds since the last exchange. */
  idleS: number;
  /** When the held process finished warming, or `null` if none is warm. */
  warmSince: number | null;
  /** Ledger entries not yet delivered to the model; they ride the next ask. */
  pendingNotes: number;
  context: CompanionEntry[];
}

function fromWire(wire: CompanionStateWire): CompanionState {
  return {
    sessionId: wire.session_id,
    model: wire.model,
    running: wire.running,
    idleS: wire.idle_s,
    warmSince: wire.warm_since,
    pendingNotes: wire.pending_notes,
    context: wire.context,
  };
}

async function readOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string" && body.detail) detail = body.detail;
    } catch {
      // A non-JSON body leaves the status-based message above.
    }
    throw new ApiError(detail, res.status, null);
  }
  return (await res.json()) as T;
}

function base(sessionId: string): string {
  return `/v1/discussion/${encodeURIComponent(sessionId)}`;
}

/** Read what the companion knows for a session. */
export async function getCompanionState(sessionId: string): Promise<CompanionState> {
  return fromWire(await readOrThrow<CompanionStateWire>(await authenticatedFetch(base(sessionId))));
}

/**
 * Start the companion's process now, so the first question is a warm one.
 *
 * A cold question costs ~3.5s and a warm one ~1s, so this is called when
 * the panel opens: the reader is reading the ledger while it warms.
 */
export async function prewarmCompanion(sessionId: string): Promise<CompanionState> {
  const res = await authenticatedFetch(`${base(sessionId)}/prewarm`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: "{}",
  });
  return fromWire(await readOrThrow<CompanionStateWire>(res));
}

/** Ask the companion a question. Returns its answer and the updated ledger. */
export async function askCompanion(
  sessionId: string,
  text: string,
): Promise<{ answer: string; state: CompanionState }> {
  const res = await authenticatedFetch(`${base(sessionId)}/ask`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text }),
  });
  const body = await readOrThrow<CompanionAskWire>(res);
  return { answer: body.answer, state: fromWire(body.state) };
}
