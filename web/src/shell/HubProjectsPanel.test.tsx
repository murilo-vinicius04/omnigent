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
  getHubProjectBrief,
  listHubBudgets,
  listHubChats,
  listHubProjects,
  openHubLane,
  refreshHubProjectBrief,
  renameHubLane,
  renameHubProject,
  saveHubProjectBrief,
  setHubProjectModel,
  type HubBudget,
  type HubProjectBrief,
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
  getHubProjectBrief: vi.fn(),
  saveHubProjectBrief: vi.fn(),
  refreshHubProjectBrief: vi.fn(),
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
const briefMock = vi.mocked(getHubProjectBrief);
const saveBriefMock = vi.mocked(saveHubProjectBrief);
const refreshMock = vi.mocked(refreshHubProjectBrief);

/** A project's brief as the server sends it, empty unless a test fills it in. */
function makeBrief(over: Partial<HubProjectBrief> = {}): HubProjectBrief {
  return {
    project_id: "p_amarras",
    name: "amarras",
    about: "",
    about_source: "",
    status: "",
    progress: [],
    updated_at: 0,
    state: "idle",
    lanes: [],
    lane_todos: [],
    brief: "",
    brief_todos: [],
    handoff_files: [],
    can_refresh: false,
    ...over,
  };
}

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
  for (const mock of [openLaneMock, renameLaneMock, closeLaneMock, briefMock, saveBriefMock, refreshMock]) {
    mock.mockReset();
  }
  listMock.mockResolvedValue([AMARRAS, NESSIE, EMPTY]);
  budgetsMock.mockReset();
  budgetsMock.mockResolvedValue(BUDGETS);
});

afterEach(cleanup);

describe("HubProjectsPanel", () => {
  it("offers Muse model and effort changes from the lane controls", async () => {
    const muse: HubSessionRow = {
      ...CODEX_CHAT,
      id: "s_muse",
      title: "Muse work",
      lane: "Muse work",
      run: {
        provider: "Muse Code",
        model: "muse-spark-1.3",
        model_label: "Muse Spark 1.3",
        requested: "",
        requested_label: "",
        effort: "high",
        selected: "muse-spark-1.3",
        models: [
          { id: "muse-spark-1.3", label: "Muse Spark 1.3", efforts: [] },
          { id: "muse-wonder-1.0", label: "Muse Wonder", efforts: [] },
        ],
        efforts: ["low", "high", "ultra"],
      },
    };
    listMock.mockResolvedValue([{ ...AMARRAS, chat: muse, lanes: [muse] }]);
    setMock.mockResolvedValue({ changed: true, text: "Updated Muse." });
    renderPanel();
    await screen.findByTestId("hub-chat-row-s_muse");
    openChanges("s_muse");
    openSelect("hub-chat-model-s_muse");
    fireEvent.click(screen.getByRole("option", { name: "Muse Wonder" }));
    await waitFor(() =>
      expect(setMock).toHaveBeenCalledWith("p_amarras", { model: "muse-wonder-1.0" }, "s_muse"),
    );
    openSelect("hub-chat-effort-s_muse");
    fireEvent.click(screen.getByRole("option", { name: "ultra" }));
    await waitFor(() =>
      expect(setMock).toHaveBeenCalledWith("p_amarras", { effort: "ultra" }, "s_muse"),
    );
  });

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
      "Claude · 21.3% of week available today",
    );
    expect(screen.getByTestId("hub-budget-claude").getAttribute("title")).toContain(
      "spent 3.0% of 24.3% today",
    );
    expect(screen.getByTestId("hub-budget-gemini").textContent).toBe(
      "Gemini · no reading since its reset",
    );
  });

  it("distinguishes the local Hub rollover from the subscription reset", async () => {
    budgetsMock.mockResolvedValue([
      {
        ...BUDGETS[0],
        daily_resets_at: "2026-10-17T04:00:00Z",
        daily_timezone: "America/Sao_Paulo",
      },
    ]);
    renderPanel();
    const badge = await screen.findByTestId("hub-budget-claude");
    expect(badge).toHaveTextContent("renews 01:00");
    expect(badge.getAttribute("title")).toContain("subscription week resets");
    expect(badge.getAttribute("title")).toContain("America/Sao_Paulo");
    expect(badge.getAttribute("title")).toContain("API credits are separate");
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

  it("renders the notification chime button in the header and toggles it", async () => {
    renderPanel();
    const chimeBtn = await screen.findByTestId("hub-chime-button");
    expect(chimeBtn).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(chimeBtn);
    expect(chimeBtn).toHaveAttribute("aria-pressed", "true");
  });

  it("previews where a project stands and opens a brief that reads like an overview", async () => {
    briefMock.mockResolvedValue(
      makeBrief({
        about: "Documentation for the Petrobras mooring chains.",
        about_source: "companion",
        status: "Chapter two is written; chapter three waits on the measurements.",
        progress: [{ at: 0, text: "Wrote chapter two.", lane: "docs" }],
        lanes: [{ id: "s_claude", name: "docs", state: "working" }],
        lane_todos: [
          {
            lane: "amarras",
            session_id: "s_claude",
            items: [{ content: "Run tests", status: "in_progress" }],
          },
        ],
        updated_at: 1,
        can_refresh: true,
      }),
    );
    listMock.mockResolvedValue([
      {
        ...AMARRAS,
        living: {
          overview: "Documentation for the Petrobras mooring chains.",
          status: "Chapter two is written; chapter three waits on the measurements.",
          progress: [],
          updated_at: 1,
        },
      },
      NESSIE,
      EMPTY,
    ]);
    renderPanel();
    const toggle = await screen.findByTestId("hub-brief-toggle-p_amarras");
    expect(toggle.textContent).toContain("Chapter two is written");
    fireEvent.click(toggle);
    expect(await screen.findByText("Documentation for the Petrobras mooring chains.")).toBeTruthy();
    expect(screen.getByTestId("hub-brief-standing-p_amarras").textContent).toBe(
      "Chapter two is written; chapter three waits on the measurements.",
    );
    expect(screen.getByTestId("hub-brief-progress").textContent).toContain("Wrote chapter two.");
    expect(screen.getByText("docs · working")).toBeTruthy();
    expect(screen.getByText("Run tests")).toBeTruthy();
    expect(briefMock).toHaveBeenCalledWith("p_amarras");
  });

  it("previews a project with no brief yet by its state, never by a tool command", async () => {
    listMock.mockResolvedValue([AMARRAS, NESSIE, EMPTY]);
    renderPanel();
    const toggle = await screen.findByTestId("hub-brief-toggle-p_amarras");
    expect(toggle.textContent).toContain("Working");
    expect(toggle.textContent).not.toContain("pytest");
  });

  it("says plainly that nothing is written yet and offers to write it", async () => {
    briefMock.mockResolvedValue(makeBrief({ state: "idle", can_refresh: true }));
    renderPanel();
    fireEvent.click(await screen.findByTestId("hub-brief-toggle-p_amarras"));
    expect(await screen.findByText(/Not written yet. The companion writes it/)).toBeTruthy();
    expect(screen.getByText("Not updated yet")).toBeTruthy();
    expect(screen.getByTestId("hub-brief-refresh-p_amarras")).not.toBeDisabled();
  });

  it("updates the brief on request and shows what the companion wrote", async () => {
    briefMock.mockResolvedValue(makeBrief({ can_refresh: true }));
    refreshMock.mockResolvedValue(
      makeBrief({
        about: "A scanner for chains.",
        about_source: "companion",
        status: "Calibrating.",
        updated_at: 1,
        can_refresh: true,
      }),
    );
    renderPanel();
    fireEvent.click(await screen.findByTestId("hub-brief-toggle-p_amarras"));
    fireEvent.click(await screen.findByTestId("hub-brief-refresh-p_amarras"));
    expect(await screen.findByText("A scanner for chains.")).toBeTruthy();
    expect(refreshMock).toHaveBeenCalledWith("p_amarras");
    expect(screen.getByTestId("hub-brief-standing-p_amarras").textContent).toBe("Calibrating.");
  });

  it("says why it could not update", async () => {
    briefMock.mockResolvedValue(makeBrief({ can_refresh: true }));
    refreshMock.mockRejectedValue(new Error("no chat of this project has reported yet"));
    renderPanel();
    fireEvent.click(await screen.findByTestId("hub-brief-toggle-p_amarras"));
    fireEvent.click(await screen.findByTestId("hub-brief-refresh-p_amarras"));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn't update it: no chat of this project has reported yet",
    );
  });

  it("disables the update when there is nothing to read", async () => {
    briefMock.mockResolvedValue(makeBrief({ state: "empty", can_refresh: false }));
    renderPanel();
    fireEvent.click(await screen.findByTestId("hub-brief-toggle-p_amarras"));
    expect(await screen.findByTestId("hub-brief-refresh-p_amarras")).toBeDisabled();
    expect(screen.getByText("No chat yet.")).toBeTruthy();
  });

  it("pins the person's own overview", async () => {
    briefMock.mockResolvedValue(
      makeBrief({ about: "Mine", about_source: "written", brief: "Mine" }),
    );
    saveBriefMock.mockResolvedValue({
      project_id: "p_amarras",
      brief: "Chains docs",
      brief_todos: [],
    });
    renderPanel();
    fireEvent.click(await screen.findByTestId("hub-brief-toggle-p_amarras"));
    expect(
      await screen.findByText("Your words. The companion leaves this one alone."),
    ).toBeTruthy();
    fireEvent.click(screen.getByTestId("hub-brief-write-p_amarras"));
    fireEvent.change(await screen.findByTestId("hub-brief-edit-p_amarras"), {
      target: { value: "Chains docs" },
    });
    fireEvent.click(screen.getByTestId("hub-brief-save-p_amarras"));
    await waitFor(() =>
      expect(saveBriefMock).toHaveBeenCalledWith("p_amarras", {
        brief: "Chains docs",
        brief_todos: [],
      }),
    );
    expect(await screen.findByText("Brief saved.")).toBeTruthy();
  });

  it("keeps a companion item's id and author when the person ticks it", async () => {
    briefMock.mockResolvedValue(
      makeBrief({
        brief_todos: [
          { id: "ab12", content: "Measure the chains", status: "open", by: "companion" },
          { id: "cd34", content: "Review the PDF", status: "open", by: "you" },
        ],
      }),
    );
    saveBriefMock.mockResolvedValue({ project_id: "p_amarras", brief: "", brief_todos: [] });
    renderPanel();
    fireEvent.click(await screen.findByTestId("hub-brief-toggle-p_amarras"));
    expect(await screen.findByText("added by the companion")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: "Measure the chains" }));
    await waitFor(() =>
      expect(saveBriefMock).toHaveBeenCalledWith("p_amarras", {
        brief: "",
        brief_todos: [
          { id: "ab12", content: "Measure the chains", status: "done", by: "companion" },
          { id: "cd34", content: "Review the PDF", status: "open", by: "you" },
        ],
      }),
    );
  });

  it("adds a to-do to the project's list", async () => {
    briefMock.mockResolvedValue(makeBrief());
    saveBriefMock.mockResolvedValue({
      project_id: "p_amarras",
      brief: "",
      brief_todos: [{ content: "Review", status: "open" }],
    });
    renderPanel();
    const card = await screen.findByTestId("hub-project-p_amarras");
    fireEvent.click(screen.getByTestId("hub-brief-toggle-p_amarras"));
    expect(await screen.findByText(/Nothing on the list/)).toBeTruthy();
    fireEvent.change(screen.getByTestId("hub-brief-add-p_amarras"), {
      target: { value: "Review" },
    });
    fireEvent.click(within(card).getByRole("button", { name: "Add" }));
    await waitFor(() =>
      expect(saveBriefMock).toHaveBeenCalledWith("p_amarras", {
        brief: "",
        brief_todos: [{ content: "Review", status: "open" }],
      }),
    );
  });
});
