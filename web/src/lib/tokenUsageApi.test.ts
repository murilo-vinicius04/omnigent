import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchTokenUsage, formatTokens, providerColor } from "./tokenUsageApi";

const authenticatedFetch = vi.hoisted(() => vi.fn());
vi.mock("@/lib/identity", () => ({ authenticatedFetch }));

afterEach(() => {
  authenticatedFetch.mockReset();
});

function respond(body: unknown, ok = true): void {
  authenticatedFetch.mockResolvedValue({
    ok,
    status: ok ? 200 : 500,
    json: async () => body,
  });
}

describe("formatTokens", () => {
  it("compacts counts the way the server's tooltip does", () => {
    // The tray tooltip and these charts read the same numbers; a different
    // rounding rule here would make them look like different measurements.
    expect(formatTokens(0)).toBe("0");
    expect(formatTokens(999)).toBe("999");
    expect(formatTokens(1_500)).toBe("1.5k");
    expect(formatTokens(12_000)).toBe("12k");
    expect(formatTokens(6_754_422)).toBe("6.75M");
    expect(formatTokens(2_000_000)).toBe("2M");
  });

  it("treats a missing or negative count as zero", () => {
    expect(formatTokens(-5)).toBe("0");
    expect(formatTokens(Number.NaN)).toBe("0");
  });
});

describe("providerColor", () => {
  it("keys colour on the vendor, so a filtered chart never repaints", () => {
    expect(providerColor("claude")).toBe("var(--provider-claude)");
    expect(providerColor("grok")).toBe("var(--provider-grok)");
  });

  it("falls back to the catch-all hue for a vendor it has never seen", () => {
    expect(providerColor("brand-new-vendor")).toBe("var(--provider-other)");
  });
});

describe("fetchTokenUsage", () => {
  it("passes the day window through and camelizes the response", async () => {
    respond({
      since: "2026-09-14",
      until: null,
      providers: [
        {
          id: "claude",
          label: "Claude",
          tokens: 1_000,
          input_tokens: 400,
          output_tokens: 100,
          cached_tokens: 500,
          cost_usd: 1.5,
          calls: 2,
          days: [
            {
              day: "2026-09-14",
              tokens: 1_000,
              input_tokens: 400,
              output_tokens: 100,
              cached_tokens: 500,
              cost_usd: 1.5,
              calls: 2,
            },
          ],
          models: [
            {
              model: "claude-opus-5",
              tokens: 1_000,
              input_tokens: 400,
              output_tokens: 100,
              cached_tokens: 500,
              cost_usd: 1.5,
              calls: 2,
            },
          ],
        },
      ],
      limits: [
        {
          provider: "antigravity",
          label: "Gemini",
          windows: [
            {
              kind: "gemini-5h",
              label: "gemini-5h",
              points: [{ at: "2026-09-14T10:00:00Z", percent: 40 }],
            },
          ],
        },
      ],
      totals: {
        tokens: 1_000,
        input_tokens: 400,
        output_tokens: 100,
        cached_tokens: 500,
        cost_usd: 1.5,
        calls: 2,
      },
    });

    const report = await fetchTokenUsage({ since: "2026-09-14", until: null });

    expect(authenticatedFetch).toHaveBeenCalledWith("/v1/usage/tokens?since=2026-09-14");
    expect(report.providers[0].cachedTokens).toBe(500);
    expect(report.providers[0].days[0].day).toBe("2026-09-14");
    expect(report.providers[0].models[0].model).toBe("claude-opus-5");
    expect(report.limits[0].windows[0].points).toEqual([
      { at: "2026-09-14T10:00:00Z", percent: 40 },
    ]);
    expect(report.totals.costUsd).toBe(1.5);
  });

  it("asks for everything when no window is set", async () => {
    respond({ since: null, until: null, providers: [], limits: [], totals: {} });
    const report = await fetchTokenUsage({ since: null, until: null });

    expect(authenticatedFetch).toHaveBeenCalledWith("/v1/usage/tokens");
    // A server too old for the route, or a host that never recorded a turn,
    // both land here — an empty report, not a crash.
    expect(report.providers).toEqual([]);
    expect(report.totals.tokens).toBe(0);
  });

  it("throws on a failed response so the page can show its error state", async () => {
    respond({}, false);
    await expect(fetchTokenUsage({})).rejects.toThrow("Token usage fetch failed: 500");
  });
});
