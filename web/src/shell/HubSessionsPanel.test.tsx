import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HubSessionsPanel } from "./HubSessionsPanel";
import {
  addHubSession,
  listHubCandidates,
  listHubSessions,
  removeHubSession,
  renameHubSession,
  setHubSessionModel,
  type HubSessionRow,
} from "@/lib/hubApi";

vi.mock("@/lib/hubApi", () => ({
  addHubSession: vi.fn(),
  listHubCandidates: vi.fn(),
  listHubSessions: vi.fn(),
  removeHubSession: vi.fn(),
  renameHubSession: vi.fn(),
  setHubSessionModel: vi.fn(),
}));
vi.mock("./SwitchAgentDialog", () => ({
  SwitchAgentDialog: ({ open, sessionId }: { open: boolean; sessionId: string }) =>
    open ? <div data-testid={`switch-dialog-${sessionId}`} /> : null,
}));

const listMock = vi.mocked(listHubSessions);
const setMock = vi.mocked(setHubSessionModel);
const addMock = vi.mocked(addHubSession);
const candidatesMock = vi.mocked(listHubCandidates);
const removeMock = vi.mocked(removeHubSession);
const renameMock = vi.mocked(renameHubSession);

const CLAUDE: HubSessionRow = {
  id: "s_claude",
  name: "amarras",
  title: "Project Analysis",
  nickname: "amarras",
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
  title: "nessie",
  nickname: null,
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

function openMenu(sessionId: string): void {
  const trigger = screen.getByTestId(`hub-session-menu-${sessionId}`);
  fireEvent.pointerDown(trigger, new MouseEvent("pointerdown", { bubbles: true, button: 0 }));
}

beforeEach(() => {
  for (const mock of [listMock, setMock, addMock, candidatesMock, removeMock, renameMock]) {
    mock.mockReset();
  }
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
    expect(await screen.findByText(/No sessions in the Hub/)).toBeTruthy();
    expect(screen.getByText("0 sessions in the Hub")).toBeTruthy();
  });

  it("shows a nicknamed session's own title under its name", async () => {
    renderPanel();
    await screen.findByTestId("hub-session-s_claude");
    expect(screen.getByTestId("hub-session-title-s_claude").textContent).toBe("Project Analysis");
    expect(screen.queryByTestId("hub-session-title-s_agent")).toBeNull();
  });

  it("adds one of their other sessions", async () => {
    candidatesMock.mockResolvedValue([
      { id: "s_sol", title: "GPT-6.1 Sol Missing", updated_at: 0, provider: "Codex" },
    ]);
    addMock.mockResolvedValue("Added GPT-6.1 Sol Missing to the hub.");
    renderPanel();
    fireEvent.click(await screen.findByTestId("hub-add-session"));
    fireEvent.click(await screen.findByTestId("hub-candidate-s_sol"));
    await waitFor(() => expect(addMock).toHaveBeenCalledWith("s_sol"));
    await waitFor(() => expect(listMock.mock.calls.length).toBeGreaterThan(1));
  });

  it("takes a session out of the hub", async () => {
    removeMock.mockResolvedValue("Took nessie out of the hub; the session itself is untouched.");
    renderPanel();
    await screen.findByTestId("hub-session-s_agent");
    listMock.mockResolvedValue([CLAUDE]);
    openMenu("s_agent");
    fireEvent.click(await screen.findByRole("menuitem", { name: "Remove from Hub" }));
    await waitFor(() => expect(removeMock).toHaveBeenCalledWith("s_agent"));
    await waitFor(() => expect(screen.queryByTestId("hub-session-s_agent")).toBeNull());
  });

  it("renames a session and says what the hub said", async () => {
    renameMock.mockResolvedValue("Renamed nessie to agente.");
    renderPanel();
    await screen.findByTestId("hub-session-s_agent");
    openMenu("s_agent");
    fireEvent.click(await screen.findByRole("menuitem", { name: "Rename" }));
    const field = await screen.findByTestId("hub-session-rename-s_agent");
    fireEvent.change(field, { target: { value: "  agente " } });
    fireEvent.submit(field.closest("form")!);
    await waitFor(() => expect(renameMock).toHaveBeenCalledWith("s_agent", "agente"));
    expect(await screen.findByText("Renamed nessie to agente.")).toBeTruthy();
    expect(screen.queryByTestId("hub-session-rename-s_agent")).toBeNull();
  });

  it("keeps the name field open when the name is taken", async () => {
    renameMock.mockRejectedValue(new Error("amarras is already called that"));
    renderPanel();
    await screen.findByTestId("hub-session-s_agent");
    openMenu("s_agent");
    fireEvent.click(await screen.findByRole("menuitem", { name: "Rename" }));
    const field = await screen.findByTestId("hub-session-rename-s_agent");
    fireEvent.change(field, { target: { value: "Amarras" } });
    fireEvent.submit(field.closest("form")!);
    expect(
      await screen.findByText("Couldn't rename it: amarras is already called that"),
    ).toBeTruthy();
    expect(screen.getByTestId("hub-session-rename-s_agent")).toBeTruthy();
  });
});
