import { SparklesIcon } from "lucide-react";
import { useChatStore } from "@/store/chatStore";
import { useVoiceBackendStore } from "@/lib/sessionVoiceBackend";

/**
 * Chooses which voice reads this session aloud, beside the volume.
 *
 * Two voices with a real trade-off, so the reader picks rather than us. The
 * local one runs on their own GPU for nothing and takes tens of seconds to
 * start; the live one begins speaking in about a second and sounds far
 * better, but bills roughly five cents a minute of wall clock.
 *
 * Off by default, and the label says what it costs. Turning on a paid voice
 * is the reader's decision to spend, and it should never be made quietly on
 * their behalf.
 */
export function ComposerVoicePicker({ disabled }: { disabled?: boolean }) {
  const sessionId = useChatStore((s) => s.conversationId);
  const choices = useVoiceBackendStore((s) => s.choices);
  const setBackend = useVoiceBackendStore((s) => s.set);
  // The read goes through the store so this re-renders when the choice changes.
  void choices;
  const backend = useVoiceBackendStore.getState().get(sessionId);
  const live = backend === "live";

  return (
    <button
      type="button"
      disabled={disabled || !sessionId}
      onClick={() => sessionId && setBackend(sessionId, live ? "local" : "live")}
      title={
        live
          ? "Live voice: starts in about a second, billed about $0.05 a minute. Click for the free local voice."
          : "Local voice: free, but takes tens of seconds to start. Click for the live voice (about $0.05 a minute)."
      }
      aria-pressed={live}
      aria-label="Voice for this session"
      data-testid="composer-voice-picker"
      className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs disabled:opacity-40"
    >
      <SparklesIcon
        className={live ? "size-4" : "size-4 opacity-60"}
        data-icon-size="16"
        aria-hidden
      />
      <span className={live ? "" : "opacity-60"}>{live ? "Live" : "Local"}</span>
    </button>
  );
}
