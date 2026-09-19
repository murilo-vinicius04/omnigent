import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ProviderTokenCards } from "./ProviderTokenCards";
import type { ProviderTokenUsage } from "@/lib/tokenUsageApi";

afterEach(() => cleanup());

function provider(overrides: Partial<ProviderTokenUsage> = {}): ProviderTokenUsage {
  return {
    id: "claude",
    label: "Claude",
    tokens: 1_250_000,
    inputTokens: 200_000,
    outputTokens: 50_000,
    cachedTokens: 1_000_000,
    costUsd: 4.5,
    calls: 12,
    days: [],
    models: [],
    ...overrides,
  };
}

describe("ProviderTokenCards", () => {
  it("prints the numbers, not just a colour", () => {
    // The palette is only legible for everyone because the figures are in
    // text beside it — these are the labels the colour check requires.
    render(<ProviderTokenCards providers={[provider()]} />);
    const card = screen.getByTestId("provider-tokens-claude");

    expect(within(card).getByText("Claude")).toBeInTheDocument();
    expect(within(card).getByText("1.25M")).toBeInTheDocument();
    expect(within(card).getByText(/12 calls/)).toBeInTheDocument();
    expect(within(card).getByText(/200k in · 50k out · 1M cached/)).toBeInTheDocument();
  });

  it("names the busiest model and counts the rest", () => {
    render(
      <ProviderTokenCards
        providers={[
          provider({
            models: [
              {
                model: "claude-opus-5",
                tokens: 9,
                inputTokens: 0,
                outputTokens: 0,
                cachedTokens: 0,
                costUsd: 0,
                calls: 1,
              },
              {
                model: "claude-sonnet-4-6",
                tokens: 1,
                inputTokens: 0,
                outputTokens: 0,
                cachedTokens: 0,
                costUsd: 0,
                calls: 1,
              },
            ],
          }),
        ]}
      />,
    );
    const card = screen.getByTestId("provider-tokens-claude");
    expect(within(card).getByText(/claude-opus-5/)).toBeInTheDocument();
    expect(within(card).getByText(/\+1/)).toBeInTheDocument();
  });

  it("uses singular for a single call and hides an unpriced cost", () => {
    render(<ProviderTokenCards providers={[provider({ calls: 1, costUsd: 0 })]} />);
    const card = screen.getByTestId("provider-tokens-claude");
    expect(within(card).getByText("1 call")).toBeInTheDocument();
    expect(within(card).queryByText(/\$/)).toBeNull();
  });

  it("explains an empty host rather than showing a row of zeros", () => {
    render(<ProviderTokenCards providers={[]} />);
    expect(screen.getByText(/No token usage recorded yet/)).toBeInTheDocument();
  });
});
