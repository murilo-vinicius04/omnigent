import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { SharedSessionNotificationsControl } from "./SharedSessionNotificationsControl";
import { readNotifySharedSessions } from "@/lib/sharedSessionNotifications";

beforeEach(() => {
  window.localStorage.clear();
});

describe("SharedSessionNotificationsControl", () => {
  it("renders off by default and persists the opt-in", () => {
    render(<SharedSessionNotificationsControl />);
    const toggle = screen.getByTestId("shared-sessions-notify-toggle");
    expect(toggle).toHaveAttribute("aria-checked", "false");

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(readNotifySharedSessions()).toBe(true);

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(readNotifySharedSessions()).toBe(false);
  });

  it("seeds from a stored opt-in", () => {
    window.localStorage.setItem("omnigent:notify-shared-sessions", "1");
    render(<SharedSessionNotificationsControl />);
    expect(screen.getByTestId("shared-sessions-notify-toggle")).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });
});
