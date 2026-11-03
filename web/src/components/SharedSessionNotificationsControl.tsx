import { useId, useState } from "react";
import { Switch } from "@/components/ui/switch";
import {
  readNotifySharedSessions,
  writeNotifySharedSessions,
} from "@/lib/sharedSessionNotifications";

/**
 * Settings control for the shared-sessions notification scope.
 *
 * Lets the viewer opt shared sessions back into OS turn-end notifications
 * and the unread badge. Defaults to owned sessions only.
 */
export function SharedSessionNotificationsControl() {
  const [includeShared, setIncludeShared] = useState<boolean>(() => readNotifySharedSessions());

  const labelId = useId();
  const descriptionId = useId();

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-start justify-between gap-6">
        <div className="flex min-w-0 flex-1 flex-col">
          <span id={labelId} className="text-ui font-medium">
            Notify about shared sessions
          </span>
          <span id={descriptionId} className="text-ui text-muted-foreground">
            Include sessions other users shared with you in turn-end notifications and the unread
            badge. Off by default, so another lane&apos;s session finishing doesn&apos;t beep here
            when you can&apos;t act on it.
          </span>
        </div>
        <Switch
          aria-labelledby={labelId}
          aria-describedby={descriptionId}
          checked={includeShared}
          onCheckedChange={(next) => {
            setIncludeShared(next);
            writeNotifySharedSessions(next);
          }}
          data-testid="shared-sessions-notify-toggle"
          className="mt-0.5 shrink-0"
          componentId="settings.general.shared_session_notifications"
        />
      </div>
    </div>
  );
}
