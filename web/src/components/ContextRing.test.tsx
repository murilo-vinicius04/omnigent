// The ring is where a compaction becomes visible. The reader watched one
// happen with nothing on screen saying so, then read the pre-compaction
// percentage beside it and concluded it had failed — these cover both halves:
// the state the server publishes, and the threshold they can move.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ContextRing } from "./ContextRing";
import { STATE_LABEL, THRESHOLD_LABEL } from "@/lib/autoCompact";

const labels = vi.hoisted(() => ({ current: {} as Record<string, string> }));

vi.mock("@/hooks/useSessionLabels", () => ({
  useSessionLabels: () => labels.current,
}));

vi.mock("@/lib/sessionsApi", () => ({
  updateSession: vi.fn(async () => ({}) as never),
}));

import { updateSession } from "@/lib/sessionsApi";

const updateSessionMock = vi.mocked(updateSession);

beforeEach(() => {
  labels.current = {};
  updateSessionMock.mockReset();
  updateSessionMock.mockResolvedValue({} as never);
});

afterEach(cleanup);

/** Render the ring and open its popover. */
function open(conversationId = "conv_a"): void {
  render(
    <ContextRing contextWindow={100_000} tokensUsed={25_000} conversationId={conversationId} />,
  );
  fireEvent.click(screen.getByTestId("context-ring-trigger"));
}

/** Type a threshold and commit it the way a reader would. */
function setThreshold(value: string, commit: "enter" | "blur" = "enter"): void {
  const input = screen.getByTestId("autocompact-pct");
  fireEvent.change(input, { target: { value } });
  if (commit === "enter") fireEvent.keyDown(input, { key: "Enter" });
  else fireEvent.blur(input);
}

describe("the context ring", () => {
  it("still reports the used percentage", () => {
    render(<ContextRing contextWindow={100_000} tokensUsed={25_000} conversationId="conv_a" />);
    expect(screen.getByLabelText("25% of context used")).toBeInTheDocument();
  });

  it("explains that a compacted session still shows its old size", async () => {
    labels.current = { [STATE_LABEL]: "compacted" };
    render(<ContextRing contextWindow={100_000} tokensUsed={81_000} conversationId="conv_a" />);
    fireEvent.click(screen.getByTestId("context-ring-trigger"));
    expect(await screen.findByTestId("context-ring-state")).toHaveTextContent(
      /updates when the next turn ends/,
    );
  });

  it("says when the notes are being written", async () => {
    labels.current = { [STATE_LABEL]: "writing-notes" };
    open();
    expect(await screen.findByTestId("context-ring-state")).toHaveTextContent(
      /Writing the context down/,
    );
  });

  it("shows the server's default threshold when the session has not chosen one", async () => {
    open();
    expect(await screen.findByTestId("autocompact-pct")).toHaveValue(60);
  });

  it("shows the threshold this session chose", async () => {
    labels.current = { [THRESHOLD_LABEL]: "40" };
    open();
    expect(await screen.findByTestId("autocompact-pct")).toHaveValue(40);
  });

  it("saves a new threshold to the session's label", async () => {
    open();
    await screen.findByTestId("autocompact-pct");
    setThreshold("45");
    await waitFor(() => {
      expect(updateSessionMock).toHaveBeenCalledWith("conv_a", {
        labels: { [THRESHOLD_LABEL]: "45" },
        silent: true,
      });
    });
  });

  it("saves on blur too, so a click away is not lost", async () => {
    open();
    await screen.findByTestId("autocompact-pct");
    setThreshold("45", "blur");
    await waitFor(() => expect(updateSessionMock).toHaveBeenCalled());
  });

  it("clamps a threshold the server would refuse", async () => {
    open();
    await screen.findByTestId("autocompact-pct");
    setThreshold("400");
    await waitFor(() => {
      expect(updateSessionMock).toHaveBeenCalledWith("conv_a", {
        labels: { [THRESHOLD_LABEL]: "95" },
        silent: true,
      });
    });
  });

  it("leaves the threshold alone when it did not change", async () => {
    open();
    await screen.findByTestId("autocompact-pct");
    setThreshold("60");
    expect(updateSessionMock).not.toHaveBeenCalled();
  });

  it("says so when the threshold could not be saved, and shows what is really in effect", async () => {
    updateSessionMock.mockRejectedValueOnce(new Error("offline"));
    open();
    await screen.findByTestId("autocompact-pct");
    setThreshold("45");
    expect(await screen.findByTestId("autocompact-pct-error")).toHaveTextContent("offline");
    await waitFor(() => expect(screen.getByTestId("autocompact-pct")).toHaveValue(60));
  });
});
