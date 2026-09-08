import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { TranslatedMessageToggle } from "./TranslatedMessageToggle";

afterEach(cleanup);

describe("TranslatedMessageToggle", () => {
  it("keeps the English hidden until asked, then reveals it", () => {
    render(<TranslatedMessageToggle translated="Show me what I said." />);

    expect(screen.queryByTestId("translated-message-text")).toBeNull();

    const toggle = screen.getByTestId("translated-message-toggle");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(toggle);
    expect(screen.getByTestId("translated-message-text").textContent).toBe("Show me what I said.");

    fireEvent.click(toggle);
    expect(screen.queryByTestId("translated-message-text")).toBeNull();
  });
});
