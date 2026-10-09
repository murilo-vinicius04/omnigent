import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import {
  AlertTriangleIcon,
  ArrowUpIcon,
  ArrowUpRightIcon,
  CheckCircle2Icon,
  MicIcon,
  MicOffIcon,
  SearchIcon,
  SendHorizontalIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { hubIsThinking, useHubStore, type HubEntry, type HubSessionInfo } from "@/lib/hubApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { useVoiceHubStore } from "@/lib/voiceHub";

/**
 * The hub: one chat, with no name, for all of your sessions. Type or talk;
 * it works out which session a message is for, sends it, and tells you here
 * when that session finishes.
 */
export function HubPage() {
  const entries = useHubStore((s) => s.entries);
  const sessions = useHubStore((s) => s.sessions);
  const loaded = useHubStore((s) => s.loaded);
  const error = useHubStore((s) => s.error);
  const sending = useHubStore((s) => s.sending);
  const [draft, setDraft] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const thinking = hubIsThinking(entries);

  useEffect(() => {
    void useHubStore.getState().load();
    return useHubStore.getState().follow();
  }, []);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [entries.length, thinking]);

  const submit = async () => {
    const text = draft.trim();
    if (!text || sending) return;
    setDraft("");
    try {
      await useHubStore.getState().send(text);
    } catch {
      setDraft(text);
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void submit();
    }
  };

  return (
    <div
      className="flex min-h-0 w-full flex-1 flex-col"
      style={{
        paddingTop: "calc(var(--omnigent-header-height) + var(--omnigent-inset-top))",
      }}
      data-testid="hub-page"
    >
      <div className="mx-auto flex w-full max-w-3xl items-center justify-between gap-3 px-4 pt-4 sm:px-6">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold">Hub</h1>
          <p className="text-ui text-muted-foreground">
            One chat for all your sessions. Say what you want; it goes to the right one.
          </p>
        </div>
        <TalkButton />
      </div>
      <SessionStrip sessions={sessions} />

      <div className="min-h-0 w-full flex-1 overflow-y-auto" data-testid="hub-log">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-3 px-4 py-4 sm:px-6">
          {loaded && entries.length === 0 && (
            <p className="py-10 text-center text-ui text-muted-foreground">
              Nothing here yet. Try “what is each session doing?” or “tell amarras to run the
              tests”.
            </p>
          )}
          {entries.map((entry) => (
            <HubLine key={entry.id} entry={entry} />
          ))}
          {thinking && (
            <p className="text-ui text-muted-foreground" data-testid="hub-thinking">
              Working out where that goes…
            </p>
          )}
          <div ref={bottom} />
        </div>
      </div>

      <div
        className="mx-auto w-full max-w-3xl px-4 pt-2 sm:px-6"
        style={{ paddingBottom: "calc(1rem + var(--omnigent-inset-bottom))" }}
      >
        {error && (
          <p className="mb-2 text-ui text-destructive" role="alert">
            {error}
          </p>
        )}
        <div className="flex items-end gap-2 rounded-xl border bg-background p-2 shadow-sm">
          <Textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Message the hub…"
            rows={1}
            className="max-h-48 min-h-9 flex-1 resize-none border-0 shadow-none focus-visible:ring-0"
            aria-label="Message the hub"
            data-testid="hub-input"
          />
          <Button
            type="button"
            size="icon"
            onClick={() => void submit()}
            disabled={!draft.trim() || sending}
            aria-label="Send"
            data-testid="hub-send"
          >
            <ArrowUpIcon />
          </Button>
        </div>
      </div>
    </div>
  );
}

function TalkButton() {
  const active = useVoiceHubStore((s) => s.active);
  const connecting = useVoiceHubStore((s) => s.connecting);
  return (
    <Button
      type="button"
      variant={active ? "default" : "outline"}
      disabled={connecting}
      onClick={() => {
        const voice = useVoiceHubStore.getState();
        if (voice.active) voice.stop();
        else void voice.start();
      }}
      aria-pressed={active}
      data-testid="hub-talk"
    >
      {active ? <MicOffIcon /> : <MicIcon className={cn(connecting && "animate-pulse")} />}
      {active ? "Stop talking" : connecting ? "Starting…" : "Talk"}
    </Button>
  );
}

function SessionStrip({ sessions }: { sessions: HubSessionInfo[] }) {
  if (sessions.length === 0) return null;
  return (
    <div className="mx-auto w-full max-w-3xl px-4 pt-3 sm:px-6">
      <div className="flex flex-wrap gap-2" data-testid="hub-sessions">
        {sessions.map((session) => (
          <Link
            key={session.id}
            to={`/c/${session.id}`}
            title={session.working && session.now ? `Now: ${session.now}` : session.title}
            className="inline-flex max-w-[16rem] items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs hover:bg-muted"
          >
            <span
              className={cn(
                "size-2 shrink-0 rounded-full",
                session.pending_approvals > 0
                  ? "bg-amber-500"
                  : session.working
                    ? "animate-pulse bg-emerald-500"
                    : "bg-muted-foreground/40",
              )}
              aria-hidden
            />
            <span className="truncate">{session.name}</span>
            <span className="sr-only">
              {session.pending_approvals > 0
                ? "waiting for approval"
                : session.working
                  ? "working"
                  : "idle"}
            </span>
          </Link>
        ))}
      </div>
    </div>
  );
}

function SessionLink({ entry }: { entry: HubEntry }) {
  if (!entry.session_id) return null;
  return (
    <Link
      to={`/c/${entry.session_id}`}
      className="inline-flex items-center gap-0.5 text-xs text-muted-foreground hover:text-foreground"
    >
      {entry.session_name ?? "Open session"}
      <ArrowUpRightIcon className="size-3" />
    </Link>
  );
}

function Detail({ label, text }: { label: string; text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-1">
      <button
        type="button"
        className="text-xs text-muted-foreground underline-offset-2 hover:underline"
        onClick={() => setOpen((v) => !v)}
      >
        {open ? "Hide" : label}
      </button>
      {open && (
        <p className="mt-1 whitespace-pre-wrap rounded-md bg-muted/60 p-2 text-ui">{text}</p>
      )}
    </div>
  );
}

function HubLine({ entry }: { entry: HubEntry }) {
  if (entry.role === "you") {
    return (
      <div className="flex justify-end" data-testid="hub-entry-you">
        <div className="max-w-[85%] rounded-2xl bg-muted px-3.5 py-2 text-ui whitespace-pre-wrap">
          {entry.text}
          {entry.via === "voice" && (
            <span className="ml-2 text-xs text-muted-foreground">(said)</span>
          )}
        </div>
      </div>
    );
  }
  const icon =
    entry.kind === "sent" ? (
      <SendHorizontalIcon className="size-4 text-sky-600 dark:text-sky-400" />
    ) : entry.kind === "finished" ? (
      <CheckCircle2Icon className="size-4 text-emerald-600 dark:text-emerald-400" />
    ) : entry.kind === "attention" || entry.kind === "error" ? (
      <AlertTriangleIcon className="size-4 text-amber-600 dark:text-amber-400" />
    ) : entry.kind === "check" ? (
      <SearchIcon className="size-4 text-muted-foreground" />
    ) : null;
  return (
    <div className="flex gap-2" data-testid={`hub-entry-${entry.kind}`}>
      <div className="mt-0.5 w-4 shrink-0">{icon}</div>
      <div className="min-w-0 flex-1">
        <p
          className={cn(
            "text-ui whitespace-pre-wrap",
            entry.kind === "error" && "text-destructive",
          )}
        >
          {entry.text}
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <SessionLink entry={entry} />
          {entry.via === "voice" && entry.kind !== "message" && (
            <span className="text-xs text-muted-foreground">by voice</span>
          )}
        </div>
        {entry.detail && entry.kind === "sent" && (
          <Detail label="What was sent" text={entry.detail} />
        )}
        {entry.detail && entry.kind === "finished" && (
          <Detail label="Claude's full reply" text={entry.detail} />
        )}
      </div>
    </div>
  );
}
