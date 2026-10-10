/**
 * The hub: one regular conversation per user that runs all of their sessions.
 *
 * Its messages travel the normal session routes; the server answers them
 * through the hub instead of a runner. This only finds (or makes) it.
 */
import { authenticatedFetch } from "@/lib/identity";

/** The caller's hub conversation id, made on first use. */
export async function openHub(): Promise<string> {
  const res = await authenticatedFetch("/v1/hub");
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
  const body = (await res.json()) as { session_id?: unknown };
  if (typeof body.session_id !== "string" || !body.session_id) {
    throw new Error("the server did not name a hub conversation");
  }
  return body.session_id;
}

/** What a hub news item carries for the voice to tell. */
export interface HubUpdate {
  id: number;
  kind: string;
  text: string;
  session_name: string | null;
}

async function readJson<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status}`);
  return (await res.json()) as T;
}

/** The newest hub entry id, so a call only hears news from after it opened. */
export async function hubLastId(): Promise<number> {
  const body = await readJson<{ last_id?: number }>(await authenticatedFetch("/v1/hub"));
  return typeof body.last_id === "number" ? body.last_id : 0;
}

/**
 * Hand what was said on a voice call to the hub and get back what to say.
 * The request and the hub's answer show in the hub conversation too.
 */
export async function askHubByVoice(text: string, context: string): Promise<string> {
  const body = await readJson<{ say?: string }>(
    await authenticatedFetch("/v1/hub/voice", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, context: context.slice(-8000) }),
    }),
  );
  return body.say ?? "";
}

/** Wait up to `waitS` for news after `after`: a session finished or needs them. */
export async function hubUpdates(
  after: number,
  waitS: number,
): Promise<{ updates: HubUpdate[]; lastId: number }> {
  const body = await readJson<{ updates?: HubUpdate[]; last_id?: number }>(
    await authenticatedFetch(`/v1/hub/updates?after=${after}&wait=${waitS}`),
  );
  return { updates: body.updates ?? [], lastId: body.last_id ?? after };
}

/** One model a session can switch to, as its own picker lists it. */
export interface HubModelChoice {
  id: string;
  label: string;
  /** Efforts this model takes; Codex lists them per model. */
  efforts: string[];
}

/** What a session runs on and what it could run on instead. */
export interface HubSessionRun {
  /** Its harness as the chat names it, e.g. "Claude Code". */
  provider: string;
  /** What it reports running, e.g. "claude-opus-5-5"; "" for its default. */
  model: string;
  model_label: string;
  /** A pick it has not reported running yet; "" when there is none. */
  requested: string;
  requested_label: string;
  effort: string;
  /** The choice its picker shows; "" when none of `models` matches. */
  selected: string;
  models: HubModelChoice[];
  efforts: string[];
}

/** One session as the hub sees it. */
export interface HubSessionRow {
  id: string;
  name: string;
  title: string;
  status: string | null;
  working: boolean;
  /** Its current step while working, e.g. "Bash: pytest". */
  now: string;
  /** Seconds since the epoch. */
  updated_at: number;
  pending_approvals: number;
  run: HubSessionRun;
}

/** The sessions the hub runs, newest first, with what each runs on. */
export async function listHubSessions(): Promise<HubSessionRow[]> {
  const body = await readJson<{ sessions?: HubSessionRow[] }>(
    await authenticatedFetch("/v1/hub/sessions"),
  );
  return body.sessions ?? [];
}

/**
 * Put a session on another model and/or effort, by the same rules as asking
 * the hub: only what that session offers, and the change is written into the
 * hub chat. `""` keeps a setting, `"default"` clears it.
 */
export async function setHubSessionModel(
  sessionId: string,
  change: { model?: string; effort?: string },
): Promise<{ changed: boolean; text: string }> {
  return readJson<{ changed: boolean; text: string }>(
    await authenticatedFetch("/v1/hub/model", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        model: change.model ?? "",
        effort: change.effort ?? "",
      }),
    }),
  );
}
