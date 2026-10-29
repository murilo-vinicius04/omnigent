/**
 * The hub: one regular conversation per user that runs all of their projects.
 *
 * Its messages travel the normal session routes; the server answers them
 * through the hub instead of a runner. This only finds (or makes) it.
 */
import { authenticatedFetch } from "@/lib/identity";
import { terminalInfoFromResource, type TerminalInfo } from "@/lib/terminals";

/** The caller's hub conversation id, made on first use. */
export async function openHub(): Promise<string> {
  const res = await authenticatedFetch("/v1/hub");
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // The status code alone says enough.
    }
    throw new Error(detail);
  }
  const body = (await res.json()) as { session_id?: unknown };
  if (typeof body.session_id !== "string" || !body.session_id) {
    throw new Error("the server did not name a hub conversation");
  }
  return body.session_id;
}

/** What a hub news item carries for the voice to tell. */
export interface HubUpdate {
  id: number;
  kind: string;
  text: string;
  session_name: string | null;
}

async function readJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // The status code alone says enough.
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

/** The newest hub entry id, so a call only hears news from after it opened. */
export async function hubLastId(): Promise<number> {
  const body = await readJson<{ last_id?: number }>(await authenticatedFetch("/v1/hub"));
  return typeof body.last_id === "number" ? body.last_id : 0;
}

/**
 * Hand what was said on a voice call to the hub and get back what to say.
 * The request and the hub's answer show in the hub conversation too.
 */
/** What the hub did with a spoken request: the words to say, and whether it went to a project. */
export interface HubVoiceReply {
  say: string;
  sent: boolean;
}

export async function askHubByVoice(text: string, context: string): Promise<HubVoiceReply> {
  const body = await readJson<{ say?: string; sent?: boolean }>(
    await authenticatedFetch("/v1/hub/voice", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, context: context.slice(-8000) }),
    }),
  );
  return { say: body.say ?? "", sent: body.sent === true };
}

/** Wait up to `waitS` for news after `after`: a project finished or needs them. */
export async function hubUpdates(
  after: number,
  waitS: number,
): Promise<{ updates: HubUpdate[]; lastId: number }> {
  const body = await readJson<{ updates?: HubUpdate[]; last_id?: number }>(
    await authenticatedFetch(`/v1/hub/updates?after=${after}&wait=${waitS}`),
  );
  return { updates: body.updates ?? [], lastId: body.last_id ?? after };
}

/** One model a project's chat can switch to, as its own picker lists it. */
export interface HubModelChoice {
  id: string;
  label: string;
  /** Efforts this model takes; Codex lists them per model. */
  efforts: string[];
}

/** What a project's chat runs on and what it could run on instead. */
export interface HubSessionRun {
  /** Its harness as the chat names it, e.g. "Claude Code". */
  provider: string;
  /** What it reports running, e.g. "claude-opus-5-5"; "" for its default. */
  model: string;
  model_label: string;
  /** A pick it has not reported running yet; "" when there is none. */
  requested: string;
  requested_label: string;
  effort: string;
  /** The choice its picker shows; "" when none of `models` matches. */
  selected: string;
  models: HubModelChoice[];
  efforts: string[];
}

/** One of a project's chats, a lane: an agent on its own topic. */
export interface HubSessionRow {
  host_id?: string;
  workspace?: string;
  id: string;
  /** What the hub calls it: its project's name, and its lane's when it has several. */
  name: string;
  /** Its lane's name: the topic the hub opened it for, else its title. */
  lane?: string;
  title: string;
  nickname: string | null;
  status: string | null;
  working: boolean;
  /** Its current step while working, e.g. "Bash: pytest". */
  now: string;
  /** Seconds since the epoch. */
  updated_at: number;
  pending_approvals: number;
  run: HubSessionRun;
  project_id: string;
  /** What it was first asked; "" when unknown. */
  first_ask?: string;
  /** What it was last asked; "" when unknown. */
  last_ask?: string;
  /** The latest thing its agent said; "" when unknown. */
  said?: string;
  /** The companion's latest spoken summary of it; null when none. */
  summary?: string | null;
  /** Messages the hub holds for it until it finishes. */
  held?: number;
  /** Its live to-do list, from its own snapshot. */
  todos?: HubTodoItem[];
}

/** One to-do item: what to do, and whether it is done. */
export interface HubTodoItem {
  content: string;
  status: string;
  /** What the companion calls it; kept as sent, absent on a new item. */
  id?: string;
  /** Who wrote it: "you" or "companion"; absent on a new item. */
  by?: string;
}

/** One line of what a project got done, as the companion wrote it. */
export interface HubBriefProgress {
  /** Seconds since the epoch. */
  at: number;
  text: string;
  /** The part of the project that did it. */
  lane: string;
}

/** The part of a project's brief the companion writes. */
export interface HubLiving {
  overview: string;
  status: string;
  progress: HubBriefProgress[];
  /** Seconds since the epoch; 0 before it ever wrote one. */
  updated_at: number;
}

/** One project as the hub runs it. */
export interface HubProjectRow {
  host_id?: string;
  id: string;
  name: string;
  handle: string;
  /** Its emoji, when one was picked; "" otherwise. */
  icon: string;
  /** The folder its new chats start in; "" when it has none. */
  folder: string;
  /** How many chats it has. */
  chats: number;
  /** Seconds since the epoch. */
  updated_at: number;
  /** Its newest chat, the one the hub sends to; `null` until it has one. */
  chat: HubSessionRow | null;
  /** Its newest chats, its lanes, newest first, each on its own provider, model and effort. */
  lanes?: HubSessionRow[];
  /** The router's newest decision for one of its chats, logged only for now. */
  route?: HubRoute | null;
  /** What the project is about, in their own words; "" when never written. */
  brief?: string;
  /** The project's to-do list, oldest first: theirs and the companion's. */
  brief_todos?: HubTodoItem[];
  /** What the companion wrote: overview, where it stands, recent progress. */
  living?: HubLiving;
}

/** What a project is doing, in one word. */
export type HubProjectState = "working" | "waiting" | "holding" | "idle" | "empty";

/** One lane's state as the project's brief reports it. */
export interface HubBriefLane {
  id: string;
  name: string;
  state: Exclude<HubProjectState, "empty">;
}

/** A lane's live to-do list. */
export interface HubLaneTodos {
  lane: string;
  session_id: string;
  items: HubTodoItem[];
}

/** Everything the panel's brief shows for one project. */
export interface HubProjectBrief {
  project_id: string;
  name: string;
  /** What the project is: their words, else the companion's; "" when neither. */
  about: string;
  /** "written" for their words, "companion" for its overview, "" for none. */
  about_source: string;
  /** Where it stands now, as the companion wrote it; "" before it did. */
  status: string;
  progress: HubBriefProgress[];
  /** Seconds since the companion last wrote the brief; 0 if never. */
  updated_at: number;
  state: HubProjectState;
  lanes: HubBriefLane[];
  lane_todos: HubLaneTodos[];
  /** Their own overview; the companion leaves it as written. */
  brief: string;
  brief_todos: HubTodoItem[];
  /** Handoff note files the lanes handed over: [label, workspace path]. */
  handoff_files: string[][];
  /** Whether the companion can write the brief now: a chat has something to read. */
  can_refresh: boolean;
}

/** Read a project's brief: what it is, where it stands, recent progress, to-dos. */
export async function getHubProjectBrief(projectId: string): Promise<HubProjectBrief> {
  return readJson<HubProjectBrief>(
    await authenticatedFetch(`/v1/hub/projects/${encodeURIComponent(projectId)}/brief`),
  );
}

/** Keep their own brief and to-do list for a project. */
export async function saveHubProjectBrief(
  projectId: string,
  brief: { brief: string; brief_todos: HubTodoItem[] },
): Promise<{ project_id: string; brief: string; brief_todos: HubTodoItem[] }> {
  return readJson(
    await authenticatedFetch(`/v1/hub/projects/${encodeURIComponent(projectId)}/brief`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(brief),
    }),
  );
}

/** Have the companion write a project's brief now, from what it last reported. */
export async function refreshHubProjectBrief(projectId: string): Promise<HubProjectBrief> {
  return readJson<HubProjectBrief>(
    await authenticatedFetch(`/v1/hub/projects/${encodeURIComponent(projectId)}/brief/refresh`, {
      method: "POST",
    }),
  );
}

/** Which provider, model and effort the quota rules would run a request on. */
export interface HubProposal {
  /** "claude", "codex" or "gemini"; "" when none could take it. */
  provider: string;
  /** The tier's model as its picker names it, e.g. "Sonnet". */
  model: string;
  effort: string;
  reason: string;
}

/** A decision the router logged for a request the hub sent. */
export interface HubRoute {
  /** Seconds since the epoch. */
  at: number | null;
  session_id: string | null;
  request: string;
  /** "economical", "middle" or "top", as reflex judged it. */
  tier: string;
  effort: string;
  proposal: HubProposal | null;
  ran_on: { provider: string; model: string; effort: string } | null;
  error: string;
}

/** One provider's budget today, in percent of its own week. */
export interface HubBudget {
  provider: string;
  label: string;
  week_used: number | null;
  resets_at: string | null;
  /** Days since its week began, 0 to 6. */
  day: number;
  daily: number;
  allowed: number;
  spent: number;
  remaining: number;
  five_hour: number | null;
  stale: boolean;
  /** Set while its week's whole leftover may be spent. */
  free_until: string | null;
  /** Why it cannot take work now; "" when it can. */
  blocked: string;
  daily_started_at?: string | null;
  daily_resets_at?: string | null;
  daily_timezone?: string | null;
}

/** Each provider's budget for today. */
export async function listHubBudgets(): Promise<HubBudget[]> {
  const body = await readJson<{ providers?: HubBudget[] }>(
    await authenticatedFetch("/v1/hub/budgets"),
  );
  return body.providers ?? [];
}

/** Their projects, most recently active first, each with its newest chat. */
export async function listHubProjects(): Promise<HubProjectRow[]> {
  const body = await readJson<{ projects?: HubProjectRow[] }>(
    await authenticatedFetch("/v1/hub/projects"),
  );
  return body.projects ?? [];
}

/**
 * Put one of a project's chats on another model and/or effort, by the same
 * rules as asking the hub: only what that chat offers, and the change is
 * written into the hub chat. `""` keeps a setting, `"default"` clears it.
 * Without `sessionId` it is the project's newest chat.
 */
export async function setHubProjectModel(
  projectId: string,
  change: { model?: string; effort?: string },
  sessionId?: string,
): Promise<{ changed: boolean; text: string }> {
  return readJson<{ changed: boolean; text: string }>(
    await authenticatedFetch("/v1/hub/model", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: projectId,
        session_id: sessionId ?? "",
        model: change.model ?? "",
        effort: change.effort ?? "",
      }),
    }),
  );
}

/** One of their chats outside every project, offered for adding to one. */
export interface HubCandidate {
  id: string;
  title: string;
  /** Seconds since the epoch. */
  updated_at: number;
  /** Its harness as the chat names it, e.g. "Codex"; "" when unknown. */
  provider: string;
}

/** Their chats outside every project, newest first. */
export async function listHubChats(): Promise<HubCandidate[]> {
  const body = await readJson<{ chats?: HubCandidate[] }>(
    await authenticatedFetch("/v1/hub/candidates"),
  );
  return body.chats ?? [];
}

/** What making a project gave back. */
export interface CreatedHubProject {
  /** What the hub chat says about it. */
  text: string;
  id: string;
  name: string;
  /** Where to start its first chat; `null` when it has one already. */
  link: string | null;
}

/**
 * Make a project. With `fromChat`, that chat (one outside every project)
 * becomes its first, and its new chats start where that one runs. Rejects
 * when the name is taken.
 */
export async function createHubProject(
  name: string,
  fromChat?: string,
): Promise<CreatedHubProject> {
  return readJson<CreatedHubProject>(
    await authenticatedFetch("/v1/hub/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, from_chat: fromChat ?? null }),
    }),
  );
}

/** Give a project another name (the sidebar's too). Rejects when it is taken. */
export async function renameHubProject(projectId: string, name: string): Promise<string> {
  const body = await readJson<{ text: string }>(
    await authenticatedFetch(`/v1/hub/projects/${encodeURIComponent(projectId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  );
  return body.text;
}

/** Delete a project; its chats are kept, outside any project. */
export async function deleteHubProject(projectId: string): Promise<string> {
  const body = await readJson<{ text: string }>(
    await authenticatedFetch(`/v1/hub/projects/${encodeURIComponent(projectId)}`, {
      method: "DELETE",
    }),
  );
  return body.text;
}

/** Put one of their chats outside every project into this project. */
export async function addHubProjectChat(projectId: string, sessionId: string): Promise<string> {
  const body = await readJson<{ text: string }>(
    await authenticatedFetch(`/v1/hub/projects/${encodeURIComponent(projectId)}/chats`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    }),
  );
  return body.text;
}

/** What opening a lane gave back. */
export interface OpenedHubLane {
  /** Whether its first request went out; `false` when no lane was opened. */
  opened: boolean;
  /** What the hub chat says about it, or why it did not open one. */
  text: string;
  session_id: string | null;
}

/**
 * Open a new lane in a project with its first request. The hub picks its
 * model (or runs it on `use`, e.g. "Sonnet") and tells it which checkouts the
 * busy lanes are writing in. This starts an agent, so it spends quota.
 */
export async function openHubLane(
  projectId: string,
  lane: { topic: string; message: string; use?: string; host_id?: string; workspace?: string },
): Promise<OpenedHubLane> {
  return readJson<OpenedHubLane>(
    await authenticatedFetch(`/v1/hub/projects/${encodeURIComponent(projectId)}/lanes`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        topic: lane.topic,
        message: lane.message,
        use: lane.use ?? "",
        ...(lane.host_id ? { host_id: lane.host_id, workspace: lane.workspace } : {}),
      }),
    }),
  );
}

/** Give a lane another name: its chat's title too. */
export async function renameHubLane(sessionId: string, name: string): Promise<string> {
  const body = await readJson<{ text: string }>(
    await authenticatedFetch(`/v1/hub/lanes/${encodeURIComponent(sessionId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  );
  return body.text;
}

/**
 * Close a lane. With `handoff`, it first writes its handoff note (one more
 * turn) and is archived once that is reported; otherwise it is archived now.
 * Rejects while it is working.
 */
export async function closeHubLane(
  sessionId: string,
  handoff: boolean,
): Promise<{ text: string; closing: boolean }> {
  return readJson<{ text: string; closing: boolean }>(
    await authenticatedFetch(`/v1/hub/lanes/${encodeURIComponent(sessionId)}/close`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ handoff }),
    }),
  );
}

/** The new-chat page, with its chat filed in this project. */
export function projectChatLink(name: string): string {
  return `/?project=${encodeURIComponent(name)}`;
}

/** Where the hub can put a message for a busy lane. */
export type HubPlace = "steer" | "after" | "new";

/**
 * Tell the hub where a message it placed beside a busy lane should have gone.
 * It is kept as a lesson either way, and fixed where it still can be (a held
 * message goes in now or to a new lane). `text` is what the hub did about it.
 */
export async function correctHubPlace(
  placementId: string,
  right: HubPlace,
): Promise<{ right: HubPlace; text: string }> {
  return readJson<{ right: HubPlace; text: string }>(
    await authenticatedFetch(`/v1/hub/placements/${encodeURIComponent(placementId)}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ right }),
    }),
  );
}

/** A shell the hub opened on the user's machine, in the session that holds them. */
export interface HubShell {
  sessionId: string;
  terminalId: string;
  terminal: TerminalInfo;
}

/**
 * Open a shell for the hub, which has no machine of its own: the server keeps
 * one session on the user's machine for it. Without `fresh` the shell already
 * open comes back; with it another one opens.
 */
export async function openHubTerminal(
  fresh: boolean,
  machine?: { host_id: string; workspace: string },
): Promise<HubShell> {
  const body = await readJson<{ session_id?: unknown; terminal?: Record<string, unknown> }>(
    await authenticatedFetch("/v1/hub/terminal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ new: fresh, ...machine }),
    }),
  );
  const terminal = body.terminal ? terminalInfoFromResource(body.terminal) : null;
  if (typeof body.session_id !== "string" || !body.session_id || terminal === null) {
    throw new Error("the server did not name a shell");
  }
  return { sessionId: body.session_id, terminalId: terminal.id, terminal };
}

export async function setHubProjectMachine(
  projectId: string,
  machine: { host_id: string; workspace: string },
): Promise<void> {
  await readJson(
    await authenticatedFetch(`/v1/hub/projects/${encodeURIComponent(projectId)}/machine`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(machine),
    }),
  );
}
