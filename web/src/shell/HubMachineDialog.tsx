import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { HubMachinePicker, type HubMachine } from "./HubMachinePicker";

export function HubMachineDialog({
  title,
  initial,
  onClose,
  onSave,
  optionalFolder = false,
}: {
  title: string;
  initial?: HubMachine;
  onClose: () => void;
  onSave: (machine: HubMachine) => Promise<void>;
  optionalFolder?: boolean;
}) {
  const [value, setValue] = useState(initial ?? { host_id: "", workspace: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {optionalFolder
              ? "Choose the machine for this shell. An empty folder uses its home directory."
              : "Future lanes start here. Existing chats keep their machines and folders."}
          </DialogDescription>
        </DialogHeader>
        <HubMachinePicker value={value} onChange={setValue} />
        {error && (
          <p role="alert" className="text-xs text-destructive">
            {error}
          </p>
        )}
        <DialogFooter>
          <Button variant="ghost" disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={
              busy ||
              !value.host_id ||
              (!optionalFolder && !value.workspace.startsWith("/")) ||
              (!!value.workspace && !value.workspace.startsWith("/"))
            }
            onClick={async () => {
              setBusy(true);
              setError("");
              try {
                await onSave(value);
                onClose();
              } catch (e) {
                setError(e instanceof Error ? e.message : String(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Opening…" : "Choose machine"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
