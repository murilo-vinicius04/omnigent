import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HubProjectsPanel } from "./HubProjectsPanel";
import type * as HubApiModule from "@/lib/hubApi";
import {
  addHubProjectChat,
  closeHubLane,
  createHubProject,
  deleteHubProject,
  listHubBudgets,
  listHubChats,
  listHubProjects,
  openHubLane,
  renameHubLane,
  renameHubProject,
  setHubProjectModel,
  type HubBudget,
  type HubProjectRow,
  type HubSessionRow,
} from "@/lib/hubApi";

vi.mock("@/lib/hubApi", async (importOriginal) => ({
  ...(await importOriginal<typeof HubApiModule>()),
  addHubProjectChat: vi.fn(),
  closeHubLane: vi.fn(),
  openHubLane: vi.fn(),
  renameHubLane: vi.fn(),
  createHubProject: vi.fn(),
  deleteHubProject: vi.fn(),
  listHubBudgets: vi.fn(),
  listHubChats: vi.fn(),
  listHubProjects: vi.fn(),
  renameHubProject: vi.fn(),
  setHubProjectModel: vi.fn(),
}));
vi.mock("./SwitchAgentDialog", () => ({
  SwitchAgentDialog: ({ open, sessionId }: { open: boolean; sessionId: string }) =>
    open ? <div data-testid={`switch-dialog-${sessionId}`} /> : null,
}));

const listMock = vi.mocked(listHubProjects);
const setMock = vi.mocked(setHubProjectModel);
const createMock = vi.mocked(createHubProject);
const chatsMock = vi.mocked(listHubChats);
const deleteMock = vi.mocked(deleteHubProject);
const renameMock = vi.mocked(renameHubProject);
const addMock = vi.mocked(addHubProjectChat);
const budgetsMock = vi.mocked(listHubBudgets);
const openLaneMock = vi.mocked(openHubLane);
const renameLaneMock = vi.mocked(renameHubLane);
const closeLaneMock = vi.mocked(closeHubLane);

const CLAUDE_CHAT: HubSessionRow = {
  id: "s_claude",
  name: "amarras",
  title: "Project Analysis",
  nickname: "amarras",
  status: "running",
  working: true,
  now: "Bash: pytest -q",
  updated_at: 0,
  pending_approvals: 0,
  project_id: "p_amarras",
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

/** An older chat of the same project, on another provider. */
const CODEX_CHAT: HubSessionRow = {
  ...CLAUDE_CHAT,
  id: "s_codex",
  name: "amarras · joystick",
  lane: "joystick",
  title: "Joystick plan",
  nickname: "amarras · joystick",
  status: "idle",
  working: false,
  now: "",
  run: {
    provider: "Codex",
    model: "gpt-6-luna",
    model_label: "GPT-6 Luna",
    requested: "",
    requested_label: "",
    effort: "high",
    selected: "gpt-6-luna",
    models: [{ id: "gpt-6-luna", label: "GPT-6 Luna", efforts: ["low", "high"] }],
    efforts: ["low", "high"],
  },
};

const AMARRAS: HubProjectRow = {
  id: "p_amarras",
  name: "amarras",
  handle: "p_am",
  icon: "",
  folder: "/home/nexus/petrobras",
  chats: 3,
  updated_at: 0,
  chat: CLAUDE_CHAT,
  lanes: [CLAUDE_CHAT, CODEX_CHAT],
  route: {
    at: 0,
    session_id: "s_claude",
    request: "Advance to the obstacle-avoidance milestone.",
    tier: "middle",
    effort: "high",
    proposal: {
      provider: "claude",
      model: "Sonnet",
      effort: "high",
      reason: "Claude: 21.3% of its week left today",
    },
    ran_on: { provider: "Claude Code", model: "claude-opus-5-5", effort: "xhigh" },
    error: "",
  },
};

const BUDGETS: HubBudget[] = [
  {
    provider: "claude",
    label: "Claude",
    week_used: 45,
    resets_at: "2026-10-16T11:59:59Z",
    day: 5,
    daily: 12.14,
    allowed: 24.29,
    spent: 3,
    remaining: 21.29,
    five_hour: 27,
    stale: false,
    free_until: null,
    blocked: "",
  },
  {
    provider: "gemini",
    label: "Gemini",
    week_used: 13,
    resets_at: "2026-10-14T02:35:11Z",
    day: 0,
    daily: 12.14,
    allowed: 0,
    spent: 0,
    remaining: 0,
    five_hour: 12,
    stale: true,
    free_until: null,
    blocked: "no reading since its reset",
  },
];

const NESSIE: HubProjectRow = {
  id: "p_nessie",
  name: "nessie",
  handle: "p_ne",
  icon: "🦕",
  folder: "",
  chats: 1,
  updated_at: 0,
  chat: {
    ...CLAUDE_CHAT,
    id: "s_agent",
    name: "nessie",
    title: "nessie",
    nickname: "nessie",
    status: "idle",
    working: false,
    now: "",
    pending_approvals: 2,
    project_id: "p_nessie",
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
  },
};

NESSIE.route = {
  at: 0,
  session_id: "s_agent",
  request: "Check the logs.",
  tier: "economical",
  effort: "xhigh",
  proposal: null,
  ran_on: null,
  error: "",
};

const EMPTY: HubProjectRow = {
  id: "p_arm",
  name: "robot arm",
  handle: "p_ar",
  icon: "",
  folder: "",
  chats: 0,
  updated_at: 0,
  chat: null,
};

function Where() {
  const location = useLocation();
  return <div data-testid="where">{`${location.pathname}${location.search}`}</div>;
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/c/hub"]}>
        <Routes>
          <Route path="*" element={<HubProjectsPanel />} />
        </Routes>
        <Where />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function openSelect(testId: string): void {
  const trigger = screen.getByTestId(testId);
  fireEvent.pointerDown(trigger, new MouseEvent("pointerdown", { bubbles: true, button: 0 }));
  fireEvent.click(trigger);
}

function openChanges(chatId: string): void {
  fireEvent.click(screen.getByTestId(`hub-chat-change-${chatId}`));
}

function openMenu(projectId: string): void {
  const trigger = screen.getByTestId(`hub-project-menu-${projectId}`);
  fireEvent.pointerDown(trigger, new MouseEvent("pointerdown", { bubbles: true, button: 0 }));
}

beforeEach(() => {
  for (const mock of [listMock, setMock, createMock, chatsMock, deleteMock, renameMock, addMock]) {
    mock.mockReset();
  }
  for (const mock of [openLaneMock, renameLaneMock, closeLaneMock]) mock.mockReset();
  listMock.mockResolvedValue([AMARRAS, NESSIE, EMPTY]);
  budgetsMock.mockReset();
  budgetsMock.mockResolvedValue(BUDGETS);
});

afterEach(cleanup);

describe("HubProjectsPanel", () => {
  it("lists a project's chats, each with its own provider, model, effort and state", async () => {
    renderPanel();
    const card = await screen.findByTestId("hub-project-p_amarras");
    expect(within(card).getByText("amarras")).toBeTruthy();
    expect(within(card).getByText("/home/nexus/petrobras")).toBeTruthy();
    const claude = screen.getByTestId("hub-chat-row-s_claude");
    const codex = screen.getByTestId("hub-chat-row-s_codex");
    expect(screen.getByTestId("hub-chat-run-s_claude").textContent).toBe(
      "Claude Code · Opus 5.5 · xhigh",
    );
    expect(screen.getByTestId("hub-chat-run-s_codex").textContent).toBe(
      "Codex · GPT-6 Luna · high",
    );
    expect(within(claude).getByText("Working · Bash: pytest -q")).toBeTruthy();
    // Two lanes, each under its own name: the Hub picks one by topic.
    expect(within(card).getByText(/2 lanes: agents working side by side/)).toBeTruthy();
    expect(screen.getByTestId("hub-lane-name-s_codex").textContent).toBe("joystick");
    expect(within(claude).queryByText("Hub sends here")).toBeNull();
    expect(within(codex).queryByText("Hub sends here")).toBeNull();
    // A project's only lane is where the Hub sends.
    expect(
      within(screen.getByTestId("hub-chat-row-s_agent")).getByText("Hub sends here"),
    ).toBeTruthy();
    expect(screen.getByTestId("hub-project-older-p_amarras").textContent).toBe("1 older chat");
    expect(
      within(claude).getByRole("link", { name: "Open Project Analysis" }).getAttribute("href"),
    ).toBe("/c/s_claude");
    // The controls open per chat.
    expect(screen.queryByTestId("hub-chat-model-s_claude")).toBeNull();
    openChanges("s_claude");
    expect(screen.getByTestId("hub-chat-model-s_claude").textContent).toContain("Sonnet 5.5");
    expect(screen.getByTestId("hub-chat-pending-s_claude").textContent).toBe(
      "Switching to Sonnet 5.5; it still reports Opus 5.5.",
    );
    expect(screen.getByTestId("hub-chat-effort-s_claude").textContent).toContain("xhigh");
    expect(screen.queryByTestId("hub-chat-model-s_codex")).toBeNull();
    expect(screen.getByText("3 projects")).toBeTruthy();
  });

  it("shows the router's newest decision and each provider's budget for today", async () => {
    renderPanel();
    await screen.findByTestId("hub-project-p_amarras");
    expect(screen.getByTestId("hub-project-route-p_amarras").textContent).toBe(
      "Router, logged only: middle · high → Claude Sonnet · high",
    );
    // A decision logged before providers were picked shows only its tier and effort.
    expect(screen.getByTestId("hub-project-route-p_nessie").textContent).toBe(
      "Router, logged only: economical · xhigh",
    );
    expect(screen.queryByTestId("hub-project-route-p_arm")).toBeNull();
    expect((await screen.findByTestId("hub-budget-claude")).textContent).toBe(
      "Claude · 21.3% left today",
    );
    expect(screen.getByTestId("hub-budget-claude").getAttribute("title")).toContain(
      "spent 3.0% of 24.3% today",
    );
    expect(screen.getByTestId("hub-budget-gemini").textContent).toBe(
      "Gemini · no reading since its reset",
    );
  });

  it("offers to start a chat in a project that has none", async () => {
    renderPanel();
    const card = await screen.findByTestId("hub-project-p_arm");
    expect(within(card).getByText("No chat yet")).toBeTruthy();
    expect(
      within(card).getByRole("link", { name: "Start a chat in robot arm" }).getAttribute("href"),
    ).toBe("/?project=robot%20arm");
    expect(within(card).queryByRole("button", { name: "Change" })).toBeNull();
  });

  it("shows a model it cannot change as plain text, and approvals it waits on", async () => {
    renderPanel();
    const card = await screen.findByTestId("hub-project-p_nessie");
    expect(within(card).getByText("Waiting for 2 approvals")).toBeTruthy();
    expect(within(card).getByText("🦕")).toBeTruthy();
    openChanges("s_agent");
    expect(within(card).getByText("claude-sonnet-5-5")).toBeTruthy();
    expect(screen.queryByTestId("hub-chat-model-s_agent")).toBeNull();
    expect(screen.queryByTestId("hub-chat-effort-s_agent")).toBeNull();
  });

  it("sends a pick through the hub for that one chat, and shows what it said", async () => {
    setMock.mockResolvedValue({
      changed: true,
      text: "Switched amarras to max effort; it applies from its next message.",
    });
    renderPanel();
    await screen.findByTestId("hub-project-p_amarras");
    openChanges("s_claude");
    openSelect("hub-chat-effort-s_claude");
    fireEvent.click(await screen.findByRole("option", { name: "max" }));
    await waitFor(() =>
      expect(setMock).toHaveBeenCalledWith("p_amarras", { effort: "max" }, "s_claude"),
    );
    expect(
      await screen.findByText("Switched amarras to max effort; it applies from its next message."),
    ).toBeTruthy();
  });

  it("opens the provider switch on a chat when it is idle", async () => {
    renderPanel();
    await screen.findByTestId("hub-project-p_amarras");
    openChanges("s_claude");
    openChanges("s_codex");
    const busy = screen.getByTestId("hub-chat-row-s_claude");
    expect(within(busy).getByRole("button", { name: "Switch…" })).toHaveProperty("disabled", true);
    const idle = screen.getByTestId("hub-chat-row-s_codex");
    fireEvent.click(within(idle).getByRole("button", { name: "Switch…" }));
    expect(screen.getByTestId("switch-dialog-s_codex")).toBeTruthy();
  });

  it("says so when there are no projects", async () => {
    listMock.mockResolvedValue([]);
    renderPanel();
    expect(await screen.findByText(/No projects yet/)).toBeTruthy();
    expect(screen.getByText("0 projects")).toBeTruthy();
  });

  it("makes a project with a new chat and opens that chat's start page", async () => {
    createMock.mockResolvedValue({
      text: "Created project robot arm.",
      id: "p_new",
      name: "robot arm",
      link: "/?project=robot%20arm",
    });
    renderPanel();
    fireEvent.click(await screen.findByTestId("hub-new-project"));
    fireEvent.change(await screen.findByTestId("hub-new-project-name"), {
      target: { value: "  robot arm " },
    });
    fireEvent.click(screen.getByTestId("hub-new-project-create"));
    await waitFor(() => expect(createMock).toHaveBeenCalledWith("robot arm", undefined));
    await waitFor(() =>
      expect(screen.getByTestId("where").textContent).toBe("/?project=robot%20arm"),
    );
  });

  it("makes a project from a chat they already have, and stays in the hub", async () => {
    chatsMock.mockResolvedValue([
      { id: "s_sol", title: "GPT-6.1 Sol Missing", updated_at: 0, provider: "Codex" },
    ]);
    createMock.mockResolvedValue({
      text: "Created project sol, with “GPT-6.1 Sol Missing” as its chat.",
      id: "p_sol",
      name: "sol",
      link: null,
    });
    renderPanel();
    fireEvent.click(await screen.findByTestId("hub-new-project"));
    fireEvent.change(await screen.findByTestId("hub-new-project-name"), {
      target: { value: "sol" },
    });
    fireEvent.click(screen.getByTestId("hub-new-project-existing"));
    expect(screen.getByTestId("hub-new-project-create")).toHaveProperty("disabled", true);
    fireEvent.click(await screen.findByTestId("hub-chat-s_sol"));
    expect(screen.getByTestId("hub-new-project-chat").textContent).toBe("GPT-6.1 Sol Missing");
    fireEvent.click(screen.getByTestId("hub-new-project-create"));
    await waitFor(() => expect(createMock).toHaveBeenCalledWith("sol", "s_sol"));
    await waitFor(() => expect(screen.queryByTestId("hub-new-project-name")).toBeNull());
    expect(screen.getByTestId("where").textContent).toBe("/c/hub");
  });

  it("keeps the dialog open and says why when the name is taken", async () => {
    createMock.mockRejectedValue(new Error("there is already a project called amarras"));
    renderPanel();
    fireEvent.click(await screen.findByTestId("hub-new-project"));
    fireEvent.change(await screen.findByTestId("hub-new-project-name"), {
      target: { value: "Amarras" },
    });
    fireEvent.click(screen.getByTestId("hub-new-project-create"));
    expect(
      await screen.findByText("Couldn't make it: there is already a project called amarras"),
    ).toBeTruthy();
    expect(screen.getByTestId("hub-new-project-name")).toBeTruthy();
  });

  it("renames a project and says what the hub said", async () => {
    renameMock.mockResolvedValue("Renamed nessie to agente.");
    renderPanel();
    await screen.findByTestId("hub-project-p_nessie");
    openMenu("p_nessie");
    fireEvent.click(await screen.findByRole("menuitem", { name: "Rename" }));
    const field = await screen.findByTestId("hub-project-rename-p_nessie");
    fireEvent.change(field, { target: { value: "  agente " } });
    fireEvent.submit(field.closest("form")!);
    await waitFor(() => expect(renameMock).toHaveBeenCalledWith("p_nessie", "agente"));
    expect(await screen.findByText("Renamed nessie to agente.")).toBeTruthy();
    expect(screen.queryByTestId("hub-project-rename-p_nessie")).toBeNull();
  });

  it("deletes a project only after asking, saying its chats are kept", async () => {
    deleteMock.mockResolvedValue(
      "Deleted project amarras; its 3 chats are kept, outside any project.",
    );
    renderPanel();
    await screen.findByTestId("hub-project-p_amarras");
    openMenu("p_amarras");
    fireEvent.click(await screen.findByRole("menuitem", { name: "Delete project…" }));
    expect(await screen.findByText("Its 3 chats are kept, outside any project.")).toBeTruthy();
    expect(deleteMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("hub-project-delete-confirm-p_amarras"));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("p_amarras"));
  });

  it("moves one of their chats into a project", async () => {
    chatsMock.mockResolvedValue([{ id: "s_notes", title: "Notes", updated_at: 0, provider: "" }]);
    addMock.mockResolvedValue("Moved “Notes” into robot arm.");
    renderPanel();
    await screen.findByTestId("hub-project-p_arm");
    openMenu("p_arm");
    fireEvent.click(await screen.findByRole("menuitem", { name: "Move a chat in…" }));
    fireEvent.click(await screen.findByTestId("hub-chat-s_notes"));
    await waitFor(() => expect(addMock).toHaveBeenCalledWith("p_arm", "s_notes"));
    expect(await screen.findByText("Moved “Notes” into robot arm.")).toBeTruthy();
  });

  it("opens a new lane with its topic, first request and model", async () => {
    openLaneMock.mockResolvedValueOnce({
      opened: false,
      text: "I didn't open a lane in amarras: it needs the top tier and no provider can take it now.",
      session_id: null,
    });
    openLaneMock.mockResolvedValueOnce({
      opened: true,
      text: "Sent.",
      session_id: "s_new",
    });
    renderPanel();
    await screen.findByTestId("hub-project-p_amarras");
    // A project without a chat has nothing to open a lane beside.
    expect(screen.queryByTestId("hub-project-new-lane-p_arm")).toBeNull();
    fireEvent.click(screen.getByTestId("hub-project-new-lane-p_amarras"));
    expect(await screen.findByText(/so it spends quota/)).toBeTruthy();
    const open = screen.getByTestId("hub-lane-open-p_amarras") as HTMLButtonElement;
    expect(open.disabled).toBe(true);
    fireEvent.change(screen.getByTestId("hub-lane-topic-p_amarras"), {
      target: { value: "joystick plan" },
    });
    fireEvent.change(screen.getByTestId("hub-lane-request-p_amarras"), {
      target: { value: "Plan the joystick work." },
    });
    openSelect("hub-lane-model-p_amarras");
    fireEvent.click(await screen.findByRole("option", { name: "Claude Sonnet" }));
    fireEvent.click(open);
    await waitFor(() =>
      expect(openLaneMock).toHaveBeenCalledWith("p_amarras", {
        topic: "joystick plan",
        message: "Plan the joystick work.",
        use: "Sonnet",
      }),
    );
    // Not opened: the reason shows and the dialog stays.
    expect(await screen.findAllByText(/no provider can take it now/)).toHaveLength(2);
    expect(screen.getByTestId("hub-lane-request-p_amarras")).toBeTruthy();
    fireEvent.click(open);
    await waitFor(() => expect(screen.queryByTestId("hub-lane-request-p_amarras")).toBeNull());
    expect(await screen.findByText("Sent.")).toBeTruthy();
  });

  it("renames a lane and closes it after its handoff note, never while it works", async () => {
    renameLaneMock.mockResolvedValue("Renamed the lane joystick to joystick plan.");
    closeLaneMock.mockResolvedValue({ text: "Sent.", closing: true });
    renderPanel();
    await screen.findByTestId("hub-project-p_amarras");

    const lanesMenu = (id: string) => {
      const trigger = screen.getByTestId(`hub-lane-menu-${id}`);
      fireEvent.pointerDown(trigger, new MouseEvent("pointerdown", { bubbles: true, button: 0 }));
    };
    lanesMenu("s_claude");
    const busyClose = await screen.findByRole("menuitem", {
      name: "Close lane (wait until it finishes)",
    });
    expect(busyClose.getAttribute("aria-disabled")).toBe("true");
    fireEvent.keyDown(busyClose, { key: "Escape" });

    lanesMenu("s_codex");
    fireEvent.click(await screen.findByRole("menuitem", { name: "Rename lane" }));
    const field = await screen.findByTestId("hub-lane-rename-s_codex");
    fireEvent.change(field, { target: { value: "joystick plan" } });
    fireEvent.submit(field.closest("form") as HTMLFormElement);
    await waitFor(() => expect(renameLaneMock).toHaveBeenCalledWith("s_codex", "joystick plan"));

    lanesMenu("s_codex");
    fireEvent.click(await screen.findByRole("menuitem", { name: "Close lane…" }));
    expect(await screen.findByText(/archived, not deleted/)).toBeTruthy();
    fireEvent.click(screen.getByTestId("hub-lane-close-confirm-s_codex"));
    await waitFor(() => expect(closeLaneMock).toHaveBeenCalledWith("s_codex", true));
    expect(
      await screen.findByText("joystick is writing its handoff note; it closes once that is done."),
    ).toBeTruthy();
  });

  it("starts a new chat in a project from its menu", async () => {
    renderPanel();
    await screen.findByTestId("hub-project-p_amarras");
    openMenu("p_amarras");
    const item = await screen.findByRole("menuitem", { name: "New chat in amarras" });
    expect(item.getAttribute("href")).toBe("/?project=amarras");
  });
});
