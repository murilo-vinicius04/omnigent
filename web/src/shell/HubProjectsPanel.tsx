import { useEffect, useRef, useState, type FormEvent, type ReactNode, type RefObject } from "react";
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import {
  ArrowUpRightIcon,
  FolderIcon,
  MessageSquarePlusIcon,
  MoreHorizontalIcon,
  PencilIcon,
  PlusIcon,
  ShieldAlertIcon,
  Trash2Icon,
} from "lucide-react";
import { Link, useNavigate } from "@/lib/routing";
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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
  addHubProjectChat,
  createHubProject,
  deleteHubProject,
  listHubChats,
  listHubProjects,
  projectChatLink,
  renameHubProject,
  setHubProjectModel,
  type HubCandidate,
  type HubProjectRow,
  type HubSessionRow,
} from "@/lib/hubApi";
import { relativeTime } from "@/lib/relativeTime";
import { cn } from "@/lib/utils";
import { SwitchAgentDialog } from "./SwitchAgentDialog";

/** How often the list is re-read while the panel is open. */
const POLL_MS = 4000;

export const HUB_PROJECTS_QUERY_KEY = ["hub-projects"] as const;
const HUB_CHATS_QUERY_KEY = ["hub-chats"] as const;

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** Re-read the hub's lists and the sidebar's projects after a change. */
async function refreshAll(queryClient: QueryClient): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: HUB_PROJECTS_QUERY_KEY }),
    queryClient.invalidateQueries({ queryKey: HUB_CHATS_QUERY_KEY }),
    queryClient.invalidateQueries({ queryKey: ["projects"] }),
    queryClient.invalidateQueries({ queryKey: ["project-sessions"] }),
    queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  ]);
}

/**
 * Every project the hub runs, each kept separate with its own chat, and the
 * controls to manage it: make a project, rename or delete one, start a new
 * chat in it or move an existing chat in, and change the model, effort and
 * provider its chat runs on.
 *
 * Model and effort picks go through the hub (`POST /v1/hub/model`), so they
 * are checked like a typed or spoken request and show up in the hub chat.
 */
export function HubProjectsPanel() {
  const { data, error, isLoading } = useQuery({
    queryKey: HUB_PROJECTS_QUERY_KEY,
    queryFn: listHubProjects,
    refetchInterval: POLL_MS,
  });

  if (isLoading) {
    return <p className="p-4 text-sm text-muted-foreground">Reading your projects…</p>;
  }
  if (error) {
    return (
      <p className="p-4 text-sm text-destructive">Couldn't read your projects: {message(error)}</p>
    );
  }
  const rows = data ?? [];
  return (
    <div
      className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-2"
      data-testid="hub-projects"
    >
      <div className="flex items-center justify-between gap-2 px-1">
        <span className="text-xs text-muted-foreground">
          {rows.length === 1 ? "1 project" : `${rows.length} projects`}
        </span>
        <NewProjectDialog />
      </div>
      {rows.length === 0 ? (
        <p className="px-1 text-sm text-muted-foreground">
          No projects yet. Make one to run it from here.
        </p>
      ) : (
        rows.map((row) => <HubProjectCard key={row.id} row={row} />)
      )}
    </div>
  );
}

/** Pick one of their chats outside every project. */
function ChatPicker({ open, onPick }: { open: boolean; onPick: (chat: HubCandidate) => void }) {
  const chats = useQuery({ queryKey: HUB_CHATS_QUERY_KEY, queryFn: listHubChats, enabled: open });
  let empty = "No chats outside a project.";
  if (chats.isLoading) empty = "Reading your chats…";
  else if (chats.error) empty = `Couldn't read your chats: ${message(chats.error)}`;
  return (
    <Command className="rounded-md border border-border">
      <CommandInput placeholder="Search your chats…" />
      <CommandList className="max-h-56">
        <CommandEmpty>{empty}</CommandEmpty>
        <CommandGroup>
          {(chats.data ?? []).map((c) => (
            <CommandItem
              key={c.id}
              value={`${c.title} ${c.id}`}
              onSelect={() => onPick(c)}
              data-testid={`hub-chat-${c.id}`}
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
  );
}

/**
 * Make a project. It starts either with a new chat (opened next, where its
 * folder, agent and model are picked as for any chat) or with one of their
 * existing chats.
 */
function NewProjectDialog() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [start, setStart] = useState<"new" | "existing">("new");
  const [chat, setChat] = useState<HubCandidate | null>(null);
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  function reset(): void {
    setName("");
    setStart("new");
    setChat(null);
    setSaving(false);
    setFailed(null);
  }

  async function create(event: FormEvent): Promise<void> {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || (start === "existing" && !chat)) return;
    setSaving(true);
    setFailed(null);
    try {
      const made = await createHubProject(trimmed, start === "existing" ? chat?.id : undefined);
      await refreshAll(queryClient);
      setOpen(false);
      reset();
      if (made.link) navigate(made.link);
    } catch (err) {
      setFailed(`Couldn't make it: ${message(err)}`);
      setSaving(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <Button
        variant="outline"
        size="xs"
        className="text-xs"
        data-testid="hub-new-project"
        onClick={() => setOpen(true)}
      >
        <PlusIcon className="size-3.5" aria-hidden />
        New project
      </Button>
      <DialogContent className="max-w-md">
        <form className="flex flex-col gap-3" onSubmit={(event) => void create(event)}>
          <DialogHeader>
            <DialogTitle>New project</DialogTitle>
            <DialogDescription>
              Each project is kept separate: its own chat, folder and agent. The Hub sends what you
              ask about it to its newest chat.
            </DialogDescription>
          </DialogHeader>
          <Input
            autoFocus
            value={name}
            maxLength={80}
            onChange={(event) => setName(event.target.value)}
            placeholder="Project name"
            aria-label="Project name"
            data-testid="hub-new-project-name"
          />
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-xs text-muted-foreground">Start it with</legend>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="radio"
                name="hub-project-start"
                className="mt-1"
                checked={start === "new"}
                onChange={() => setStart("new")}
              />
              <span>
                A new chat
                <span className="block text-xs text-muted-foreground">
                  Opens next: pick its folder, agent and model, and give it its first task.
                </span>
              </span>
            </label>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="radio"
                name="hub-project-start"
                className="mt-1"
                checked={start === "existing"}
                onChange={() => setStart("existing")}
                data-testid="hub-new-project-existing"
              />
              <span>
                A chat I already have
                <span className="block text-xs text-muted-foreground">
                  It moves into the project; new chats start in its folder.
                </span>
              </span>
            </label>
          </fieldset>
          {start === "existing" &&
            (chat ? (
              <div className="flex items-center justify-between gap-2 rounded-md border border-border px-2 py-1.5 text-sm">
                <span className="truncate" data-testid="hub-new-project-chat">
                  {chat.title || "Untitled"}
                </span>
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  className="text-xs"
                  onClick={() => setChat(null)}
                >
                  Change
                </Button>
              </div>
            ) : (
              <ChatPicker open={open && start === "existing"} onPick={setChat} />
            ))}
          {failed && (
            <p className="text-xs text-destructive" role="alert">
              {failed}
            </p>
          )}
          <DialogFooter className="border-t-0 bg-transparent">
            <Button type="button" variant="ghost" onClick={() => setOpen(false)} disabled={saving}>
              Cancel
            </Button>
            <Button
              type="submit"
              data-testid="hub-new-project-create"
              disabled={saving || !name.trim() || (start === "existing" && !chat)}
            >
              {start === "new" ? "Create and start its chat" : "Create"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** The name field that replaces a card's title while renaming it. */
function RenameForm({
  row,
  input,
  onDone,
}: {
  row: HubProjectRow;
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
    if (!name || name === row.name) {
      onDone(null);
      return;
    }
    setSaving(true);
    setFailed(null);
    try {
      onDone(await renameHubProject(row.id, name));
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
          maxLength={80}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") onDone(null);
          }}
          aria-label={`New name for ${row.name}`}
          className="h-7 text-sm"
          data-testid={`hub-project-rename-${row.id}`}
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
      {failed && <p className="text-xs text-destructive">{failed}</p>}
    </form>
  );
}

function StatusLine({ chat }: { chat: HubSessionRow | null }) {
  if (chat === null) {
    return <span className="text-muted-foreground">No chat yet</span>;
  }
  if (chat.pending_approvals > 0) {
    return (
      <span className="flex items-center gap-1 text-warning">
        <ShieldAlertIcon className="size-3.5" aria-hidden />
        Waiting for{" "}
        {chat.pending_approvals === 1 ? "your approval" : `${chat.pending_approvals} approvals`}
      </span>
    );
  }
  if (chat.working) {
    return (
      <span className="flex min-w-0 items-center gap-1 text-success">
        <RunningDot className="size-3.5 shrink-0" />
        <span className="truncate">{chat.now ? `Working · ${chat.now}` : "Working"}</span>
      </span>
    );
  }
  return (
    <span className="text-muted-foreground">
      Idle{chat.updated_at ? ` · ${relativeTime(chat.updated_at * 1000)}` : ""}
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

/** Ask before deleting a project; its chats stay. */
function DeleteDialog({
  row,
  open,
  onOpenChange,
  onDeleted,
}: {
  row: HubProjectRow;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDeleted: (said: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  async function remove(): Promise<void> {
    setBusy(true);
    setFailed(null);
    try {
      onDeleted(await deleteHubProject(row.id));
      onOpenChange(false);
    } catch (err) {
      setFailed(`Couldn't delete it: ${message(err)}`);
    } finally {
      setBusy(false);
    }
  }

  const kept =
    row.chats === 0
      ? "It has no chats."
      : row.chats === 1
        ? "Its chat is kept, outside any project."
        : `Its ${row.chats} chats are kept, outside any project.`;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Delete {row.name}?</DialogTitle>
          <DialogDescription>{kept}</DialogDescription>
        </DialogHeader>
        {failed && <p className="text-xs text-destructive">{failed}</p>}
        <DialogFooter className="border-t-0 bg-transparent">
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() => void remove()}
            disabled={busy}
            data-testid={`hub-project-delete-confirm-${row.id}`}
          >
            Delete project
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Move one of their chats outside every project into this one. */
function AddChatDialog({
  row,
  open,
  onOpenChange,
  onAdded,
}: {
  row: HubProjectRow;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAdded: (said: string) => void;
}) {
  const [failed, setFailed] = useState<string | null>(null);

  async function add(chat: HubCandidate): Promise<void> {
    setFailed(null);
    try {
      onAdded(await addHubProjectChat(row.id, chat.id));
      onOpenChange(false);
    } catch (err) {
      setFailed(`Couldn't move it: ${message(err)}`);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Move a chat into {row.name}</DialogTitle>
          <DialogDescription>
            Only chats outside every project are listed. The newest chat in a project is the one the
            Hub sends to.
          </DialogDescription>
        </DialogHeader>
        <ChatPicker open={open} onPick={(chat) => void add(chat)} />
        {failed && <p className="text-xs text-destructive">{failed}</p>}
      </DialogContent>
    </Dialog>
  );
}

function HubProjectCard({ row }: { row: HubProjectRow }) {
  const queryClient = useQueryClient();
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState<{ text: string; ok: boolean } | null>(null);
  const [switchOpen, setSwitchOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [adding, setAdding] = useState(false);
  // The menu traps focus until it has closed, then hands it back to its
  // button; after Rename it goes to the name field instead.
  const keepFocus = useRef(false);
  const renameInput = useRef<HTMLInputElement>(null);
  const { chat } = row;
  const newChat = projectChatLink(row.name);

  async function change(pick: { model?: string; effort?: string }): Promise<void> {
    setSaving(true);
    setNote(null);
    try {
      const result = await setHubProjectModel(row.id, pick);
      setNote({ text: result.text, ok: result.changed });
    } catch (err) {
      setNote({ text: `Couldn't change it: ${message(err)}`, ok: false });
    } finally {
      setSaving(false);
      // The chat's own page reads its snapshot; refresh it too.
      if (chat) await queryClient.invalidateQueries({ queryKey: ["session", chat.id] });
      await queryClient.invalidateQueries({ queryKey: HUB_PROJECTS_QUERY_KEY });
    }
  }

  function said(text: string): void {
    setNote({ text, ok: true });
    void refreshAll(queryClient);
  }

  return (
    <section
      className="flex flex-col gap-2 rounded-lg border border-border bg-card p-3"
      data-testid={`hub-project-${row.id}`}
    >
      <header className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          {renaming ? (
            <RenameForm
              row={row}
              input={renameInput}
              onDone={(text) => {
                setRenaming(false);
                if (text) said(text);
              }}
            />
          ) : (
            <h3 className="flex min-w-0 items-center gap-1.5 text-sm font-medium">
              {row.icon ? (
                <span aria-hidden>{row.icon}</span>
              ) : (
                <FolderIcon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
              )}
              <span className="truncate">{row.name}</span>
            </h3>
          )}
          <div className="text-xs">
            <StatusLine chat={chat} />
          </div>
          {chat && (
            <p
              className="truncate text-xs text-muted-foreground"
              title={chat.title}
              data-testid={`hub-project-chat-${row.id}`}
            >
              {chat.title || "Untitled chat"}
              {row.chats > 1 ? ` · newest of ${row.chats} chats` : ""}
            </p>
          )}
          {row.folder && (
            <p className="truncate font-mono text-[11px] text-muted-foreground" title={row.folder}>
              {row.folder}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {chat ? (
            <Link
              to={`/c/${chat.id}`}
              className="flex items-center gap-0.5 text-xs text-muted-foreground hover:text-foreground"
              aria-label={`Open ${row.name}`}
            >
              Open
              <ArrowUpRightIcon className="size-3" aria-hidden />
            </Link>
          ) : (
            <Link
              to={newChat}
              className="flex items-center gap-0.5 text-xs text-primary hover:underline"
              aria-label={`Start a chat in ${row.name}`}
            >
              Start chat
              <ArrowUpRightIcon className="size-3" aria-hidden />
            </Link>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label={`More for ${row.name}`}
                data-testid={`hub-project-menu-${row.id}`}
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
              <DropdownMenuItem asChild>
                <Link to={newChat}>
                  <MessageSquarePlusIcon aria-hidden />
                  New chat in {row.name}
                </Link>
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={() => {
                  setNote(null);
                  setAdding(true);
                }}
              >
                <PlusIcon aria-hidden />
                Move a chat in…
              </DropdownMenuItem>
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
              <DropdownMenuSeparator />
              <DropdownMenuItem
                variant="destructive"
                onSelect={() => {
                  setNote(null);
                  setDeleting(true);
                }}
              >
                <Trash2Icon aria-hidden />
                Delete project…
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>

      {chat && <ChatControls row={row} chat={chat} onChange={change} onSwitch={setSwitchOpen} />}

      {(saving || note) && (
        <p
          className={cn("text-xs", note && !note.ok ? "text-destructive" : "text-muted-foreground")}
          role="status"
        >
          {saving ? "Saving…" : note?.text}
        </p>
      )}

      {chat && (
        <SwitchAgentDialog
          sessionId={chat.id}
          open={switchOpen}
          onOpenChange={(open) => {
            setSwitchOpen(open);
            if (!open) void queryClient.invalidateQueries({ queryKey: HUB_PROJECTS_QUERY_KEY });
          }}
        />
      )}
      <DeleteDialog row={row} open={deleting} onOpenChange={setDeleting} onDeleted={said} />
      <AddChatDialog row={row} open={adding} onOpenChange={setAdding} onAdded={said} />
    </section>
  );
}

/** Provider, model and effort of a project's chat, and the controls to change them. */
function ChatControls({
  row,
  chat,
  onChange,
  onSwitch,
}: {
  row: HubProjectRow;
  chat: HubSessionRow;
  onChange: (pick: { model?: string; effort?: string }) => Promise<void>;
  onSwitch: (open: boolean) => void;
}) {
  const { run } = chat;
  const modelValue = run.selected || MODEL_SELECT_DEFAULT;
  // A model it runs that its list does not name still shows, as itself.
  const models =
    run.model && !run.selected && !run.requested
      ? [...run.models, { id: run.model, label: run.model, efforts: [] }]
      : run.models;

  return (
    <>
      <Row label="Provider">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-sm">{run.provider || "Unknown"}</span>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 px-2 text-xs"
            disabled={chat.working}
            title={chat.working ? "Wait for it to finish its turn to switch" : undefined}
            onClick={() => onSwitch(true)}
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
              void onChange({ model: value === MODEL_SELECT_DEFAULT ? "default" : value })
            }
            offerSmartRouting={false}
            testId={`hub-project-model-${row.id}`}
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
        <p className="text-xs text-muted-foreground" data-testid={`hub-project-pending-${row.id}`}>
          Switching to {run.requested_label || run.requested}; it still reports{" "}
          {run.model_label || run.model}.
        </p>
      )}

      {run.efforts.length > 0 && (
        <Row label="Effort">
          <Select
            value={run.effort || EFFORT_SELECT_NONE}
            onValueChange={(value) =>
              void onChange({ effort: value === EFFORT_SELECT_NONE ? "default" : value })
            }
          >
            <SelectTrigger
              className="h-8 w-full text-sm"
              data-testid={`hub-project-effort-${row.id}`}
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
    </>
  );
}
