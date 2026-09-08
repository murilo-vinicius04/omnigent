import { useCallback, useId, useState } from "react";
import { Switch } from "@/components/ui/switch";
import { getSpeechEngine, useSpeechPlaybackStore } from "@/lib/speechPlayback";
import {
  readSpokenSummaryPlayback,
  writeSpokenSummaryPlayback,
} from "@/lib/spokenSummaryPlaybackPreferences";

export function SpokenSummaryPlaybackControl() {
  const [value, setValue] = useState(() => readSpokenSummaryPlayback());
  const labelId = useId();
  const descriptionId = useId();
  const isSupported = getSpeechEngine().isSupported();
  const stopPlayback = useSpeechPlaybackStore((s) => s.stop);

  const toggle = useCallback(
    (next: boolean) => {
      setValue(next);
      writeSpokenSummaryPlayback(next);
      if (!next) {
        stopPlayback();
      }
    },
    [stopPlayback],
  );

  return (
    <div className="flex items-start justify-between gap-6">
      <div className="flex min-w-0 flex-1 flex-col">
        <span id={labelId} className="text-ui font-medium">
          Speak responses
        </span>
        <div id={descriptionId} className="text-ui text-muted-foreground">
          <span>Read aloud generated spoken summaries using browser speech synthesis.</span>
          {!isSupported && (
            <p className="mt-1 text-xs text-destructive">
              Speech synthesis is not supported in this browser.
            </p>
          )}
        </div>
      </div>
      <Switch
        aria-labelledby={labelId}
        aria-describedby={descriptionId}
        checked={value && isSupported}
        onCheckedChange={toggle}
        disabled={!isSupported}
        data-testid="spoken-summary-playback-toggle"
        className="mt-0.5 shrink-0"
        componentId="settings.general.spoken_summary_playback"
      />
    </div>
  );
}
