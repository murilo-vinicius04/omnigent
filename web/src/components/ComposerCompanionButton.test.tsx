// Tests for ComposerCompanionButton — the panel for talking to the
// session's companion without interrupting Claude.
//
// The companion API is mocked: the real path spawns an `agy` subprocess
// server-side. What matters here is the panel's contract with the reader —
// that it shows the ledger (the whole claim of the feature is that what it
// knows is visible), that opening it warms the process, and that a failure
// says so instead of looking like an empty answer.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ComposerCompanionButton } from "./ComposerCompanionButton";

const mocks = vi.hoisted(() => ({
  getCompanionState: vi.fn(),
  prewarmCompanion: vi.fn(),
  askCompanion: vi.fn(),
}));

vi.mock("@/lib/companionApi", () => ({
  getCompanionState: mocks.getCompanionState,
  prewarmCompanion: mocks.prewarmCompanion,
  askCompanion: mocks.askCompanion,
}));

vi.mock("@/store/chatStore", () => ({
  useChatStore: (selector: (s: { conversationId: string | null }) => unknown) =>
    selector({ conversationId: "conv_abc" }),
}));

function state(overrides: Record<string, unknown> = {}) {
  return {
    sessionId: "conv_abc",
    model: "gemini-3.8-flash-low",
    running: false,
    idleS: 0,
    warmSince: null,
    pendingNotes: 0,
    context: [],
    ...overrides,
  };
}

beforeEach(() => {
  mocks.getCompanionState.mockReset().mockResolvedValue(state());
  mocks.prewarmCompanion.mockReset().mockResolvedValue(state({ running: true, warmSince: 1 }));
  mocks.askCompanion.mockReset();
});

afterEach(cleanup);

async function open() {
  render(<ComposerCompanionButton />);
  fireEvent.click(screen.getByTestId("composer-companion"));
  await waitFor(() => expect(mocks.getCompanionState).toHaveBeenCalledWith("conv_abc"));
}

describe("ComposerCompanionButton", () => {
  it("shows what the companion knows when opened", async () => {
    mocks.getCompanionState.mockResolvedValue(
      state({
        context: [
          { id: 1, kind: "activity", text: "they asked Claude: fix the parser", at: 1 },
          { id: 2, kind: "summary", text: "I fixed the parser bug.", at: 2 },
        ],
      }),
    );
    await open();
    expect(await screen.findByText("I fixed the parser bug.")).toBeInTheDocument();
    expect(screen.getByText("Claude said")).toBeInTheDocument();
    expect(screen.getByText("Claude is")).toBeInTheDocument();
  });

  it("warms the process on open so the first question is not a cold one", async () => {
    await open();
    await waitFor(() => expect(mocks.prewarmCompanion).toHaveBeenCalledWith("conv_abc"));
  });

  it("says so plainly when it has heard nothing yet", async () => {
    await open();
    expect(await screen.findByText(/has not heard anything yet/i)).toBeInTheDocument();
  });

  it("asks, then renders the answer from the returned ledger", async () => {
    mocks.askCompanion.mockResolvedValue({
      answer: "It is running the tests.",
      state: state({
        context: [
          { id: 1, kind: "question", text: "what's going on?", at: 3 },
          { id: 2, kind: "answer", text: "It is running the tests.", at: 4 },
        ],
      }),
    });
    await open();
    fireEvent.change(screen.getByTestId("companion-input"), {
      target: { value: "what's going on?" },
    });
    fireEvent.keyDown(screen.getByTestId("companion-input"), { key: "Enter" });
    expect(await screen.findByText("It is running the tests.")).toBeInTheDocument();
    expect(mocks.askCompanion).toHaveBeenCalledWith("conv_abc", "what's going on?");
  });

  it("surfaces a failure instead of looking like an empty answer", async () => {
    mocks.askCompanion.mockRejectedValue(new Error("no answer within 30s"));
    await open();
    fireEvent.change(screen.getByTestId("companion-input"), { target: { value: "hi" } });
    fireEvent.keyDown(screen.getByTestId("companion-input"), { key: "Enter" });
    expect(await screen.findByRole("alert")).toHaveTextContent("no answer within 30s");
  });

  it("dims the notes the process has not been told yet", async () => {
    mocks.getCompanionState.mockResolvedValue(
      state({
        pendingNotes: 1,
        context: [
          { id: 1, kind: "summary", text: "delivered already", at: 1 },
          { id: 2, kind: "activity", text: "not yet delivered", at: 2 },
        ],
      }),
    );
    await open();
    const pending = (await screen.findByText("not yet delivered")).closest("li");
    const delivered = screen.getByText("delivered already").closest("li");
    expect(pending?.className).toContain("opacity-60");
    expect(delivered?.className).not.toContain("opacity-60");
  });
});
