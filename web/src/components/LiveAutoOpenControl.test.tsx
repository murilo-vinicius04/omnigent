import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { LiveAutoOpenControl } from "./LiveAutoOpenControl";
import { LIVE_AUTO_OPEN_KEY } from "@/lib/speechPlayback";

describe("LiveAutoOpenControl", () => {
  afterEach(cleanup);

  beforeEach(() => {
    window.localStorage.removeItem(LIVE_AUTO_OPEN_KEY);
  });

  afterEach(() => {
    window.localStorage.removeItem(LIVE_AUTO_OPEN_KEY);
  });

  it("renders on by default", () => {
    render(<LiveAutoOpenControl />);
    expect(screen.getByTestId("live-auto-open-toggle")).toHaveAttribute("aria-checked", "true");
  });

  it("toggling off persists the opt-out", () => {
    render(<LiveAutoOpenControl />);
    fireEvent.click(screen.getByTestId("live-auto-open-toggle"));
    expect(window.localStorage.getItem(LIVE_AUTO_OPEN_KEY)).toBe("false");
    expect(screen.getByTestId("live-auto-open-toggle")).toHaveAttribute("aria-checked", "false");
  });

  it("starts on when the opt-in was already stored", () => {
    window.localStorage.setItem(LIVE_AUTO_OPEN_KEY, "true");
    render(<LiveAutoOpenControl />);
    expect(screen.getByTestId("live-auto-open-toggle")).toHaveAttribute("aria-checked", "true");
  });
});
