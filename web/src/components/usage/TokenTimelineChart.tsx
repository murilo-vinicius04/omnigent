/**
 * Tokens per UTC day, stacked by provider.
 *
 * Stacked rather than grouped because the question is "how much did the day
 * cost us in total, and who drove it" — the total is the shape, the segments
 * are the attribution.
 */

import { Bar, BarChart, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { formatTokens, providerColor, type ProviderTokenUsage } from "@/lib/tokenUsageApi";

interface Props {
  providers: ProviderTokenUsage[];
  animate?: boolean;
}

interface DayRow {
  label: string;
  day: string;
  [providerId: string]: string | number;
}

/**
 * Merge every provider's day series onto one continuous axis.
 *
 * Days nobody used still get a column: a gap in the log would otherwise read
 * as a busy day sitting next to a quiet one.
 */
export function mergeDays(providers: ProviderTokenUsage[]): DayRow[] {
  const days = new Set<string>();
  for (const provider of providers) {
    for (const day of provider.days) days.add(day.day);
  }
  if (days.size === 0) return [];

  const sorted = Array.from(days).sort();
  const byProvider = new Map(
    providers.map((provider) => [
      provider.id,
      new Map(provider.days.map((d) => [d.day, d.tokens])),
    ]),
  );

  const rows: DayRow[] = [];
  const end = new Date(`${sorted[sorted.length - 1]}T00:00:00Z`).getTime();
  for (
    let cursor = new Date(`${sorted[0]}T00:00:00Z`);
    cursor.getTime() <= end;
    cursor.setUTCDate(cursor.getUTCDate() + 1)
  ) {
    const iso = cursor.toISOString().slice(0, 10);
    const row: DayRow = {
      day: iso,
      label: `${cursor.toLocaleString(undefined, { month: "short", timeZone: "UTC" })} ${cursor.getUTCDate()}`,
    };
    for (const provider of providers) {
      row[provider.id] = byProvider.get(provider.id)?.get(iso) ?? 0;
    }
    rows.push(row);
  }
  return rows;
}

function DayTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: readonly { name?: string; value?: number; color?: string }[];
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  const rows = payload.filter((entry) => (entry.value ?? 0) > 0);
  const total = rows.reduce((sum, entry) => sum + (entry.value ?? 0), 0);
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-2 text-sm shadow-md">
      <p className="text-muted-foreground">{label}</p>
      {rows.map((entry) => (
        <p key={entry.name} className="flex items-center gap-1.5 tabular-nums">
          <span
            aria-hidden="true"
            className="h-2 w-2 rounded-full"
            style={{ backgroundColor: entry.color }}
          />
          {entry.name}: {formatTokens(entry.value ?? 0)}
        </p>
      ))}
      <p className="mt-1 border-t border-border pt-1 font-medium tabular-nums">
        {formatTokens(total)} total
      </p>
    </div>
  );
}

export function TokenTimelineChart({ providers, animate = true }: Props) {
  const data = mergeDays(providers);
  if (data.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">
        No token history yet
      </div>
    );
  }

  return (
    <div className="h-72">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <XAxis
            dataKey="label"
            tick={{ fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
            className="fill-muted-foreground"
          />
          <YAxis
            tick={{ fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            tickFormatter={formatTokens}
            width={52}
            className="fill-muted-foreground"
          />
          <Tooltip content={<DayTooltip />} cursor={{ fill: "var(--accent)", opacity: 0.3 }} />
          <Legend
            verticalAlign="bottom"
            height={28}
            iconType="circle"
            iconSize={8}
            wrapperStyle={{ fontSize: 11 }}
          />
          {providers.map((provider, index) => (
            <Bar
              key={provider.id}
              dataKey={provider.id}
              name={provider.label}
              stackId="tokens"
              fill={providerColor(provider.id)}
              // A hairline of the card surface between segments, so adjacent
              // fills stay separable without a border colour of their own.
              stroke="var(--card)"
              strokeWidth={1}
              radius={index === providers.length - 1 ? [3, 3, 0, 0] : undefined}
              isAnimationActive={animate}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
