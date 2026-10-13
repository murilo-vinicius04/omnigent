// The hub opens a message about a project with the project's name as a chip
// rather than saying "RAL finished:" in the text.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { FilePathAwareMessageResponse } from "./ChatMarkdown";

afterEach(cleanup);

function renderMarkdown(markdown: string): void {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <FilePathAwareMessageResponse>{markdown}</FilePathAwareMessageResponse>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("hub project chips", () => {
  it("draws a project-titled link as a chip that opens the project's chat in the app", () => {
    renderMarkdown(
      '[RAL](http://localhost:6767/c/4a1867870bb74b44b3fb98f1ddd42887 "omnigent-project")\n\n' +
        "Compilei o cuRobo.",
    );

    const chip = screen.getByTestId("hub-project-chip");
    expect(chip).toHaveTextContent("RAL");
    expect(chip).toHaveAttribute("href", "/c/4a1867870bb74b44b3fb98f1ddd42887");
    expect(chip).not.toHaveAttribute("title");
    expect(screen.getByText("Compilei o cuRobo.")).toBeInTheDocument();
  });

  it("leaves any other link a plain link", () => {
    renderMarkdown("[Open RAL ↗](http://localhost:6767/c/4a18)");

    expect(screen.queryByTestId("hub-project-chip")).toBeNull();
    expect(screen.getByRole("link", { name: "Open RAL ↗" })).toHaveAttribute(
      "href",
      "http://localhost:6767/c/4a18",
    );
  });
});
