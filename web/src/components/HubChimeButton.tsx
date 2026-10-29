import { BellIcon, BellOffIcon } from "lucide-react";
import { useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useHubChimeStore } from "@/lib/hubChime";
import { cn } from "@/lib/utils";

export interface HubChimeButtonProps {
  className?: string;
  iconClassName?: string;
  disabled?: boolean;
  componentId?: string;
}

/**
 * Button to toggle the discrete notification chime for the Hub.
 *
 * Plays a subtle, pleasant chime when a Hub task finishes or requires attention/input,
 * without voice narration.
 */
export function HubChimeButton({
  className,
  iconClassName,
  disabled = false,
  componentId = "hub.chime.toggle",
}: HubChimeButtonProps) {
  const enabled = useHubChimeStore((s) => s.enabled);
  const setEnabled = useHubChimeStore((s) => s.setEnabled);
  const playChime = useHubChimeStore((s) => s.playChime);

  const toggle = useCallback(() => {
    const next = !enabled;
    setEnabled(next);
    if (next) {
      // Play a quick preview chime on enable so the user hears what it sounds like
      playChime("preview");
    }
  }, [enabled, setEnabled, playChime]);

  const label = enabled
    ? "Hub notification chime: on (click to mute)"
    : "Hub notification chime: off (click to enable subtle chime for finished tasks / input needed)";

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            size="icon-xs"
            variant="ghost"
            disabled={disabled}
            aria-label={label}
            aria-pressed={enabled}
            data-testid="hub-chime-button"
            componentId={componentId}
            onClick={toggle}
            className={cn(
              "size-7 shrink-0 text-muted-foreground hover:text-foreground",
              enabled && "text-sky-600 dark:text-sky-400 font-medium",
              className,
            )}
          >
            {enabled ? (
              <BellIcon className={cn("size-3.5", iconClassName)} />
            ) : (
              <BellOffIcon className={cn("size-3.5 opacity-60", iconClassName)} />
            )}
            <span className="sr-only">{label}</span>
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom">{label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
