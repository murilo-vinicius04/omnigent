import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProEquivalentCard } from "./ProEquivalentCard";
import { fetchPlanLimits, type PlanLimits } from "@/lib/planLimitsApi";
import type * as PlanLimitsApiModule from "@/lib/planLimitsApi";

vi.mock("@/lib/planLimitsApi", async (importOriginal) => {
  const actual = await importOriginal<typeof PlanLimitsApiModule>();
  return { ...actual, fetchPlanLimits: vi.fn() };
});

const fetchMock = vi.mocked(fetchPlanLimits);

function limitsWith(equivalent: unknown): PlanLimits {
  return {
    fetched_at: 0,
    providers: [
      {
        id: "claude",
        label: "Claude",
        state: "ok",
        windows: [],
        pro_equivalent: equivalent as PlanLimits["providers"][number]["pro_equivalent"],
      },
    ],
  };
}

afterEach(() => {
  cleanup();
  fetchMock.mockReset();
});

describe("ProEquivalentCard", () => {
  it("shows each window as a share of Pro, flagging one Pro could not hold", async () => {
    fetchMock.mockResolvedValue(
      limitsWith({
        multiplier: 5,
        windows: [
          {
            kind: "session",
            label: "5h",
            used_pct: 150,
            weighted_tokens_used: 8_250_000,
            weighted_token_budget: 5_500_000,
          },
          {
            kind: "weekly",
            label: "week",
            used_pct: 15,
            weighted_tokens_used: 6_000_000,
            weighted_token_budget: 40_000_000,
          },
        ],
      }),
    );
    render(<ProEquivalentCard />);
    expect(await screen.findByText("150% of Pro")).toBeTruthy();
    expect(screen.getByText("15% of Pro")).toBeTruthy();
    expect(screen.getByText(/Pro would have stopped here/)).toBeTruthy();
    expect(screen.getByText(/5× Pro/)).toBeTruthy();
  });

  it("renders nothing when the plan's multiple of Pro is unknown", async () => {
    fetchMock.mockResolvedValue(limitsWith(undefined));
    const { container } = render(<ProEquivalentCard />);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });
});
