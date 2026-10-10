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
