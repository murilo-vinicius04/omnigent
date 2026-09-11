import { Volume1Icon } from "lucide-react";
import { useChatStore } from "@/store/chatStore";
import { useVolumeStore } from "@/lib/sessionNarrationVolume";
import { useNarrationStore } from "@/lib/sessionNarrationPreferences";

/**
 * Sets how loud narration is for this session, beside the narrate toggle.
 *
 * A slider rather than a mute button: the useful range here is "quiet enough to
 * keep talking over" rather than on/off, which the toggle already covers. The
 * level applies to audio that starts after it, and to the recording playing
 * right now, so dragging it is audible immediately rather than next summary.
 */
export function ComposerNarrateVolume({ disabled }: { disabled?: boolean }) {
  const sessionId = useChatStore((s) => s.conversationId);
  const levels = useVolumeStore((s) => s.levels);
  const setLevel = useVolumeStore((s) => s.set);
  const overrides = useNarrationStore((s) => s.overrides);
  // Both reads go through their stores so this re-renders when either changes.
  void levels;
  void overrides;
  const value = useVolumeStore.getState().get(sessionId);
  const narrating = useNarrationStore.getState().isEnabled(sessionId);

  const onChange = (next: number) => {
    if (!sessionId) return;
    setLevel(sessionId, next);
    // Anything mid-sentence should follow the slider, not wait for the next one.
    for (const el of document.querySelectorAll("audio[data-summary-audio]")) {
      if (el instanceof HTMLAudioElement) el.volume = next;
    }
  };

  return (
    <label
      className="flex items-center gap-1.5 px-1"
      title={`Narration volume for this session: ${Math.round(value * 100)}%`}
      data-testid="composer-narrate-volume"
    >
      <Volume1Icon className="size-4 opacity-60" data-icon-size="16" aria-hidden />
      <input
        type="range"
        min={0}
        max={1}
        step={0.05}
        value={value}
        disabled={disabled || !sessionId || !narrating}
        onChange={(e) => onChange(Number.parseFloat(e.target.value))}
        className="h-1 w-16 cursor-pointer accent-current disabled:opacity-40"
        aria-label="Narration volume for this session"
      />
    </label>
  );
}
