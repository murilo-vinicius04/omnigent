import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PlanLimitPills } from "./PlanLimitPills";
import { TooltipProvider } from "@/components/ui/tooltip";
import { fetchPlanLimits } from "@/lib/planLimitsApi";
import type * as PlanLimitsApiModule from "@/lib/planLimitsApi";

vi.mock("@/lib/planLimitsApi", async (importOriginal) => {
  const actual = await importOriginal<typeof PlanLimitsApiModule>();
  return {
    ...actual,
    fetchPlanLimits: vi.fn(),
  };
});

const fetchPlanLimitsMock = vi.mocked(fetchPlanLimits);

beforeEach(() => {
  fetchPlanLimitsMock.mockReset();
});

afterEach(cleanup);

describe("PlanLimitPills", () => {
  it("renders the stale Claude pill with reduced opacity and rate limit retry tooltip", async () => {
    fetchPlanLimitsMock.mockResolvedValueOnce({
      fetched_at: Date.now(),
      providers: [
        {
          id: "claude",
          label: "Claude",
          state: "stale",
          windows: [{ kind: "session", label: "5h", percent: 45, resets_at: null }],
          as_of: "2026-09-14T12:00:00Z",
        },
      ],
    });

    render(
      <TooltipProvider delayDuration={0}>
        <PlanLimitPills />
      </TooltipProvider>,
    );

    const pill = await screen.findByTestId("plan-limit-claude");
    expect(pill).toBeInTheDocument();
    expect(pill.getAttribute("data-stale")).toBe("true");
    expect(pill.className).toContain("opacity-50");
    expect(pill.textContent).toContain("45%");

    fireEvent.focus(pill);
    const bubble = await waitFor(() => {
      const els = document.querySelectorAll<HTMLElement>('[data-slot="tooltip-content"]');
      expect(els.length).toBeGreaterThan(0);
      return els[0];
    });
    expect(bubble.textContent).toContain("Anthropic rate limit, retrying");
  });

  it("renders a muted 'Claude —' pill with retry time tooltip on error with reason rate_limited", async () => {
    fetchPlanLimitsMock.mockResolvedValueOnce({
      fetched_at: Date.now(),
      providers: [
        {
          id: "claude",
          label: "Claude",
          state: "error",
          reason: "rate_limited",
          retry_at: 1789387240,
          windows: [],
        },
      ],
    });

    render(
      <TooltipProvider delayDuration={0}>
        <PlanLimitPills />
      </TooltipProvider>,
    );

    const pill = await screen.findByTestId("plan-limit-claude");
    expect(pill).toBeInTheDocument();
    expect(pill.textContent).toContain("Claude —");
    expect(pill.className).toContain("opacity-50");
    expect(pill.className).toContain("text-muted-foreground");

    fireEvent.focus(pill);
    const bubble = await waitFor(() => {
      const els = document.querySelectorAll<HTMLElement>('[data-slot="tooltip-content"]');
      expect(els.length).toBeGreaterThan(0);
      return els[0];
    });
    expect(bubble.textContent).toContain("Claude");
    expect(bubble.textContent).toContain("Anthropic rate limit, retrying");
  });

  it("renders nothing when provider has an error that is not rate_limited", async () => {
    fetchPlanLimitsMock.mockResolvedValueOnce({
      fetched_at: Date.now(),
      providers: [
        {
          id: "claude",
          label: "Claude",
          state: "error",
          reason: "network_error",
          windows: [],
        },
      ],
    });

    const { container } = render(
      <TooltipProvider delayDuration={0}>
        <PlanLimitPills />
      </TooltipProvider>,
    );

    await waitFor(() => {
      expect(fetchPlanLimitsMock).toHaveBeenCalled();
    });
    expect(container).toBeEmptyDOMElement();
  });

  it("renders Grok pill as a ring with 42% and details in tooltip", async () => {
    fetchPlanLimitsMock.mockResolvedValueOnce({
      fetched_at: Date.now(),
      providers: [
        {
          id: "grok",
          label: "Grok",
          state: "ok",
          windows: [
            {
              kind: "weekly",
              label: "week",
              percent: 42,
              resets_at: "2026-09-20T20:11:53Z",
            },
          ],
          details: [
            "Grok Chat 35%",
            "Grok Voice 7%",
            "Omnigent counted today: 456k tokens · 14 model calls",
          ],
        },
      ],
    });

    render(
      <TooltipProvider delayDuration={0}>
        <PlanLimitPills />
      </TooltipProvider>,
    );

    const pill = await screen.findByTestId("plan-limit-grok");
    expect(pill).toBeInTheDocument();
    expect(pill.textContent).toContain("42%");

    fireEvent.focus(pill);
    const bubble = await waitFor(() => {
      const els = document.querySelectorAll<HTMLElement>('[data-slot="tooltip-content"]');
      expect(els.length).toBeGreaterThan(0);
      return els[0];
    });
    expect(bubble.textContent).toContain("Grok");
    expect(bubble.textContent).toContain("week: 42% used");
    expect(bubble.textContent).toContain("Grok Chat 35%");
    expect(bubble.textContent).toContain("Grok Voice 7%");
    expect(bubble.textContent).toContain("Omnigent counted today: 456k tokens · 14 model calls");
  });
});
