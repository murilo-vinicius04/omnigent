/**
 * Client for ``GET /v1/usage/tokens`` — how many tokens each provider burned,
 * and when.
 *
 * The Usage page's cost charts answer "what did this cost"; a subscription
 * plan does not bill per dollar, it burns a token budget, so the number that
 * actually predicts hitting a wall is this one. The series is built from the
 * server's append-only usage log, which is rotated once it grows past a few
 * megabytes — so an empty or short history is normal, not an error.
 */

import { authenticatedFetch } from "@/lib/identity";

/** Vendor families the server groups model ids into. */
export type ProviderId = "claude" | "gemini" | "openai" | "grok" | "other";

/**
 * Chart colour per vendor, resolved from the theme.
 *
 * Keyed by the entity, never by rank: hiding one provider must not repaint the
 * ones that remain. The tokens behind these are colourblind-safe as a set (see
 * ``--provider-*`` in ``index.css``).
 */
export const PROVIDER_COLORS: Record<ProviderId, string> = {
  claude: "var(--provider-claude)",
  gemini: "var(--provider-gemini)",
  openai: "var(--provider-openai)",
  grok: "var(--provider-grok)",
  other: "var(--provider-other)",
};

/** Stacking / legend order, matching the server's provider order. */
export const PROVIDER_ORDER: ProviderId[] = ["claude", "gemini", "openai", "grok", "other"];

/** Colour for a provider id, falling back to the catch-all hue. */
export function providerColor(id: string): string {
  return PROVIDER_COLORS[id as ProviderId] ?? PROVIDER_COLORS.other;
}

// ── Wire types (snake_case from server) ─────────────────────────

interface TokenCountsWire {
  tokens: number;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  cost_usd: number;
  calls: number;
}

interface ProviderTokenUsageWire extends TokenCountsWire {
  id: string;
  label: string;
  days: (TokenCountsWire & { day: string })[];
  models: (TokenCountsWire & { model: string })[];
}

interface PlanLimitHistoryWire {
  provider: string;
  label: string;
  windows: {
    kind: string;
    label: string;
    points: { at: string; percent: number }[];
  }[];
}

interface TokenUsageReportWire {
  since: string | null;
  until: string | null;
  providers: ProviderTokenUsageWire[];
  limits: PlanLimitHistoryWire[];
  totals: TokenCountsWire;
}

// ── App types (camelCase) ───────────────────────────────────────

export interface TokenCounts {
  tokens: number;
  inputTokens: number;
  outputTokens: number;
  cachedTokens: number;
  costUsd: number;
  calls: number;
}

export interface ProviderTokenDay extends TokenCounts {
  day: string;
}

export interface ProviderTokenModel extends TokenCounts {
  model: string;
}

export interface ProviderTokenUsage extends TokenCounts {
  id: string;
  label: string;
  days: ProviderTokenDay[];
  models: ProviderTokenModel[];
}

export interface PlanLimitWindowSeries {
  kind: string;
  label: string;
  points: { at: string; percent: number }[];
}

export interface PlanLimitHistory {
  provider: string;
  label: string;
  windows: PlanLimitWindowSeries[];
}

export interface TokenUsageReport {
  since: string | null;
  until: string | null;
  providers: ProviderTokenUsage[];
  limits: PlanLimitHistory[];
  totals: TokenCounts;
}

function counts(wire: TokenCountsWire): TokenCounts {
  return {
    tokens: wire.tokens ?? 0,
    inputTokens: wire.input_tokens ?? 0,
    outputTokens: wire.output_tokens ?? 0,
    cachedTokens: wire.cached_tokens ?? 0,
    costUsd: wire.cost_usd ?? 0,
    calls: wire.calls ?? 0,
  };
}

// ── Fetch ───────────────────────────────────────────────────────

/**
 * Fetch the per-provider token report.
 *
 * @param window - Inclusive UTC day bounds; omit either for "unbounded".
 */
export async function fetchTokenUsage(window: {
  since?: string | null;
  until?: string | null;
}): Promise<TokenUsageReport> {
  const params = new URLSearchParams();
  if (window.since) params.set("since", window.since);
  if (window.until) params.set("until", window.until);
  const query = params.toString();
  const res = await authenticatedFetch(`/v1/usage/tokens${query ? `?${query}` : ""}`);
  if (!res.ok) throw new Error(`Token usage fetch failed: ${res.status}`);
  const wire: TokenUsageReportWire = await res.json();
  return {
    since: wire.since ?? null,
    until: wire.until ?? null,
    providers: (wire.providers ?? []).map((provider) => ({
      ...counts(provider),
      id: provider.id,
      label: provider.label,
      days: (provider.days ?? []).map((day) => ({ ...counts(day), day: day.day })),
      models: (provider.models ?? []).map((model) => ({ ...counts(model), model: model.model })),
    })),
    limits: (wire.limits ?? []).map((limit) => ({
      provider: limit.provider,
      label: limit.label,
      windows: (limit.windows ?? []).map((w) => ({
        kind: w.kind,
        label: w.label,
        points: w.points ?? [],
      })),
    })),
    totals: counts(wire.totals ?? ({} as TokenCountsWire)),
  };
}

/**
 * Render a token count compactly, e.g. ``184k`` / ``2.5M``.
 *
 * Mirrors the server's ``format_tokens`` so the tray tooltip and these charts
 * never disagree about the same number.
 */
export function formatTokens(count: number): string {
  if (!Number.isFinite(count) || count <= 0) return "0";
  if (count >= 1_000_000) return `${trimZeros((count / 1_000_000).toFixed(2))}M`;
  if (count >= 1_000) return `${trimZeros((count / 1_000).toFixed(1))}k`;
  return String(Math.round(count));
}

function trimZeros(value: string): string {
  return value.replace(/\.?0+$/, "");
}
