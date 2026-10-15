// The hub conversation's right rail: its Projects panel, nothing else.

import type * as UseTerminalsModule from "@/hooks/useTerminals";
import type * as UseChildSessionsModule from "@/hooks/useChildSessions";
import type * as UseSessionModule from "@/hooks/useSession";
import type * as UseConversationsModule from "@/hooks/useConversations";
import type * as UseGithubModule from "@/hooks/useGithub";
import type * as HubApiModule from "@/lib/hubApi";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import { writeSessionWorkspaceState } from "@/lib/sessionWorkspaceState";

vi.mock("@/hooks/useConversations", async (importOriginal) => ({
  ...(await importOriginal<typeof UseConversationsModule>()),
  useConversations: vi.fn(),
  useStopSession: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));
vi.mock("@/hooks/useTerminals", async (importOriginal) => ({
  // Keep the real module (inventoryTerminals etc.) — only the
  // network-backed hook is replaced.
  ...(await importOriginal<typeof UseTerminalsModule>()),
  useTerminals: vi.fn(() => ({ terminals: [], isLoading: false, error: null })),
}));
vi.mock("@/hooks/useWorkspaceChangedFiles", () => ({
  useWorkspaceEnvironment: vi.fn(() => ({
    data: { available: true, root: null },
    isLoading: false,
  })),
  useWorkspaceChangedFiles: vi.fn(() => ({
    data: { data: [] },
    isSuccess: true,
    isLoading: false,
  })),
  useWorkspaceFileExists: vi.fn(() => false),
  toWorkspaceRelativePath: vi.fn(() => null),
}));
vi.mock("@/hooks/useGithub", async (importOriginal) => ({
  // Keep the real module (types, the panel's sibling hooks) — only the
  // info hook AppShell reads is replaced, per-test below.
  ...(await importOriginal<typeof UseGithubModule>()),
  useGithubInfo: vi.fn(() => ({ data: undefined, isLoading: true })),
}));
vi.mock("@/hooks/useChildSessions", async (importOriginal) => ({
  ...(await importOriginal<typeof UseChildSessionsModule>()),
  useChildSessions: vi.fn(() => ({ children: [], isLoading: false, error: null })),
}));
vi.mock("@/hooks/useSession", async (importOriginal) => ({
  ...(await importOriginal<typeof UseSessionModule>()),
  useSession: vi.fn(() => ({
    session: { id: "conv_ws", labels: { "omnigent.hub": "1" } },
    isLoading: false,
    error: null,
  })),
}));
vi.mock("@/hooks/useAgents", () => ({
  useSessionAgent: vi.fn(() => ({ data: undefined })),
  useCreateMcpServer: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useUpdateMcpServer: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useDeleteMcpServer: () => ({ mutate: vi.fn(), isPending: false, error: null }),
}));
vi.mock("./Sidebar", () => ({ Sidebar: () => <div data-testid="sidebar" /> }));
vi.mock("./HubProjectsPanel", () => ({
  HubProjectsPanel: () => <div data-testid="hub-projects-panel" />,
}));
vi.mock("./FilesPanel", () => ({
  FilesPanel: () => <div data-testid="files-panel" />,
}));
vi.mock("./FileViewer", () => ({
  FileViewer: ({ conversationId, path }: { conversationId: string; path: string }) => (
    <div data-testid="file-viewer" data-chat={conversationId} data-path={path} />
  ),
}));
vi.mock("./InlineTerminalsSection", () => ({
  InlineTerminalsSection: () => <div data-testid="inline-terminals-section" />,
}));
vi.mock("./FilesPanelDrawer", () => ({
  FilesPanelDrawer: () => <div data-testid="files-panel-drawer" />,
}));
vi.mock("./TerminalsPanel", () => ({
  TerminalsPanel: (props: { conversationId: string; initialTerminalKey: string | null }) => (
    <div
      data-testid="terminals-panel"
      data-chat={props.conversationId}
      data-key={props.initialTerminalKey ?? ""}
    />
  ),
}));
vi.mock("@/lib/hubApi", async (importOriginal) => ({
  ...(await importOriginal<typeof HubApiModule>()),
  openHubTerminal: vi.fn(async () => ({ sessionId: "conv_term", terminalId: "terminal_bash_hub" })),
}));

import { FilePathAwareMessageResponse } from "@/components/blocks/ChatMarkdown";
import { AppShell } from "./AppShell";
import { useGithubInfo } from "@/hooks/useGithub";
import { useConversations } from "@/hooks/useConversations";
import { openHubTerminal } from "@/lib/hubApi";

const useGithubInfoMock = vi.mocked(useGithubInfo);

afterEach(cleanup);

beforeEach(() => {
  // The rail persists per-session state (selected tab, width) in
  // localStorage; clear it so one test's writes can't leak into another.
  localStorage.clear();
  sessionStorage.clear();
  useGithubInfoMock.mockReset();
  useGithubInfoMock.mockReturnValue({ data: undefined, isLoading: true } as ReturnType<
    typeof useGithubInfo
  >);
  vi.mocked(useConversations).mockReset();
  vi.mocked(useConversations).mockReturnValue({
    data: {
      pages: [
        {
          data: [
            {
              id: "conv_ws",
              object: "conversation" as const,
              title: null,
              created_at: 0,
              updated_at: 0,
              labels: {},
              permission_level: null,
              host_id: null,
              runner_id: null,
            },
          ],
          first_id: null,
          last_id: null,
          has_more: false,
        },
      ],
      pageParams: [undefined],
    },
  } as never);
});

function renderShell(chat: React.ReactNode = <div>chat</div>) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <MemoryRouter initialEntries={["/c/conv_ws"]}>
          <Routes>
            <Route element={<AppShell />}>
              <Route path="c/:conversationId" element={chat} />
            </Route>
          </Routes>
        </MemoryRouter>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("the hub's rail", () => {
  it("is the Projects tab alone, even over a remembered Files tab", async () => {
    // The hub runs other projects and has no workspace of its own: files,
    // agents, companion and to-do would all be empty for it.
    writeSessionWorkspaceState("conv_ws", { rightRailTab: "files" });
    renderShell();

    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Projects" })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    expect(screen.getByTestId("hub-projects-panel")).toBeInTheDocument();
    for (const name of [/^Files$/, /^Changes/, "GitHub", /^Agents/, "Companion", /^To-do/]) {
      expect(screen.queryByRole("tab", { name })).toBeNull();
    }
  });

  it("opens a project's file from a hub link in its own rail", async () => {
    const chat = "4a1867870bb74b44b3fb98f1ddd42887";
    const href = `http://localhost:6767/c/${chat}?file=RA-L/reviews/REVISION_PLAN.md`;
    renderShell(
      <FilePathAwareMessageResponse>{`[REVISION_PLAN.md](${href})`}</FilePathAwareMessageResponse>,
    );

    const link = await screen.findByRole("link", { name: "REVISION_PLAN.md" });
    expect(fireEvent.click(link)).toBe(false); // no new tab

    // The viewer reads the project's workspace, not the hub's.
    const viewers = await screen.findAllByTestId("file-viewer");
    for (const viewer of viewers) {
      expect(viewer).toHaveAttribute("data-chat", chat);
      expect(viewer).toHaveAttribute("data-path", "RA-L/reviews/REVISION_PLAN.md");
    }
    expect(screen.getByTitle("RA-L/reviews/REVISION_PLAN.md")).toBeInTheDocument();
  });
});

describe("the hub's terminal", () => {
  beforeEach(() => vi.mocked(openHubTerminal).mockClear());

  it("opens the shell already open on the machine from the header button", async () => {
    renderShell();
    expect(screen.queryByTestId("terminals-panel")).toBeNull();

    fireEvent.click(await screen.findByRole("button", { name: "Open a terminal" }));

    // Beside the hub chat, attached to the session that holds the shells.
    const panel = await screen.findByTestId("terminals-panel");
    expect(panel).toHaveAttribute("data-chat", "conv_term");
    expect(panel).toHaveAttribute("data-key", "terminal:terminal_bash_hub");
    expect(openHubTerminal).toHaveBeenCalledWith(false);
    expect(screen.getByText("chat")).toBeVisible();
  });

  it("opens a new shell with the new-shell shortcut", async () => {
    renderShell();
    await screen.findByRole("button", { name: "Open a terminal" });

    fireEvent.keyDown(document, { key: "t", code: "KeyT", ctrlKey: true, altKey: true });

    await screen.findByTestId("terminals-panel");
    expect(openHubTerminal).toHaveBeenCalledWith(true);
  });
});
