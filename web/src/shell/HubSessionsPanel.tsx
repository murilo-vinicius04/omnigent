import { useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRightIcon, ShieldAlertIcon } from "lucide-react";
import { Link } from "@/lib/routing";
import { Button } from "@/components/ui/button";
import { RunningDot } from "@/components/RunningDot";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  EFFORT_SELECT_NONE,
  MODEL_SELECT_DEFAULT,
  RoutingModelSelect,
} from "@/components/HarnessConfigControls";
import { listHubSessions, setHubSessionModel, type HubSessionRow } from "@/lib/hubApi";
import { relativeTime } from "@/lib/relativeTime";
import { cn } from "@/lib/utils";
import { SwitchAgentDialog } from "./SwitchAgentDialog";

/** How often the list is re-read while the panel is open. */
const POLL_MS = 4000;

export const HUB_SESSIONS_QUERY_KEY = ["hub-sessions"] as const;

/**
 * Every session the hub runs, with what each runs on, and the controls to
 * change it: model and effort from that session's own lists, and the switch
 * to another provider.
 *
 * Model and effort picks go through the hub (`POST /v1/hub/model`), so they
 * are checked like a typed or spoken request and show up in the hub chat.
 */
export function HubSessionsPanel() {
  const { data, error, isLoading } = useQuery({
    queryKey: HUB_SESSIONS_QUERY_KEY,
    queryFn: listHubSessions,
    refetchInterval: POLL_MS,
  });

  if (isLoading) {
    return <p className="p-4 text-sm text-muted-foreground">Reading your sessions…</p>;
  }
  if (error) {
    return (
      <p className="p-4 text-sm text-destructive">
        Couldn't read your sessions: {error instanceof Error ? error.message : String(error)}
      </p>
    );
  }
  if (!data || data.length === 0) {
    return (
      <p className="p-4 text-sm text-muted-foreground">
        No sessions in the last week. Start one from the sidebar and it shows up here.
      </p>
    );
  }
  return (
    <div
      className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-2"
      data-testid="hub-sessions"
    >
      {data.map((row) => (
        <HubSessionCard key={row.id} row={row} />
      ))}
    </div>
  );
}

function StatusLine({ row }: { row: HubSessionRow }) {
  if (row.pending_approvals > 0) {
    return (
      <span className="flex items-center gap-1 text-warning">
        <ShieldAlertIcon className="size-3.5" aria-hidden />
        Waiting for{" "}
        {row.pending_approvals === 1 ? "your approval" : `${row.pending_approvals} approvals`}
      </span>
    );
  }
  if (row.working) {
    return (
      <span className="flex min-w-0 items-center gap-1 text-success">
        <RunningDot className="size-3.5 shrink-0" />
        <span className="truncate">{row.now ? `Working · ${row.now}` : "Working"}</span>
      </span>
    );
  }
  return (
    <span className="text-muted-foreground">
      Idle{row.updated_at ? ` · ${relativeTime(row.updated_at * 1000)}` : ""}
    </span>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[4.5rem_minmax(0,1fr)] items-center gap-2">
      <span className="text-xs text-muted-foreground">{label}</span>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

function HubSessionCard({ row }: { row: HubSessionRow }) {
  const queryClient = useQueryClient();
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState<{ text: string; ok: boolean } | null>(null);
  const [switchOpen, setSwitchOpen] = useState(false);
  const { run } = row;

  async function change(pick: { model?: string; effort?: string }): Promise<void> {
    setSaving(true);
    setNote(null);
    try {
      const result = await setHubSessionModel(row.id, pick);
      setNote({ text: result.text, ok: result.changed });
    } catch (err) {
      setNote({
        text: `Couldn't change it: ${err instanceof Error ? err.message : String(err)}`,
        ok: false,
      });
    } finally {
      setSaving(false);
      // The session's own chat reads its snapshot; refresh it too.
      await queryClient.invalidateQueries({ queryKey: ["session", row.id] });
      await queryClient.invalidateQueries({ queryKey: HUB_SESSIONS_QUERY_KEY });
    }
  }

  const modelValue = run.selected || MODEL_SELECT_DEFAULT;
  // A model it runs that its list does not name still shows, as itself.
  const models =
    run.model && !run.selected && !run.requested
      ? [...run.models, { id: run.model, label: run.model, efforts: [] }]
      : run.models;

  return (
    <section
      className="flex flex-col gap-2 rounded-lg border border-border bg-card p-3"
      data-testid={`hub-session-${row.id}`}
    >
      <header className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-medium" title={row.title}>
            {row.name}
          </h3>
          <div className="text-xs">
            <StatusLine row={row} />
          </div>
        </div>
        <Link
          to={`/c/${row.id}`}
          className="flex shrink-0 items-center gap-0.5 text-xs text-muted-foreground hover:text-foreground"
          aria-label={`Open ${row.name}`}
        >
          Open
          <ArrowUpRightIcon className="size-3" aria-hidden />
        </Link>
      </header>

      <Row label="Provider">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-sm">{run.provider || "Unknown"}</span>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 px-2 text-xs"
            disabled={row.working}
            title={row.working ? "Wait for it to finish its turn to switch" : undefined}
            onClick={() => setSwitchOpen(true)}
          >
            Switch…
          </Button>
        </div>
      </Row>

      <Row label="Model">
        {run.models.length > 0 ? (
          <RoutingModelSelect
            value={modelValue}
            onValueChange={(value) =>
              void change({ model: value === MODEL_SELECT_DEFAULT ? "default" : value })
            }
            offerSmartRouting={false}
            testId={`hub-session-model-${row.id}`}
            models={models}
            activeModelId={run.selected || null}
          />
        ) : (
          <span className="text-sm" title="This agent's model is set by its own config">
            {run.model_label || run.model || "Its default"}
          </span>
        )}
      </Row>
      {run.requested && (
        <p className="text-xs text-muted-foreground" data-testid={`hub-session-pending-${row.id}`}>
          Switching to {run.requested_label || run.requested}; it still reports{" "}
          {run.model_label || run.model}.
        </p>
      )}

      {run.efforts.length > 0 && (
        <Row label="Effort">
          <Select
            value={run.effort || EFFORT_SELECT_NONE}
            onValueChange={(value) =>
              void change({ effort: value === EFFORT_SELECT_NONE ? "default" : value })
            }
          >
            <SelectTrigger
              className="h-8 w-full text-sm"
              data-testid={`hub-session-effort-${row.id}`}
              aria-label="Effort"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent position="popper" align="start">
              <SelectItem value={EFFORT_SELECT_NONE}>Default</SelectItem>
              {run.efforts.map((effort) => (
                <SelectItem key={effort} value={effort}>
                  {effort}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Row>
      )}

      {(saving || note) && (
        <p
          className={cn("text-xs", note && !note.ok ? "text-destructive" : "text-muted-foreground")}
          role="status"
        >
          {saving ? "Saving…" : note?.text}
        </p>
      )}

      <SwitchAgentDialog
        sessionId={row.id}
        open={switchOpen}
        onOpenChange={(open) => {
          setSwitchOpen(open);
          if (!open) void queryClient.invalidateQueries({ queryKey: HUB_SESSIONS_QUERY_KEY });
        }}
      />
    </section>
  );
}
