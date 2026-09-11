import { beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { SummaryShowBlock } from "@/lib/blockStream";
import { SummaryShowBlocks } from "./SummaryShowBlocks";
import { FriendlyResponse } from "./FriendlyResponse";
import { SUMMARY_SHOW_STORAGE_KEY } from "@/lib/summaryShowPreferences";
import { useSpeechPlaybackStore } from "@/lib/speechPlayback";
import { useChatStore } from "@/store/chatStore";

const table: SummaryShowBlock = {
  kind: "table",
  label: "table of 1 row (engine, WER)",
  content: "| engine | WER |\n|---|---|\n| whisper | 3.9% |",
};
const code: SummaryShowBlock = { kind: "code", label: "code in python, 2 lines", content: "x = 1" };
const file: SummaryShowBlock = {
  kind: "file",
  label: "file (report.pdf)",
  content: "f_1",
  filename: "report.pdf",
  mime_type: "application/pdf",
};

describe("SummaryShowBlocks", () => {
  beforeEach(() => {
    cleanup();
    localStorage.clear();
    useChatStore.setState({ conversationId: "conv_1" } as never);
  });

  it("shows a table instead of leaving it to the prose", () => {
    render(<SummaryShowBlocks blocks={[table]} />);
    expect(screen.getByTestId("summary-show-table").textContent).toContain("whisper");
  });

  it("hides the kinds the reader turned off, and keeps files regardless", () => {
    // Code is off by default; a file was sent deliberately, so it is not a
    // preference at all.
    render(<SummaryShowBlocks blocks={[table, code, file]} />);
    expect(screen.queryByTestId("summary-show-code")).toBeNull();
    expect(screen.getByTestId("summary-show-table")).toBeTruthy();
    expect(screen.getByTestId("attached-files")).toBeTruthy();
  });

  it("shows code once the reader asks for it", () => {
    localStorage.setItem(SUMMARY_SHOW_STORAGE_KEY, JSON.stringify({ code: true }));
    render(<SummaryShowBlocks blocks={[code]} />);
    expect(screen.getByTestId("summary-show-code").textContent).toContain("x = 1");
  });

  it("links a remote image rather than fetching it", () => {
    // The transcript's markdown refuses remote images; a summary must not be
    // the way one gets loaded.
    render(
      <SummaryShowBlocks
        blocks={[{ kind: "image", label: "chart", content: "https://elsewhere.test/c.png" }]}
      />,
    );
    expect(screen.queryByTestId("summary-show-image")).toBeNull();
    expect(screen.getByTestId("summary-show-link").getAttribute("href")).toBe(
      "https://elsewhere.test/c.png",
    );
  });

  it("embeds an image that is one of this session's own files", () => {
    render(
      <SummaryShowBlocks
        blocks={[
          {
            kind: "image",
            label: "chart",
            content: "/v1/sessions/conv_1/resources/files/f_2/content",
          },
        ]}
      />,
    );
    expect(screen.getByTestId("summary-show-image")).toBeTruthy();
  });

  it("renders nothing when there is nothing to show", () => {
    const { container } = render(<SummaryShowBlocks blocks={[]} />);
    expect(container.textContent).toBe("");
  });
});

describe("shown blocks are not spoken", () => {
  beforeEach(() => {
    cleanup();
    localStorage.clear();
    useChatStore.setState({ conversationId: "conv_1" } as never);
  });

  it("keeps the table out of the text handed to playback", () => {
    // The voice reads the summary's prose; a table read aloud is long to hear
    // and still has no numbers in it.
    const speak = vi.fn();
    useSpeechPlaybackStore.setState({ playManual: speak } as never);

    render(
      <FriendlyResponse
        summary={{ text: "Whisper won on every term.", lang: "en-US", show: [table] }}
        id="resp_1"
      >
        <div>original</div>
      </FriendlyResponse>,
    );

    expect(screen.getByTestId("friendly-response-text").textContent).toBe(
      "Whisper won on every term.",
    );
    expect(screen.getByTestId("summary-show-table")).toBeTruthy();
  });
});
