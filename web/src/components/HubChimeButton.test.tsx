import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HubChimeButton } from "./HubChimeButton";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useHubChimeStore } from "@/lib/hubChime";

describe("HubChimeButton", () => {
  afterEach(cleanup);

  beforeEach(() => {
    useHubChimeStore.setState({ enabled: false, volume: 0.4 });
  });

  it("renders with disabled chime state initially", () => {
    render(
      <TooltipProvider>
        <HubChimeButton />
      </TooltipProvider>,
    );
    const btn = screen.getByTestId("hub-chime-button");
    expect(btn).toHaveAttribute("aria-pressed", "false");
  });

  it("toggles enabled state when clicked", () => {
    const playChimeSpy = vi.spyOn(useHubChimeStore.getState(), "playChime");
    render(
      <TooltipProvider>
        <HubChimeButton />
      </TooltipProvider>,
    );
    const btn = screen.getByTestId("hub-chime-button");
    fireEvent.click(btn);
    expect(useHubChimeStore.getState().enabled).toBe(true);
    expect(btn).toHaveAttribute("aria-pressed", "true");
    expect(playChimeSpy).toHaveBeenCalledWith("preview");

    fireEvent.click(btn);
    expect(useHubChimeStore.getState().enabled).toBe(false);
    expect(btn).toHaveAttribute("aria-pressed", "false");
  });
});
