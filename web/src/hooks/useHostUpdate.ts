import { useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { authenticatedFetch } from "@/lib/identity";

const POLL_MS = 3000;
const GIVE_UP_MS = 5 * 60 * 1000;

/** Ask hosts to update to the server's code and keep the host list fresh while they do. */
export function useHostUpdate() {
  const client = useQueryClient();
  const [startedAt, setStartedAt] = useState<Record<string, number>>({});
  const [error, setError] = useState("");

  const start = useCallback(async (hostId: string, force = false) => {
    setError("");
    const at = Date.now();
    setStartedAt((prev) => ({ ...prev, [hostId]: at }));
    try {
      const res = await authenticatedFetch(`/v1/hosts/${encodeURIComponent(hostId)}/update`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force }),
      });
      if (!res.ok) throw new Error(`Update request failed (${res.status}).`);
    } catch (e) {
      setStartedAt((prev) => {
        const { [hostId]: _gone, ...rest } = prev;
        return rest;
      });
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const watching = Object.keys(startedAt).length > 0;
  useEffect(() => {
    if (!watching) return;
    const timer = setInterval(() => {
      void client.invalidateQueries({ queryKey: ["hosts"] });
      const now = Date.now();
      setStartedAt((prev) => {
        const kept = Object.fromEntries(
          Object.entries(prev).filter(([, t]) => now - t < GIVE_UP_MS),
        );
        return Object.keys(kept).length === Object.keys(prev).length ? prev : kept;
      });
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [watching, client]);

  return { start, startedAt, error };
}
