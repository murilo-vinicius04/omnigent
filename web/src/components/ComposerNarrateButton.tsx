import { Volume2Icon, VolumeXIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { getSpeechEngine, useSpeechPlaybackStore } from "@/lib/speechPlayback";
import { useNarrationStore } from "@/lib/sessionNarrationPreferences";
import { useChatStore } from "@/store/chatStore";

export interface ComposerNarrateButtonProps {
  disabled?: boolean;
}

/**
 * Turns read-aloud on and off for the current session, next to the mic.
 *
 * Narration belongs with the other in-conversation controls rather than in
 * Settings: whether you want to be read to is a property of the moment, not of
 * the device, and it changes far more often than a setting does.
 */
export function ComposerNarrateButton({ disabled }: ComposerNarrateButtonProps) {
  const sessionId = useChatStore((s) => s.conversationId);
  const overrides = useNarrationStore((s) => s.overrides);
  const setEnabled = useNarrationStore((s) => s.setEnabled);
  const stop = useSpeechPlaybackStore((s) => s.stop);
  // Subscribing to `overrides` above is what re-renders this button; the read
  // itself has to go through the store so an untouched session still resolves
  // to the device default.
  void overrides;
  const enabled = useNarrationStore.getState().isEnabled(sessionId);
  const supported = getSpeechEngine().isSupported();

  const toggle = () => {
    if (!sessionId) return;
    const next = !enabled;
    setEnabled(sessionId, next);
    // Turning it off should silence what is playing right now, not just the
    // next summary.
    if (!next) stop();
  };

  return (
    <Button
      type="button"
      size="icon"
      variant="ghost"
      className="size-9 md:size-8"
      disabled={disabled || !sessionId}
      onClick={toggle}
      aria-pressed={enabled}
      title={
        enabled
          ? "Narration on for this session — summaries are read aloud"
          : "Narration off for this session"
      }
      data-testid="composer-narrate-toggle"
      componentId="chat.composer.narrate"
    >
      {enabled ? (
        <Volume2Icon className="size-4" data-icon-size="16" />
      ) : (
        <VolumeXIcon className="size-4 opacity-60" data-icon-size="16" />
      )}
      <span className="sr-only">
        {enabled ? "Turn narration off for this session" : "Turn narration on for this session"}
      </span>
      {!supported && <span className="sr-only">Speech synthesis unavailable</span>}
    </Button>
  );
}
