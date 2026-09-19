import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { mergeDays, TokenTimelineChart } from "./TokenTimelineChart";
import type { ProviderTokenUsage } from "@/lib/tokenUsageApi";

afterEach(() => cleanup());

function provider(
  id: string,
  days: { day: string; tokens: number }[],
  label = id,
): ProviderTokenUsage {
  return {
    id,
    label,
    tokens: days.reduce((sum, d) => sum + d.tokens, 0),
    inputTokens: 0,
    outputTokens: 0,
    cachedTokens: 0,
    costUsd: 0,
    calls: days.length,
    days: days.map((d) => ({
      day: d.day,
      tokens: d.tokens,
      inputTokens: 0,
      outputTokens: 0,
      cachedTokens: 0,
      costUsd: 0,
      calls: 1,
    })),
    models: [],
  };
}

describe("mergeDays", () => {
  it("puts every provider on one continuous day axis", () => {
    const rows = mergeDays([
      provider("claude", [{ day: "2026-09-14", tokens: 100 }]),
      provider("grok", [{ day: "2026-09-16", tokens: 50 }]),
    ]);

    // 15 September saw no traffic at all, but it still gets a column — a
    // skipped day would make the 14th and the 16th look adjacent.
    expect(rows.map((row) => row.day)).toEqual(["2026-09-14", "2026-09-15", "2026-09-16"]);
    expect(rows[0]).toMatchObject({ claude: 100, grok: 0 });
    expect(rows[1]).toMatchObject({ claude: 0, grok: 0 });
    expect(rows[2]).toMatchObject({ claude: 0, grok: 50 });
  });

  it("labels days in UTC, matching the buckets the server counted", () => {
    const [row] = mergeDays([provider("claude", [{ day: "2026-09-14", tokens: 1 }])]);
    expect(row.label).toBe("Sep 14");
  });

  it("returns nothing when no provider recorded a day", () => {
    expect(mergeDays([])).toEqual([]);
    expect(mergeDays([provider("claude", [])])).toEqual([]);
  });
});

describe("TokenTimelineChart", () => {
  it("says so plainly when there is no history to draw", () => {
    render(<TokenTimelineChart providers={[]} animate={false} />);
    expect(screen.getByText("No token history yet")).toBeInTheDocument();
  });
});
