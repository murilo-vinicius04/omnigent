import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HubProjectsPanel } from "./HubProjectsPanel";
import type * as HubApiModule from "@/lib/hubApi";
import {
  addHubProjectChat,
  createHubProject,
  deleteHubProject,
  listHubChats,
  listHubProjects,
  renameHubProject,
  setHubProjectModel,
  type HubProjectRow,
  type HubSessionRow,
} from "@/lib/hubApi";

vi.mock("@/lib/hubApi", async (importOriginal) => ({
  ...(await importOriginal<typeof HubApiModule>()),
  addHubProjectChat: vi.fn(),
  createHubProject: vi.fn(),
  deleteHubProject: vi.fn(),
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

const AMARRAS: HubProjectRow = {
  id: "p_amarras",
  name: "amarras",
  handle: "p_am",
  icon: "",
  folder: "/home/nexus/petrobras",
  chats: 3,
  updated_at: 0,
  chat: CLAUDE_CHAT,
};

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

function openMenu(projectId: string): void {
  const trigger = screen.getByTestId(`hub-project-menu-${projectId}`);
  fireEvent.pointerDown(trigger, new MouseEvent("pointerdown", { bubbles: true, button: 0 }));
}

beforeEach(() => {
  for (const mock of [listMock, setMock, createMock, chatsMock, deleteMock, renameMock, addMock]) {
    mock.mockReset();
  }
  listMock.mockResolvedValue([AMARRAS, NESSIE, EMPTY]);
});

afterEach(cleanup);

describe("HubProjectsPanel", () => {
  it("shows each project's chat, state, folder, provider, model and effort", async () => {
    renderPanel();
    const card = await screen.findByTestId("hub-project-p_amarras");
    expect(within(card).getByText("amarras")).toBeTruthy();
    expect(within(card).getByText("Working · Bash: pytest -q")).toBeTruthy();
    expect(screen.getByTestId("hub-project-chat-p_amarras").textContent).toBe(
      "Project Analysis · newest of 3 chats",
    );
    expect(within(card).getByText("/home/nexus/petrobras")).toBeTruthy();
    expect(within(card).getByText("Claude Code")).toBeTruthy();
    expect(screen.getByTestId("hub-project-model-p_amarras").textContent).toContain("Sonnet 5.5");
    expect(screen.getByTestId("hub-project-pending-p_amarras").textContent).toBe(
      "Switching to Sonnet 5.5; it still reports Opus 5.5.",
    );
    expect(screen.getByTestId("hub-project-effort-p_amarras").textContent).toContain("xhigh");
    expect(within(card).getByRole("link", { name: "Open amarras" }).getAttribute("href")).toBe(
      "/c/s_claude",
    );
    expect(screen.getByText("3 projects")).toBeTruthy();
  });

  it("offers to start a chat in a project that has none", async () => {
    renderPanel();
    const card = await screen.findByTestId("hub-project-p_arm");
    expect(within(card).getByText("No chat yet")).toBeTruthy();
    expect(
      within(card).getByRole("link", { name: "Start a chat in robot arm" }).getAttribute("href"),
    ).toBe("/?project=robot%20arm");
    expect(screen.queryByTestId("hub-project-model-p_arm")).toBeNull();
  });

  it("shows a model it cannot change as plain text, and approvals it waits on", async () => {
    renderPanel();
    const card = await screen.findByTestId("hub-project-p_nessie");
    expect(within(card).getByText("Waiting for 2 approvals")).toBeTruthy();
    expect(within(card).getByText("claude-sonnet-5-5")).toBeTruthy();
    expect(within(card).getByText("🦕")).toBeTruthy();
    expect(screen.queryByTestId("hub-project-model-p_nessie")).toBeNull();
    expect(screen.queryByTestId("hub-project-effort-p_nessie")).toBeNull();
  });

  it("sends a pick through the hub, by project, and shows what it said", async () => {
    setMock.mockResolvedValue({
      changed: true,
      text: "Switched amarras to max effort; it applies from its next message.",
    });
    renderPanel();
    await screen.findByTestId("hub-project-p_amarras");
    openSelect("hub-project-effort-p_amarras");
    fireEvent.click(await screen.findByRole("option", { name: "max" }));
    await waitFor(() => expect(setMock).toHaveBeenCalledWith("p_amarras", { effort: "max" }));
    expect(
      await screen.findByText("Switched amarras to max effort; it applies from its next message."),
    ).toBeTruthy();
  });

  it("opens the provider switch on the project's chat when it is idle", async () => {
    renderPanel();
    const busy = await screen.findByTestId("hub-project-p_amarras");
    expect(within(busy).getByRole("button", { name: "Switch…" })).toHaveProperty("disabled", true);
    const idle = screen.getByTestId("hub-project-p_nessie");
    fireEvent.click(within(idle).getByRole("button", { name: "Switch…" }));
    expect(screen.getByTestId("switch-dialog-s_agent")).toBeTruthy();
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

  it("starts a new chat in a project from its menu", async () => {
    renderPanel();
    await screen.findByTestId("hub-project-p_amarras");
    openMenu("p_amarras");
    const item = await screen.findByRole("menuitem", { name: "New chat in amarras" });
    expect(item.getAttribute("href")).toBe("/?project=amarras");
  });
});
