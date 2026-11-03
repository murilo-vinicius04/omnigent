import { useQuery } from "@tanstack/react-query";
import { MessagesSquareIcon } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { authenticatedFetch } from "@/lib/identity";
import { Link } from "@/lib/routing";

interface Activity {
  notes: {
    id: string;
    created_at: number;
    from_user: string;
    from_title: string;
    to_title: string;
    topic: string;
    text: string;
    status: string;
  }[];
  shared_sessions: {
    id: string;
    title: string;
    project: string;
    owner: string;
    status: string;
    task_summary: string;
  }[];
}

export function AgentActivity() {
  const [open, setOpen] = useState(false);
  const { data, error, isLoading } = useQuery({
    queryKey: ["duet-activity"],
    enabled: open,
    refetchInterval: open ? 15000 : false,
    queryFn: async (): Promise<Activity> => {
      const response = await authenticatedFetch("/v1/duet/activity");
      if (!response.ok) throw new Error(`Unable to load activity (${response.status})`);
      return response.json() as Promise<Activity>;
    },
  });
  return (
    <>
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label="Agent activity"
        onClick={() => setOpen(true)}
      >
        <MessagesSquareIcon className="ui-icon" />
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Agent activity</DialogTitle>
            <DialogDescription>
              Messages and shared sessions you have access to. Reading this view does not consume
              agent inboxes.
            </DialogDescription>
          </DialogHeader>
          {isLoading && <p>Loading activity…</p>}
          {error && (
            <p role="alert">{error instanceof Error ? error.message : "Unable to load activity"}</p>
          )}
          {data && (
            <div className="space-y-5 text-sm">
              <section>
                <h3 className="font-semibold">Shared sessions</h3>
                {data.shared_sessions.length === 0 ? (
                  <p className="text-muted-foreground">No shared sessions found.</p>
                ) : (
                  <ul className="space-y-1">
                    {data.shared_sessions.map((s) => (
                      <li key={s.id}>
                        <Link
                          to={`/c/${s.id}`}
                          onClick={() => setOpen(false)}
                          className="underline"
                        >
                          {s.title || "Untitled"}
                        </Link>
                        <span className="text-muted-foreground">
                          {" "}
                          · {s.owner}
                          {s.project ? ` / ${s.project}` : ""}
                          {s.status ? ` · ${s.status}` : ""}
                        </span>
                        {s.task_summary && (
                          <p className="text-muted-foreground">{s.task_summary}</p>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </section>
              <section>
                <h3 className="font-semibold">Agent messages</h3>
                {data.notes.length === 0 ? (
                  <p className="text-muted-foreground">No visible messages yet.</p>
                ) : (
                  <ul className="space-y-3">
                    {data.notes.map((n) => (
                      <li key={n.id} className="rounded border p-3">
                        <p className="font-medium">
                          {n.from_title || n.from_user} → {n.to_title || "Untitled"}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {new Date(n.created_at * 1000).toLocaleString()} ·{" "}
                          {n.status === "read" ? "Taken from inbox" : "Queued"}
                        </p>
                        <p className="mt-1 font-medium">{n.topic}</p>
                        <p className="whitespace-pre-wrap">{n.text}</p>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
