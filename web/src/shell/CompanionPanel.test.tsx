// Tests for the rail panel showing what the companion knows.
//
// The companion decides which messages Claude never sees, so "what is it
// deciding from?" has to be inspectable. This panel is that answer, and it
// is read-only on purpose — an input here would recreate the second chat
// this feature replaced.

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CompanionPanel } from "./CompanionPanel";

const mocks = vi.hoisted(() => ({ getCompanionState: vi.fn() }));

vi.mock("@/lib/companionApi", () => ({ getCompanionState: mocks.getCompanionState }));

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
  vi.useRealTimers();
  mocks.getCompanionState.mockReset().mockResolvedValue(state());
});

afterEach(cleanup);

describe("CompanionPanel", () => {
  it("lists the ledger in order with who said what", async () => {
    mocks.getCompanionState.mockResolvedValue(
      state({
        context: [
          { id: 1, kind: "activity", text: "fix the parser", at: 1 },
          { id: 2, kind: "summary", text: "I fixed it.", at: 2 },
        ],
      }),
    );
    render(<CompanionPanel conversationId="conv_abc" />);
    expect(await screen.findByText("I fixed it.")).toBeInTheDocument();
    expect(screen.getByText("You asked Claude")).toBeInTheDocument();
    expect(screen.getByText("Claude said")).toBeInTheDocument();
  });

  it("says plainly that it knows nothing yet", async () => {
    render(<CompanionPanel conversationId="conv_abc" />);
    expect(await screen.findByText(/Nothing yet/)).toBeInTheDocument();
  });

  it("has no input of its own — the composer is how you talk to it", async () => {
    render(<CompanionPanel conversationId="conv_abc" />);
    await waitFor(() => expect(mocks.getCompanionState).toHaveBeenCalled());
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("surfaces a read failure instead of showing an empty ledger", async () => {
    mocks.getCompanionState.mockRejectedValue(new Error("companion is offline"));
    render(<CompanionPanel conversationId="conv_abc" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("companion is offline");
  });

  it("dims context the process has not been told yet", async () => {
    mocks.getCompanionState.mockResolvedValue(
      state({
        pendingNotes: 1,
        context: [
          { id: 1, kind: "summary", text: "delivered", at: 1 },
          { id: 2, kind: "activity", text: "still pending", at: 2 },
        ],
      }),
    );
    render(<CompanionPanel conversationId="conv_abc" />);
    const pending = (await screen.findByText("still pending")).closest("li");
    expect(pending?.className).toContain("opacity-60");
    expect(screen.getByText("delivered").closest("li")?.className).not.toContain("opacity-60");
  });
});
