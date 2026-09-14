// A live view of one session's labels.
//
// `useSession` is `staleTime: Infinity` with no poll — right for the things it
// serves (they change on a bind), wrong for a label the server writes DURING a
// turn. The session-updates socket already pushes whole session rows, labels
// included, so this seeds from the snapshot and then follows the socket.
//
// The socket only pushes sessions in the client's watch-set, which the sidebar
// derives from what it displays. The active conversation is in it in practice;
// when it isn't, this simply stays on the seeded snapshot.

import { useEffect, useState } from "react";
import { useSession } from "@/hooks/useSession";
import { sessionUpdatesSocket, type SessionUpdatesFrame } from "@/lib/sessionUpdatesSocket";

/**
 * Follow a session's labels as the server rewrites them.
 *
 * @param conversationId - The session to watch, e.g. `"conv_abc123"`; null
 *   disables the subscription.
 * @returns The labels, or null before any have arrived.
 */
export function useSessionLabels(
  conversationId: string | null | undefined,
): Record<string, string> | null {
  const { session } = useSession(conversationId);
  const seeded = session?.labels ?? null;
  const [pushed, setPushed] = useState<Record<string, string> | null>(null);

  // A different conversation's labels must not linger on screen while the
  // socket has yet to say anything about the new one.
  useEffect(() => {
    setPushed(null);
  }, [conversationId]);

  useEffect(() => {
    if (!conversationId) return;
    return sessionUpdatesSocket.subscribe((frame: SessionUpdatesFrame) => {
      if (frame.type !== "snapshot" && frame.type !== "changed") return;
      for (const item of frame.items) {
        if (item?.id !== conversationId) continue;
        const labels = item.labels;
        // A row can arrive without labels (a diff about something else);
        // absent means "no change", not "none".
        if (labels) setPushed({ ...labels });
      }
    });
  }, [conversationId]);

  return pushed ?? seeded;
}
