// Files the capped content envelope cannot show: videos, which stream from the
// uncapped download URL, and HTML pages past the read cap, fetched whole.

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { useFileContent } from "@/hooks/useFileContent";
import { CodeViewer } from "./CodeViewer";
import { isVideoFile } from "./codeViewerHelpers";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/usePermissions", () => ({ useCanEdit: vi.fn(() => false) }));
vi.mock("@/components/ai-elements/code-block", () => ({ highlightCode: vi.fn(() => null) }));
vi.mock("./MarkdownRichTextViewer", () => ({ MarkdownRichTextViewer: () => null }));
vi.mock("./MonacoCodeEditor", () => ({ MonacoCodeEditor: () => null }));

function query(data: object | undefined): ReturnType<typeof useFileContent> {
  return {
    data,
    isLoading: false,
    isPending: data === undefined,
    isError: false,
    isSuccess: data !== undefined,
    error: null,
  } as unknown as ReturnType<typeof useFileContent>;
}

function renderViewer(
  path: string,
  fileQuery: ReturnType<typeof useFileContent>,
  viewMode = "source",
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CodeViewer
        conversationId="conv_1"
        path={path}
        fileQuery={fileQuery}
        comments={[]}
        activeSelection={null}
        onSetActiveSelection={() => {}}
        panelOpen
        searchOpen={false}
        setSearchOpen={() => {}}
        searchInputRef={{ current: null }}
        viewMode={viewMode as "source" | "preview"}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => authenticatedFetch.mockReset());
afterEach(cleanup);

describe("video files", () => {
  it("are recognised by extension, whatever the case", () => {
    expect(["a.mp4", "b.MOV", "c.webm", "d.m4v", "e.ogv"].every(isVideoFile)).toBe(true);
    expect(["a.mp3", "b.ogg", "c.png"].some(isVideoFile)).toBe(false);
  });

  it("play in the viewer from the uncapped download stream", () => {
    renderViewer("runs/seed2/clip.mp4", query(undefined));
    const video = screen.getByTestId("workspace-video");
    expect(video.getAttribute("src")).toBe(
      "/v1/sessions/conv_1/resources/environments/default/filesystem/runs/seed2/clip.mp4?download=true",
    );
    expect(video.hasAttribute("controls")).toBe(true);
    expect(screen.queryByText(/binary files/)).toBeNull();
  });
});

describe("an HTML page past the read cap", () => {
  it("is fetched whole and shown without the truncated banner", async () => {
    authenticatedFetch.mockResolvedValue(
      new Response("<html><body>cut<script>window.FULL_PAGE_END = 1</script></body></html>"),
    );
    const { container } = renderViewer(
      "runs/viewer.html",
      query({ content: "<html><body>cut", encoding: "utf-8", truncated: true }),
      "preview",
    );
    await waitFor(() =>
      expect(container.querySelector("iframe")?.getAttribute("srcdoc")).toContain("FULL_PAGE_END"),
    );
    expect(authenticatedFetch).toHaveBeenCalledWith(
      "/v1/sessions/conv_1/resources/environments/default/filesystem/runs/viewer.html?download=true",
    );
    expect(screen.queryByText(/truncated/i)).toBeNull();
  });

  it("falls back to the truncated page when the full fetch fails", async () => {
    authenticatedFetch.mockResolvedValue(new Response("gone", { status: 502 }));
    const { container } = renderViewer(
      "runs/viewer.html",
      query({ content: "<html><body>cut", encoding: "utf-8", truncated: true }),
      "preview",
    );
    await waitFor(() =>
      expect(container.querySelector("iframe")?.getAttribute("srcdoc")).toContain("cut"),
    );
  });

  it("is not fetched again when it was never cut", () => {
    renderViewer(
      "runs/small.html",
      query({ content: "<html><body>small</body></html>", encoding: "utf-8", truncated: false }),
      "preview",
    );
    expect(authenticatedFetch).not.toHaveBeenCalled();
  });
});
