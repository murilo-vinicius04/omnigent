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
