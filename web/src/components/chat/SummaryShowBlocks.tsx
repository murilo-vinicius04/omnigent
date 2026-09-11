import type { SummaryShowBlock } from "@/lib/blockStream";
import { AttachedFiles } from "@/components/chat/AttachedFiles";
import { FilePathAwareMessageResponse } from "@/components/blocks/ChatMarkdown";
import { SessionImage } from "@/components/SessionImage";
import { visibleShowBlocks } from "@/lib/summaryShowPreferences";

/** Whether a URL is one of this session's stored files, safe to embed. */
function isSessionFilePath(url: string): boolean {
  return url.startsWith("/v1/sessions/") && url.includes("/resources/files/");
}

export interface SummaryShowBlocksProps {
  blocks?: SummaryShowBlock[];
}

/**
 * The parts of an answer the summary shows rather than describes.
 *
 * A spoken summary is prose, so a table read aloud is the worst of both: long
 * to hear and still no numbers. These render under the text and are never
 * spoken — the voice keeps to the prose above them.
 *
 * The reader chooses which kinds they want; a file is not one of those choices,
 * because attaching it was already the decision to show it.
 */
export function SummaryShowBlocks({ blocks }: SummaryShowBlocksProps) {
  const visible = visibleShowBlocks(blocks);
  if (visible.length === 0) return null;

  const files = visible.filter((block) => block.kind === "file");
  const rest = visible.filter((block) => block.kind !== "file");

  return (
    <div className="mt-2 flex flex-col gap-2" data-testid="summary-show-blocks">
      {rest.map((block, index) => {
        const key = `${block.kind}-${index}`;
        if (block.kind === "table") {
          return (
            <div key={key} className="min-w-0 overflow-x-auto" data-testid="summary-show-table">
              <FilePathAwareMessageResponse>{block.content}</FilePathAwareMessageResponse>
            </div>
          );
        }
        if (block.kind === "image") {
          // Only this session's own files are embedded. A remote image would
          // fetch from wherever the answer pointed, which the transcript's
          // markdown deliberately refuses to do; that one gets a link instead.
          if (isSessionFilePath(block.content)) {
            return (
              <div key={key} data-testid="summary-show-image">
                <SessionImage path={block.content} alt={block.label || "image"} />
              </div>
            );
          }
          return (
            <a
              key={key}
              href={block.content}
              target="_blank"
              rel="noreferrer"
              className="text-ui text-muted-foreground underline underline-offset-2 hover:text-foreground"
              data-testid="summary-show-link"
            >
              {block.label || block.content}
            </a>
          );
        }
        if (block.kind === "link") {
          return (
            <a
              key={key}
              href={block.content}
              target="_blank"
              rel="noreferrer"
              className="text-ui text-muted-foreground underline underline-offset-2 hover:text-foreground"
              data-testid="summary-show-link"
            >
              {block.label || block.content}
            </a>
          );
        }
        // Output and code are both quoted verbatim; only the label differs.
        return (
          <pre
            key={key}
            className="min-w-0 overflow-x-auto rounded-md bg-muted/50 px-3 py-2 text-xs"
            data-testid={block.kind === "code" ? "summary-show-code" : "summary-show-output"}
          >
            {block.content}
          </pre>
        );
      })}

      {files.length > 0 && (
        <AttachedFiles
          files={files.map((block) => ({
            fileId: block.content,
            filename: block.filename ?? "file",
            mimeType: block.mime_type ?? "",
          }))}
        />
      )}
    </div>
  );
}
