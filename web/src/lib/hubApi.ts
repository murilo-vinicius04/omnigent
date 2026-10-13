/**
 * The hub: one regular conversation per user that runs all of their projects.
 *
 * Its messages travel the normal session routes; the server answers them
 * through the hub instead of a runner. This only finds (or makes) it.
 */
import { authenticatedFetch } from "@/lib/identity";

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

/** A project's newest chat, the one the hub sends to. */
export interface HubSessionRow {
  id: string;
  /** What the hub calls it: its project's name. */
  name: string;
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
}

/** One project as the hub runs it. */
export interface HubProjectRow {
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
  /** Its newest chat; `null` until it has one. */
  chat: HubSessionRow | null;
}

/** Their projects, most recently active first, each with its newest chat. */
export async function listHubProjects(): Promise<HubProjectRow[]> {
  const body = await readJson<{ projects?: HubProjectRow[] }>(
    await authenticatedFetch("/v1/hub/projects"),
  );
  return body.projects ?? [];
}

/**
 * Put a project on another model and/or effort, by the same rules as asking
 * the hub: only what its chat offers, and the change is written into the
 * hub chat. `""` keeps a setting, `"default"` clears it.
 */
export async function setHubProjectModel(
  projectId: string,
  change: { model?: string; effort?: string },
): Promise<{ changed: boolean; text: string }> {
  return readJson<{ changed: boolean; text: string }>(
    await authenticatedFetch("/v1/hub/model", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: projectId,
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

/** The new-chat page, with its chat filed in this project. */
export function projectChatLink(name: string): string {
  return `/?project=${encodeURIComponent(name)}`;
}
