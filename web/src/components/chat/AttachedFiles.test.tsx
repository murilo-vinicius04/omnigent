import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { AttachedFiles } from "./AttachedFiles";
import { outputFilesFromMessageContent } from "@/lib/blockStream";

afterEach(cleanup);

describe("outputFilesFromMessageContent", () => {
  it("reads output_file blocks and skips malformed ones", () => {
    const files = outputFilesFromMessageContent([
      { type: "output_text", text: "here you go" },
      { type: "output_file", file_id: "f1", filename: "voz.mp3", mime_type: "audio/mpeg" },
      { type: "output_file", file_id: "" },
      { type: "output_file", filename: "no-id.png" },
    ]);

    expect(files).toHaveLength(1);
    expect(files![0]).toEqual({ fileId: "f1", filename: "voz.mp3", mimeType: "audio/mpeg" });
  });

  it("returns undefined when a message carries no files", () => {
    expect(outputFilesFromMessageContent([{ type: "output_text", text: "hi" }])).toBeUndefined();
  });
});

describe("AttachedFiles", () => {
  it("plays audio inline rather than offering a download", () => {
    render(
      <AttachedFiles files={[{ fileId: "f1", filename: "voz.mp3", mimeType: "audio/mpeg" }]} />,
    );

    const audio = screen.getByTestId("attached-audio");
    expect(audio).toBeInTheDocument();
    expect(audio.getAttribute("controls")).not.toBeNull();
    expect(screen.queryByTestId("attached-download")).toBeNull();
  });

  it("falls back to a download link for types it cannot render", () => {
    render(
      <AttachedFiles
        files={[{ fileId: "f2", filename: "report.pdf", mimeType: "application/pdf" }]}
      />,
    );

    const link = screen.getByTestId("attached-download");
    expect(link).toHaveTextContent("report.pdf");
    expect(link.getAttribute("download")).toBe("report.pdf");
  });

  it("renders nothing for an empty list", () => {
    const { container } = render(<AttachedFiles files={[]} />);
    expect(container.firstChild).toBeNull();
  });
});
