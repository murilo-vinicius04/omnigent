import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HubChimeSettingsControl } from "./HubChimeSettingsControl";
import { useHubChimeStore } from "@/lib/hubChime";

describe("HubChimeSettingsControl", () => {
  afterEach(cleanup);

  beforeEach(() => {
    useHubChimeStore.setState({ enabled: false, volume: 0.4 });
  });

  it("renders with toggle off and hides volume controls when disabled", () => {
    render(<HubChimeSettingsControl />);
    const toggle = screen.getByTestId("hub-chime-settings-toggle");
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(screen.queryByTestId("hub-chime-volume-slider")).toBeNull();
    expect(screen.queryByTestId("hub-chime-test-sound")).toBeNull();
  });

  it("shows volume slider and test sound when enabled, and triggers test chime", () => {
    const playChimeSpy = vi.spyOn(useHubChimeStore.getState(), "playChime");
    render(<HubChimeSettingsControl />);

    const toggle = screen.getByTestId("hub-chime-settings-toggle");
    fireEvent.click(toggle);

    expect(useHubChimeStore.getState().enabled).toBe(true);
    expect(screen.getByTestId("hub-chime-volume-slider")).toBeInTheDocument();

    const testBtn = screen.getByTestId("hub-chime-test-sound");
    fireEvent.click(testBtn);
    expect(playChimeSpy).toHaveBeenCalledWith("preview");
  });

  it("adjusts volume through slider", () => {
    useHubChimeStore.setState({ enabled: true, volume: 0.4 });
    render(<HubChimeSettingsControl />);

    const slider = screen.getByTestId("hub-chime-volume-slider");
    fireEvent.change(slider, { target: { value: "0.75" } });
    expect(useHubChimeStore.getState().volume).toBe(0.75);
  });
});
