// Whether session notifications (OS turn-end cues + the unread badge) cover
// sessions other users shared with the viewer.
//
// Device-local view preference — no account or session state changes — stored
// in localStorage like the other `*Preferences` helpers. Defaults to false:
// on a shared server another user's session finishing would otherwise beep
// here, and a read-only grant gives the viewer nothing to do about it, so it
// reads as noise from their own machine. Anyone who relays for another lane
// can opt back in.

const STORAGE_KEY = "omnigent:notify-shared-sessions";

/**
 * Read the shared-sessions notification scope. Returns false (owned sessions
 * only) when nothing is stored, on a server render, when storage is
 * inaccessible, or when the stored value is unrecognized — never throws.
 */
export function readNotifySharedSessions(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * Persist the shared-sessions notification scope. Swallows quota/access
 * errors so a failed write can't break settings.
 */
export function writeNotifySharedSessions(value: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, value ? "1" : "0");
  } catch {
    // A lost view preference is harmless.
  }
}

/**
 * Ownership check with the sidebar's exact semantics (see `isOwnedByViewer`
 * in the sidebar): a null/absent owner reads as owned (single-user /
 * legacy shape), otherwise the viewer owns it iff the ids match. A null
 * viewer id (identity unresolved) owns nothing but the ownerless rows.
 */
export function isOwnedByViewer(
  conversation: { owner?: string | null },
  viewerId: string | null,
): boolean {
  const owner = conversation.owner ?? null;
  if (owner === null) return true;
  return owner === viewerId;
}

/**
 * Narrow a session list to the rows allowed to notify. With `includeShared`
 * the list passes through; otherwise only viewer-owned rows survive.
 */
export function filterNotifiableSessions<T extends { owner?: string | null }>(
  conversations: readonly T[],
  viewerId: string | null,
  includeShared: boolean,
): T[] {
  if (includeShared) return [...conversations];
  return conversations.filter((conversation) => isOwnedByViewer(conversation, viewerId));
}
