import { describe, expect, it } from "vitest";

import {
  formatResetAt,
  tightestWindow,
  usableProviders,
  type PlanLimitProvider,
  type PlanLimits,
} from "@/lib/planLimitsApi";

function provider(overrides: Partial<PlanLimitProvider> = {}): PlanLimitProvider {
  return {
    id: "claude",
    label: "Claude",
    state: "ok",
    windows: [
      { kind: "session", label: "5h", percent: 31, resets_at: null },
      { kind: "weekly", label: "week", percent: 11, resets_at: null },
    ],
    ...overrides,
  };
}

describe("usableProviders", () => {
  it("keeps only providers that resolved windows", () => {
    const limits: PlanLimits = {
      fetched_at: 0,
      providers: [
        provider(),
        // Authenticated but tier-gated (consumer Antigravity) — must not render.
        provider({ id: "antigravity", state: "unsupported", windows: [] }),
        provider({ id: "codex", state: "signed-out", windows: [] }),
        provider({ id: "cursor", state: "error", windows: [] }),
      ],
    };
    expect(usableProviders(limits).map((p) => p.id)).toEqual(["claude"]);
  });

  it("keeps stale rows, which carry a real last-known reading", () => {
    // Antigravity quota is only readable while an agy process lives. Dropping
    // the replayed row would make the pill appear and vanish as sub-agents
    // start and stop, which is worse than showing a labelled stale number.
    const limits: PlanLimits = {
      fetched_at: 0,
      providers: [
        provider({ id: "antigravity", state: "stale", as_of: "2026-09-06T18:00:00Z" }),
        provider({ id: "empty-stale", state: "stale", windows: [] }),
      ],
    };
    expect(usableProviders(limits).map((p) => p.id)).toEqual(["antigravity"]);
  });

  it("keeps rate-limited error providers even with no windows", () => {
    const limits: PlanLimits = {
      fetched_at: 0,
      providers: [
        provider({ id: "claude", state: "error", reason: "rate_limited", windows: [] }),
      ],
    };
    expect(usableProviders(limits).map((p) => p.id)).toEqual(["claude"]);
  });

  it("drops error providers with reasons other than rate_limited", () => {
    const limits: PlanLimits = {
      fetched_at: 0,
      providers: [
        provider({ id: "claude", state: "error", reason: "network_error", windows: [] }),
      ],
    };
    expect(usableProviders(limits)).toEqual([]);
  });

  it("drops an ok provider that reported no windows", () => {
    const limits: PlanLimits = { fetched_at: 0, providers: [provider({ windows: [] })] };
    expect(usableProviders(limits)).toEqual([]);
  });

  it("returns nothing when the endpoint was unreachable", () => {
    expect(usableProviders(null)).toEqual([]);
  });
});

describe("tightestWindow", () => {
  it("leads with the fullest window, not the first", () => {
    // The binding constraint is whichever window is closest to its cap; a
    // nearly-exhausted weekly must win over a fresh session window.
    const p = provider({
      windows: [
        { kind: "session", label: "5h", percent: 12, resets_at: null },
        { kind: "weekly", label: "week", percent: 87, resets_at: null },
      ],
    });
    expect(tightestWindow(p)?.kind).toBe("weekly");
  });

  it("keeps the earlier window when percentages tie", () => {
    const p = provider({
      windows: [
        { kind: "session", label: "5h", percent: 50, resets_at: null },
        { kind: "weekly", label: "week", percent: 50, resets_at: null },
      ],
    });
    expect(tightestWindow(p)?.kind).toBe("session");
  });

  it("returns null for an empty window list", () => {
    expect(tightestWindow(provider({ windows: [] }))).toBeNull();
  });
});

describe("formatResetAt", () => {
  it("formats ISO-8601 strings", () => {
    const res = formatResetAt("2026-09-14T12:00:00Z");
    expect(res).toBeTruthy();
  });

  it("formats epoch seconds numbers", () => {
    const res = formatResetAt(1789387200);
    expect(res).toBeTruthy();
  });

  it("returns null for null, undefined, or empty", () => {
    expect(formatResetAt(null)).toBeNull();
    expect(formatResetAt(undefined)).toBeNull();
    expect(formatResetAt("")).toBeNull();
    expect(formatResetAt("not-a-date")).toBeNull();
  });
});
