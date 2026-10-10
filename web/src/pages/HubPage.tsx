import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { PageScroll } from "@/components/PageScroll";
import { PINNED_CONVERSATIONS_KEY } from "@/hooks/useConversations";
import { openHub } from "@/lib/hubApi";
import { useNavigate } from "@/lib/routing";

/**
 * `/hub` opens the hub conversation: a regular chat, pinned in the sidebar,
 * made on first visit.
 */
export function HubPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    openHub().then(
      (sessionId) => {
        if (!live) return;
        // A hub made just now has to appear in the sidebar, pinned.
        void queryClient.invalidateQueries({ queryKey: ["conversations"] });
        void queryClient.invalidateQueries({ queryKey: PINNED_CONVERSATIONS_KEY });
        navigate(`/c/${sessionId}`, { replace: true });
      },
      (err: unknown) => {
        if (live) setError(err instanceof Error ? err.message : String(err));
      },
    );
    return () => {
      live = false;
    };
  }, [navigate, queryClient]);

  return (
    <PageScroll contentClassName="px-6">
      <p className="text-ui text-muted-foreground" data-testid="hub-opening">
        {error ? `Couldn't open the hub: ${error}` : "Opening the hub…"}
      </p>
    </PageScroll>
  );
}
