import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useHosts } from "@/hooks/useHosts";
import { authenticatedFetch } from "@/lib/identity";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";

export function HubMachinesDialog() {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const { data: hosts = [] } = useHosts({ enabled: open, refetchOnFocus: true });
  const client = useQueryClient();
  async function change(hostId: string, method: string, body?: object, suffix = "") {
    setBusy(true);
    setError("");
    try {
      const response = await authenticatedFetch(
        `/v1/hosts/${encodeURIComponent(hostId)}${suffix}`,
        {
          method,
          headers: { "Content-Type": "application/json" },
          ...(body ? { body: JSON.stringify(body) } : {}),
        },
      );
      if (!response.ok) throw new Error(`Machine update failed (${response.status}).`);
      await client.invalidateQueries({ queryKey: ["hosts"] });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        Machines
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Machines</DialogTitle>
            <DialogDescription>
              Connect another computer with Omnigent installed using its own identity and login. Run
              omni host --server followed by this server's reachable URL on that computer.
            </DialogDescription>
          </DialogHeader>
          {hosts.map((host) => (
            <form
              key={`${host.host_id}:${host.name}`}
              className="flex flex-wrap items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                const data = new FormData(e.currentTarget);
                void change(host.host_id, "PATCH", { name: String(data.get("name")) });
              }}
            >
              <Input
                name="name"
                aria-label={`Name for ${host.name}`}
                defaultValue={host.name}
                maxLength={64}
                className="min-w-0 flex-1"
                required
              />
              <span className="text-xs text-muted-foreground">{host.status}</span>
              <Button type="submit" size="sm" disabled={busy}>
                Rename
              </Button>
              {host.credential_expires_at && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={() => {
                    if (window.confirm(`Revoke ${host.name}'s host credential and disconnect it?`))
                      void change(host.host_id, "DELETE", undefined, "/credential");
                  }}
                >
                  Revoke
                </Button>
              )}
              {host.status === "offline" && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={() => {
                    if (
                      window.confirm(
                        `Remove ${host.name}? Its chats remain, without this machine binding.`,
                      )
                    )
                      void change(host.host_id, "DELETE");
                  }}
                >
                  Remove
                </Button>
              )}
            </form>
          ))}
          {!hosts.length && <p className="text-sm text-muted-foreground">No machines connected.</p>}
          {error && (
            <p role="alert" className="text-xs text-destructive">
              {error}
            </p>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
