// The hub runs other chats and has no workspace of its own, so a file tab in
// its rail names the chat as well as the path: "<chat id>/<path in that chat>".

const CHAT_ID = /^[0-9a-f]{32}$/;
const CHAT_ROUTE = /^\/c\/([0-9a-f]{32})\/?$/;

/** A file in one chat's workspace. */
export interface ChatFile {
  chatId: string;
  path: string;
}

export function chatFileRef(chatId: string, path: string): string {
  return `${chatId}/${path}`;
}

/** The chat and path a hub file tab names, or null for a plain path. */
export function parseChatFileRef(ref: string): ChatFile | null {
  const chatId = ref.slice(0, 32);
  const path = ref.slice(33);
  if (ref[32] !== "/" || !CHAT_ID.test(chatId) || !path) return null;
  return { chatId, path };
}

/**
 * The file an app link (`<origin>/c/<chat>?file=<path>`) opens, with the
 * origin it was written for, or null for any other link.
 */
export function appFileLink(href: string, base: string): (ChatFile & { origin: string }) | null {
  let url: URL;
  try {
    url = new URL(href, base);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  const match = CHAT_ROUTE.exec(url.pathname);
  const path = url.searchParams.get("file");
  if (!match || !path) return null;
  return { chatId: match[1], path, origin: url.origin };
}
