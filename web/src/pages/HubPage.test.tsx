import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({ send: vi.fn(), start: vi.fn(), stop: vi.fn() }));

vi.mock("@/lib/voiceHub", async () => {
  const { create } = await import("zustand");
  const useVoiceHubStore = create(() => ({
    active: false,
    connecting: false,
    start: h.start,
    stop: h.stop,
  }));
  return { useVoiceHubStore };
});

import { useHubStore, type HubEntry } from "@/lib/hubApi";
import { HubPage } from "@/pages/HubPage";

function entry(over: Partial<HubEntry>): HubEntry {
  return {
    id: 1,
    at: 0,
    role: "hub",
    kind: "message",
    text: "",
    via: null,
    session_id: null,
    session_name: null,
    detail: null,
    ...over,
  };
}

function renderHub() {
  return render(
    <MemoryRouter>
      <HubPage />
    </MemoryRouter>,
  );
}

describe("HubPage", () => {
  beforeEach(() => {
    h.send.mockReset().mockResolvedValue(undefined);
    useHubStore.setState({
      entries: [
        entry({ id: 1, role: "you", text: "manda a amarras rodar os testes" }),
        entry({
          id: 2,
          kind: "sent",
          text: "Mandei pra amarras.",
          session_id: "s1",
          session_name: "amarras",
          detail: "Run the tests.",
        }),
        entry({
          id: 3,
          kind: "finished",
          text: "amarras finished: all tests pass.",
          session_id: "s1",
          session_name: "amarras",
          detail: "All 40 tests pass.",
        }),
      ],
      sessions: [
        {
          id: "s1",
          name: "amarras",
          title: "Project Analysis",
          nickname: "amarras",
          handle: "s1",
          status: "running",
          working: true,
          now: "Bash: pytest",
          updated_at: 0,
          pending_approvals: 0,
        },
      ],
      lastId: 3,
      loaded: true,
      error: null,
      sending: false,
      load: vi.fn().mockResolvedValue(undefined),
      follow: vi.fn(() => () => {}),
      send: h.send,
    });
  });

  it("shows the chat, the sessions, and links each report to its session", () => {
    renderHub();
    expect(screen.getByText("manda a amarras rodar os testes")).toBeTruthy();
    expect(screen.getByTestId("hub-entry-sent").textContent).toContain("Mandei pra amarras.");
    expect(screen.getByTestId("hub-entry-finished").textContent).toContain("all tests pass");
    const strip = screen.getByTestId("hub-sessions");
    expect(strip.textContent).toContain("amarras");
    expect(strip.querySelector("a")?.getAttribute("href")).toBe("/c/s1");
    // The full reply is one click away, not in the way.
    expect(screen.queryByText("All 40 tests pass.")).toBeNull();
    fireEvent.click(screen.getByText("Claude's full reply"));
    expect(screen.getByText("All 40 tests pass.")).toBeTruthy();
  });

  it("sends on Enter and clears the box", () => {
    renderHub();
    const input = screen.getByTestId("hub-input") as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "o que a brasa tá fazendo?" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(h.send).toHaveBeenCalledWith("o que a brasa tá fazendo?");
    expect(input.value).toBe("");
  });

  it("says it is working on a message it has not answered", () => {
    useHubStore.setState({ entries: [entry({ id: 9, role: "you", text: "oi" })] });
    renderHub();
    expect(screen.getByTestId("hub-thinking")).toBeTruthy();
  });

  it("the talk button starts the voice hub", () => {
    renderHub();
    fireEvent.click(screen.getByTestId("hub-talk"));
    expect(h.start).toHaveBeenCalled();
  });
});
