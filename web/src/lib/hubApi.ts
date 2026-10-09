/**
 * The hub chat: one chat, with no name, that runs your sessions for you.
 *
 * The server keeps the log (`/v1/hub`). This store mirrors it, posts what
 * they type, and long-polls for what the hub says back while the page is
 * open. Sends and reports from the voice hub land in the same log, so a
 * call and the typed chat read as one conversation.
 */
import { create } from "zustand";

import { authenticatedFetch } from "@/lib/identity";

export type HubEntryKind =
  "message" | "sent" | "finished" | "status" | "check" | "attention" | "error";

export interface HubEntry {
  id: number;
  at: number;
  role: "you" | "hub";
  kind: HubEntryKind;
  text: string;
  via: string | null;
  session_id: string | null;
  session_name: string | null;
  /** Longer text the page can unfold: what was sent, or the full reply. */
  detail: string | null;
}

export interface HubSessionInfo {
  id: string;
  name: string;
  title: string;
  nickname: string | null;
  handle: string;
  status: string | null;
  working: boolean;
  now: string;
  updated_at: number;
  pending_approvals: number;
}

/** How long one long-poll waits on the server for a new entry. */
const WAIT_S = 25;
/** Pause after a failed poll before trying again. */
const RETRY_MS = 3_000;
/** How often the session strip refreshes while the page is open. */
const SESSIONS_MS = 10_000;

interface HubStore {
  entries: HubEntry[];
  sessions: HubSessionInfo[];
  lastId: number;
  loaded: boolean;
  error: string | null;
  sending: boolean;
  load: () => Promise<void>;
  send: (text: string) => Promise<void>;
  refreshSessions: () => Promise<void>;
  /** Keep the log and sessions current; returns the function that stops it. */
  follow: () => () => void;
}

async function readJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // The status code alone says enough.
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

/** Add entries the store does not have yet, keeping id order. */
export function mergeEntries(have: HubEntry[], incoming: HubEntry[]): HubEntry[] {
  if (incoming.length === 0) return have;
  const known = new Set(have.map((e) => e.id));
  const fresh = incoming.filter((e) => !known.has(e.id));
  if (fresh.length === 0) return have;
  return [...have, ...fresh].sort((a, b) => a.id - b.id);
}

/** Whether the hub still owes an answer to the last thing they said. */
export function hubIsThinking(entries: HubEntry[]): boolean {
  const last = entries[entries.length - 1];
  return last?.role === "you";
}

export const useHubStore = create<HubStore>((set, get) => ({
  entries: [],
  sessions: [],
  lastId: 0,
  loaded: false,
  error: null,
  sending: false,

  load: async () => {
    try {
      const body = await readJson<{
        entries: HubEntry[];
        last_id: number;
        sessions: HubSessionInfo[];
      }>(await authenticatedFetch("/v1/hub"));
      set((s) => ({
        entries: mergeEntries(s.entries, body.entries),
        lastId: Math.max(s.lastId, body.last_id),
        sessions: body.sessions,
        loaded: true,
        error: null,
      }));
    } catch (err) {
      set({ loaded: true, error: err instanceof Error ? err.message : String(err) });
    }
  },

  send: async (text) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    set({ sending: true });
    try {
      const entry = await readJson<HubEntry>(
        await authenticatedFetch("/v1/hub/messages", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: trimmed }),
        }),
      );
      set((s) => ({
        entries: mergeEntries(s.entries, [entry]),
        lastId: Math.max(s.lastId, entry.id),
        error: null,
      }));
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) });
      throw err;
    } finally {
      set({ sending: false });
    }
  },

  refreshSessions: async () => {
    try {
      const body = await readJson<{ sessions: HubSessionInfo[] }>(
        await authenticatedFetch("/v1/hub/sessions"),
      );
      set({ sessions: body.sessions });
    } catch {
      // The strip keeps its last reading; the log is what matters.
    }
  },

  follow: () => {
    let stopped = false;
    // One long-poll at a time; each schedules the next, sooner after a success.
    const poll = async () => {
      if (stopped) return;
      let retryMs = 0;
      try {
        const body = await readJson<{ entries: HubEntry[]; last_id: number }>(
          await authenticatedFetch(`/v1/hub/entries?after=${get().lastId}&wait=${WAIT_S}`),
        );
        if (stopped) return;
        set((s) => ({
          entries: mergeEntries(s.entries, body.entries),
          lastId: Math.max(s.lastId, body.last_id),
          error: null,
        }));
        // A send or a finish changes what the sessions are doing.
        if (body.entries.some((e) => e.kind !== "message")) void get().refreshSessions();
      } catch {
        retryMs = RETRY_MS;
      }
      if (!stopped) setTimeout(() => void poll(), retryMs);
    };
    void poll();
    const timer = setInterval(() => void get().refreshSessions(), SESSIONS_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  },
}));
