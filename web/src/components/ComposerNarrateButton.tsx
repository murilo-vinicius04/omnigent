import { Volume2Icon, VolumeXIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useSpeechPlaybackStore } from "@/lib/speechPlayback";
import { UNMUTE_VOLUME, useVolumeStore } from "@/lib/sessionNarrationVolume";
import { useChatStore } from "@/store/chatStore";

export interface ComposerNarrateButtonProps {
  disabled?: boolean;
}

/**
 * Mutes and unmutes narration for the current session, beside its volume.
 *
 * Muting is how a reader turns narration off: one control for "read to me" and
 * "how loud", rather than a switch, a per-session toggle and a level that could
 * each silence the same summary on their own.
 */
export function ComposerNarrateButton({ disabled }: ComposerNarrateButtonProps) {
  const sessionId = useChatStore((s) => s.conversationId);
  const levels = useVolumeStore((s) => s.levels);
  const setLevel = useVolumeStore((s) => s.set);
  const stop = useSpeechPlaybackStore((s) => s.stop);
  // Subscribing to `levels` is what re-renders this button; the read itself
  // goes through the store so an untouched session resolves to the default.
  void levels;
  const volume = useVolumeStore.getState().get(sessionId);
  const muted = volume <= 0;

  const toggle = () => {
    if (!sessionId) return;
    if (muted) {
      setLevel(sessionId, lastAudible.get(sessionId) ?? UNMUTE_VOLUME);
      return;
    }
    lastAudible.set(sessionId, volume);
    setLevel(sessionId, 0);
    // Muting should silence what is playing now, not just the next summary.
    stop();
  };

  return (
    <Button
      type="button"
      size="icon"
      variant="ghost"
      className="size-9 md:size-8"
      disabled={disabled || !sessionId}
      onClick={toggle}
      aria-pressed={!muted}
      title={
        muted
          ? "Narration muted for this session"
          : "Narration on for this session — summaries are read aloud"
      }
      data-testid="composer-narrate-toggle"
      componentId="chat.composer.narrate"
    >
      {muted ? (
        <VolumeXIcon className="size-4 opacity-60" data-icon-size="16" />
      ) : (
        <Volume2Icon className="size-4" data-icon-size="16" />
      )}
      <span className="sr-only">
        {muted ? "Unmute narration for this session" : "Mute narration for this session"}
      </span>
    </Button>
  );
}

/** The level to restore on unmute, per session, for this page load. */
const lastAudible = new Map<string, number>();
