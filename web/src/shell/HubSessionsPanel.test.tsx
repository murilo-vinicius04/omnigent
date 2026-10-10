import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HubSessionsPanel } from "./HubSessionsPanel";
import { listHubSessions, setHubSessionModel, type HubSessionRow } from "@/lib/hubApi";

vi.mock("@/lib/hubApi", () => ({ listHubSessions: vi.fn(), setHubSessionModel: vi.fn() }));
vi.mock("./SwitchAgentDialog", () => ({
  SwitchAgentDialog: ({ open, sessionId }: { open: boolean; sessionId: string }) =>
    open ? <div data-testid={`switch-dialog-${sessionId}`} /> : null,
}));

const listMock = vi.mocked(listHubSessions);
const setMock = vi.mocked(setHubSessionModel);

const CLAUDE: HubSessionRow = {
  id: "s_claude",
  name: "amarras",
  title: "Project Analysis",
  status: "running",
  working: true,
  now: "Bash: pytest -q",
  updated_at: 0,
  pending_approvals: 0,
  run: {
    provider: "Claude Code",
    model: "claude-opus-5-5",
    model_label: "Opus 5.5",
    requested: "sonnet",
    requested_label: "Sonnet 5.5",
    effort: "xhigh",
    selected: "sonnet",
    models: [
      { id: "opus", label: "Opus 5.5", efforts: [] },
      { id: "sonnet", label: "Sonnet 5.5", efforts: [] },
    ],
    efforts: ["low", "medium", "high", "xhigh", "max"],
  },
};

const AGENT: HubSessionRow = {
  ...CLAUDE,
  id: "s_agent",
  name: "nessie",
  status: "idle",
  working: false,
  now: "",
  pending_approvals: 2,
  run: {
    provider: "nessie",
    model: "claude-sonnet-5-5",
    model_label: "",
    requested: "",
    requested_label: "",
    effort: "",
    selected: "",
    models: [],
    efforts: [],
  },
};

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <HubSessionsPanel />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function openSelect(testId: string): void {
  const trigger = screen.getByTestId(testId);
  fireEvent.pointerDown(trigger, new MouseEvent("pointerdown", { bubbles: true, button: 0 }));
  fireEvent.click(trigger);
}

beforeEach(() => {
  listMock.mockReset();
  setMock.mockReset();
  listMock.mockResolvedValue([CLAUDE, AGENT]);
});

afterEach(cleanup);

describe("HubSessionsPanel", () => {
  it("shows each session's state, provider, model and effort", async () => {
    renderPanel();
    const card = await screen.findByTestId("hub-session-s_claude");
    expect(within(card).getByText("amarras")).toBeTruthy();
    expect(within(card).getByText("Working · Bash: pytest -q")).toBeTruthy();
    expect(within(card).getByText("Claude Code")).toBeTruthy();
    expect(screen.getByTestId("hub-session-model-s_claude").textContent).toContain("Sonnet 5.5");
    expect(screen.getByTestId("hub-session-pending-s_claude").textContent).toBe(
      "Switching to Sonnet 5.5; it still reports Opus 5.5.",
    );
    expect(screen.getByTestId("hub-session-effort-s_claude").textContent).toContain("xhigh");
    expect(within(card).getByRole("link", { name: "Open amarras" }).getAttribute("href")).toBe(
      "/c/s_claude",
    );
  });

  it("shows a model it cannot change as plain text, and approvals it waits on", async () => {
    renderPanel();
    const card = await screen.findByTestId("hub-session-s_agent");
    expect(within(card).getByText("Waiting for 2 approvals")).toBeTruthy();
    expect(within(card).getByText("claude-sonnet-5-5")).toBeTruthy();
    expect(screen.queryByTestId("hub-session-model-s_agent")).toBeNull();
    expect(screen.queryByTestId("hub-session-effort-s_agent")).toBeNull();
  });

  it("sends a pick through the hub and shows what it said", async () => {
    setMock.mockResolvedValue({
      changed: true,
      text: "Switched amarras to max effort; it applies from its next message.",
    });
    renderPanel();
    await screen.findByTestId("hub-session-s_claude");
    openSelect("hub-session-effort-s_claude");
    fireEvent.click(await screen.findByRole("option", { name: "max" }));
    await waitFor(() => expect(setMock).toHaveBeenCalledWith("s_claude", { effort: "max" }));
    expect(
      await screen.findByText("Switched amarras to max effort; it applies from its next message."),
    ).toBeTruthy();
  });

  it("clears a model pick with Default", async () => {
    setMock.mockResolvedValue({ changed: true, text: "Switched amarras to its default model." });
    renderPanel();
    await screen.findByTestId("hub-session-s_claude");
    openSelect("hub-session-model-s_claude");
    fireEvent.click(await screen.findByRole("option", { name: "Default" }));
    await waitFor(() => expect(setMock).toHaveBeenCalledWith("s_claude", { model: "default" }));
  });

  it("opens the provider switch for an idle session only", async () => {
    renderPanel();
    const busy = await screen.findByTestId("hub-session-s_claude");
    expect(within(busy).getByRole("button", { name: "Switch…" })).toHaveProperty("disabled", true);
    const idle = screen.getByTestId("hub-session-s_agent");
    fireEvent.click(within(idle).getByRole("button", { name: "Switch…" }));
    expect(screen.getByTestId("switch-dialog-s_agent")).toBeTruthy();
  });

  it("says so when there are no sessions", async () => {
    listMock.mockResolvedValue([]);
    renderPanel();
    expect(await screen.findByText(/No sessions in the last week/)).toBeTruthy();
  });
});
