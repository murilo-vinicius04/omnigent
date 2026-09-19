/**
 * Plan-window utilization over time — the curve behind the composer tray's
 * pills.
 *
 * Each provider contributes one line: its *tightest* window at that moment,
 * which is exactly the number the pill shows. Charting every window instead
 * would put nine near-identical lines on one axis and answer nothing.
 */

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { providerColor, type PlanLimitHistory } from "@/lib/tokenUsageApi";
import { PLAN_PROVIDER_FAMILY } from "@/lib/planProviders";

interface Props {
  limits: PlanLimitHistory[];
  animate?: boolean;
}

interface Series {
  provider: string;
  label: string;
  color: string;
  /** The window that was tightest overall, named in the legend. */
  windowLabel: string;
}

interface Row {
  at: number;
  label: string;
  [provider: string]: number | string;
}

/**
 * Collapse each provider's windows into "how close to a wall is it right now",
 * then align every provider onto one time axis.
 *
 * Providers sample independently, so a reading carries forward until that
 * provider reports again — a line that dropped to zero between samples would
 * invent a recovery that never happened.
 */
export function tightestSeries(limits: PlanLimitHistory[]): { series: Series[]; rows: Row[] } {
  const series: Series[] = [];
  const perProvider = new Map<string, Map<number, number>>();

  for (const limit of limits) {
    const worstAt = new Map<number, number>();
    let peak = -1;
    let peakWindow = "";
    for (const window of limit.windows) {
      for (const point of window.points) {
        const at = Date.parse(point.at);
        if (Number.isNaN(at)) continue;
        worstAt.set(at, Math.max(worstAt.get(at) ?? 0, point.percent));
        if (point.percent > peak) {
          peak = point.percent;
          peakWindow = window.label;
        }
      }
    }
    if (worstAt.size === 0) continue;
    perProvider.set(limit.provider, worstAt);
    series.push({
      provider: limit.provider,
      label: limit.label,
      color: providerColor(PLAN_PROVIDER_FAMILY[limit.provider] ?? limit.provider),
      windowLabel: peakWindow,
    });
  }

  const stamps = new Set<number>();
  for (const worstAt of perProvider.values()) {
    for (const at of worstAt.keys()) stamps.add(at);
  }

  const carried = new Map<string, number>();
  const rows: Row[] = Array.from(stamps)
    .sort((a, b) => a - b)
    .map((at) => {
      const row: Row = {
        at,
        label: new Date(at).toLocaleString(undefined, {
          month: "short",
          day: "numeric",
          hour: "numeric",
        }),
      };
      for (const entry of series) {
        const value = perProvider.get(entry.provider)?.get(at);
        if (value !== undefined) carried.set(entry.provider, value);
        const last = carried.get(entry.provider);
        if (last !== undefined) row[entry.provider] = last;
      }
      return row;
    });

  return { series, rows };
}

function LimitTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: readonly { name?: string; value?: number; color?: string }[];
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-2 text-sm shadow-md">
      <p className="text-muted-foreground">{label}</p>
      {payload.map((entry) => (
        <p key={entry.name} className="flex items-center gap-1.5 tabular-nums">
          <span
            aria-hidden="true"
            className="h-2 w-2 rounded-full"
            style={{ backgroundColor: entry.color }}
          />
          {entry.name}: {entry.value}% used
        </p>
      ))}
    </div>
  );
}

export function PlanLimitHistoryChart({ limits, animate = true }: Props) {
  const { series, rows } = tightestSeries(limits);
  if (rows.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">
        No plan-limit readings yet
      </div>
    );
  }

  return (
    <div className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--border)" strokeOpacity={0.5} />
          <XAxis
            dataKey="label"
            tick={{ fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
            minTickGap={48}
            className="fill-muted-foreground"
          />
          <YAxis
            domain={[0, 100]}
            tick={{ fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(value: number) => `${value}%`}
            width={44}
            className="fill-muted-foreground"
          />
          <Tooltip content={<LimitTooltip />} cursor={{ stroke: "var(--border)" }} />
          <Legend
            verticalAlign="bottom"
            height={28}
            iconType="plainline"
            iconSize={12}
            wrapperStyle={{ fontSize: 11 }}
          />
          {series.map((entry) => (
            <Line
              key={entry.provider}
              type="monotone"
              dataKey={entry.provider}
              name={`${entry.label} (${entry.windowLabel})`}
              stroke={entry.color}
              strokeWidth={2}
              dot={false}
              connectNulls
              isAnimationActive={animate}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
