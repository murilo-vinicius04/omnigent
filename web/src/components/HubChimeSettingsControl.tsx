import { Volume2Icon } from "lucide-react";
import { useCallback, useId } from "react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useHubChimeStore } from "@/lib/hubChime";

/**
 * Settings control for the Hub notification sound / chime.
 *
 * Allows enabling/disabling the chime, adjusting its volume, and previewing
 * the sound.
 */
export function HubChimeSettingsControl() {
  const enabled = useHubChimeStore((s) => s.enabled);
  const volume = useHubChimeStore((s) => s.volume);
  const setEnabled = useHubChimeStore((s) => s.setEnabled);
  const setVolume = useHubChimeStore((s) => s.setVolume);
  const playChime = useHubChimeStore((s) => s.playChime);

  const labelId = useId();
  const descriptionId = useId();

  const onToggle = useCallback(
    (next: boolean) => {
      setEnabled(next);
      if (next) {
        playChime("preview");
      }
    },
    [setEnabled, playChime],
  );

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-start justify-between gap-6">
        <div className="flex min-w-0 flex-1 flex-col">
          <span id={labelId} className="text-ui font-medium">
            Hub notification chime
          </span>
          <span id={descriptionId} className="text-ui text-muted-foreground">
            Play a subtle, discrete chime when a Hub task finishes or requires your response, without
            voice narration.
          </span>
        </div>
        <Switch
          aria-labelledby={labelId}
          aria-describedby={descriptionId}
          checked={enabled}
          onCheckedChange={onToggle}
          data-testid="hub-chime-settings-toggle"
          className="mt-0.5 shrink-0"
          componentId="settings.general.hub_chime"
        />
      </div>

      {enabled && (
        <div className="flex flex-wrap items-center gap-4 pl-1 pt-1 text-xs text-muted-foreground">
          <div className="flex items-center gap-2">
            <Volume2Icon className="size-4" aria-hidden />
            <span>Volume: {Math.round(volume * 100)}%</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={volume}
              onChange={(e) => setVolume(Number.parseFloat(e.target.value))}
              className="h-1 w-24 cursor-pointer accent-current"
              aria-label="Hub notification chime volume"
              data-testid="hub-chime-volume-slider"
            />
          </div>
          <Button
            type="button"
            variant="outline"
            size="xs"
            onClick={() => playChime("preview")}
            data-testid="hub-chime-test-sound"
          >
            Test sound
          </Button>
        </div>
      )}
    </div>
  );
}
