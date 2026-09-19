/**
 * Client for ``GET /v1/plan-limits`` — the provider-side subscription windows
 * (Claude's 5-hour / weekly, Antigravity's quota) rendered next to the
 * composer's context ring.
 *
 * The context ring measures ONE conversation; these windows measure the budget
 * shared by every session on the host, which is what actually stops work. They
 * are deliberately separate readings.
 *
 * Every provider is best-effort and reports its own ``state``; the server never
 * fails the whole response because one vendor is signed out. Callers should
 * render only rows with usable windows (see ``usableProviders``) and treat the
 * rest as absent.
 */

import { authenticatedFetch } from "@/lib/identity";

/** Server-reported health of one provider's plan-limit lookup. */
export type PlanLimitState =
  /** Windows resolved and populated, live. */
  | "ok"
  /**
   * Windows are a replay of the last good reading, not a live one.
   *
   * Antigravity quota is only readable while an agy process is running, which
   * on most hosts means "while a Gemini-backed sub-agent is alive". Replaying
   * the last reading keeps the pill from flickering in and out; the ``as_of``
   * stamp is what makes that honest, so a stale row must be rendered visibly
   * differently from a live one.
   */
  | "stale"
  /** No credential on this host for that vendor. */
  | "signed-out"
  /** Authenticated, but the account's tier exposes no quota API. */
  | "unsupported"
  /** Provider unavailable or disabled on this host. */
  | "unavailable"
  /** Network/upstream failure; transient, retried on the next poll. */
  | "error";

/** One subscription window (a rolling session window, a weekly cap, …). */
export interface PlanLimitWindow {
  /** Stable machine id, e.g. ``session`` / ``daily`` / ``weekly``. */
  kind: string;
  /** Short label rendered in the tray, e.g. ``5h``. */
  label: string;
  /** Consumed share of the window, 0-100. */
  percent: number;
  /** ISO-8601 reset instant, or ``null`` when the provider omits one. */
  resets_at: string | null;
}

/** One vendor's plan-limit row. */
export interface PlanLimitProvider {
  id: string;
  label: string;
  state: PlanLimitState;
  windows: PlanLimitWindow[];
  /** Why a non-``ok`` state happened, when the server can explain it. */
  reason?: string | null;
  /** Human plan/tier name, when resolvable (shown in the tooltip). */
  tier?: string | null;
  /** ISO-8601 capture instant for ``stale`` rows. */
  as_of?: string | null;
  /** Unix epoch seconds when the rate limit cooldown ends. */
  retry_at?: number | null;
  /** Optional generic detail lines rendered in the tooltip. */
  details?: string[];
  /**
   * Tokens Omnigent counted against this vendor today (UTC), when it counted
   * any. Absent means "nothing recorded", never "zero used" — the count covers
   * only turns this host ran.
   */
  tokens_today?: number;
  /**
   * Claude only: what these readings would be on the Pro plan, when the
   * signed-in plan is a known multiple of Pro (Max 5x = 5).
   */
  pro_equivalent?: ProEquivalent;
}

/** One window scaled to the Pro plan. ``used_pct`` can exceed 100. */
export interface ProEquivalentWindow {
  kind: string;
  label: string | null;
  used_pct: number;
  /** Weighted tokens: input x1, output x5, cache read x0.1, cache write x1.25. */
  weighted_tokens_used: number;
  weighted_token_budget: number;
}

export interface ProEquivalent {
  /** Pro allowances in the current plan, e.g. 5 for Max 5x. */
  multiplier: number;
  windows: ProEquivalentWindow[];
}

export interface PlanLimits {
  providers: PlanLimitProvider[];
  fetched_at: number;
}

/**
 * Fetch the current plan limits.
 *
 * @returns Parsed limits, or ``null`` when the endpoint is unreachable or
 *   answers non-2xx. Returning ``null`` rather than throwing keeps a decorative
 *   tray from surfacing errors into the composer; the caller simply renders
 *   nothing until a later poll succeeds. Older servers without the route answer
 *   404, which lands here as ``null`` too.
 */
export async function fetchPlanLimits(signal?: AbortSignal): Promise<PlanLimits | null> {
  try {
    const res = await authenticatedFetch("/v1/plan-limits", { signal });
    if (!res.ok) return null;
    const data = (await res.json()) as PlanLimits;
    if (!data || !Array.isArray(data.providers)) return null;
    return data;
  } catch {
    return null;
  }
}

/**
 * Providers with at least one usable window, in server order.
 *
 * ``stale`` counts as usable: a last-known plan figure is still decision-useful
 * (and is labelled as such in the tray), whereas hiding it would make the pill
 * appear and vanish as sub-agents start and stop.
 *
 * An ``error`` provider with reason ``rate_limited`` is also usable so the tray
 * can show a muted retry indicator rather than flickering away.
 */
export function usableProviders(limits: PlanLimits | null): PlanLimitProvider[] {
  if (!limits) return [];
  return limits.providers.filter(
    (p) =>
      ((p.state === "ok" || p.state === "stale") && p.windows.length > 0) ||
      (p.state === "error" && p.reason === "rate_limited"),
  );
}

/**
 * The window a provider should lead with: the one closest to its cap.
 *
 * The tray has room for one number per vendor, and the binding constraint is
 * whichever window is closest to its cap — a 90%-consumed 5-hour window matters more than
 * a 10%-consumed weekly one, regardless of declaration order.
 */
export function tightestWindow(provider: PlanLimitProvider): PlanLimitWindow | null {
  return provider.windows.reduce<PlanLimitWindow | null>((worst, w) => {
    if (worst === null) return w;
    return w.percent > worst.percent ? w : worst;
  }, null);
}

/**
 * Format a reset instant or epoch timestamp as a short local time for the tooltip.
 *
 * @param iso - ISO-8601 instant or unix epoch seconds, or ``null``.
 * @returns A localized short form, or ``null`` when absent/unparseable.
 */
export function formatResetAt(iso: string | number | null | undefined): string | null {
  if (iso === null || iso === undefined || iso === "") return null;
  const at =
    typeof iso === "number"
      ? new Date(iso * 1000)
      : !Number.isNaN(Number(iso))
        ? new Date(Number(iso) * 1000)
        : new Date(iso);
  if (Number.isNaN(at.getTime())) return null;
  const sameDay = at.toDateString() === new Date().toDateString();
  return at.toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
    ...(sameDay ? {} : { weekday: "short" }),
  });
}
