/**
 * Subscription plan-limit pills for the composer status tray.
 *
 * Sits beside the context ring and answers the question the ring cannot: not
 * "how full is this conversation" but "how much of my *plan* have I burned".
 * That second number is the one that actually stops work, and it is shared by
 * every session on the host — so it belongs next to the ring, not inside it.
 *
 * One pill per signed-in provider, each showing that provider's tightest
 * window (see ``tightestWindow``). Providers that are signed out, unsupported,
 * or erroring render nothing at all: this is ambient decoration, and a host
 * with one vendor configured should not display a row of dead placeholders.
 */

import { useEffect, useState } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  fetchPlanLimits,
  formatResetAt,
  tightestWindow,
  usableProviders,
  type PlanLimits,
} from "@/lib/planLimitsApi";
import { cn } from "@/lib/utils";

const RING_CIRCUMFERENCE = 2 * Math.PI * 5.5;

/**
 * Poll interval. Plan windows move on the order of minutes and the server
 * caches for 60s, so anything faster only burns requests without moving the
 * number.
 */
const POLL_INTERVAL_MS = 60_000;

/** Colour ramp mirrors ``ContextRing`` so the tray reads as one instrument. */
function severityClass(percent: number): string {
  if (percent > 80) return "text-destructive";
  if (percent > 60) return "text-warning";
  return "text-muted-foreground";
}

/** Small filled ring + percentage, matching the context ring's geometry. */
function LimitRing({ percent }: { percent: number }) {
  const arc = (Math.min(Math.max(percent, 0), 100) / 100) * RING_CIRCUMFERENCE;
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" fill="none" aria-hidden="true">
      <circle cx="8" cy="8" r="5.5" stroke="currentColor" strokeWidth="2" opacity="0.2" />
      {arc > 0 && (
        <circle
          cx="8"
          cy="8"
          r="5.5"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeDasharray={`${arc} ${RING_CIRCUMFERENCE}`}
          transform="rotate(-90 8 8)"
        />
      )}
    </svg>
  );
}

/**
 * Render one pill per provider that reports usable plan windows.
 *
 * Renders ``null`` (no wrapper, no spacing) when nothing is available, so the
 * tray collapses cleanly on hosts with no subscription-backed provider.
 */
export function PlanLimitPills() {
  const [limits, setLimits] = useState<PlanLimits | null>(null);

  useEffect(() => {
    // One controller per mount, aborted on unmount so a slow in-flight request
    // can't call setState after teardown.
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;

    const tick = async () => {
      const next = await fetchPlanLimits(controller.signal);
      if (controller.signal.aborted) return;
      // Keep the last good reading on a transient failure rather than blanking
      // the tray — a dropped poll shouldn't make the number disappear.
      if (next) setLimits(next);
      timer = setTimeout(tick, POLL_INTERVAL_MS);
    };
    void tick();

    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, []);

  const providers = usableProviders(limits);
  if (providers.length === 0) return null;

  return (
    <>
      {providers.map((provider) => {
        if (provider.state === "error" && provider.reason === "rate_limited") {
          const retryTime = formatResetAt(provider.retry_at);
          return (
            <Tooltip key={provider.id}>
              <TooltipTrigger asChild>
                <span
                  data-testid={`plan-limit-${provider.id}`}
                  className="flex items-center gap-1.5 opacity-50 text-muted-foreground"
                  aria-label={`${provider.label} rate limited${
                    retryTime ? `, retrying at ${retryTime}` : ", retrying"
                  }`}
                >
                  <span className="text-sm tabular-nums" aria-hidden="true">
                    {provider.label} —
                  </span>
                </span>
              </TooltipTrigger>
              <TooltipContent side="top" className="max-w-56 text-center text-sm">
                <p className="font-medium">{provider.label}</p>
                <p className="text-muted-foreground">
                  {provider.id === "claude" ? "Anthropic" : provider.label} rate limit, retrying
                  {retryTime ? ` at ${retryTime}` : ""}
                </p>
              </TooltipContent>
            </Tooltip>
          );
        }

        const window = tightestWindow(provider);
        if (!window) return null;
        const resets = formatResetAt(window.resets_at);
        const stale = provider.state === "stale";
        const asOf = stale ? formatResetAt(provider.as_of) : null;
        const percent = window.percent;

        return (
          <Tooltip key={provider.id}>
            <TooltipTrigger asChild>
              <span
                data-testid={`plan-limit-${provider.id}`}
                data-stale={stale ? "true" : undefined}
                className={cn(
                  "flex items-center gap-1.5",
                  severityClass(percent),
                  // A replayed reading must not look like a live one.
                  stale && "opacity-50",
                )}
                aria-label={`${provider.label} ${window.label} plan limit ${percent}% used${
                  stale ? " (last known)" : ""
                }`}
              >
                <LimitRing percent={percent} />
                <span className="text-sm tabular-nums" aria-hidden="true">
                  {percent}%
                </span>
              </span>
            </TooltipTrigger>
            <TooltipContent side="top" className="max-w-56 text-center text-sm">
              <p className="font-medium">
                {provider.label}
                {provider.tier ? ` · ${provider.tier}` : ""}
              </p>
              {provider.windows.map((w) => (
                <p key={w.kind} className="tabular-nums">
                  {w.label}: {w.percent}% used
                  {w.kind === window.kind && resets ? ` · resets ${resets}` : ""}
                </p>
              ))}
              {provider.details?.map((line) => (
                <p key={line} className="tabular-nums">
                  {line}
                </p>
              ))}
              {stale && (
                <p className="text-muted-foreground">
                  {provider.id === "claude"
                    ? `${asOf ? `as of ${asOf} — ` : ""}Anthropic rate limit, retrying`
                    : `last known${asOf ? ` · ${asOf}` : ""}`}
                </p>
              )}
            </TooltipContent>
          </Tooltip>
        );
      })}
    </>
  );
}
