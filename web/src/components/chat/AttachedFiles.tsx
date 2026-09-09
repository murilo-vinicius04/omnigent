import { DownloadIcon, FileTextIcon } from "lucide-react";
import type { AttachedFile } from "@/lib/blocks";
import { SessionImage } from "@/components/SessionImage";
import { useChatStore } from "@/store/chatStore";

export interface AttachedFilesProps {
  files: AttachedFile[];
}

function contentPath(sessionId: string, fileId: string): string {
  return `/v1/sessions/${encodeURIComponent(sessionId)}/resources/files/${encodeURIComponent(fileId)}/content`;
}

/**
 * Render files an assistant turn produced, inline in the transcript.
 *
 * Audio and images play or display in place; anything else gets a download
 * link, so a generated artifact never becomes a path the reader has to go
 * hunting for outside the conversation.
 */
export function AttachedFiles({ files }: AttachedFilesProps) {
  const sessionId = useChatStore((s) => s.conversationId);
  if (files.length === 0) return null;

  return (
    <div className="mt-2 flex flex-col gap-2" data-testid="attached-files">
      {files.map((file) => {
        const href = sessionId ? contentPath(sessionId, file.fileId) : undefined;

        if (file.mimeType.startsWith("audio/")) {
          return (
            <div key={file.fileId} className="flex flex-col gap-1">
              <audio
                controls
                preload="metadata"
                src={href}
                data-testid="attached-audio"
                className="w-full max-w-md"
              />
              <span className="text-xs text-muted-foreground">{file.filename}</span>
            </div>
          );
        }

        if (file.mimeType.startsWith("image/")) {
          return (
            <SessionImage
              key={file.fileId}
              path={href}
              alt={file.filename}
              className="max-w-md rounded-md object-contain"
            />
          );
        }

        return (
          <a
            key={file.fileId}
            href={href}
            download={file.filename}
            data-testid="attached-download"
            className="inline-flex w-fit items-center gap-1.5 rounded-full border border-border bg-muted px-2.5 py-1 text-sm text-muted-foreground hover:text-foreground"
          >
            <FileTextIcon className="size-3.5 shrink-0" />
            <span className="max-w-[240px] truncate">{file.filename}</span>
            <DownloadIcon className="size-3 shrink-0" />
          </a>
        );
      })}
    </div>
  );
}
