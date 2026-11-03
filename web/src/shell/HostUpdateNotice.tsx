import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { useHosts } from "@/hooks/useHosts";
import { useHostUpdate } from "@/hooks/useHostUpdate";
import { hostUpdateViews, type HostUpdateView } from "@/lib/hostUpdate";

const DISMISSED_KEY = "omnigent.hostUpdate.dismissed";

function readDismissed(): string[] {
  try {
    const parsed: unknown = JSON.parse(sessionStorage.getItem(DISMISSED_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === "string") : [];
  } catch {
    return [];
  }
}

/** A notice about a machine can be set aside, except while an update is in progress. */
function isDismissible(view: HostUpdateView): boolean {
  return view.force !== null || Boolean(view.command || view.host.update?.same_machine);
}

const toastId = (hostId: string) => `host-update:${hostId}`;

/**
 * Tells the user, with a toast per machine, when it runs older code than the server and
 * offers the way to bring it up to date. Renders nothing itself.
 */
export function HostUpdateNotice() {
  const { data: hosts = [] } = useHosts({ refetchOnFocus: true });
  const { start, startedAt, error } = useHostUpdate();
  const [dismissed, setDismissed] = useState<string[]>(readDismissed);
  const shown = useRef(new Set<string>());

  const views = useMemo(
    () =>
      hostUpdateViews(hosts, startedAt, window.location.origin).filter(
        (view) => !(isDismissible(view) && dismissed.includes(view.host.host_id)),
      ),
    [hosts, startedAt, dismissed],
  );

  useEffect(() => {
    function dismiss(hostId: string) {
      const next = [...dismissed, hostId];
      setDismissed(next);
      try {
        sessionStorage.setItem(DISMISSED_KEY, JSON.stringify(next));
      } catch {
        // Storage can be unavailable; the notice then just comes back on reload.
      }
    }

    const current = new Set<string>();
    for (const view of views) {
      const { host, message, force, command } = view;
      const id = toastId(host.host_id);
      current.add(id);
      if (!isDismissible(view)) {
        toast.loading(message, { id, duration: Number.POSITIVE_INFINITY });
        continue;
      }
      const action =
        force !== null
          ? {
              label: force ? "Update anyway" : "Update",
              onClick: (event: { preventDefault: () => void }) => {
                event.preventDefault();
                if (
                  force &&
                  !window.confirm(`Update ${host.name} now? Its running sessions will end.`)
                )
                  return;
                void start(host.host_id, force);
              },
            }
          : command
            ? {
                label: "Copy command",
                onClick: (event: { preventDefault: () => void }) => {
                  event.preventDefault();
                  void navigator.clipboard.writeText(command);
                },
              }
            : undefined;
      toast.warning(message, {
        id,
        duration: Number.POSITIVE_INFINITY,
        action,
        cancel: { label: "Dismiss", onClick: () => dismiss(host.host_id) },
      });
    }
    for (const id of shown.current) if (!current.has(id)) toast.dismiss(id);
    shown.current = current;
  }, [views, dismissed, start]);

  useEffect(() => {
    if (error) toast.error(error);
  }, [error]);

  useEffect(
    () => () => {
      for (const id of shown.current) toast.dismiss(id);
    },
    [],
  );

  return null;
}
