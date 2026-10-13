import { SquareIcon, Volume2Icon } from "lucide-react";
import { useSpeechPlaybackStore } from "@/lib/speechPlayback";
import { currentVoiceBackend, useVoiceBackendStore } from "@/lib/sessionVoiceBackend";
import { useChatStore } from "@/store/chatStore";

export interface ReadAloudButtonProps {
  /** The words to read: a speak-only summary, the message's own text. */
  summary: { text: string; lang: string; audioFileId?: string };
  /** The turn it belongs to, so the control shows when that turn is being read. */
  id: string;
}

/**
 * Read a message aloud again, or stop it: the play control a project's reply
 * has under its summary, for a message that is narrated but shown as written
 * (the hub's). Plays through the same queue as autoplay, so it stops whatever
 * is being read rather than talking over it.
 */
export function ReadAloudButton({ summary, id }: ReadAloudButtonProps) {
  const speakingItemId = useSpeechPlaybackStore((s) => s.speakingItemId);
  const stop = useSpeechPlaybackStore((s) => s.stop);
  const speakNow = useSpeechPlaybackStore((s) => s.speakNow);
  const sessionId = useChatStore((s) => s.conversationId);
  // Subscribing re-renders this when the session's voice is switched.
  void useVoiceBackendStore((s) => s.choices);
  const audioUrl =
    summary.audioFileId && sessionId
      ? `/v1/sessions/${encodeURIComponent(sessionId)}/resources/files/${encodeURIComponent(summary.audioFileId)}/content`
      : undefined;
  const liveVoice = currentVoiceBackend(sessionId) === "live" && Boolean(summary.text.trim());
  // Nothing to play: no live voice to read it and no recording of it.
  if (!id || (!liveVoice && !audioUrl)) return null;
  const isSpeaking = speakingItemId === id;

  return (
    <div className="mt-1.5 flex items-center gap-3 text-xs text-muted-foreground">
      <button
        type="button"
        onClick={() =>
          isSpeaking ? stop() : speakNow(id, summary.text, summary.lang, audioUrl, sessionId)
        }
        data-testid={isSpeaking ? "read-aloud-stop" : "read-aloud-play"}
        aria-label={isSpeaking ? "Stop reading aloud" : "Read aloud"}
        title={isSpeaking ? "Stop reading aloud" : "Read aloud"}
        className="inline-flex items-center gap-1 rounded hover:text-foreground focus-visible:outline-none"
      >
        {isSpeaking ? (
          <>
            <SquareIcon className="size-3 fill-current text-primary" aria-hidden="true" />
            <span className="font-medium text-primary">Stop</span>
          </>
        ) : (
          <Volume2Icon className="size-3.5" aria-hidden="true" />
        )}
      </button>
    </div>
  );
}
