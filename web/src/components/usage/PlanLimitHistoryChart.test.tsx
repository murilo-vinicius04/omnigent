import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { PlanLimitHistoryChart, tightestSeries } from "./PlanLimitHistoryChart";
import type { PlanLimitHistory } from "@/lib/tokenUsageApi";

afterEach(() => cleanup());

const CLAUDE: PlanLimitHistory = {
  provider: "claude",
  label: "Claude",
  windows: [
    {
      kind: "session",
      label: "5h",
      points: [
        { at: "2026-09-16T10:00:00Z", percent: 10 },
        { at: "2026-09-16T11:00:00Z", percent: 80 },
      ],
    },
    {
      kind: "weekly",
      label: "week",
      points: [
        { at: "2026-09-16T10:00:00Z", percent: 40 },
        { at: "2026-09-16T11:00:00Z", percent: 41 },
      ],
    },
  ],
};

const GEMINI: PlanLimitHistory = {
  provider: "antigravity",
  label: "Gemini",
  windows: [
    {
      kind: "gemini-5h",
      label: "gemini-5h",
      points: [{ at: "2026-09-16T10:30:00Z", percent: 55 }],
    },
  ],
};

describe("tightestSeries", () => {
  it("charts the window closest to its cap — the number the tray pill shows", () => {
    const { rows } = tightestSeries([CLAUDE]);

    // At 10:00 the weekly window (40%) is the binding one; an hour later the
    // 5-hour window (80%) has overtaken it.
    expect(rows.map((row) => row.claude)).toEqual([40, 80]);
  });

  it("names the provider's worst window so the legend is not just a colour", () => {
    const { series } = tightestSeries([CLAUDE]);
    expect(series[0]).toMatchObject({ label: "Claude", windowLabel: "5h" });
  });

  it("colours Gemini's row by the vendor, not by the client it is read from", () => {
    const { series } = tightestSeries([GEMINI]);
    expect(series[0].color).toBe("var(--provider-gemini)");
  });

  it("carries a reading forward until that provider reports again", () => {
    const { rows } = tightestSeries([CLAUDE, GEMINI]);

    // Providers sample independently: Gemini reported only at 10:30, so its
    // line holds 55% across the neighbouring Claude samples rather than
    // dropping to zero and inventing a recovery.
    expect(rows.map((row) => row.label.length > 0)).toEqual([true, true, true]);
    expect(rows.map((row) => row.antigravity)).toEqual([undefined, 55, 55]);
    expect(rows.map((row) => row.claude)).toEqual([40, 40, 80]);
  });

  it("skips a provider whose readings are all unparseable", () => {
    const { series, rows } = tightestSeries([
      {
        provider: "claude",
        label: "Claude",
        windows: [{ kind: "s", label: "5h", points: [{ at: "nope", percent: 5 }] }],
      },
    ]);
    expect(series).toEqual([]);
    expect(rows).toEqual([]);
  });
});

describe("PlanLimitHistoryChart", () => {
  it("says so plainly when nothing has been recorded", () => {
    render(<PlanLimitHistoryChart limits={[]} animate={false} />);
    expect(screen.getByText("No plan-limit readings yet")).toBeInTheDocument();
  });
});
