import { RadioIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { describeHubTool, useVoiceHubStore } from "@/lib/voiceHub";

/**
 * Turns the voice hub on and off: one call across every project, from anywhere
 * in the app rather than from one chat's composer.
 */
export function VoiceHubButton({
  className,
  iconClassName,
}: {
  className?: string;
  iconClassName?: string;
}) {
  const active = useVoiceHubStore((s) => s.active);
  const connecting = useVoiceHubStore((s) => s.connecting);
  const label = active ? "Stop the voice hub" : "Voice hub: talk to every project";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          aria-label={label}
          aria-pressed={active}
          data-testid="voice-hub-button"
          disabled={connecting}
          onClick={() => {
            const hub = useVoiceHubStore.getState();
            if (hub.active) hub.stop();
            else void hub.start();
          }}
          className={cn(className, active && "text-sky-600 dark:text-sky-400")}
        >
          <RadioIcon className={cn(iconClassName, (active || connecting) && "animate-pulse")} />
        </Button>
      </TooltipTrigger>
      <TooltipContent side="bottom">{label}</TooltipContent>
    </Tooltip>
  );
}

/**
 * What the hub is doing, while it is on: listening, or which session it is
 * reading, sending to or checking. Mounted once, at the bottom of the screen.
 */
export function VoiceHubStatus() {
  const active = useVoiceHubStore((s) => s.active);
  const connecting = useVoiceHubStore((s) => s.connecting);
  const notice = useVoiceHubStore((s) => s.notice);
  const tone = useVoiceHubStore((s) => s.noticeTone);
  const activity = useVoiceHubStore((s) => s.activity);
  if (!active && !connecting && !notice) return null;
  let line: string;
  if (connecting) line = "Starting the voice hub…";
  else if (activity?.state === "running")
    line = `${describeHubTool(activity.tool, activity.project)}…`;
  else if (activity) line = `${describeHubTool(activity.tool, activity.project)} — done`;
  else line = notice ?? "Listening — every project";
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="voice-hub-status"
      className={cn(
        "pointer-events-auto fixed bottom-4 left-1/2 z-50 flex max-w-[min(32rem,calc(100vw-2rem))] -translate-x-1/2 items-center gap-2 rounded-full border bg-background/95 px-3 py-1.5 text-xs shadow-md",
        tone === "warn" && !activity && "border-amber-500/60",
      )}
    >
      <RadioIcon
        className={cn(
          "size-3.5 shrink-0",
          active ? "text-sky-600 dark:text-sky-400" : "text-muted-foreground",
        )}
      />
      <span className="truncate">{line}</span>
      {active ? (
        <button
          type="button"
          className="ml-1 shrink-0 rounded-full px-2 py-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
          onClick={() => useVoiceHubStore.getState().stop()}
        >
          Stop
        </button>
      ) : (
        <button
          type="button"
          className="ml-1 shrink-0 rounded-full px-2 py-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
          onClick={() => useVoiceHubStore.setState({ notice: null })}
        >
          Dismiss
        </button>
      )}
    </div>
  );
}
