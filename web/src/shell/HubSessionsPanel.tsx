import { useEffect, useRef, useState, type FormEvent, type ReactNode, type RefObject } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpRightIcon,
  MinusCircleIcon,
  MoreHorizontalIcon,
  PencilIcon,
  PlusIcon,
  ShieldAlertIcon,
} from "lucide-react";
import { Link } from "@/lib/routing";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { RunningDot } from "@/components/RunningDot";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
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
import {
  addHubSession,
  listHubCandidates,
  listHubSessions,
  removeHubSession,
  renameHubSession,
  setHubSessionModel,
  type HubSessionRow,
} from "@/lib/hubApi";
import { relativeTime } from "@/lib/relativeTime";
import { cn } from "@/lib/utils";
import { SwitchAgentDialog } from "./SwitchAgentDialog";

/** How often the list is re-read while the panel is open. */
const POLL_MS = 4000;

export const HUB_SESSIONS_QUERY_KEY = ["hub-sessions"] as const;
const HUB_CANDIDATES_QUERY_KEY = ["hub-candidates"] as const;

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/**
 * Every session the hub runs, with what each runs on, and the controls to
 * change it: model and effort from that session's own lists, and the switch
 * to another provider. Sessions are added, renamed and taken out here too.
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
      <p className="p-4 text-sm text-destructive">Couldn't read your sessions: {message(error)}</p>
    );
  }
  const rows = data ?? [];
  return (
    <div
      className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-2"
      data-testid="hub-sessions"
    >
      <AddSessionBar count={rows.length} />
      {rows.length === 0 ? (
        <p className="px-1 text-sm text-muted-foreground">
          No sessions in the Hub. Add one to run it from here.
        </p>
      ) : (
        rows.map((row) => <HubSessionCard key={row.id} row={row} />)
      )}
    </div>
  );
}

/** How many sessions the hub runs, and the picker that adds another. */
function AddSessionBar({ count }: { count: number }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const candidates = useQuery({
    queryKey: HUB_CANDIDATES_QUERY_KEY,
    queryFn: listHubCandidates,
    enabled: open,
  });

  async function add(sessionId: string): Promise<void> {
    setOpen(false);
    setFailed(null);
    try {
      await addHubSession(sessionId);
    } catch (err) {
      setFailed(`Couldn't add it: ${message(err)}`);
    } finally {
      await queryClient.invalidateQueries({ queryKey: HUB_SESSIONS_QUERY_KEY });
      await queryClient.invalidateQueries({ queryKey: HUB_CANDIDATES_QUERY_KEY });
    }
  }

  let empty = "No other sessions.";
  if (candidates.isLoading) empty = "Reading your sessions…";
  else if (candidates.error) empty = `Couldn't read your sessions: ${message(candidates.error)}`;

  return (
    <div className="flex flex-col gap-1 px-1">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">
          {count === 1 ? "1 session" : `${count} sessions`} in the Hub
        </span>
        <Popover open={open} onOpenChange={setOpen}>
          <PopoverTrigger asChild>
            <Button variant="outline" size="xs" className="text-xs" data-testid="hub-add-session">
              <PlusIcon className="size-3.5" aria-hidden />
              Add session
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-72 p-0">
            <Command>
              <CommandInput placeholder="Search your sessions…" />
              <CommandList>
                <CommandEmpty>{empty}</CommandEmpty>
                <CommandGroup>
                  {(candidates.data ?? []).map((c) => (
                    <CommandItem
                      key={c.id}
                      value={`${c.title} ${c.id}`}
                      onSelect={() => void add(c.id)}
                      data-testid={`hub-candidate-${c.id}`}
                    >
                      <div className="flex min-w-0 flex-col">
                        <span className="truncate">{c.title || "Untitled"}</span>
                        <span className="truncate text-xs text-muted-foreground">
                          {[c.provider, c.updated_at ? relativeTime(c.updated_at * 1000) : ""]
                            .filter(Boolean)
                            .join(" · ")}
                        </span>
                      </div>
                    </CommandItem>
                  ))}
                </CommandGroup>
              </CommandList>
            </Command>
          </PopoverContent>
        </Popover>
      </div>
      {failed && (
        <p className="text-xs text-destructive" role="status">
          {failed}
        </p>
      )}
    </div>
  );
}

/** The name field that replaces a card's title while renaming it. */
function RenameForm({
  row,
  input,
  onDone,
}: {
  row: HubSessionRow;
  /** The name field, so the card can focus it once its menu has closed. */
  input: RefObject<HTMLInputElement | null>;
  /** Called with what the hub said, or `null` when nothing changed. */
  onDone: (said: string | null) => void;
}) {
  const [value, setValue] = useState(row.name);
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    input.current?.focus();
    input.current?.select();
  }, [input]);

  async function save(event: FormEvent): Promise<void> {
    event.preventDefault();
    const name = value.trim();
    if (name === row.name || (!name && !row.nickname)) {
      onDone(null);
      return;
    }
    setSaving(true);
    setFailed(null);
    try {
      onDone(await renameHubSession(row.id, name));
    } catch (err) {
      setFailed(`Couldn't rename it: ${message(err)}`);
      setSaving(false);
    }
  }

  return (
    <form className="flex flex-col gap-1" onSubmit={(event) => void save(event)}>
      <div className="flex items-center gap-1">
        <Input
          ref={input}
          value={value}
          maxLength={60}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") onDone(null);
          }}
          placeholder={row.title || "Name"}
          aria-label={`New name for ${row.name}`}
          className="h-7 text-sm"
          data-testid={`hub-session-rename-${row.id}`}
        />
        <Button type="submit" size="xs" className="text-xs" disabled={saving}>
          Save
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="xs"
          className="text-xs"
          onClick={() => onDone(null)}
        >
          Cancel
        </Button>
      </div>
      {row.title && (
        <p className="text-xs text-muted-foreground">Leave it empty to use “{row.title}”.</p>
      )}
      {failed && <p className="text-xs text-destructive">{failed}</p>}
    </form>
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
  const [renaming, setRenaming] = useState(false);
  // The menu traps focus until it has closed, then hands it back to its
  // button; after Rename it goes to the name field instead.
  const keepFocus = useRef(false);
  const renameInput = useRef<HTMLInputElement>(null);
  const { run } = row;

  async function remove(): Promise<void> {
    setNote(null);
    try {
      await removeHubSession(row.id);
    } catch (err) {
      setNote({ text: `Couldn't take it out: ${message(err)}`, ok: false });
      return;
    }
    queryClient.setQueryData<HubSessionRow[]>(HUB_SESSIONS_QUERY_KEY, (rows) =>
      rows?.filter((r) => r.id !== row.id),
    );
    await queryClient.invalidateQueries({ queryKey: HUB_SESSIONS_QUERY_KEY });
    await queryClient.invalidateQueries({ queryKey: HUB_CANDIDATES_QUERY_KEY });
  }

  async function change(pick: { model?: string; effort?: string }): Promise<void> {
    setSaving(true);
    setNote(null);
    try {
      const result = await setHubSessionModel(row.id, pick);
      setNote({ text: result.text, ok: result.changed });
    } catch (err) {
      setNote({ text: `Couldn't change it: ${message(err)}`, ok: false });
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
        <div className="min-w-0 flex-1">
          {renaming ? (
            <RenameForm
              row={row}
              input={renameInput}
              onDone={(said) => {
                setRenaming(false);
                if (said) {
                  setNote({ text: said, ok: true });
                  void queryClient.invalidateQueries({ queryKey: HUB_SESSIONS_QUERY_KEY });
                }
              }}
            />
          ) : (
            <>
              <h3 className="truncate text-sm font-medium" title={row.title}>
                {row.name}
              </h3>
              {row.nickname && row.title && row.title !== row.name && (
                <p
                  className="truncate text-xs text-muted-foreground"
                  title={row.title}
                  data-testid={`hub-session-title-${row.id}`}
                >
                  {row.title}
                </p>
              )}
            </>
          )}
          <div className="text-xs">
            <StatusLine row={row} />
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Link
            to={`/c/${row.id}`}
            className="flex items-center gap-0.5 text-xs text-muted-foreground hover:text-foreground"
            aria-label={`Open ${row.name}`}
          >
            Open
            <ArrowUpRightIcon className="size-3" aria-hidden />
          </Link>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label={`More for ${row.name}`}
                data-testid={`hub-session-menu-${row.id}`}
              >
                <MoreHorizontalIcon className="size-4" aria-hidden />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              align="end"
              onCloseAutoFocus={(event) => {
                if (!keepFocus.current) return;
                keepFocus.current = false;
                event.preventDefault();
                renameInput.current?.focus();
                renameInput.current?.select();
              }}
            >
              <DropdownMenuItem
                onSelect={() => {
                  keepFocus.current = true;
                  setNote(null);
                  setRenaming(true);
                }}
              >
                <PencilIcon aria-hidden />
                Rename
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={() => {
                  keepFocus.current = true;
                  void remove();
                }}
              >
                <MinusCircleIcon aria-hidden />
                Remove from Hub
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
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
