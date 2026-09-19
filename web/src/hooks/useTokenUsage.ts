import { useQuery } from "@tanstack/react-query";
import { fetchTokenUsage, type TokenUsageReport } from "@/lib/tokenUsageApi";

/**
 * Per-provider token usage for a UTC day window.
 *
 * Keyed on the window so switching the Usage page's range refetches rather
 * than reusing the previous range's series.
 */
export function useTokenUsage(window: { since: string | null; until: string | null }) {
  return useQuery<TokenUsageReport>({
    queryKey: ["usage", "tokens", window.since, window.until],
    queryFn: () => fetchTokenUsage(window),
    staleTime: 60_000,
  });
}
