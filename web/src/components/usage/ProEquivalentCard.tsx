/**
 * "If you were on Pro" — the live Claude reading scaled to the Pro plan.
 *
 * On a Max plan the tray says how full *that* plan is; this card answers
 * whether the same work would have fit on Pro, as a share of each Pro window
 * and in weighted tokens. Hidden when the plan's multiple of Pro is unknown.
 */
import { useEffect, useState } from "react";
import { fetchPlanLimits, type ProEquivalent } from "@/lib/planLimitsApi";
import { formatTokens } from "@/lib/tokenUsageApi";
import { cn } from "@/lib/utils";

const POLL_INTERVAL_MS = 60_000;

const WINDOW_TITLES: Record<string, string> = {
  session: "5-hour window",
  weekly: "Week",
};

export function ProEquivalentCard() {
  const [equivalent, setEquivalent] = useState<ProEquivalent | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      const next = await fetchPlanLimits(controller.signal);
      if (controller.signal.aborted) return;
      const claude = next?.providers.find((p) => p.id === "claude");
      // Keep the last reading on a failed poll rather than blanking the card.
      if (claude?.pro_equivalent) setEquivalent(claude.pro_equivalent);
      timer = setTimeout(tick, POLL_INTERVAL_MS);
    };
    void tick();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, []);

  if (!equivalent || equivalent.multiplier === 1) return null;

  return (
    <section>
      <h2 className="mb-3 text-sm font-medium text-muted-foreground">If you were on Pro</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        {equivalent.windows.map((w) => {
          const over = w.used_pct > 100;
          return (
            <div key={w.kind} className="rounded-lg border border-border bg-card p-4">
              <p className="text-xs text-muted-foreground">{WINDOW_TITLES[w.kind] ?? w.kind}</p>
              <p
                className={cn(
                  "mt-1 text-2xl font-semibold tabular-nums",
                  over && "text-destructive",
                )}
              >
                {Math.round(w.used_pct)}% of Pro
              </p>
              <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-muted">
                <div
                  className={cn("h-full rounded-full", over ? "bg-destructive" : "bg-primary")}
                  style={{ width: `${Math.min(100, w.used_pct)}%` }}
                />
              </div>
              <p className="mt-1.5 text-xs text-muted-foreground tabular-nums">
                {formatTokens(w.weighted_tokens_used)} of ~{formatTokens(w.weighted_token_budget)}{" "}
                weighted tokens
                {over && " — Pro would have stopped here"}
              </p>
            </div>
          );
        })}
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        Your plan is {equivalent.multiplier}× Pro, so each point on it is {equivalent.multiplier} on
        Pro. Weighted tokens count output ×5 and cache reads ×0.1; the Pro budgets are estimates
        calibrated from this machine&apos;s own usage.
      </p>
    </section>
  );
}
