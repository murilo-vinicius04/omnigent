/**
 * One card per provider: the tokens that vendor burned over the selected range.
 *
 * These carry the numbers in text, not just in the charts below — which is what
 * makes the palette legible for readers who cannot separate two hues, and what
 * lets someone read an exact figure instead of estimating a bar.
 */

import { formatSessionCostUsd } from "@/lib/formatCost";
import { formatTokens, providerColor, type ProviderTokenUsage } from "@/lib/tokenUsageApi";

interface Props {
  providers: ProviderTokenUsage[];
}

export function ProviderTokenCards({ providers }: Props) {
  if (providers.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
        No token usage recorded yet. Counts appear here as turns run.
      </div>
    );
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {providers.map((provider) => (
        <div
          key={provider.id}
          data-testid={`provider-tokens-${provider.id}`}
          className="rounded-lg border border-border bg-card p-4"
        >
          <div className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className="h-2.5 w-2.5 shrink-0 rounded-full"
              style={{ backgroundColor: providerColor(provider.id) }}
            />
            <p className="truncate text-xs font-medium text-muted-foreground">{provider.label}</p>
          </div>
          <p className="mt-1 text-2xl font-semibold tabular-nums">
            {formatTokens(provider.tokens)}
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground tabular-nums">
            {provider.calls} call{provider.calls === 1 ? "" : "s"}
            {provider.costUsd > 0 && ` · ${formatSessionCostUsd(provider.costUsd)}`}
          </p>
          <p className="mt-2 text-xs text-muted-foreground tabular-nums">
            {formatTokens(provider.inputTokens)} in · {formatTokens(provider.outputTokens)} out ·{" "}
            {formatTokens(provider.cachedTokens)} cached
          </p>
          {provider.models.length > 0 && (
            <p
              className="mt-2 truncate text-xs text-muted-foreground"
              title={provider.models[0].model}
            >
              {provider.models[0].model}
              {provider.models.length > 1 && ` +${provider.models.length - 1}`}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}
