import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { FriendlyResponse } from "./FriendlyResponse";

afterEach(cleanup);

describe("FriendlyResponse", () => {
  const summary = { text: "Consertei o vazamento e os testes passaram.", lang: "pt-BR" };

  it("shows the rewrite by default and keeps the original hidden", () => {
    render(
      <FriendlyResponse summary={summary} id="resp_1">
        <div>ORIGINAL WITH `code`</div>
      </FriendlyResponse>,
    );

    expect(screen.getByTestId("friendly-response-text").textContent).toBe(summary.text);
    expect(screen.queryByTestId("friendly-response-original")).toBeNull();
  });

  it("reveals the original on click and hides it again", () => {
    render(
      <FriendlyResponse summary={summary} id="resp_1">
        <div>ORIGINAL WITH `code`</div>
      </FriendlyResponse>,
    );

    const toggle = screen.getByTestId("friendly-response-toggle");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(toggle);
    // The original must always be reachable: the rewrite drops code on purpose.
    expect(screen.getByTestId("friendly-response-original").textContent).toContain(
      "ORIGINAL WITH `code`",
    );
    expect(toggle.getAttribute("aria-expanded")).toBe("true");

    fireEvent.click(toggle);
    expect(screen.queryByTestId("friendly-response-original")).toBeNull();
  });
});
