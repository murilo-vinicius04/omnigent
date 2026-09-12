// Tests for the marker on a reply the companion wrote instead of Claude.
//
// The label is the point: the companion cannot see the code, so a reply of
// its that reads as Claude's is the UI misleading the reader. The button is
// the other half — the routing decision is a guess, and overruling it must
// not mean retyping.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CompanionAnswerNote } from "./CompanionAnswerNote";

const mocks = vi.hoisted(() => ({ send: vi.fn(() => Promise.resolve()) }));

let storeState = { send: mocks.send, boundAgentId: "agent_1" as string | null };

vi.mock("@/store/chatStore", () => ({
  useChatStore: (selector: (s: typeof storeState) => unknown) => selector(storeState),
}));

beforeEach(() => {
  mocks.send.mockClear();
  storeState = { send: mocks.send, boundAgentId: "agent_1" };
});

afterEach(cleanup);

describe("CompanionAnswerNote", () => {
  it("says who answered", () => {
    render(<CompanionAnswerNote asked="tudo bem?" />);
    expect(screen.getByText(/Answered by the companion/)).toBeInTheDocument();
    expect(screen.getByText(/Claude never saw this/)).toBeInTheDocument();
  });

  it("re-sends the original question, bypassing the routing", () => {
    render(<CompanionAnswerNote asked="tudo bem?" />);
    fireEvent.click(screen.getByRole("button", { name: /ask claude anyway/i }));
    // Without forceClaude the companion would simply answer it again.
    expect(mocks.send).toHaveBeenCalledWith("tudo bem?", "agent_1", undefined, {
      forceClaude: true,
    });
  });

  it("does not send the same question twice", () => {
    render(<CompanionAnswerNote asked="tudo bem?" />);
    const button = screen.getByRole("button", { name: /ask claude anyway/i });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(mocks.send).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: /sent to claude/i })).toBeDisabled();
  });

  it("offers no button when the original question was not recorded", () => {
    render(<CompanionAnswerNote asked="" />);
    expect(screen.queryByRole("button")).toBeNull();
    // The label still has to appear: who answered matters even when the
    // re-send affordance cannot.
    expect(screen.getByText(/Answered by the companion/)).toBeInTheDocument();
  });
});
