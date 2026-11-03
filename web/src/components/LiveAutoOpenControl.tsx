import { useCallback, useId, useState } from "react";
import { Switch } from "@/components/ui/switch";
import { readLiveAutoOpen, writeLiveAutoOpen } from "@/lib/speechPlayback";

/**
 * Settings control for opening a live voice call when a narration starts.
 *
 * Off by default: an auto-opened call bills by the minute and takes the
 * microphone, so it only happens when the reader turns this on -- and even
 * then only when the microphone permission is already granted.
 */
export function LiveAutoOpenControl() {
  const [enabled, setEnabled] = useState(() => readLiveAutoOpen());

  const labelId = useId();
  const descriptionId = useId();

  const onToggle = useCallback((next: boolean) => {
    writeLiveAutoOpen(next);
    setEnabled(next);
  }, []);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-start justify-between gap-6">
        <div className="flex min-w-0 flex-1 flex-col">
          <span id={labelId} className="text-ui font-medium">
            Open a live call when narration starts
          </span>
          <span id={descriptionId} className="text-ui text-muted-foreground">
            When a message is read aloud, open a live voice call automatically so you can reply
            hands-free. Only fires when the microphone permission is already granted.
          </span>
        </div>
        <Switch
          aria-labelledby={labelId}
          aria-describedby={descriptionId}
          checked={enabled}
          onCheckedChange={onToggle}
          data-testid="live-auto-open-toggle"
          className="mt-0.5 shrink-0"
          componentId="settings.general.live_voice_auto_open"
        />
      </div>
    </div>
  );
}
