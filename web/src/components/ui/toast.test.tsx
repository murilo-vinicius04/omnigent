// Tests for the shadcn Sonner renderer and the legacy showToast wrapper.

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { toast } from "sonner";
import { Toaster } from "./sonner";
import { showToast } from "./toast";

afterEach(async () => {
  act(() => toast.dismiss());
  cleanup();
  // Sonner schedules the toast removal TIME_BEFORE_UNMOUNT (200ms) after a
  // dismiss. Drain it here, while jsdom is still alive: if it fires after
  // the file's environment tears down, React's dispatch reads a dead
  // `window` and vitest reports an unhandled error.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 500));
  });
});

describe("Toaster", () => {
  it("renders nothing until a toast is shown", () => {
    render(<Toaster />);
    expect(document.querySelector("[data-sonner-toast]")).toBeNull();
  });

  it("shows compatibility content and dismisses on the close button", async () => {
    render(<Toaster />);
    act(() => showToast(<span>Hello there</span>, { duration: 0 }));

    const toastRoot = await screen.findByTestId("toast");
    expect(toastRoot).toHaveTextContent("Hello there");

    fireEvent.click(screen.getByRole("button", { name: "Close toast" }));
    await waitFor(() => expect(screen.queryByTestId("toast")).toBeNull());
  });

  it("renders Sonner descriptions and actions", async () => {
    render(<Toaster />);
    act(() => {
      const id = toast("Permission required", {
        description: "Choose whether to continue.",
        duration: Number.POSITIVE_INFINITY,
        action: {
          label: "Continue",
          onClick: () => toast.dismiss(id),
        },
      });
    });

    expect(await screen.findByText("Permission required")).toBeInTheDocument();
    expect(screen.getByText("Choose whether to continue.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(screen.queryByText("Permission required")).toBeNull());
  });
});
