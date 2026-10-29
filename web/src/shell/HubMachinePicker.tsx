import { useHosts } from "@/hooks/useHosts";
import { Input } from "@/components/ui/input";

export interface HubMachine {
  host_id: string;
  workspace: string;
}

export function HubMachineName({ hostId }: { hostId?: string }) {
  const { data: hosts = [] } = useHosts({ enabled: !!hostId });
  if (!hostId) return null;
  const host = hosts.find((h) => h.host_id === hostId);
  return (
    <span title={hostId}>
      {host?.name ?? hostId.slice(0, 8)}
      {host?.status === "offline" ? " (offline)" : ""}
    </span>
  );
}

export function HubMachinePicker({
  value,
  onChange,
  allowInherited = false,
}: {
  value: HubMachine;
  onChange: (value: HubMachine) => void;
  allowInherited?: boolean;
}) {
  const { data: hosts = [], isLoading, error } = useHosts({ refetchOnFocus: true });
  return (
    <div className="flex flex-col gap-2">
      <label className="flex flex-col gap-1 text-xs">
        Machine
        <select
          className="h-8 rounded-md border border-input bg-background px-2 text-sm"
          aria-label="Machine"
          value={value.host_id}
          onChange={(e) => onChange({ host_id: e.target.value, workspace: "" })}
        >
          <option value="">{allowInherited ? "Project machine" : "Choose machine"}</option>
          {hosts.map((h) => (
            <option key={h.host_id} value={h.host_id} disabled={h.status !== "online"}>
              {h.name}
              {h.status !== "online" ? " (offline)" : ""}
            </option>
          ))}
        </select>
      </label>
      {value.host_id && (
        <label className="flex flex-col gap-1 text-xs">
          Folder on this machine
          <Input
            aria-label="Folder on this machine"
            placeholder="/home/user/project"
            value={value.workspace}
            onChange={(e) => onChange({ ...value, workspace: e.target.value })}
          />
        </label>
      )}
      {isLoading && <p className="text-xs text-muted-foreground">Reading machines…</p>}
      {error && <p className="text-xs text-destructive">Couldn't read machines.</p>}
    </div>
  );
}
