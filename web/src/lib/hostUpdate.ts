import type { Host } from "@/hooks/useHosts";

/**
 * One-time command for a host too old to update itself: fetches the server's update script
 * with the saved login and runs it. Restarting is left to the person, since it ends sessions.
 */
export function syncCommand(origin: string): string {
  const token =
    `python3 -c "import json,os;d=os.environ.get('OMNIGENT_DATA_DIR') or os.path.expanduser('~/.omnigent');` +
    `print(json.load(open(d+'/auth_tokens.json'))['${origin}']['token'])"`;
  return (
    `curl -fsS -H "Authorization: Bearer $(${token})" ${origin}/v1/code/update-script ` +
    `| python3 - --server ${origin}`
  );
}

export interface HostUpdateView {
  host: Host;
  /** Command to run by hand on the machine, for hosts that cannot update themselves. */
  command?: string;
  /** What to tell the user about this host. */
  message: string;
  /** Offer "Update" (`false`), "Update anyway" (`true`), or no button (`null`). */
  force: boolean | null;
}

/**
 * Work out which online hosts need an update notice and what it says.
 *
 * @param hosts Hosts from `/v1/hosts`.
 * @param startedAt Epoch ms at which the user pressed Update per host id; a result older
 *   than that press is not about the current attempt.
 */
export function hostUpdateViews(
  hosts: Host[],
  startedAt: Record<string, number>,
  origin = "",
): HostUpdateView[] {
  const views: HostUpdateView[] = [];
  for (const host of hosts) {
    const state = host.update?.state;
    if (host.status !== "online" || (state !== "outdated" && state !== "unknown")) continue;
    if (state === "unknown" && host.update?.same_machine) {
      views.push({
        host,
        message: `${host.name} runs on this machine but still has an old build in memory; restart its host when idle to load the new code.`,
        force: null,
      });
      continue;
    }
    if (state === "unknown") {
      // Predates version reporting, so it cannot handle an update request either.
      views.push({
        host,
        message: `${host.name} runs a build too old to update itself; copy the command and run it on it once.`,
        command: origin ? syncCommand(origin) : undefined,
        force: null,
      });
      continue;
    }
    const mine = host.code?.label ?? "an unknown version";
    const server = host.update?.server_label ?? "the server's";
    const last = host.update?.last;
    const asked = startedAt[host.host_id];
    const fresh =
      asked !== undefined && last !== null && last !== undefined && last.at * 1000 >= asked;
    if (fresh && last.status === "busy") {
      views.push({ host, message: `${host.name}: ${last.message}.`, force: true });
    } else if (fresh && last.status === "failed") {
      views.push({ host, message: `${host.name}: update failed — ${last.message}`, force: false });
    } else if (asked !== undefined) {
      views.push({ host, message: `Updating ${host.name}…`, force: null });
    } else {
      views.push({
        host,
        message: `${host.name} runs older code (${mine}; server ${server}).`,
        force: false,
      });
    }
  }
  return views;
}
