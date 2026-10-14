// Beside a project's chip, the hub shows where it put a message for a busy
// lane; they can say where it belonged instead.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FilePathAwareMessageResponse } from "./ChatMarkdown";

const correctHubPlace = vi.fn();
vi.mock("@/lib/hubApi", () => ({
  correctHubPlace: (...args: unknown[]) => correctHubPlace(...args),
}));

afterEach(() => {
  cleanup();
  correctHubPlace.mockReset();
  window.localStorage.clear();
});

function openMenu(): void {
  const trigger = screen.getByTestId("hub-place-control");
  fireEvent.pointerDown(trigger, new MouseEvent("pointerdown", { bubbles: true, button: 0 }));
  fireEvent.click(trigger);
}

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

const HELD =
  '[RAL](http://localhost:6767/c/4a18 "omnigent-project") ' +
  '[after it finishes](http://localhost:6767/hub/placements/ab12?picked=after "omnigent-hub-place")' +
  "\n\nVai quando terminar.";

describe("where the hub put a message for a busy lane", () => {
  it("shows the place beside the chip, not as a link", () => {
    renderMarkdown(HELD);

    expect(screen.getByTestId("hub-project-chip")).toHaveTextContent("RAL");
    expect(screen.getByTestId("hub-place-control")).toHaveTextContent("After it finishes");
    expect(screen.queryByRole("link", { name: /after it finishes/i })).toBeNull();
  });

  it("tells the hub where it belonged and shows what the hub did", async () => {
    correctHubPlace.mockResolvedValue({ right: "steer", text: "Sent it to RAL now." });
    renderMarkdown(HELD);

    openMenu();
    fireEvent.click(await screen.findByText("Into its work now"));

    expect(correctHubPlace).toHaveBeenCalledWith("ab12", "steer");
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("Sent it to RAL now."),
    );
    expect(screen.getByTestId("hub-place-control")).toHaveTextContent("Moved: into its work now");
  });

  it("thanks them when the hub had it right", async () => {
    correctHubPlace.mockResolvedValue({ right: "after", text: "Noted." });
    renderMarkdown(HELD);

    openMenu();
    fireEvent.click(await screen.findByRole("menuitem", { name: /after it finishes/i }));

    expect(correctHubPlace).toHaveBeenCalledWith("ab12", "after");
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Thanks, noted."));
  });

  it("asks with the three places when the hub could not tell, its guess marked", async () => {
    correctHubPlace.mockResolvedValue({ right: "new", text: "New lane: joystick plan." });
    renderMarkdown(
      '[RAL](http://localhost:6767/c/4a18 "omnigent-project") ' +
        "[where should this go?](http://localhost:6767/hub/placements/cd34?picked=ask&guess=steer" +
        ' "omnigent-hub-place")\n\nOnde vai?',
    );

    const ask = screen.getByTestId("hub-place-ask");
    expect(ask).toHaveTextContent("Where should this go?");
    expect(screen.getByRole("button", { name: /into its work now/i })).toHaveTextContent(
      "(suggested)",
    );
    fireEvent.click(screen.getByRole("button", { name: /a new lane/i }));

    expect(correctHubPlace).toHaveBeenCalledWith("cd34", "new");
    await waitFor(() =>
      expect(screen.getByTestId("hub-place-control")).toHaveTextContent("A new lane"),
    );
    expect(screen.queryByTestId("hub-place-ask")).toBeNull();
  });
});
