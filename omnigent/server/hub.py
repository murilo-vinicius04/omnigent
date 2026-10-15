"""The hub: one chat, with no name, that runs your projects for you.

You talk to the hub -- typed in its chat, or aloud through the voice hub --
and it works out which of your projects a message is for, sends it to that
project's chat, and tells you in this same chat when its Claude finishes.
A project is one of your Omnigent projects (the sidebar's Projects). Its
newest chats are its lanes: agents working side by side, each on its own
topic (``hub_lanes``). A message for a busy lane waits until it finishes; new
work while a lane is busy opens a new lane. Projects are made, renamed and
deleted in the hub's Projects panel, or made by asking the hub.

The router is a companion process (warm ``agy``, Gemini) with its own role,
so deciding where a message goes costs no Claude quota. What the hub says
and does is a log on disk per user; the router's own ledger is its memory.
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import json
import logging
import os
import pathlib
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Final, Literal

from omnigent.antigravity_native_catalog import _probe_agy_models
from omnigent.model_fallbacks import SPOKEN_SUMMARY_AGY_DEFAULT_MODEL
from omnigent.process_logging import data_dir
from omnigent.server import budget, hub_lanes, hub_models, hub_place, model_router, voice_hub
from omnigent.server.discussion import GOOGLE_REFUSED, DiscussionSession, DiscussionUnavailable
from omnigent.server.hub_models import SessionRun

_logger = logging.getLogger(__name__)


#: Where each user's hub log, nicknames and conversation id are kept: beside
#: the server's database, since the conversation id belongs to that database.
HUB_DIR: Final[pathlib.Path] = data_dir() / "hub"

#: The router's model family when ``OMNIGENT_HUB_MODEL`` is unset: the newest
#: Gemini Flash at low effort that ``agy models`` lists. Not medium: it can
#: think for 40 s and more over one unclear message.
_FLASH_LOW: Final[re.Pattern[str]] = re.compile(r"^gemini-(\d+(?:\.\d+)*)-flash-low$")

#: Chats offered for adding to a project, newest first.
LISTING_LIMIT: Final[int] = 100

#: A project's chats read per listing, newest activity first.
CHATS_PER_PROJECT: Final[int] = 50

#: A project's newest chats, its lanes: the router and the Projects panel see these.
CHATS_SHOWN: Final[int] = 4

#: Longest project name the hub takes (the projects API allows 100).
MAX_PROJECT_NAME_CHARS: Final[int] = 80

#: Log entries kept on disk and served to the page.
MAX_LOG: Final[int] = 400

#: How often a session the hub sent work to is looked at.
WATCH_POLL_S: Final[float] = 4.0

#: A watch gives up after this long; the reply is still in that session.
WATCH_MAX_S: Final[float] = 6 * 3600.0

#: A session that has not started on what it was sent after this long.
NOT_STARTED_S: Final[float] = 120.0

#: How long a lane the quota refused waits for them to name a model for it.
PENDING_LANE_S: Final[float] = 15 * 60.0

#: A reply that names a model for a refused lane is short: "use sonnet".
_MAX_MODEL_REPLY_WORDS: Final[int] = 6

#: After a report, how long the hub keeps following a lane that may go on by itself.
FOLLOW_MAX_S: Final[float] = 6 * 3600.0

#: A lane that went on by itself is reported once it has been quiet this long:
#: a background job's progress pings come seconds apart.
FOLLOW_SETTLE_S: Final[float] = 90.0

#: How a turn a lane's own background job started begins (a Claude Code task
#: notification, and the reminder that comes with it).
_SELF_WAKE_PREFIXES: Final[tuple[str, ...]] = ("<task-notification>", "<system-reminder>")

#: A turn's newest tool calls read for the folders it worked in.
_TURN_CALLS: Final[int] = 300

#: Longest project reply kept for the hub chat to show in full.
MAX_REPLY_CHARS: Final[int] = 20000

#: How long a report waits for the project's spoken summary, which picks what
#: its chat shows under the answer and lands a few seconds after it.
SUMMARY_WAIT_S: Final[float] = 30.0

#: The summary's ``show`` kinds the chat displays unless the reader opts in.
SHOWN_KINDS: Final[frozenset[str]] = frozenset({"table", "image", "link", "output", "file"})

#: Most files of one reply copied into the hub chat, and the largest one.
MAX_REPLY_FILES: Final[int] = 8
MAX_REPLY_FILE_BYTES: Final[int] = 25 * 1024 * 1024

#: Names for the language tags a chat's language setting uses.
_LANGUAGE_NAMES: Final[dict[str, str]] = {
    "pt-br": "Brazilian Portuguese",
    "pt-pt": "European Portuguese",
    "pt": "Portuguese",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
}

#: Files attached in the hub wait this long for a send to carry them.
FILES_HOLD_S: Final[float] = 30 * 60.0

#: Budget for one routing turn, a cold start included. The chat shows the hub
#: as busy meanwhile; a slow decision beats "nothing was sent".
ROUTE_TIMEOUT_S: Final[float] = 90.0

#: A routing turn's first try. Flash Low answers in 2-13 s; a call that has not
#: come back by then has usually stalled, and a fresh process gets the rest.
ROUTE_FIRST_TRY_S: Final[float] = 40.0

#: The least time left for which a stalled turn is tried again.
ROUTE_RETRY_MIN_S: Final[float] = 20.0

#: The least a second try gets after an empty reply, however late it is.
EMPTY_RETRY_MIN_S: Final[float] = 20.0

#: What the hub asks when it cannot place a message for a busy lane yet.
ASK_PLACE: Final[str] = (
    "Where should this go: into {lane}'s work now, after it finishes, or to a new lane? "
    "If you don't pick, it goes when {lane} finishes."
)

#: A correction they say refers to their newest placement only this recent.
CORRECT_PLACE_WITHIN_S: Final[float] = 2 * 3600

#: Wait before asking again when Google turns the router away. Its outages so
#: far lasted about a minute (10-13 16:38 and 17:13), inside one routing budget.
ROUTE_REFUSED_WAIT_S: Final[float] = 10.0


#: What the router is told after a reply that came back empty.
EMPTY_REPLY_NUDGE: Final[str] = (
    "[Your reply was empty: you have no tools here and nothing to look up. "
    'Decide from what you were given; if you are unsure, use "ask".] Reply '
    "with ONE line of JSON as before."
)

#: Longest text quoted per session in the router's list.
_QUOTE_CHARS: Final[int] = 180

HUB_ROUTER_ROLE: Final[str] = (
    "You are the hub: the one chat an engineer uses to run several projects on "
    "their machine. Each project is kept separate: its own chat with its own "
    "Claude (or another coding agent) working in its own folder. You are not "
    "Claude and cannot see code or files yourself. With every message you get "
    "the list of their projects, what each project's chat was started with, "
    "what it was last asked and what it is doing now. Your job is to work out "
    "which project a message is for and pass it on, to say what the projects "
    "are doing, and to answer small things yourself. You have no tools: never "
    "search files or run commands; everything you need is in the message. Be "
    "brief: one or two plain sentences, no lists unless asked."
)

#: The routing turn. One JSON line keeps parsing trivial and every failure loud.
_DECIDE_PROMPT: Final[str] = """\
[Their projects right now, newest first. Each starts with its handle in \
brackets; wherever "<handle>" appears below, that code or the project's name \
both work. A project with several lanes (agents working side by side, each on \
its own topic) lists each lane under its own handle.]
{projects}
{lessons}{context}{files}
[They {how}]
{message}

Reply with ONE line of JSON and nothing else, exactly one of:
{{"answer": "..."}} -- you reply yourself: greetings, small talk, and anything \
the list above or this chat already answers.
{{"send": "<handle>", "lane": "<lane handle>", "message": "...", "say": "...", \
"place": "...", "topic": "..."}} -- a project's Claude has to do or answer it. \
"message" is their request in {language} for that Claude, faithful and complete: \
Claude cannot see this chat, so carry what the request depends on from earlier \
here, and keep code, paths and names exactly. "lane" is the lane it goes to, for \
a project with lanes; leave it out otherwise. "say" is a short confirmation for \
them that names the project.
When that lane is WORKING, "place" says where the message goes, judged from what \
it says and what the lane is doing; leave it out for an idle lane:
  "steer" -- it is about the task the lane is doing now: a direction, a \
correction, more detail, a change of priority, stop or pause, an answer it \
needs, or a question about how that work is going. It goes into the lane's work \
now.
  "after" -- more work for that lane that needs it to finish first, such as a \
next step that uses its result. The hub sends it when the lane finishes.
  "new" -- a different task, even in the same project or on a related subject, \
that does not need what the lane is doing. A new lane starts it in parallel; \
"topic" names that work in two to four words, and "use" goes with it only when \
they name a model or provider for it. If another lane of that project is idle \
and its topic fits, "send" it to that lane instead, without "place".
  "ask" -- you cannot tell which of these it is. The hub asks them, and their \
answer teaches you; "say" is that question, naming the lane.
"say" tells them what happens: that it went into the lane's work, that it goes \
when the lane finishes, or that a new lane takes it. Add "now": true only when \
they ask to tell it now; that also delivers what is held for it, so when they \
only say "tell it now", "message" is empty.
{{"new_lane": "<handle>", "topic": "...", "message": "...", "say": "...", \
"use": "..."}} -- they ask for a separate or parallel agent in that project. \
"topic" names the work in two to four words. "message" as for "send". "use" \
only when they name a model or provider for it (e.g. "Sonnet", "Codex"); \
otherwise leave it out and the hub picks. "say" names the project and the topic.
{{"correct_place": "steer" | "after" | "new", "say": "..."}} -- they say where \
their last message for a busy lane belongs, answering the hub's question about \
it or correcting where the hub put it: that lane's work now ("steer"), after the \
lane finishes ("after"), or a different task for a new lane ("new"). The hub \
moves it if it still can and keeps it as a lesson; "say" is empty, the hub says \
what it did.
{{"status": "<handle>"}} -- they ask what a project is doing now or whether it \
finished, and its line above does not say enough.
{{"check": "<handle>", "question": "..."}} -- a fact on their machine (disk, \
processes, services, logs, files, git) that read-only commands in that project's \
folder can show. Nothing is changed by a check.
{{"model": "<handle>", "to": "<model>", "effort": "<effort>"}} -- they want a \
project to run on another model or reasoning effort. Give only what they asked \
to change: "to" is one of the models that project offers (its id) or \
"default"; "effort" is one of its efforts or "default". If what they want is \
not in its list, use "ask" and name what it offers. A project never changes \
provider here (Claude Code stays Claude Code): that is the Switch button in the \
Projects panel.
{{"new_project": "<name>", "say": "..."}} -- they ask for a new project. Use \
the name they gave; if they gave none, use "ask" for one. The project starts \
empty: they give it its first task from the link the hub posts.
{{"ask": "..."}} -- you cannot tell which project they mean, or whether they \
want anything sent at all: one short question that names the likely projects \
or what you would send.
Projects are renamed and deleted, and existing chats added to them, only in \
the Projects panel: say so when they ask, and never claim it was done.

When files are attached, they go along with the next "send", whichever \
project it is for: say which project, never claim a file was read. No \
"[Files they attached" line above means no file came with this message, \
whatever the message says: use "ask" and tell them none arrived.
Requests about the hub itself (this chat, its router, its panel, what it \
shows) are work for the project that builds the hub: the one they sent hub \
problems to earlier in this chat. If none was, use "ask".
Decide straight away from what is here: this is routing, not research.
When you mention a file a project wrote, put its path or name in backticks: \
the chat links it to that file.
Reply to them in the language they wrote in; "message" for Claude is in \
{language} (the project's chat translates it for Claude). Never say a project \
did, found or finished anything the list or this chat does not show. When it is \
unclear whether they want something sent, use "ask".\
"""

#: After a ``status`` decision: the live reading, then the answer.
_STATUS_FOLLOW_UP: Final[str] = """\
[Live status of {name}, read just now]
{activity}

Reply with ONE line of JSON: {{"answer": "..."}} -- two or three plain \
sentences for them, in their language, about what it is doing or whether it \
finished. Say only what the status shows.\
"""

#: A finished turn, told back to them.
_REPORT_TASK: Final[str] = """\
{name}'s Claude just finished what they sent it. Its reply is below. Tell \
them in one or two plain sentences, in the language they have been writing \
to you in, what it did or found, and whether it needs anything from them. No \
code, no lists. The hub puts "{name} finished:" in front itself: do not repeat it.

{reply}\
"""

#: A project's own summary in another language than the hub's. Translated
#: whole: shortening it again is what lost the reader the details.
_TRANSLATE_TASK: Final[str] = """\
Translate {name}'s summary below into {language}. Keep every sentence, \
number, name and question; do not shorten, summarize or add anything. Reply \
with the translation only.

{summary}\
"""

#: A voice send, rewritten so it carries everything they asked. The call's voice
#: writes its own summary, and on a long request it left questions out.
_COMPLETE_TASK: Final[str] = """\
On a voice call they asked for a message to go to {name}'s Claude. The call's \
voice wrote the message below; the transcript of what they said is under it. \
Keep everything the message says, and add every question, request and detail \
from the transcript that it leaves out, in the order they said them. It stays \
a summary for Claude: not the transcript, no filler, nothing they did not say. \
Leave out only what was for the call itself (greetings, asking what a project \
is doing). Keep names, paths, numbers and code exactly. Write it in \
{language}. Reply with the message only.

[The voice's message]
{message}

[What they said, transcribed; it may have errors]
{heard}\
"""

#: Budget for that rewrite. Flash Low answers in 2-13 s; past this the send goes
#: out with their words under the voice's message instead.
COMPLETE_TIMEOUT_S: Final[float] = 25.0

EntryKind = Literal[
    "message",
    "sent",
    "finished",
    "status",
    "check",
    "model",
    "project",
    "members",
    "attention",
    "error",
    "held",
    "lane",
]

#: Entries an open voice hub call is told about as they happen.
SPOKEN_KINDS: Final[frozenset[str]] = frozenset({"finished", "attention"})


@dataclass(frozen=True)
class HubSession:
    """One chat as the hub sees it: a project's newest, the one it sends to.

    :param id: The session id.
    :param title: Its title, often generated, e.g. ``"Project Analysis"``.
    :param nickname: Its project's name, which is what the hub calls it.
    :param status: Live status, e.g. ``"running"`` or ``"idle"``.
    :param handle: Short unique id prefix the router uses, e.g. ``"d14b"``.
    :param first_ask: The first thing it was asked.
    :param last_ask: The latest thing it was asked.
    :param said: The latest thing its Claude said.
    :param summary: The companion's latest spoken summary of it.
    :param now: Its current step while working, e.g. ``"Bash: pytest"``.
    :param pending_approvals: Approvals it is waiting on.
    :param run: Its provider, model and effort, and what it could switch to.
    :param project_id: The project it belongs to.
    :param lane: The topic the hub opened it for, from its lane label; empty
        for a chat the hub did not open.
    """

    id: str
    title: str
    nickname: str | None = None
    status: str | None = None
    handle: str = ""
    updated_at: float = 0.0
    first_ask: str = ""
    last_ask: str = ""
    said: str = ""
    summary: str | None = None
    now: str = ""
    pending_approvals: int = 0
    run: SessionRun = field(default_factory=SessionRun)
    project_id: str = ""
    lane: str = ""
    #: Messages the hub holds for it until it finishes.
    held: int = 0

    @property
    def name(self) -> str:
        """What to call it: their nickname, else its title."""
        return self.nickname or self.title or self.handle

    @property
    def working(self) -> bool:
        """Whether its Claude is in the middle of a turn."""
        return (self.status or "") in voice_hub.WORKING_STATUSES

    def as_dict(self) -> dict[str, Any]:
        """The shape the hub page reads."""
        return {
            "id": self.id,
            "name": self.name,
            "title": self.title,
            "nickname": self.nickname,
            "handle": self.handle,
            "status": self.status,
            "working": self.working,
            "now": self.now,
            "updated_at": self.updated_at,
            "pending_approvals": self.pending_approvals,
            "run": self.run.as_dict(),
            "project_id": self.project_id,
            "lane": self.lane_name,
        }

    @property
    def lane_name(self) -> str:
        """What its lane is called: the topic it was opened for, else its title."""
        return self.lane or self.title or self.handle


@dataclass(frozen=True)
class HubCandidate:
    """A chat of theirs outside every project, as listed for adding to one.

    :param provider: Its harness as the chat names it, e.g. ``"Codex"``.
    """

    id: str
    title: str
    updated_at: float = 0.0
    provider: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HubFile:
    """A file they attached in the hub chat, held until a send carries it.

    :param file_id: Its id in the conversation it was uploaded to.
    :param filename: e.g. ``"Review541750(Reviewer2)Of26-4238v1.txt"``.
    :param kind: Its content block type: ``"input_file"`` or ``"input_image"``.
    :param session_id: The conversation that owns it (the hub's).
    """

    file_id: str
    filename: str
    kind: str = "input_file"
    session_id: str = ""


def attached_files(blocks: Sequence[Any], session_id: str) -> list[HubFile]:
    """The files among a message's content blocks."""
    return [
        HubFile(
            file_id=str(block["file_id"]),
            filename=str(block.get("filename") or block["file_id"]),
            kind=str(block["type"]),
            session_id=session_id,
        )
        for block in blocks
        if isinstance(block, dict)
        and block.get("type") in ("input_file", "input_image")
        and block.get("file_id")
    ]


#: Tool-call arguments that name a file or folder the call worked on.
_PATH_ARGS: Final[tuple[str, ...]] = ("file_path", "notebook_path", "path", "workdir", "cwd")

#: ``cd <folder>`` in a shell command, for an absolute or home folder.
_CD: Final[re.Pattern[str]] = re.compile(r"(?:^|[;&|(]\s*)cd\s+(['\"]?)([~/][^\s'\";&|)]*)\1")

#: Most folders kept per report to find relative paths in.
MAX_ANCHORS: Final[int] = 40


#: Longest the hub waits for the plan meters before deciding on the readings it has.
METER_REFRESH_S: Final[float] = 10.0


async def _refresh_meters() -> None:
    from omnigent.server.routes.plan_limits import collect_plan_limits

    await collect_plan_limits()


async def fresh_budgets() -> list[budget.ProviderBudget]:
    """Every provider's budget, after reading the plan meters (cached for a minute).

    Without this the meters were read only while a page showed them, and a
    provider no page asked about stayed blocked on an old reading.
    """
    try:
        await asyncio.wait_for(_refresh_meters(), METER_REFRESH_S)
    except Exception:  # noqa: BLE001 - decide on the readings already kept
        _logger.info("hub: plan meters not refreshed", exc_info=True)
    return await asyncio.to_thread(budget.today, HUB_DIR)


def touched_paths(items_newest_first: Sequence[Any]) -> list[str]:
    """Absolute paths a turn's tool calls name: files they read or wrote, folders they ran in."""
    found: list[str] = []
    for item in items_newest_first:
        _kind, _response, data = voice_hub.item_fields(item)
        args = data.get("arguments") if isinstance(data, dict) else None
        try:
            parsed = json.loads(args) if isinstance(args, str) else args
        except ValueError:
            continue
        if not isinstance(parsed, dict):
            continue
        found.extend(
            value
            for key in _PATH_ARGS
            if isinstance(value := parsed.get(key), str) and value.startswith(("/", "~"))
        )
        command = parsed.get("command")
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        if isinstance(command, str):
            found.extend(m.group(2) for m in _CD.finditer(command))
    return found


def anchor_dirs(touched: Sequence[str], workspace: str | None) -> list[str]:
    """The folders inside *workspace* those paths sit in, each with its parents, newest first."""
    if not workspace:
        return []
    root = pathlib.Path(workspace).expanduser().resolve()
    anchors: list[str] = []
    for raw in touched:
        try:
            path = pathlib.Path(raw).expanduser().resolve()
            # A folder a command ran in may be gone since; a name without an
            # extension still reads as one.
            gone_folder = not path.exists() and not path.suffix
            folder = path if path.is_dir() or gone_folder else path.parent
            folder.relative_to(root)
        except (ValueError, OSError):
            continue
        while folder != root and len(anchors) < MAX_ANCHORS:
            if str(folder) not in anchors:
                anchors.append(str(folder))
            folder = folder.parent
    return anchors


@dataclass(frozen=True)
class ProjectRecord:
    """One of their projects as stored, before the hub reads its chat.

    :param id: The project id.
    :param name: Its name, e.g. ``"amarras"``.
    :param icon: Its emoji, when they picked one.
    :param folder: The folder its new chats start in, when it has one.
    :param chat_ids: Its chats, newest activity first; archived ones left out.
    :param created_at: Seconds since the epoch.
    """

    id: str
    name: str
    icon: str = ""
    folder: str = ""
    chat_ids: tuple[str, ...] = ()
    created_at: float = 0.0


@dataclass(frozen=True)
class HubProject:
    """One project as the hub shows and routes it.

    :param chats: How many chats it has, up to :data:`CHATS_PER_PROJECT`.
    :param chat: Its newest chat, under the project's handle: what a send
        that names no lane goes to. ``None`` while it has no chat yet.
    :param lanes: Its newest chats, newest first, each under its own handle
        and on its own provider, model and effort.
    :param route: The newest tier decision logged for one of its chats.
    """

    id: str
    name: str
    handle: str = ""
    icon: str = ""
    folder: str = ""
    chats: int = 0
    updated_at: float = 0.0
    chat: HubSession | None = None
    lanes: tuple[HubSession, ...] = ()
    route: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        """The shape the Projects panel reads."""
        return {
            "id": self.id,
            "name": self.name,
            "handle": self.handle,
            "icon": self.icon,
            "folder": self.folder,
            "chats": self.chats,
            "updated_at": self.updated_at,
            "chat": self.chat.as_dict() if self.chat is not None else None,
            "lanes": [s.as_dict() for s in self.lanes],
            "route": self.route,
        }


@dataclass(frozen=True)
class HubEntry:
    """One line of the hub chat.

    :param id: Position in the log, from 1.
    :param role: ``"you"`` for what they said, ``"hub"`` for the hub.
    :param kind: What it is: a plain message, a send, a finished report...
    :param via: ``"typed"`` or ``"voice"`` for what they said.
    :param detail: Longer text the page can unfold: what was sent, the reply.
    """

    id: int
    at: float
    role: Literal["you", "hub"]
    kind: EntryKind
    text: str
    via: str | None = None
    session_id: str | None = None
    session_name: str | None = None
    detail: str | None = None
    #: A path in the app to start a chat in a project, e.g. ``"/?project=x"``.
    link: str | None = None
    #: The project's folder, so file paths in a reply link into its chat.
    workspace: str | None = None
    #: Folders inside it the turn read or wrote in, newest first: a path the
    #: reply gives relative to the repository it worked in is found there.
    anchors: list[str] | None = None
    #: Files a reply came with, copied into the hub chat as ``output_file`` blocks.
    files: list[dict[str, Any]] | None = None
    #: What the project's chat shows under its summary of the reply: tables,
    #: images, links, output (its spoken summary's ``show`` list).
    show: list[dict[str, Any]] | None = None
    #: For a message placed beside a working lane: ``{"id", "picked"}``, so
    #: they can correct where it went.
    place: dict[str, Any] | None = None
    #: The files a report's reply handed over, ``[label, workspace path]``:
    #: linked to, or changed by its turn. Only these are linked.
    links: list[list[str]] | None = None

    def as_dict(self) -> dict[str, Any]:
        """The JSON shape the page and the voice relay read."""
        return asdict(self)


@dataclass(frozen=True)
class ReplyFile:
    """A file a project's reply came with, still in that project's chat."""

    file_id: str
    filename: str
    mime_type: str = ""


@dataclass(frozen=True)
class TurnState:
    """Where a session is with the work the hub sent it."""

    status: str | None
    reply: str = ""
    pending_approvals: int = 0
    #: Images and other files the reply came with, oldest first.
    files: tuple[ReplyFile, ...] = ()
    #: The project's folder, for the file paths its reply names.
    workspace: str | None = None
    #: Absolute paths the turn's tool calls read, wrote or ran in, newest first.
    touched: tuple[str, ...] = ()
    #: The ``show`` list of the project's spoken summary of the reply.
    show: tuple[dict[str, Any], ...] = ()
    #: That summary's words, and the language tag it was written in. The hub
    #: reports them as they are, instead of summarizing the reply again.
    summary: str = ""
    summary_lang: str = ""
    #: The project writes spoken summaries and this reply's has not landed yet.
    summary_due: bool = False
    #: When the hub sent the work: files changed since are the turn's.
    since: float | None = None
    #: Since then, a turn its own background job started (a task notification).
    woke_itself: bool = False
    #: Since then, a message someone typed or sent into the chat.
    typed: bool = False

    @property
    def working(self) -> bool:
        return (self.status or "") in voice_hub.WORKING_STATUSES


@dataclass(frozen=True)
class HubDecision:
    """What the router decided about one message."""

    action: Literal[
        "answer",
        "send",
        "status",
        "check",
        "model",
        "new_project",
        "new_lane",
        "correct_place",
        "ask",
        "none",
    ]
    text: str = ""
    handle: str | None = None
    message: str = ""
    #: For ``model``: the model and effort asked for; empty leaves it as is.
    model: str = ""
    effort: str = ""
    #: For ``send``: the lane's handle; empty for the project's newest chat.
    lane: str = ""
    #: For ``send``: deliver into a busy lane instead of holding it.
    now: bool = False
    #: For ``send`` to a working lane, where it goes (:data:`hub_place.PLACES`);
    #: for ``correct_place``, where their last such message should have gone.
    place: str = ""
    #: For ``new_lane`` (and a send placed ``"new"``): what it works on, and
    #: a model or provider they named.
    topic: str = ""
    use: str = ""


#: The server's file metadata and byte stores, set at startup: copying a
#: reply's files into the hub chat needs both.
_file_stores: tuple[Any, Any] | None = None


def use_file_stores(file_store: Any, artifact_store: Any) -> None:
    """Give the hub the server's file stores, so it can copy files between chats."""
    global _file_stores
    _file_stores = (file_store, artifact_store)


#: The Flash model ``agy models`` offered, once asked; ``None`` until then.
_listed_model: str | None = None
#: Every model agy listed, for the tiers Google offers (Argon or not).
_agy_models: tuple[str, ...] = ()


def pick_router_model(model_ids: Sequence[str]) -> str | None:
    """The newest Gemini Flash (Low) among these agy model ids."""
    best: tuple[tuple[int, ...], str] | None = None
    for model_id in model_ids:
        found = _FLASH_LOW.match(model_id)
        if found is None:
            continue
        version = tuple(int(part) for part in found.group(1).split("."))
        if best is None or version > best[0]:
            best = (version, model_id)
    return best[1] if best else None


async def resolve_hub_model() -> str:
    """Ask agy which Flash it offers, once; agy drops old versions."""
    global _listed_model, _agy_models
    if _listed_model is None and not os.environ.get("OMNIGENT_HUB_MODEL", "").strip():
        from omnigent.server.discussion import _binary

        rows = await _probe_agy_models(_binary())
        _agy_models = tuple(str(r["id"]) for r in rows or [])
        _listed_model = pick_router_model(list(_agy_models))
        if _listed_model is None:
            _logger.warning("agy lists no Gemini Flash (Low); the hub router uses its fallback")
    return hub_model()


def hub_model() -> str:
    """The router's model: ``OMNIGENT_HUB_MODEL``, else what agy offers, else the fallback."""
    return (
        os.environ.get("OMNIGENT_HUB_MODEL", "").strip()
        or _listed_model
        or SPOKEN_SUMMARY_AGY_DEFAULT_MODEL
    )


def language_phrase(tag: str) -> str:
    """How the router is told the hub's language, e.g. ``"Brazilian Portuguese (pt-BR)"``."""
    tag = tag.strip()
    if not tag or tag.lower() == "auto":
        return "the language they wrote in"
    name = _LANGUAGE_NAMES.get(tag.lower()) or _LANGUAGE_NAMES.get(tag.split("-")[0].lower())
    return f"{name} ({tag})" if name else tag


def _same_language(a: str, b: str) -> bool:
    """Whether two language tags name one language: ``pt-BR``, ``pt-br`` and ``pt`` do."""
    return a.strip().lower().split("-")[0] == b.strip().lower().split("-")[0]


def _without_label(text: str, label: str) -> str:
    """The text without a leading copy of the label the hub adds itself."""
    if text.casefold().startswith(label.casefold()):
        return text[len(label) :].lstrip()
    return text


def _same_request(a: str, b: str) -> bool:
    """Whether two messages say the same thing: the router often restates a held one."""
    a, b = " ".join(a.lower().split()), " ".join(b.lower().split())
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def assign_handles(ids: Sequence[str]) -> dict[str, str]:
    """Shortest unique id prefix per id, at least four characters."""
    handles: dict[str, str] = {}
    for sid in ids:
        size = 4
        while size < len(sid) and any(
            other != sid and other.startswith(sid[:size]) for other in ids
        ):
            size += 1
        handles[sid] = sid[:size]
    return handles


def project_link(name: str) -> str:
    """The app's path that starts a new chat filed in this project."""
    from urllib.parse import quote

    return f"/?project={quote(name, safe='')}"


def session_line(session: HubSession) -> str:
    """One project's chat as the router reads it."""
    name = f"{session.nickname} · " if session.nickname else ""
    state = "WORKING" if session.working else "idle"
    if session.working and session.now:
        state += f" (now: {_short(session.now, 90)})"
    if session.pending_approvals:
        state += f", waiting for {session.pending_approvals} approval(s)"
    if session.held:
        state += f", {session.held} message(s) held for it until it finishes"
    parts = [f'[{session.handle}] {name}"{session.title or "untitled"}" · {state}']
    if session.run.provider:
        parts.append(f"runs {session.run.summary()}")
    if session.run.models:
        offered = ", ".join(
            m.id if m.label == m.id else f"{m.id} ({m.label})" for m in session.run.models
        )
        parts.append(f"models it offers: {offered}")
    if session.run.efforts:
        parts.append(f"efforts: {', '.join(session.run.efforts)}")
    if session.first_ask:
        parts.append(f'started with: "{_short(session.first_ask, _QUOTE_CHARS)}"')
    if session.last_ask and session.last_ask != session.first_ask:
        parts.append(f'last asked: "{_short(session.last_ask, _QUOTE_CHARS)}"')
    if session.summary:
        parts.append(f'latest update: "{_short(session.summary, _QUOTE_CHARS)}"')
    elif session.said:
        parts.append(f'last said: "{_short(session.said, _QUOTE_CHARS)}"')
    return " · ".join(parts)


def sessions_block(sessions: Sequence[HubSession]) -> str:
    """Every project's chat, one line each, or a note that there are none."""
    if not sessions:
        return "(No projects with a chat yet. They make one in the Projects panel.)"
    return "\n".join(session_line(s) for s in sessions)


def project_line(project: HubProject) -> str:
    """One project as the router reads it: no chat yet, its one chat, or each lane."""
    if project.chat is None:
        return f"[{project.handle}] {project.name} · no chat yet: nothing can be sent to it"
    if len(project.lanes) <= 1:
        line = session_line(project.chat)
        if project.chats > 1:
            line += f" · {project.chats} chats, this is the newest"
        return line
    lines = [
        f"[{project.handle}] {project.name} · {len(project.lanes)} lanes, each its own "
        "agent on its own topic, newest first:"
    ]
    lines.extend(f"  - lane {session_line(lane)}" for lane in project.lanes)
    if project.chats > len(project.lanes):
        lines.append(f"  ({project.chats - len(project.lanes)} older chats, not lanes)")
    return "\n".join(lines)


def route_summary(row: dict[str, Any]) -> dict[str, Any]:
    """What the Projects panel shows of a logged tier decision."""
    return {
        "at": row.get("at"),
        "session_id": row.get("session_id"),
        "request": _short(str(row.get("request") or ""), 160),
        "tier": row.get("tier") or "",
        "effort": row.get("effort") or "",
        "proposal": row.get("proposal") or None,
        "ran_on": row.get("ran_on") or None,
        "error": row.get("error") or "",
    }


def projects_block(projects: Sequence[HubProject]) -> str:
    """Every project, one line each, or a note that there are none."""
    if not projects:
        return "(No projects yet. They make one in the Projects panel, or ask you to.)"
    return "\n".join(project_line(p) for p in projects)


def parse_decision(reply: str) -> HubDecision:
    """Read the router's JSON line. Anything unreadable is ``none``."""
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end <= start:
        return HubDecision(action="none", text=reply.strip())
    try:
        parsed = json.loads(reply[start : end + 1])
    except json.JSONDecodeError:
        return HubDecision(action="none", text=reply.strip())
    if not isinstance(parsed, dict):
        return HubDecision(action="none")

    def text(key: str) -> str:
        value = parsed.get(key)
        return value.strip() if isinstance(value, str) else ""

    if text("send"):
        return HubDecision(
            action="send",
            handle=text("send"),
            message=text("message"),
            text=text("say"),
            lane=text("lane"),
            now=parsed.get("now") is True,
            place=text("place") if text("place") in hub_place.ROUTER_PLACES else "",
            topic=text("topic"),
            use=text("use"),
        )
    if text("correct_place") in hub_place.PLACES:
        return HubDecision(action="correct_place", place=text("correct_place"), text=text("say"))
    if text("new_lane"):
        return HubDecision(
            action="new_lane",
            handle=text("new_lane"),
            message=text("message"),
            text=text("say"),
            topic=text("topic"),
            use=text("use"),
        )
    if text("check"):
        return HubDecision(action="check", handle=text("check"), message=text("question"))
    if text("status"):
        return HubDecision(action="status", handle=text("status"))
    if text("model"):
        return HubDecision(
            action="model", handle=text("model"), model=text("to"), effort=text("effort")
        )
    if text("new_project"):
        return HubDecision(action="new_project", message=text("new_project"), text=text("say"))
    if text("ask"):
        return HubDecision(action="ask", text=text("ask"))
    if text("answer"):
        return HubDecision(action="answer", text=text("answer"))
    return HubDecision(action="none")


def _find(
    spoken: str, ids: Sequence[tuple[str, ...]], names: Sequence[tuple[str | None, ...]]
) -> int | None:
    """Which item an id, handle or name means, forgiving speech.

    :param ids: Each item's exact ids, e.g. its handle and full id.
    :param names: Each item's names, matched loosely.
    :returns: The item's index, or ``None`` when none or several fit.
    """
    want = voice_hub.normalize(spoken)
    if not want:
        return None
    for index, exact in enumerate(ids):
        if spoken.strip() in exact:
            return index
    keyed: dict[str, int] = {}
    for index, keys in enumerate(names):
        for key in keys:
            norm = voice_hub.normalize(key or "")
            if norm and norm not in keyed:
                keyed[norm] = index
    if want in keyed:
        return keyed[want]
    containing = {i for key, i in keyed.items() if key in want or want in key}
    if len(containing) == 1:
        return next(iter(containing))
    close = difflib.get_close_matches(want, list(keyed), n=1, cutoff=0.6)
    return keyed[close[0]] if close else None


def find_session(sessions: Sequence[HubSession], spoken: str) -> HubSession | None:
    """The chat a handle, project name or title means, forgiving speech."""
    index = _find(
        spoken,
        [(s.handle, s.id) for s in sessions],
        [(s.nickname, s.title) for s in sessions],
    )
    return sessions[index] if index is not None else None


def find_project(projects: Sequence[HubProject], spoken: str) -> HubProject | None:
    """The project a handle, name or its chat's title means, forgiving speech."""
    index = _find(
        spoken,
        [(p.handle, p.id) for p in projects],
        [(p.name, p.chat.title if p.chat is not None else None) for p in projects],
    )
    return projects[index] if index is not None else None


def resolve_target(
    projects: Sequence[HubProject], handle: str, lane: str = ""
) -> tuple[HubProject | None, HubSession | None]:
    """The project and chat a decision names.

    A project's handle or name means its newest chat, or the lane *lane*
    names; a lane's own handle or name means that lane.
    """
    if not handle.strip():
        return None, None
    wanted = handle.strip()
    for project in projects:
        for found in project.lanes:
            if wanted in (found.handle, found.id):
                return project, found
    project = next((p for p in projects if p.handle == wanted), None) or find_project(
        projects, wanted
    )
    if project is None:
        found = find_session([chat for p in projects for chat in p.lanes], wanted)
        if found is None:
            return None, None
        return next(p for p in projects if p.id == found.project_id), found
    target = project.chat
    if lane.strip() and project.lanes:
        target = (
            next((c for c in project.lanes if lane.strip() in (c.handle, c.id)), None)
            or find_session(project.lanes, lane)
            or target
        )
    return project, target


class HubRouter(DiscussionSession):
    """The hub's warm Gemini: a companion whose job is routing, not chatting."""

    role = HUB_ROUTER_ROLE

    @classmethod
    def _default_model(cls) -> str:
        return hub_model()

    def _workdir(self) -> str:
        """An empty folder: started in the repo, it went searching the code."""
        folder = HUB_DIR / ".router"
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder)

    async def _routing_turn(self, message: str, *, timeout_s: float) -> str:
        """One turn, asked again once if the reply comes back empty.

        An empty reply is the model reaching for a tool of its own, which
        the CLI refuses here. That turn did end, so the process is still in
        step and the same exchange can go on.
        """
        deadline = time.monotonic() + timeout_s
        try:
            return await self._turn(message, timeout_s=timeout_s)
        except DiscussionUnavailable as exc:
            if not str(exc).startswith("empty answer"):
                raise
            _logger.info("hub router replied empty; asking again")
        left_s = max(deadline - time.monotonic(), EMPTY_RETRY_MIN_S)
        return await self._turn(EMPTY_REPLY_NUDGE, timeout_s=left_s)

    async def decide(self, prompt: str, *, said: str, timeout_s: float) -> str:
        """Run one routing turn; record what they said, not the session list.

        :param prompt: The whole turn: sessions, their message, the format.
        :param said: Their message alone, for the ledger.
        :param timeout_s: Budget, a cold start included.
        :returns: The raw reply.
        :raises DiscussionUnavailable: When the process cannot answer.
        """
        async with self._lock:
            self._last_used = time.time()
            deadline = time.monotonic() + timeout_s
            first = True
            while True:
                left = deadline - time.monotonic()
                try:
                    await self._ensure_process()
                    blocks = self._preamble()
                    reply = await self._routing_turn(
                        "\n\n".join([*blocks, prompt]) if blocks else prompt,
                        timeout_s=min(left, ROUTE_FIRST_TRY_S) if first else left,
                    )
                    break
                except DiscussionUnavailable as exc:
                    await self._kill()
                    left = deadline - time.monotonic()
                    stalled = first and str(exc).startswith("no answer within")
                    if stalled:
                        retry = left >= ROUTE_RETRY_MIN_S
                    else:
                        retry = bool(GOOGLE_REFUSED.search(str(exc))) and (
                            left >= ROUTE_REFUSED_WAIT_S + ROUTE_RETRY_MIN_S
                        )
                    if not retry:
                        self._record("question", said)
                        raise
                    if stalled:
                        # A model call that never returns: a fresh process gets the rest.
                        _logger.warning("hub router stalled; asking a fresh process")
                    else:
                        # Google turned it away: its outages pass within the budget.
                        _logger.warning(
                            "hub router refused by Google; asking again in %g s: %s",
                            ROUTE_REFUSED_WAIT_S,
                            str(exc)[:200],
                        )
                        await asyncio.sleep(ROUTE_REFUSED_WAIT_S)
                    first = False
            self._briefed = True
            self._undelivered.clear()
            self._record("question", said)
            self._last_used = time.time()
            return reply

    async def follow_up(self, prompt: str, *, timeout_s: float) -> str:
        """One more turn in the same exchange, recording nothing."""
        async with self._lock:
            self._last_used = time.time()
            try:
                reply = await self._routing_turn(prompt, timeout_s=timeout_s)
            except DiscussionUnavailable:
                await self._kill()
                raise
            self._last_used = time.time()
            return reply

    def remember(self, text: str) -> None:
        """Note what the hub said in answer to its last turn, which the process already knows."""
        self._record("answer", text[:2000])

    def tell(self, text: str) -> None:
        """Note what happened between its turns (a report, a send): the next turn carries it."""
        self.note("note", text)


def _wake(waiter: asyncio.Future[None]) -> None:
    if not waiter.done():
        waiter.set_result(None)


class HubState:
    """One user's hub chat on disk, and who is waiting for the next line."""

    def __init__(self, key: str, *, root: pathlib.Path | None = None) -> None:
        self._dir = (root or HUB_DIR) / key
        self._entries: list[HubEntry] = []
        #: What they called each session before the hub ran projects.
        self.names: dict[str, str] = {}
        #: The sessions the hub ran before it ran projects; ``None`` if never set.
        self.members: list[str] | None = None
        #: Sends not yet reported back, kept across restarts: session id ->
        #: ``{"sent_at": ..., "name": ...}``.
        self.watches: dict[str, dict[str, Any]] = {}
        #: Whose hub this is and the address links use, for resuming watches.
        self.owner: dict[str, Any] = {}
        #: Files attached and not yet sent, and when they were attached.
        self.files: list[dict[str, Any]] = []
        self.files_at = 0.0
        #: Messages waiting for a busy lane, kept across restarts: session id
        #: -> ``{"name": ..., "items": [{"message", "via", "files", "at"}]}``.
        self.held: dict[str, dict[str, Any]] = {}
        self._waiters: set[asyncio.Future[None]] = set()
        self._load()

    @property
    def folder(self) -> pathlib.Path:
        """This user's hub folder: the log, nicknames and watches."""
        return self._dir

    @property
    def moved_to_projects(self) -> bool:
        """Whether its old session list has been made into projects."""
        return (self._dir / "projects-moved.json").exists()

    def mark_moved_to_projects(self, made: dict[str, str]) -> None:
        """Record that the old session list is now projects: ``{session: project}``."""
        self._write("projects-moved.json", made)

    @property
    def entries(self) -> list[HubEntry]:
        return list(self._entries)

    @property
    def last_id(self) -> int:
        return self._entries[-1].id if self._entries else 0

    def _load(self) -> None:
        with contextlib.suppress(OSError):
            for line in (self._dir / "log.jsonl").read_text(encoding="utf-8").splitlines():
                with contextlib.suppress(ValueError, TypeError):
                    self._entries.append(HubEntry(**json.loads(line)))
        self._entries = self._entries[-MAX_LOG:]
        with contextlib.suppress(OSError, ValueError):
            raw = json.loads((self._dir / "names.json").read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.names = {str(k): str(v) for k, v in raw.items()}
        with contextlib.suppress(OSError, ValueError):
            raw = json.loads((self._dir / "members.json").read_text(encoding="utf-8"))
            if isinstance(raw, list):
                self.members = [str(m) for m in raw]
        with contextlib.suppress(OSError, ValueError, AttributeError):
            raw = json.loads((self._dir / "watches.json").read_text(encoding="utf-8"))
            self.watches = {str(k): dict(v) for k, v in (raw.get("watches") or {}).items()}
            self.owner = {k: raw.get(k) for k in ("user_id", "base_url")}
        with contextlib.suppress(OSError, ValueError, AttributeError):
            raw = json.loads((self._dir / "files.json").read_text(encoding="utf-8"))
            self.files = [dict(f) for f in raw.get("files") or []]
            self.files_at = float(raw.get("at") or 0.0)
        with contextlib.suppress(OSError, ValueError, AttributeError):
            raw = json.loads((self._dir / "held.json").read_text(encoding="utf-8"))
            self.held = {str(k): dict(v) for k, v in raw.items()}

    def keep_watch(
        self, session_id: str, *, sent_at: float, name: str, follow: bool = False, **owner: Any
    ) -> None:
        """Remember a send still to report back, so a restart does not lose it.

        :param follow: It was reported; the lane is followed for what it does by itself.
        :param owner: ``user_id`` and ``base_url``, for resuming it after a restart.
        """
        self.watches[session_id] = {
            "sent_at": sent_at,
            "name": name,
            **({"follow": True} if follow else {}),
        }
        self.owner = {**self.owner, **owner}
        self._write("watches.json", {**self.owner, "watches": self.watches})

    def drop_watch(self, session_id: str) -> None:
        """Forget a send: it was reported back, or will never be."""
        if self.watches.pop(session_id, None) is not None:
            self._write("watches.json", {**self.owner, "watches": self.watches})

    def hold(self, session_id: str, name: str, item: dict[str, Any]) -> None:
        """Keep a message for a busy lane until it finishes, across restarts."""
        kept = self.held.setdefault(session_id, {"name": name, "items": []})
        kept["name"] = name
        kept["items"].append(item)
        self._write("held.json", self.held)

    def release(self, session_id: str) -> list[dict[str, Any]]:
        """The messages held for a lane, oldest first, forgotten once taken."""
        kept = self.held.pop(session_id, None)
        if kept is not None:
            self._write("held.json", self.held)
        return list((kept or {}).get("items") or [])

    def take_held(self, session_id: str, place_id: str) -> dict[str, Any] | None:
        """One held message, by the placement that held it, forgotten once taken."""
        kept = self.held.get(session_id)
        items = list((kept or {}).get("items") or [])
        found = next((i for i in items if i.get("place") == place_id), None)
        if kept is None or found is None:
            return None
        items.remove(found)
        if items:
            kept["items"] = items
        else:
            self.held.pop(session_id, None)
        self._write("held.json", self.held)
        return found

    def keep_files(self, files: Sequence[dict[str, Any]], at: float) -> None:
        """Remember the files waiting for a send; an empty list forgets them."""
        self.files, self.files_at = list(files), at
        self._write("files.json", {"at": at, "files": self.files})

    def append(self, **fields: Any) -> HubEntry:
        """Add a line, write it out, and wake everyone waiting for one."""
        entry = HubEntry(id=self.last_id + 1, at=time.time(), **fields)
        self._entries.append(entry)
        trimmed = len(self._entries) > MAX_LOG
        self._entries = self._entries[-MAX_LOG:]
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            path = self._dir / "log.jsonl"
            if trimmed:
                tmp = path.with_suffix(".tmp")
                tmp.write_text(
                    "".join(json.dumps(e.as_dict()) + "\n" for e in self._entries),
                    encoding="utf-8",
                )
                tmp.replace(path)
            else:
                with path.open("a", encoding="utf-8") as out:
                    out.write(json.dumps(entry.as_dict()) + "\n")
        except OSError:
            _logger.warning("hub log not written", exc_info=True)
        for waiter in self._waiters:
            # Safe from any thread: the waiter's own loop resolves it.
            waiter.get_loop().call_soon_threadsafe(_wake, waiter)
        self._waiters.clear()
        return entry

    def name(self, session_id: str, nickname: str) -> None:
        """Remember what they call a session; an empty name forgets it."""
        if nickname:
            self.names[session_id] = nickname
        else:
            self.names.pop(session_id, None)
        self._write("names.json", self.names)

    def set_members(self, session_ids: Sequence[str]) -> None:
        """Keep exactly these sessions in the hub."""
        self.members = list(dict.fromkeys(session_ids))
        self._write("members.json", self.members)

    def _write(self, filename: str, data: Any) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = self._dir / f"{filename}.tmp"
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(self._dir / filename)
        except OSError:
            _logger.warning("hub %s not written", filename, exc_info=True)

    def after(self, entry_id: int) -> list[HubEntry]:
        return [e for e in self._entries if e.id > entry_id]

    async def wait_after(self, entry_id: int, timeout_s: float) -> list[HubEntry]:
        """Entries newer than *entry_id*, waiting up to *timeout_s* for one."""
        newer = self.after(entry_id)
        if newer or timeout_s <= 0:
            return newer
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.add(waiter)
        with contextlib.suppress(asyncio.TimeoutError, TimeoutError):
            await asyncio.wait_for(asyncio.shield(waiter), timeout=timeout_s)
        self._waiters.discard(waiter)
        return self.after(entry_id)


@dataclass
class HubBackend:
    """What the hub needs from the server, injected so tests can fake it.

    :param sessions: The chats with these ids, in that order; gone and
        archived ones are left out.
    :param listing: Their chats outside every project, newest first, to pick
        one to add to a project from.
    :param activity: A chat's live status, in full.
    :param send: Post a message into a chat, as if typed there, with the
        files attached in the hub copied into that chat.
    :param check: Answer a question with read-only commands in its folder.
    :param turn: Where a chat is with work sent at a given time.
    :param projects: Their projects, each with its chats newest first.
    :param create_project: Make a project with this name; with a chat id,
        file that chat in it and start its new chats where that chat runs.
    :param rename_project: Give a project another name.
    :param delete_project: Delete a project, its chats kept outside any
        project; returns how many chats it had.
    :param file_chat: Put a chat in a project.
    :param configure: Set a chat's model and effort, as its own picker
        does: ``None`` leaves one as is, ``"default"`` clears it.
    """

    sessions: Callable[[Sequence[str]], Awaitable[list[HubSession]]]
    listing: Callable[[], Awaitable[list[HubCandidate]]]
    activity: Callable[[str], Awaitable[str]]
    send: Callable[[str, str, Sequence[HubFile]], Awaitable[None]]
    check: Callable[[str, str], Awaitable[str]]
    #: ``(session id, since, until=None)``: the turn since a moment, or a past one.
    turn: Callable[..., Awaitable[TurnState]]
    projects: Callable[[], Awaitable[list[ProjectRecord]]]
    create_project: Callable[[str, str | None], Awaitable[ProjectRecord]]
    rename_project: Callable[[str, str], Awaitable[None]]
    delete_project: Callable[[str], Awaitable[int]]
    file_chat: Callable[[str, str], Awaitable[None]]
    configure: Callable[[str, str | None, str | None], Awaitable[None]] | None = None
    #: Copy a reply's files from a project's chat into the hub chat:
    #: ``(from_chat, to_chat, files)`` -> ``output_file`` blocks.
    import_files: (
        Callable[[str, str, Sequence[ReplyFile]], Awaitable[list[dict[str, Any]]]] | None
    ) = None
    #: A chat's live status alone, e.g. ``"running"``; ``None`` once it is
    #: archived or gone.
    status: Callable[[str], Awaitable[str | None]] | None = None
    #: Open a chat in a project: ``(project_id, spec)`` -> the new chat's id.
    open_chat: Callable[[str, hub_lanes.LaneSpec], Awaitable[str]] | None = None
    #: The model ids a provider's CLI offers on the machine a chat runs on:
    #: ``(provider, chat id)``.
    catalog: Callable[[str, str], Awaitable[list[str]]] | None = None
    #: Absolute paths a chat's newest tool calls read, wrote or ran in.
    paths_in_use: Callable[[str], Awaitable[list[str]]] | None = None
    #: Give a chat another title (its lane's name).
    rename_chat: Callable[[str, str], Awaitable[None]] | None = None
    #: Archive a chat: it leaves its project's lanes.
    close_chat: Callable[[str], Awaitable[None]] | None = None


#: Sent to a lane being closed, so the next agent can pick up where it stopped.
HANDOFF_REQUEST: Final[str] = (
    "This chat is being closed. Before it is: write or update the project's handoff "
    "note with what you did, what is left, and where things are, so the next agent "
    "can carry on. Reply with the note's path."
)


@dataclass(frozen=True)
class _PendingLane:
    """A lane the quota refused, kept until they name a model for it."""

    project: str
    topic: str
    message: str
    at: float


@dataclass(frozen=True)
class _LanePick:
    """What a new lane runs on, or why none was picked, and the judgement behind it."""

    run: hub_lanes.LaneRun | None = None
    why: str = ""
    task: model_router.RouterTask = field(
        default_factory=lambda: model_router.RouterTask(request="", project="")
    )
    decision: model_router.TierDecision | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class Hub:
    """One user's hub: their chat, their router, and the sessions it watches."""

    def __init__(
        self,
        key: str,
        *,
        router_factory: Callable[[], Awaitable[HubRouter]],
        state: HubState | None = None,
        poll_s: float = WATCH_POLL_S,
        user_id: str | None = None,
        judge_tiers: bool = False,
    ) -> None:
        self.key = key
        self.user_id = user_id
        #: Log the decision model's tier judgement of each send (shadow mode),
        #: while ``model_router`` finds its endpoint switched on.
        self._judge_tiers = judge_tiers
        self.state = state or HubState(key)
        self._router_factory = router_factory
        self._backend: HubBackend | None = None
        self._busy = asyncio.Lock()
        self._watches: dict[str, asyncio.Task[None]] = {}
        #: Lanes with messages held until they finish, each with its deliverer.
        self._holds: dict[str, asyncio.Task[None]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._poll_s = poll_s
        #: Lanes followed after their report, in case they go on by themselves.
        self._follows: dict[str, asyncio.Future[None]] = {}
        self._follow_settle_s = FOLLOW_SETTLE_S
        self._pending_lane: _PendingLane | None = None
        #: The hub's conversation, when it has one: what the hub says is
        #: written there too (:class:`omnigent.server.hub_chat.HubConversation`).
        self.chat: Any = None
        #: Files attached in the hub chat that no send has carried yet.
        self._files = [HubFile(**f) for f in self.state.files]
        self._files_at = self.state.files_at

    def bind(self, backend: HubBackend) -> None:
        """Use this backend from now on; the newest request's wins."""
        self._backend = backend

    def require_backend(self) -> HubBackend:
        """The bound backend; raises when no request has bound one yet."""
        if self._backend is None:
            raise RuntimeError("hub has no backend bound")
        return self._backend

    async def _move_sessions_to_projects(self) -> None:
        """Once: make each session the hub ran before into a project of its own.

        The project takes the name they called the session, else its title,
        and its new chats start where that session runs.
        """
        if self.state.moved_to_projects:
            return
        made: dict[str, str] = {}
        backend = self.require_backend()
        if self.state.members:
            existing = {r.name.casefold(): r for r in await backend.projects()}
            chats = {s.id: s for s in await backend.sessions(self.state.members)}
            for session_id in self.state.members:
                chat = chats.get(session_id)
                if chat is None or chat.project_id:
                    continue
                name = self._free_name(
                    self.state.names.get(session_id) or chat.title or session_id[:8], existing
                )
                try:
                    record = await backend.create_project(name, session_id)
                except Exception:  # noqa: BLE001 - the rest still move; this one stays a chat
                    _logger.warning("hub could not make %s a project", session_id, exc_info=True)
                    continue
                existing[record.name.casefold()] = record
                made[session_id] = record.id
        self.state.mark_moved_to_projects(made)
        if made:
            _logger.info("hub moved %d sessions into projects", len(made))

    @staticmethod
    def _free_name(wanted: str, taken: dict[str, ProjectRecord]) -> str:
        """*wanted*, or *wanted* with a number when a project has that name."""
        base = " ".join(wanted.split())[:MAX_PROJECT_NAME_CHARS] or "Project"
        name, n = base, 2
        while name.casefold() in taken:
            name, n = f"{base} {n}", n + 1
        return name

    async def projects(self, *, shown: int = 1) -> list[HubProject]:
        """Their projects, most recently active first, each with its newest chats.

        :param shown: How many of each project's newest chats to read as its
            lanes; more than one also reads each project's newest tier decision.
        """
        await self._move_sessions_to_projects()
        backend = self.require_backend()
        records = await backend.projects()
        wanted = [c for r in records for c in r.chat_ids[: max(1, shown)]]
        chats = {s.id: s for s in await backend.sessions(wanted)} if wanted else {}
        routes = self.last_routes() if shown > 1 else {}
        # Lanes get handles of their own, unique among the projects' too.
        handles = assign_handles([r.id for r in records] + [c for c in wanted if c in chats])
        out: list[HubProject] = []
        for record in records:
            found = [chats[c] for c in record.chat_ids[: max(1, shown)] if c in chats]
            several = len(found) > 1
            lanes = tuple(
                replace(
                    lane,
                    nickname=f"{record.name} · {lane.lane_name}" if several else record.name,
                    handle=handles[lane.id],
                    project_id=record.id,
                    held=len((self.state.held.get(lane.id) or {}).get("items") or ()),
                )
                for lane in found
            )
            chat = replace(lanes[0], handle=handles[record.id]) if lanes else None
            logged = [routes[c] for c in record.chat_ids if c in routes]
            out.append(
                HubProject(
                    id=record.id,
                    name=record.name,
                    handle=handles[record.id],
                    icon=record.icon,
                    folder=record.folder,
                    chats=len(record.chat_ids),
                    updated_at=max(chat.updated_at if chat else 0.0, record.created_at),
                    chat=chat,
                    lanes=lanes if shown > 1 else (),
                    route=route_summary(max(logged, key=lambda r: r.get("at") or 0))
                    if logged
                    else None,
                )
            )
        out.sort(key=lambda p: p.updated_at, reverse=True)
        return out

    async def sessions(self) -> list[HubSession]:
        """Each project's newest chat, named after its project, newest first."""
        return [p.chat for p in await self.projects() if p.chat is not None]

    async def candidates(self) -> list[HubCandidate]:
        """Their chats outside every project, newest first."""
        return await self.require_backend().listing()

    def _project_entry(
        self, text: str, project: HubProject | ProjectRecord, **fields: Any
    ) -> HubEntry:
        return self.say(text, kind="project", session_name=project.name, **fields)

    async def create_project(
        self, name: str, *, from_chat: str | None = None, via: str = "panel"
    ) -> tuple[HubEntry, ProjectRecord]:
        """Make a project; with *from_chat*, that chat becomes its first.

        :raises ValueError: The name is empty, already a project's, or the
            chat is not one of theirs outside every project.
        """
        name = " ".join(name.split())
        if not name:
            raise ValueError("a project needs a name")
        if len(name) > MAX_PROJECT_NAME_CHARS:
            raise ValueError(f"a project name has at most {MAX_PROJECT_NAME_CHARS} characters")
        backend = self.require_backend()
        for record in await backend.projects():
            if record.name.casefold() == name.casefold():
                raise ValueError(f"there is already a project called {record.name}")
        chat_title = ""
        if from_chat:
            found = next((c for c in await self.candidates() if c.id == from_chat), None)
            if found is None:
                raise ValueError("that chat is not one of yours outside every project")
            chat_title = found.title or "that chat"
        record = await backend.create_project(name, from_chat)
        if from_chat:
            entry = self._project_entry(
                f"Created project {record.name}, with “{chat_title}” as its chat.",
                record,
                via=via,
                session_id=from_chat,
            )
        else:
            entry = self._project_entry(
                f"Created project {record.name}. It has no chat yet: start one to give it "
                "its first task, and pick its folder and agent there.",
                record,
                via=via,
                link=project_link(record.name),
            )
        return entry, record

    async def rename_project(self, project: HubProject, name: str) -> HubEntry:
        """Give a project another name, in the sidebar too.

        :raises ValueError: The name is empty or another project's.
        """
        name = " ".join(name.split())
        if not name:
            raise ValueError("a project needs a name")
        if len(name) > MAX_PROJECT_NAME_CHARS:
            raise ValueError(f"a project name has at most {MAX_PROJECT_NAME_CHARS} characters")
        for other in await self.projects():
            if other.id != project.id and other.name.casefold() == name.casefold():
                raise ValueError(f"there is already a project called {other.name}")
        if name == project.name:
            return self._project_entry(f"{project.name} is already called that.", project)
        await self.require_backend().rename_project(project.id, name)
        return self.say(f"Renamed {project.name} to {name}.", kind="project", session_name=name)

    async def delete_project(self, project: HubProject) -> HubEntry:
        """Delete a project; its chats are kept, outside any project."""
        count = await self.require_backend().delete_project(project.id)
        kept = (
            "it had no chats"
            if count == 0
            else ("its chat is kept" if count == 1 else f"its {count} chats are kept")
        )
        return self.say(
            f"Deleted project {project.name}; {kept}, outside any project.", kind="project"
        )

    async def add_chat(self, project: HubProject, session_id: str) -> HubEntry:
        """Put one of their chats outside every project into this one.

        :raises ValueError: The chat is not one of theirs outside every project.
        """
        found = next((c for c in await self.candidates() if c.id == session_id), None)
        if found is None:
            raise ValueError("that chat is not one of yours outside every project")
        await self.require_backend().file_chat(session_id, project.id)
        return self._project_entry(
            f"Moved “{found.title or 'Untitled'}” into {project.name}.",
            project,
            session_id=session_id,
        )

    def submit(self, text: str, *, via: str = "typed", files: Sequence[HubFile] = ()) -> HubEntry:
        """Record what they said and answer it in the background.

        :param files: Files attached to it; they wait for the next send.
        """
        said = text.strip()
        if files:
            self.hold_files(files)
            names = ", ".join(f.filename for f in files)
            said = f"{said}\n[attached: {names}]" if said else f"[attached: {names}]"
        entry = self.state.append(role="you", kind="message", text=said, via=via)
        if not text.strip():
            # Files alone: nothing to route yet, so ask where they go.
            self.say(
                f"Got {self._count(files)}. Tell me which project they are for and what to do "
                "with them; they go along with the next thing I send."
            )
            return entry
        self._spawn(self._handle(text.strip(), via))
        return entry

    @staticmethod
    def _count(files: Sequence[HubFile]) -> str:
        return "1 file" if len(files) == 1 else f"{len(files)} files"

    def hold_files(self, files: Sequence[HubFile]) -> None:
        """Keep files for the next send; ones attached earlier stay too."""
        held = {f.file_id: f for f in self.held_files()}
        for f in files:
            held[f.file_id] = f
        self._set_files(list(held.values()))

    def held_files(self) -> list[HubFile]:
        """Files waiting for a send, until they are :data:`FILES_HOLD_S` old."""
        if self._files and time.time() - self._files_at > FILES_HOLD_S:
            self._set_files([])
        return list(self._files)

    def _set_files(self, files: list[HubFile]) -> None:
        self._files, self._files_at = files, time.time()
        self.state.keep_files([asdict(f) for f in files], self._files_at)

    async def ask(self, text: str, *, via: str = "voice", context: str = "") -> list[HubEntry]:
        """Answer what a voice handed over, and return what the hub said.

        For a voice that cannot call tools itself (GPT-Live delegates): the
        request shows in the hub conversation, the hub acts on it as on a
        typed one, and the caller speaks the returned entries.

        :param context: The call so far, so a request can refer back to it.
        """
        said = text.strip()
        entry = self.state.append(role="you", kind="message", text=said, via=via)
        if self.chat is not None:
            self.chat.post_user(said)
        await self._handle(said, via, context=context)
        return [e for e in self.state.after(entry.id) if e.role == "hub"]

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def say(self, text: str, **fields: Any) -> HubEntry:
        """Log a line from the hub and write it into its conversation."""
        entry = self.state.append(role="hub", text=text, **{"kind": "message", **fields})
        if self.chat is not None:
            self.chat.post(entry)
        return entry

    async def _handle(self, text: str, via: str, *, context: str = "") -> None:
        async with self._busy:
            if self.chat is not None:
                self.chat.busy(True)
            try:
                await self._answer(text, via, context=context)
            except Exception:  # noqa: BLE001 - the chat must always get a reply
                _logger.warning("hub message failed", exc_info=True)
                self.say("Something went wrong on my side; nothing was sent.", kind="error")
            finally:
                if self.chat is not None:
                    self.chat.busy(False)

    async def _answer(self, text: str, via: str, *, context: str = "") -> None:
        if await self._open_pending_lane(text, via):
            return
        projects = await self.projects(shown=CHATS_SHOWN)
        router = await self._router_factory()
        how = "said, aloud, on a voice call" if via == "voice" else "wrote"
        held = self.held_files()
        lessons = hub_place.lessons(self.state.folder / hub_place.LOG_NAME)
        prompt = _DECIDE_PROMPT.format(
            projects=projects_block(projects),
            lessons=f"\n{lessons}\n" if lessons else "",
            context=(
                f"\n[The voice call so far, oldest first]\n{context.strip()}\n"
                if context.strip()
                else ""
            ),
            files=(
                "\n[Files they attached, which go with the next send: "
                + ", ".join(f.filename for f in held)
                + "]"
                if held
                else ""
            ),
            how=how,
            message=text,
            language=language_phrase(await self.language()),
        )
        try:
            reply = await router.decide(prompt, said=text, timeout_s=ROUTE_TIMEOUT_S)
        except DiscussionUnavailable as exc:
            _logger.warning("hub router unavailable: %s", exc)
            why = (
                f"I took over {ROUTE_TIMEOUT_S:g} s to work out where that goes"
                if str(exc).startswith("no answer within")
                else "I can't think right now (the Gemini router is not answering)"
            )
            self.say(
                f"{why}, so nothing was sent. Try again in a moment, or type it in "
                "the project's chat.",
                kind="error",
            )
            return
        decision = parse_decision(reply)
        _logger.info(
            "hub decision | action=%s handle=%s said=%r",
            decision.action,
            decision.handle,
            text[:200],
        )
        if decision.action == "new_project":
            try:
                entry, _ = await self.create_project(decision.message, via=via)
            except ValueError as exc:
                entry = self.say(f"I didn't make it: {exc}.")
            router.remember(entry.text)
            return
        if decision.action == "correct_place":
            entry = await self.correct_latest_place(decision.place, via=via)
            router.remember(entry.text)
            return
        project, target = resolve_target(projects, decision.handle or "", decision.lane)
        if decision.action in ("send", "status", "check", "model", "new_lane") and project is None:
            names = ", ".join(p.name for p in projects) or "none yet"
            said = f"I couldn't tell which project you mean. Your projects: {names}."
            self.say(said)
            router.remember(said)
            return
        if project is not None and target is None and decision.action != "ask":
            said = f"{project.name} has no chat yet, so nothing was sent. Start one first."
            self.say(
                said, kind="attention", session_name=project.name, link=project_link(project.name)
            )
            router.remember(said)
            return
        if decision.action == "new_lane" and project is not None:
            entry = await self.open_lane(
                project,
                decision.topic,
                decision.message or text,
                via=via,
                say=decision.text,
                use=decision.use,
            )
            router.remember(entry.text)
            return
        if decision.action == "send" and target is not None:
            if decision.now and self.state.held.get(target.id):
                await self.send_held_now(target, decision.message, via=via, say=decision.text)
            elif target.working and not decision.now and project is not None:
                await self.place(project, target, decision, said=text, via=via)
            else:
                await self.send_to(target, decision.message or text, via=via, say=decision.text)
            return
        if decision.action == "status" and target is not None:
            activity = await self.require_backend().activity(target.id)
            try:
                follow = await router.follow_up(
                    _STATUS_FOLLOW_UP.format(name=target.name, activity=activity),
                    timeout_s=ROUTE_TIMEOUT_S,
                )
                answer = parse_decision(follow).text or follow.strip()
            except DiscussionUnavailable:
                answer = activity
            self.say(answer, kind="status", session_id=target.id, session_name=target.name)
            router.remember(f"{target.name}: {answer}")
            return
        if decision.action == "check" and target is not None:
            self.say(
                f"Checking on {target.name}…",
                kind="check",
                session_id=target.id,
                session_name=target.name,
            )
            found = await self.require_backend().check(target.id, decision.message or text)
            self.say(found, kind="check", session_id=target.id, session_name=target.name)
            router.remember(f"checked {target.name}: {found}")
            return
        if decision.action == "model" and target is not None:
            entry = await self.set_model(target, decision.model, decision.effort, via=via)
            router.remember(entry.text)
            return
        said = decision.text or (
            "I didn't understand that well enough to act on it; nothing was sent."
        )
        self.say(said)
        router.remember(said)

    async def set_model(
        self, session: HubSession, model: str, effort: str, *, via: str
    ) -> HubEntry:
        """Put a session on another model and/or effort, from its own lists.

        Nothing is changed unless every part asked for is one the session
        offers; otherwise the reply says what it does offer.

        :param model: A model id, label or partial name; ``"default"``
            clears the pick; empty leaves the model as is.
        :param effort: An effort, ``"default"``, or empty for as is.
        """
        run = session.run
        fields: dict[str, Any] = {"session_id": session.id, "session_name": session.name}
        new_model: str | None = None
        efforts = run.efforts
        if model:
            if not run.models:
                return self.say(
                    f"{session.name} runs {run.provider or 'an agent'} and offers no model "
                    "choice here; nothing was changed.",
                    **fields,
                )
            if hub_models.normalize_default(model):
                new_model = "default"
                default = next((m for m in run.models if m.is_default), None)
                efforts = default.efforts if default and run.kind == "codex" else efforts
            else:
                found = hub_models.match_model(run.models, model)
                if len(found) != 1:
                    offered = ", ".join(m.label for m in (found or run.models))
                    lead = "Which one" if found else f"{session.name} has no model like '{model}'"
                    return self.say(
                        f"{lead}? {session.name} offers: {offered}. Nothing was changed.",
                        **fields,
                    )
                new_model = found[0].id
                if run.kind == "codex":
                    efforts = found[0].efforts
        new_effort: str | None = None
        if effort:
            if hub_models.normalize_default(effort):
                new_effort = "default"
            else:
                new_effort = hub_models.match_effort(efforts, effort)
                if new_effort is None:
                    offered = ", ".join(efforts) or "no effort setting"
                    return self.say(
                        f"{session.name} can't take '{effort}' effort; it offers {offered}. "
                        "Nothing was changed.",
                        **fields,
                    )
        if new_model is None and new_effort is None:
            return self.say(f"{session.name} runs {run.summary()}. Nothing was changed.", **fields)
        backend = self.require_backend()
        if backend.configure is None:
            return self.say("I can't change models from here yet.", kind="error", **fields)
        try:
            await backend.configure(session.id, new_model, new_effort)
        except Exception as exc:  # noqa: BLE001 - say it failed, never pretend
            _logger.warning("hub model change on %s failed", session.id, exc_info=True)
            return self.say(
                f"Changing {session.name} failed ({type(exc).__name__}); its model is as it was.",
                kind="error",
                **fields,
            )
        parts = []
        if new_model is not None:
            parts.append("its default model" if new_model == "default" else run.label(new_model))
        if new_effort is not None:
            parts.append("default effort" if new_effort == "default" else f"{new_effort} effort")
        return self.say(
            f"Switched {session.name} to {' at '.join(parts)}; it applies from its next message.",
            kind="model",
            via=via,
            **fields,
        )

    async def place(
        self, project: HubProject, lane: HubSession, decision: HubDecision, *, said: str, via: str
    ) -> HubEntry:
        """Send a message for a working lane where the router placed it.

        Into the lane's work now, held until it finishes, or to a new lane.
        Without a place from the router it is held, which never steers the
        lane off its task. Every placement is logged for correcting it later.
        """
        path = self.state.folder / hub_place.LOG_NAME
        guess = decision.place if decision.place in hub_place.PLACES else ""
        if decision.place == hub_place.ASK or not hub_place.trusted(path):
            picked = hub_place.ASK
        else:
            picked = decision.place or "after"
        message = decision.message or said
        placement = hub_place.Placement(
            id=hub_place.new_id(),
            at=time.time(),
            project=project.name,
            project_id=project.id,
            lane=lane.name,
            lane_id=lane.id,
            task=lane.last_ask or lane.first_ask,
            message=message,
            said=said,
            picked=picked,
            topic=decision.topic,
            via=via,
            by="router" if decision.place else "default",
            guess=guess,
        )
        tag = {"id": placement.id, "picked": picked, "guess": guess}
        if picked == hub_place.ASK:
            # Until it has learned where their messages go, the hub asks.
            asked = decision.text if decision.place == hub_place.ASK else ""
            entry = await self.hold(
                lane, message, via=via, say=asked or ASK_PLACE.format(lane=lane.name), place=tag
            )
        elif picked == "steer":
            entry = await self.send_to(lane, message, via=via, say=decision.text, place=tag)
        elif picked == "new":
            entry = await self.open_lane(
                project,
                decision.topic,
                message,
                via=via,
                say=decision.text,
                use=decision.use,
                place=tag,
            )
            if entry.kind == "sent":
                placement = replace(placement, opened=entry.session_id or "")
        else:
            entry = await self.hold(lane, message, via=via, say=decision.text, place=tag)
        hub_place.log_placement(path, placement)
        backend = model_router.configured(HUB_DIR) if self._judge_tiers else None
        if backend is not None:
            self._spawn(self._shadow_place(path, placement, backend))
        return entry

    async def _shadow_place(
        self, path: pathlib.Path, placement: hub_place.Placement, backend: Any
    ) -> None:
        hub_place.log_shadow(path, placement.id, await hub_place.shadow_judge(placement, backend))

    async def correct_place(self, placement_id: str, right: str, *, via: str) -> HubEntry | None:
        """Their word on where a message for a busy lane should have gone.

        Kept as a lesson either way; fixed where that can still be done: a
        held message goes in now or to a new lane, and a lane opened for it a
        few minutes ago is closed again and the message goes to the busy lane.
        A message already in a lane's work stays there.

        :returns: What the hub said about it; ``None`` when it was right.
        :raises KeyError: No such placement.
        :raises ValueError: *right* is not a place.
        """
        if right not in hub_place.PLACES:
            raise ValueError(f"not a place: {right!r}")
        path = self.state.folder / hub_place.LOG_NAME
        record = hub_place.find(path, placement_id)
        if record is None:
            raise KeyError(placement_id)
        p = record.placement
        hub_place.log_feedback(path, p.id, right, via=via)
        if right == p.picked:
            return None
        label = hub_place.PLACE_LABELS[right]
        tag = {"id": p.id, "picked": right}
        about = {"session_id": p.lane_id, "session_name": p.lane}
        project, lane = await self._lane_by_id(p.project_id, p.lane_id)
        if p.picked in ("after", hub_place.ASK):
            if right == "after":
                waiting = any(
                    i.get("place") == p.id
                    for i in (self.state.held.get(p.lane_id) or {}).get("items") or ()
                )
                return self.say(
                    f"Ok: it goes to {p.lane} when it finishes."
                    if waiting
                    else f"Ok. It had already gone to {p.lane} when it finished.",
                    kind="attention",
                    **about,
                )
            item = self.state.take_held(p.lane_id, p.id)
            if item is None:
                return self.say(
                    f"Noted: that belonged {label}. It had already gone to {p.lane} when it "
                    "finished.",
                    kind="attention",
                    **about,
                )
            files = [HubFile(**f) for f in item.get("files") or []]
            if right == "new" and project is not None:
                return await self.open_lane(
                    project, p.topic, str(item["message"]), via=via, place=tag
                )
            target = lane or HubSession(id=p.lane_id, title=p.lane, nickname=p.lane)
            return await self.send_to(
                target, str(item["message"]), via=via, files=files, place=tag
            )
        if p.picked == "new":
            backend = self.require_backend()
            fresh = time.time() - p.at <= hub_place.CLOSE_FRESH_LANE_S
            if not (p.opened and fresh and backend.close_chat is not None and lane is not None):
                return self.say(
                    f"Noted: that belonged {label}. The new lane has been at it for a while, "
                    "so I left it open: close it in the Projects panel if you don't need it.",
                    kind="attention",
                    **about,
                )
            watch = self._watches.pop(p.opened, None)
            if watch is not None:
                watch.cancel()
            self.state.drop_watch(p.opened)
            await backend.close_chat(p.opened)
            if right == "after" and lane.working:
                return await self.hold(
                    lane,
                    p.message,
                    via=via,
                    say=f"Closed the new lane; this goes to {p.lane} when it finishes.",
                    place=tag,
                )
            return await self.send_to(
                lane,
                p.message,
                via=via,
                say=f"Closed the new lane and sent this to {p.lane}.",
                place=tag,
            )
        return self.say(
            f"Noted: that belonged {label}. It had already gone into {p.lane}'s work, so I "
            "left it there.",
            kind="attention",
            **about,
        )

    async def correct_latest_place(self, right: str, *, via: str) -> HubEntry:
        """A correction they said aloud or typed: about their newest placement."""
        path = self.state.folder / hub_place.LOG_NAME
        record = hub_place.latest(path, within_s=CORRECT_PLACE_WITHIN_S)
        if record is None:
            return self.say("I have no recent message for a busy lane to move.")
        entry = await self.correct_place(record.placement.id, right, via=via)
        if entry is None:
            return self.say(
                f"That's where it went: {hub_place.PLACE_LABELS[right]}. Noted.",
                kind="attention",
                session_id=record.placement.lane_id,
                session_name=record.placement.lane,
            )
        return entry

    async def _lane_by_id(
        self, project_id: str, lane_id: str
    ) -> tuple[HubProject | None, HubSession | None]:
        """A project and one of its lanes as they are now."""
        for project in await self.projects(shown=CHATS_SHOWN):
            if project.id == project_id:
                return project, next((s for s in project.lanes if s.id == lane_id), None)
        return None, None

    async def send_to(
        self,
        session: HubSession,
        message: str,
        *,
        via: str,
        say: str = "",
        files: Sequence[HubFile] | None = None,
        brief: str = "",
        judge: bool = True,
        place: dict[str, Any] | None = None,
    ) -> HubEntry:
        """Post into a chat, log it here, and watch for its answer.

        :param files: What goes with it; ``None`` takes the files attached in
            the hub that no send has carried yet.
        :param brief: Context for the agent after the request, kept out of the
            hub chat's quote of what was sent.
        :param judge: Log the decision model's judgement of it (shadow mode).
        :param place: The placement it was sent by, for correcting it later.
        """
        sent_at = time.time()
        attached_here = files is None
        files = self.held_files() if files is None else list(files)
        try:
            await self.require_backend().send(
                session.id, f"{message}\n\n{brief}" if brief else message, files
            )
        except Exception as exc:  # noqa: BLE001 - say it failed, never pretend
            _logger.warning("hub send to %s failed", session.id, exc_info=True)
            kept = (
                f" The {self._count(files)} stay here for the next try."
                if files and attached_here
                else ""
            )
            return self.say(
                f"Sending to {session.name} failed ({type(exc).__name__}); nothing was sent."
                + kept,
                kind="error",
                session_id=session.id,
                session_name=session.name,
                detail=message,
            )
        if files and attached_here:
            self._set_files([])
        names = ", ".join(f.filename for f in files)
        entry = self.say(
            (say or f"Sent to {session.name}.")
            + (f" With {self._count(files)}: {names}." if files else ""),
            kind="sent",
            via=via,
            session_id=session.id,
            session_name=session.name,
            detail=message + (f"\n\nAttached: {names}" if files else ""),
            place=place,
        )
        router = await self._router_factory()
        router.tell(
            f"sent to {session.name}: {message}" + (f" (attached: {names})" if files else "")
        )
        self.watch(session, sent_at)
        if self._judge_tiers and judge:
            self._spawn(self._judge_tier(session, message, before=entry.id))
        return entry

    async def hold(
        self,
        session: HubSession,
        message: str,
        *,
        via: str,
        say: str = "",
        place: dict[str, Any] | None = None,
    ) -> HubEntry:
        """Keep a message for a busy lane and send it once that lane finishes.

        A busy chat is never steered off its task by default; the files
        attached in the hub wait with the message.
        """
        files = self.held_files()
        if files:
            self._set_files([])
        self.state.hold(
            session.id,
            session.name,
            {
                "message": message,
                "via": via,
                "files": [asdict(f) for f in files],
                "at": time.time(),
                **({"place": place["id"]} if place else {}),
            },
        )
        names = ", ".join(f.filename for f in files)
        entry = self.say(
            say or "It's busy right now; I'll send this when it finishes.",
            kind="held",
            via=via,
            session_id=session.id,
            session_name=session.name,
            detail=message + (f"\n\nAttached: {names}" if files else ""),
            place=place,
        )
        router = await self._router_factory()
        router.tell(f"holding for {session.name}, which is busy: {message}")
        self._deliver_when_idle(session)
        return entry

    async def send_held_now(
        self, session: HubSession, message: str, *, via: str, say: str = ""
    ) -> HubEntry:
        """Deliver what is held for a lane at once, with *message* unless it repeats one of them.

        Taken off the queue, so nothing goes twice once the lane finishes.
        """
        held = self.state.release(session.id)
        path = self.state.folder / hub_place.LOG_NAME
        for item in held:
            # Telling it now says where these belonged.
            if item.get("place"):
                hub_place.log_feedback(path, str(item["place"]), "steer", via="now")
        texts = [str(h["message"]) for h in held]
        if message.strip() and not any(_same_request(message, t) for t in texts):
            texts.append(message.strip())
        files = [HubFile(**f) for h in held for f in h.get("files") or []]
        return await self.send_to(session, "\n\n".join(texts), via=via, say=say, files=files)

    def _deliver_when_idle(self, session: HubSession) -> None:
        running = self._holds.get(session.id)
        if running is None or running.done():
            self._holds[session.id] = asyncio.ensure_future(self._deliver_held(session))

    async def _lane_status(self, session_id: str) -> str | None:
        backend = self.require_backend()
        if backend.status is not None:
            return await backend.status(session_id)
        found = await backend.sessions([session_id])
        return (found[0].status or "") if found else None

    async def _deliver_held(self, session: HubSession) -> None:
        """Send what was held for a lane once it is idle and its last report is in."""
        deadline = time.monotonic() + WATCH_MAX_S
        idle_polls = 0
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(self._poll_s)
                watch = self._watches.get(session.id)
                if watch is not None and not watch.done():
                    # Its report comes first: a send now would be read as its answer.
                    idle_polls = 0
                    continue
                status = await self._lane_status(session.id)
                if status is None:
                    held = self.state.release(session.id)
                    if held:
                        self.say(
                            f"{session.name} was closed, so what I held for it was not sent.",
                            kind="attention",
                            session_id=session.id,
                            session_name=session.name,
                            detail="\n\n".join(h["message"] for h in held),
                        )
                    return
                if status in voice_hub.WORKING_STATUSES:
                    idle_polls = 0
                    continue
                idle_polls += 1
                # Idle twice in a row: a native chat goes idle a moment early.
                if idle_polls < 2:
                    continue
                held = self.state.release(session.id)
                if held:
                    await self.send_to(
                        session,
                        "\n\n".join(h["message"] for h in held),
                        via=str(held[-1].get("via") or "typed"),
                        files=[HubFile(**f) for h in held for f in h.get("files") or []],
                    )
                return
            held = self.state.release(session.id)
            if held:
                self.say(
                    f"{session.name} stayed busy for {WATCH_MAX_S / 3600:g} h, so what I held "
                    "for it was not sent.",
                    kind="attention",
                    session_id=session.id,
                    session_name=session.name,
                    detail="\n\n".join(h["message"] for h in held),
                )
        except asyncio.CancelledError:
            # A shutdown: the held messages stay on disk for the restart.
            raise
        except Exception:  # noqa: BLE001 - a broken delivery must not take the hub down
            _logger.warning("hub delivery to %s failed", session.id, exc_info=True)
        finally:
            self._holds.pop(session.id, None)

    async def _open_pending_lane(self, text: str, via: str) -> bool:
        """Open the lane the quota refused when their reply names a model for it."""
        pending = self._pending_lane
        if pending is None or time.time() - pending.at > PENDING_LANE_S:
            return False
        provider, word = hub_lanes.named_run(text)
        if len(text.split()) > _MAX_MODEL_REPLY_WORDS or not provider:
            return False
        self._pending_lane = None
        projects = await self.projects(shown=CHATS_SHOWN)
        project = next((p for p in projects if p.name == pending.project), None)
        if project is None:
            return False
        use = word or budget.PROVIDER_LABELS.get(provider, provider)
        entry = await self.open_lane(project, pending.topic, pending.message, via=via, use=use)
        router = await self._router_factory()
        router.tell(entry.text)
        return True

    async def open_lane(
        self,
        project: HubProject,
        topic: str,
        message: str,
        *,
        via: str,
        say: str = "",
        use: str = "",
        place: dict[str, Any] | None = None,
    ) -> HubEntry:
        """Open a new chat in the project for this request, and send it there.

        It runs on the tier and effort the decision model picks and the
        provider the quota rules pick, unless *use* names one; it starts in
        the folder of the project's newest chat, told what the busy lanes are
        doing and which checkouts they write in.
        """
        backend = self.require_backend()
        like = project.chat
        if like is None:
            return self.say(
                f"{project.name} has no chat yet to open a lane beside. Start one first.",
                kind="attention",
                session_name=project.name,
                link=project_link(project.name),
            )
        if backend.open_chat is None:
            return self.say(
                "I can't open lanes here yet.", kind="error", session_name=project.name
            )
        topic = hub_lanes.topic_name(topic, message)
        pick = await self._lane_run(project, like, message, use)
        if pick.run is None:
            # A model they name next opens it ("use sonnet"), past the quota.
            self._pending_lane = _PendingLane(project.name, topic, message, time.time())
            return self.say(pick.why, kind="attention", session_name=project.name)
        self._pending_lane = None
        busy = [lane for lane in project.lanes if lane.working]
        in_use = []
        for lane in busy:
            paths = await backend.paths_in_use(lane.id) if backend.paths_in_use else []
            in_use.append(
                hub_lanes.BusyLane(
                    name=lane.name,
                    task=_short(lane.last_ask, _QUOTE_CHARS),
                    repos=tuple(hub_lanes.repos_in_use(paths)),
                )
            )
        # A folder that is itself a checkout gets its own worktree while another lane works.
        branch = ""
        root = hub_lanes.repo_root(project.folder) if busy and project.folder else None
        if root is not None and any(root in lane.repos for lane in in_use):
            branch = f"hub/{hub_lanes.branch_slug(topic)}"
        spec = hub_lanes.LaneSpec(topic=topic, run=pick.run, like=like.id, branch=branch)
        try:
            session_id = await backend.open_chat(project.id, spec)
        except Exception as exc:  # noqa: BLE001 - say it failed, never pretend
            _logger.warning("hub could not open a lane in %s", project.name, exc_info=True)
            return self.say(
                f"Opening a new lane in {project.name} failed ({type(exc).__name__}); "
                "nothing was sent.",
                kind="error",
                session_name=project.name,
                detail=message,
            )
        name = f"{project.name} · {topic}"
        lane = HubSession(
            id=session_id,
            title=topic,
            nickname=name,
            lane=topic,
            project_id=project.id,
            run=SessionRun(
                provider=hub_lanes.PROVIDER_LABELS.get(pick.run.provider, pick.run.provider),
                requested=pick.run.model,
                effort=pick.run.effort,
            ),
        )
        self.say(
            f"New lane: {topic}, on {pick.run.summary()}. {pick.run.reason}",
            kind="lane",
            via=via,
            session_id=session_id,
            session_name=name,
        )
        entry = await self.send_to(
            lane,
            message,
            via=via,
            say=say,
            brief=hub_lanes.brief(project.name, topic, in_use),
            judge=False,
            place=place,
        )
        if pick.decision is not None:
            task = replace(
                pick.task,
                session_id=session_id,
                provider=lane.run.provider,
                model=pick.run.model,
                effort=pick.run.effort,
            )
            model_router.log_decision(
                self.state.folder / model_router.LOG_NAME,
                task,
                pick.decision,
                extra={**pick.extra, "lane": topic},
            )
        return entry

    async def _lane_run(
        self, project: HubProject, like: HubSession, message: str, use: str
    ) -> _LanePick:
        """What a new lane runs on: what they named, else the router's pick, else *like*'s."""
        task = model_router.RouterTask(
            request=message,
            project=project.name,
            provider=like.run.provider,
            model=like.run.requested or like.run.model,
            effort=like.run.effort,
        )
        router_backend = model_router.configured(HUB_DIR) if self._judge_tiers else None
        decision = (
            await model_router.judge(task, router_backend) if router_backend is not None else None
        )
        judged = decision is not None and bool(decision.tier)
        extra: dict[str, Any] = {}
        provider, word = hub_lanes.named_run(use)
        if provider:
            tier = (decision.tier if judged else "") or "top"
            offered = budget.offered_tiers(provider, _agy_models)
            if not word:
                word = offered.get(tier) or next(iter(offered.values()), "")
            effort = (
                decision.efforts_by_tier.get(tier, decision.effort) if judged else ""
            ) or model_router.DEFAULT_EFFORT
            effort = budget.fit_effort(provider, effort)
            reason = f"You asked for {use}."
            # Their choice stands over the quota; say when it is short.
            mine = next((b for b in await fresh_budgets() if b.provider == provider), None)
            if mine is not None and mine.blocked:
                reason += f" {budget.PROVIDER_LABELS.get(provider, provider)}: {mine.blocked}."
        elif judged:
            assert decision is not None
            budgets = await fresh_budgets()
            # No provider with room at the judged tier: the nearest tier that has one.
            for tier in budget.NEAREST_TIERS.get(decision.tier, (decision.tier,)):
                effort = decision.efforts_by_tier.get(tier, decision.effort)
                proposal = budget.pick(tier, effort, budgets, agy_models=_agy_models)
                if proposal.provider:
                    break
            extra = {"proposal": proposal.as_dict(), "budgets": budget.snapshot(budgets)}
            if not proposal.provider:
                full = "; ".join(
                    f"{budget.PROVIDER_LABELS.get(b.provider, b.provider)}: {b.blocked}"
                    for b in budgets
                    if b.blocked
                )
                return _LanePick(
                    why=(
                        f"I didn't open a lane in {project.name}: no provider has room now "
                        f"({full or proposal.reason}). Name a model to use anyway, for example "
                        '"use Sonnet" or "use Luna", and I open it on that.'
                    ),
                    task=task,
                    decision=decision,
                    extra=extra,
                )
            provider, word, effort = proposal.provider, proposal.model, proposal.effort
            judged_as = (
                f"a close call, so {decision.tier} tier"
                if decision.close_call
                else f"{decision.tier} tier"
            )
            moved = (
                ""
                if tier == decision.tier
                else f"; no provider had room for {decision.tier}, so {tier}"
            )
            reason = (
                f"The router judged it {judged_as}, {decision.effort} effort{moved}; "
                f"{proposal.reason}."
            )
        else:
            # No decision model: the same run as the chat it opens beside, or
            # Claude Code's defaults beside an agent lanes do not run on.
            known = like.run.provider in hub_lanes.PROVIDER_OF_HARNESS
            why = (decision.error if decision is not None else "") or "the router is off"
            run = hub_lanes.LaneRun(
                provider=hub_lanes.PROVIDER_OF_HARNESS.get(like.run.provider, "claude"),
                model=(like.run.requested or like.run.model) if known else "",
                effort=like.run.effort if known else "",
                reason=f"Same as {like.name}: {why}.",
            )
            return _LanePick(run=run, task=task, decision=decision)
        catalog = await self._catalog(provider, like.id)
        launch = hub_lanes.launch_model(provider, word, effort, catalog)
        if launch is None:
            label = hub_lanes.PROVIDER_LABELS.get(provider, provider)
            return _LanePick(
                why=f"I didn't open a lane: {label} offers no {word} model on this machine.",
                task=task,
                decision=decision,
                extra=extra,
            )
        run = hub_lanes.LaneRun(
            provider=provider,
            model=launch[0],
            effort=launch[1],
            tier=tier if judged else "",
            reason=reason,
        )
        return _LanePick(run=run, task=task, decision=decision if judged else None, extra=extra)

    async def _catalog(self, provider: str, like_id: str) -> list[str]:
        backend = self.require_backend()
        listed = await backend.catalog(provider, like_id) if backend.catalog is not None else []
        if provider == "gemini" and not listed:
            listed = list(_agy_models)
        return listed

    async def rename_lane(self, lane: HubSession, name: str) -> HubEntry:
        """Give a lane another name: its chat's title too."""
        name = hub_lanes.topic_name(name, "")
        if not name:
            raise ValueError("a lane needs a name")
        backend = self.require_backend()
        if backend.rename_chat is None:
            raise ValueError("lanes can't be renamed here")
        await backend.rename_chat(lane.id, name)
        return self.say(
            f"Renamed the lane {lane.lane_name} to {name}.",
            kind="project",
            session_id=lane.id,
            session_name=lane.name,
        )

    async def close_lane(self, lane: HubSession, *, handoff: bool) -> HubEntry:
        """Archive a lane; with *handoff*, it first writes its handoff note.

        :raises ValueError: It is working: closing would cut its task short.
        """
        backend = self.require_backend()
        if backend.close_chat is None:
            raise ValueError("lanes can't be closed here")
        if lane.working:
            raise ValueError(f"{lane.name} is working; wait for it to finish first")
        if not handoff:
            await backend.close_chat(lane.id)
            return self.say(
                f"Closed the lane {lane.lane_name}.",
                kind="project",
                session_id=lane.id,
                session_name=lane.name,
            )
        entry = await self.send_to(lane, HANDOFF_REQUEST, via="panel", judge=False)
        if entry.kind == "sent":
            self._spawn(self._close_after_report(lane))
        return entry

    async def _close_after_report(self, lane: HubSession) -> None:
        # The watch ends once the handoff turn is reported back.
        watch = self._watches.get(lane.id)
        if watch is not None:
            await asyncio.wait({watch})
        await self.require_backend().close_chat(lane.id)  # type: ignore[misc]
        self.say(
            f"Closed the lane {lane.lane_name}; its handoff note is in its last reply.",
            kind="project",
            session_id=lane.id,
            session_name=lane.name,
        )

    def previous_exchange(self, session_id: str, *, before: int) -> tuple[str, str]:
        """The last request sent to a chat before entry ``before``, and its report."""
        entries = [e for e in self.state.entries if e.id < before and e.session_id == session_id]
        for index in range(len(entries) - 1, -1, -1):
            if entries[index].kind != "sent":
                continue
            reports = [e for e in entries[index + 1 :] if e.kind == "finished"]
            result = reports[0].text if reports else ""
            name = entries[index].session_name or ""
            return entries[index].detail or "", _without_label(result, f"{name} finished:")
        return "", ""

    async def _judge_tier(self, session: HubSession, message: str, *, before: int) -> None:
        backend = model_router.configured(HUB_DIR)
        if backend is None:
            return
        previous, result = self.previous_exchange(session.id, before=before)
        task = model_router.RouterTask(
            request=message,
            project=session.name,
            session_id=session.id,
            provider=session.run.provider,
            model=session.run.requested or session.run.model,
            effort=session.run.effort,
            previous=previous,
            previous_result=result,
        )
        decision = await model_router.judge(task, backend)
        extra: dict[str, Any] = {}
        if decision.tier:
            budgets = await fresh_budgets()
            extra = {
                "proposal": budget.pick(
                    decision.tier, decision.effort, budgets, agy_models=_agy_models
                ).as_dict(),
                "budgets": budget.snapshot(budgets),
            }
        model_router.log_decision(
            self.state.folder / model_router.LOG_NAME, task, decision, extra=extra
        )

    def last_routes(self) -> dict[str, dict[str, Any]]:
        """The newest logged decision for each chat: what the router would pick."""
        return model_router.last_by_session(self.state.folder / model_router.LOG_NAME)

    def watch(self, session: HubSession, sent_at: float) -> None:
        """Report back when the session finishes; one watch per session.

        The watch is written down first, so a server restart resumes it
        (:meth:`resume_watches`) instead of losing the report.
        """
        running = self._watches.get(session.id)
        if running is not None and not running.done():
            return
        # New work replaces following the last: its watch reports what comes next.
        following = self._follows.pop(session.id, None)
        if following is not None:
            following.cancel()
        self.state.keep_watch(
            session.id,
            sent_at=sent_at,
            name=session.name,
            user_id=self.user_id,
            base_url=getattr(self.chat, "base_url", "") or "",
        )
        task = asyncio.ensure_future(self._watch(session, sent_at))
        self._watches[session.id] = task

    def resume_watches(self) -> int:
        """Start again the watches and held deliveries a restart stopped; returns how many."""
        resumed = 0
        for session_id, kept in list(self.state.watches.items()):
            sent_at = float(kept.get("sent_at") or 0.0)
            if time.time() - sent_at > WATCH_MAX_S:
                self.state.drop_watch(session_id)
                continue
            running = self._watches.get(session_id) or self._follows.get(session_id)
            if running is not None and not running.done():
                continue
            name = str(kept.get("name") or session_id[:8])
            session = HubSession(id=session_id, title=name, nickname=name)
            if kept.get("follow"):
                self.follow(session, sent_at)
            else:
                self._watches[session_id] = asyncio.ensure_future(self._watch(session, sent_at))
            resumed += 1
        for session_id, kept in list(self.state.held.items()):
            name = str(kept.get("name") or session_id[:8])
            self._deliver_when_idle(HubSession(id=session_id, title=name, nickname=name))
            resumed += 1
        return resumed

    async def _watch(self, session: HubSession, sent_at: float) -> None:
        # A resumed watch keeps its original budget, not a fresh one.
        deadline = time.monotonic() + WATCH_MAX_S - max(0.0, time.time() - sent_at)
        started = time.monotonic()
        seen_working = False
        idle_polls = 0
        summary_polls = max(2, round(SUMMARY_WAIT_S / self._poll_s))
        told_approval = False
        ended = True
        reported = False
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(self._poll_s)
                state = await self.require_backend().turn(session.id, sent_at)
                if state.pending_approvals and not told_approval:
                    told_approval = True
                    self.say(
                        f"{session.name} is waiting for your approval.",
                        kind="attention",
                        session_id=session.id,
                        session_name=session.name,
                    )
                if state.working:
                    seen_working = True
                    idle_polls = 0
                    continue
                idle_polls += 1
                # Idle twice in a row: a native session can go idle a moment
                # before its final message is stored.
                if (
                    state.reply
                    and idle_polls >= 2
                    and (not state.summary_due or idle_polls >= summary_polls)
                ):
                    await self._report(session, state)
                    reported = True
                    return
                # A reply waiting on its summary is reported above once the wait ends.
                if seen_working and not state.reply and idle_polls >= 6:
                    self.say(
                        f"{session.name} finished without writing a reply.",
                        kind="finished",
                        session_id=session.id,
                        session_name=session.name,
                    )
                    return
                if not seen_working and time.monotonic() - started > NOT_STARTED_S:
                    self.say(
                        f"{session.name} hasn't started on it yet. It may need you to open it.",
                        kind="attention",
                        session_id=session.id,
                        session_name=session.name,
                    )
                    return
        except asyncio.CancelledError:
            # Stopped, not finished (a shutdown): kept on disk for the restart.
            ended = False
            raise
        except Exception:  # noqa: BLE001 - a broken watch must not take the hub down
            _logger.warning("hub watch on %s failed", session.id, exc_info=True)
        finally:
            self._watches.pop(session.id, None)
            if ended:
                self.state.drop_watch(session.id)
            if reported:
                self.follow(session, time.time())

    async def report_turn(
        self, session: HubSession, since: float, until: float
    ) -> HubEntry | None:
        """Report a lane's turn between *since* and *until* that the hub missed.

        :returns: The report, or ``None`` when that window has no reply.
        """
        backend = self.require_backend()
        state = await backend.turn(session.id, since, until)
        if not state.reply:
            return None
        await self._report(session, state)
        return next((e for e in reversed(self.state.entries) if e.kind == "finished"), None)

    def follow(self, session: HubSession, since: float) -> None:
        """After a report, report again what the lane goes on to do by itself.

        A lane can end its reply while a background job of its own runs (a
        build it monitors) and carry on when that job pings it, with no message
        from anyone. Kept on disk like a watch, so a restart resumes it.
        """
        running = self._follows.get(session.id)
        if running is not None and not running.done():
            running.cancel()
        self.state.keep_watch(
            session.id,
            sent_at=since,
            name=session.name,
            follow=True,
            user_id=self.user_id,
            base_url=getattr(self.chat, "base_url", "") or "",
        )
        self._follows[session.id] = asyncio.ensure_future(self._follow(session, since))

    async def _follow(self, session: HubSession, since: float) -> None:
        deadline = time.monotonic() + FOLLOW_MAX_S - max(0.0, time.time() - since)
        settle_polls = max(2, round(self._follow_settle_s / self._poll_s))
        quiet = 0
        ended = True
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(self._poll_s)
                state = await self.require_backend().turn(session.id, since)
                if state.typed:
                    # Someone wrote in the lane's own chat: they are reading it there.
                    return
                if not state.woke_itself:
                    continue
                if state.working:
                    quiet = 0
                    continue
                quiet += 1
                if state.reply and quiet >= settle_polls:
                    await self._report(session, state)
                    since = time.time()
                    self.state.keep_watch(
                        session.id, sent_at=since, name=session.name, follow=True
                    )
                    deadline = time.monotonic() + FOLLOW_MAX_S
                    quiet = 0
        except asyncio.CancelledError:
            ended = False
            raise
        except Exception:  # noqa: BLE001 - a broken follow must not take the hub down
            _logger.warning("hub follow on %s failed", session.id, exc_info=True)
        finally:
            if self._follows.get(session.id) is asyncio.current_task():
                self._follows.pop(session.id, None)
                if ended:
                    self.state.drop_watch(session.id)

    async def _report(self, session: HubSession, state: TurnState) -> None:
        reply = state.reply
        router = await self._router_factory()
        # The project's own summary is the report: it is what its chat shows,
        # and a summary of it lost the details the reader came for.
        told = await self._in_hub_language(router, session, state) if state.summary else ""
        if not told:
            try:
                told = (
                    await router.perform(
                        _REPORT_TASK.format(name=session.name, reply=_short(reply, 6000)),
                        timeout_s=ROUTE_TIMEOUT_S,
                    )
                ).strip()
            except DiscussionUnavailable:
                told = _short(reply, 300)
        told = _without_label(told, f"{session.name} finished:")
        from omnigent.server.hub_chat import handed_files

        anchors = anchor_dirs(state.touched, state.workspace)
        handed = handed_files(reply, state.workspace, anchors, since=state.since)
        self.say(
            f"{session.name} finished: {told}",
            kind="finished",
            session_id=session.id,
            session_name=session.name,
            detail=reply[:MAX_REPLY_CHARS],
            workspace=state.workspace,
            anchors=anchors or None,
            files=await self._import_files(session, _files_to_copy(state)) or None,
            show=[b for b in state.show if b.get("kind") not in ("file", None)] or None,
            links=[[label, relative] for label, relative in handed],
        )
        root = pathlib.Path(state.workspace or "/")
        router.tell(
            f"{session.name} finished: {told}"
            + "".join(f"\n  file it handed over: {root / relative}" for _, relative in handed)
        )

    async def _in_hub_language(
        self, router: HubRouter, session: HubSession, state: TurnState
    ) -> str:
        """The project's summary in the hub's language: as written, or translated whole."""
        summary = state.summary.strip()
        hub_lang = await self.language()
        if not hub_lang or not state.summary_lang or _same_language(hub_lang, state.summary_lang):
            return summary
        try:
            translated = (
                await router.perform(
                    _TRANSLATE_TASK.format(
                        name=session.name, language=language_phrase(hub_lang), summary=summary
                    ),
                    timeout_s=ROUTE_TIMEOUT_S,
                )
            ).strip()
        except DiscussionUnavailable:
            translated = ""
        # Untranslated beats lost.
        return translated or summary

    async def complete_spoken(self, name: str, message: str, heard: str) -> str:
        """A voice's message for a project, rewritten to carry everything they said.

        Falls back to the message with their words under it when the router
        cannot answer in time, or answers with less than the voice wrote: a
        long message beats a lost question.
        """
        words = " ".join(heard.split())
        if not words or voice_hub.normalize(words) in voice_hub.normalize(message):
            return message
        lang = await self.language()
        router = await self._router_factory()
        try:
            rewritten = (
                await router.perform(
                    _COMPLETE_TASK.format(
                        name=name,
                        language=language_phrase(lang),
                        message=message,
                        heard=words,
                    ),
                    timeout_s=COMPLETE_TIMEOUT_S,
                )
            ).strip()
        except DiscussionUnavailable:
            rewritten = ""
        if len(rewritten) < len(message):
            _logger.info("hub voice send kept their words | rewrite=%d chars", len(rewritten))
            return voice_hub.with_their_words(message, words, lang)
        return rewritten

    async def _import_files(
        self, session: HubSession, files: Sequence[ReplyFile]
    ) -> list[dict[str, Any]]:
        """Copy a reply's files into the hub chat; a file id only works in its own chat."""
        backend = self.require_backend()
        if not files or self.chat is None or backend.import_files is None:
            return []
        try:
            return await backend.import_files(session.id, self.chat.session_id, files)
        except Exception:  # noqa: BLE001 - the report still goes out, without its files
            _logger.warning("hub could not copy %s's files", session.name, exc_info=True)
            return []

    async def language(self) -> str:
        """The hub chat's language setting, e.g. ``"pt-BR"``; ``""`` when unset."""
        if self.chat is None or not hasattr(self.chat, "language"):
            return ""
        try:
            return await self.chat.language()
        except Exception:  # noqa: BLE001 - unset is a safe answer
            _logger.debug("hub language unknown", exc_info=True)
            return ""

    async def close(self) -> None:
        """Stop every watch, held delivery and pending answer."""
        running = [
            *self._watches.values(),
            *self._follows.values(),
            *self._holds.values(),
            *self._tasks,
        ]
        for task in running:
            task.cancel()
        for task in running:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._watches.clear()
        self._follows.clear()
        self._holds.clear()


def user_key(user_id: str | None) -> str:
    """A directory-safe key for a user, ``"local"`` when auth is off."""
    raw = user_id or "local"
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in raw)[:80] or "local"


_HUBS: dict[str, Hub] = {}


def hub_for(user_id: str | None) -> Hub:
    """The process-wide hub for one user, made on first use."""
    from omnigent.server.discussion import registry

    key = user_key(user_id)
    hub = _HUBS.get(key)
    if hub is None:

        async def router() -> HubRouter:
            await resolve_hub_model()
            made = await registry().get(f"hub-{key}", factory=HubRouter)
            assert isinstance(made, HubRouter)
            return made

        hub = Hub(key, router_factory=router, user_id=user_id, judge_tiers=True)
        _HUBS[key] = hub
    return hub


async def close_all() -> None:
    """Stop every hub's watches, for shutdown."""
    for hub in list(_HUBS.values()):
        await hub.close()
    _HUBS.clear()


# -- reading sessions from the stores ------------------------------------------


def _item_text(data: Any) -> str:
    content = getattr(data, "content", None)
    if content is None and isinstance(data, dict):
        content = data.get("content")
    parts = []
    for block in content or []:
        text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return " ".join(parts).strip()


def _role(data: Any) -> str:
    role = getattr(data, "role", None)
    if role is None and isinstance(data, dict):
        role = data.get("role")
    return str(role or "")


def _field(obj: Any, key: str) -> Any:
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def _blocks(data: Any) -> list[Any]:
    content = _field(data, "content")
    return list(content) if isinstance(content, list) else []


def reply_text(data: Any) -> str:
    """A message's answer text: its ``output_text`` blocks, paragraphs kept.

    Not its spoken summary or translation, which ride along as other blocks.
    """
    parts = [
        str(_field(b, "text") or "").strip()
        for b in _blocks(data)
        if _field(b, "type") in ("output_text", "text")
    ]
    return "\n\n".join(p for p in parts if p)


def spoken_summary(data: Any) -> dict[str, Any] | None:
    """The spoken summary block a message carries, if any."""
    for block in _blocks(data):
        if _field(block, "type") == "spoken_summary":
            return block if isinstance(block, dict) else dict(vars(block))
    return None


_SESSION_FILE: Final[re.Pattern[str]] = re.compile(r"^/v1/sessions/[^/]+/resources/files/([^/]+)")


def _files_to_copy(state: TurnState) -> list[ReplyFile]:
    """The reply's attachments plus the files its summary shows, each once."""
    found = list(state.files)
    for block in state.show:
        content = str(block.get("content") or "")
        session_file = _SESSION_FILE.match(content)
        if block.get("kind") == "file" and content:
            file_id = content
        elif block.get("kind") == "image" and session_file:
            file_id = session_file.group(1)
        else:
            continue
        found.append(
            ReplyFile(
                file_id=file_id,
                filename=str(block.get("filename") or block.get("label") or file_id),
                mime_type=str(block.get("mime_type") or ""),
            )
        )
    seen: set[str] = set()
    return [f for f in found if not (f.file_id in seen or seen.add(f.file_id))]


def reply_files(data: Any) -> list[ReplyFile]:
    """The files a message carries (``output_file`` blocks: charts, images)."""
    found = []
    for block in _blocks(data):
        file_id = _field(block, "file_id")
        if _field(block, "type") == "output_file" and isinstance(file_id, str) and file_id:
            found.append(
                ReplyFile(
                    file_id=file_id,
                    filename=str(_field(block, "filename") or file_id),
                    mime_type=str(_field(block, "mime_type") or ""),
                )
            )
    return found


#: Messages written into a session that are not the person asking: system
#: notices and harness notifications such as ``<task-notification>``.
_SYSTEM_PREFIXES: Final[tuple[str, ...]] = ("[System:", "[system:", "<")


def _is_ask(data: Any) -> bool:
    if _role(data) != "user":
        return False
    text = _item_text(data)
    return bool(text) and not text.startswith(_SYSTEM_PREFIXES)


def live_status(session_id: str, stored: str | None) -> str | None:
    """The freshest status: the in-memory cache, else the stored one."""
    try:
        from omnigent.server.routes._sessions.common import _session_status_cache

        return _session_status_cache.get(session_id, stored)
    except Exception:  # noqa: BLE001 - the stored status still answers
        return stored


async def live_activity(conversation_store: Any, session_id: str) -> str:
    """What a session's Claude is doing now, from its newest stored items."""
    conv = await asyncio.to_thread(conversation_store.get_conversation, session_id)
    page = await asyncio.to_thread(
        conversation_store.list_items, session_id, limit=80, order="desc"
    )
    status = live_status(session_id, getattr(conv, "live_status", None))
    writing = ""
    try:
        from omnigent.runtime import inflight_text

        # Text Claude is streaming now is held in memory until the turn ends.
        writing = " ".join(
            str(event.get("delta", ""))
            for event in inflight_text.snapshot_for(session_id)
            if event.get("type") == "response.output_text.delta"
        ).strip()
    except Exception:  # noqa: BLE001 - the stored items still answer
        _logger.debug("live activity extras unavailable", exc_info=True)
    return voice_hub.render_activity(page.data, status, writing=writing)


def _current_step(items_newest_first: Sequence[Any]) -> str:
    """The newest tool call of the current turn, named the way a person would."""
    for item in items_newest_first:
        kind, _response_id, data = voice_hub.item_fields(item)
        if kind == "function_call":
            return voice_hub.step_label(
                str(data.get("name") or "tool"), str(data.get("arguments") or "")
            )
        if kind == "message" and data.get("role") == "user":
            return ""
    return ""


def store_backend(
    conversation_store: Any,
    *,
    user_id: str | None,
    app: Any,
    headers: dict[str, str],
    companions: Callable[[], Any],
    exclude: set[str] | frozenset[str] = frozenset(),
) -> HubBackend:
    """The hub's backend over the server's own stores and routes.

    :param conversation_store: Sessions and their items.
    :param user_id: Whose sessions; ``None`` when auth is off.
    :param app: The ASGI app, for sending through its own events route.
    :param headers: The caller's credentials, forwarded on sends.
    :param companions: The companion registry, for summaries and checks.
    :param exclude: Sessions never offered, such as the hub's own conversation.
    """
    details: dict[str, tuple[float, dict[str, Any]]] = {}
    runs: dict[str, tuple[tuple[Any, ...], SessionRun]] = {}

    def _client() -> Any:
        import httpx

        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://hub", timeout=60
        )

    async def _run(conv: Any) -> SessionRun:
        """What it runs on, from its own snapshot; re-read only when it changed."""
        key = (
            getattr(conv, "updated_at", None),
            getattr(conv, "model_override", None),
            getattr(conv, "reasoning_effort", None),
            getattr(conv, "reported_model", None),
            getattr(conv, "agent_id", None),
        )
        cached = runs.get(conv.id)
        if cached is not None and cached[0] == key:
            return cached[1]
        run = await read_run(conv.id)
        runs[conv.id] = (key, run)
        return run

    async def read_run(session_id: str) -> SessionRun:
        try:
            async with _client() as client:
                response = await client.get(
                    f"/v1/sessions/{session_id}",
                    params={"include_items": "false", "include_liveness": "false"},
                    headers=headers,
                )
                response.raise_for_status()
                snapshot = response.json()
                fallback: list[Any] = []
                harness = hub_models.HOST_CATALOG_HARNESS.get(hub_models.picker_kind(snapshot))
                if harness and not snapshot.get("model_options") and snapshot.get("host_id"):
                    hosted = await client.get(
                        f"/v1/hosts/{snapshot['host_id']}/harnesses/{harness}/model-options",
                        headers=headers,
                    )
                    if hosted.status_code == 200:
                        fallback = list(hosted.json().get("models") or [])
        except Exception:  # noqa: BLE001 - a session without its run info still routes
            _logger.debug("hub could not read %s's model settings", session_id, exc_info=True)
            return SessionRun()
        return hub_models.run_from_snapshot(snapshot, fallback)

    def _details(session_id: str, updated_at: float) -> dict[str, Any]:
        cached = details.get(session_id)
        if cached is not None and cached[0] == updated_at:
            return cached[1]
        first = conversation_store.list_items(session_id, limit=15, order="asc", type="message")
        recent = conversation_store.list_items(session_id, limit=40, order="desc", type="message")
        asks_first = [i for i in first.data if _is_ask(i.data)]
        asks_recent = [i for i in recent.data if _is_ask(i.data)]
        said = next(
            (
                _item_text(i.data)
                for i in recent.data
                if _role(i.data) == "assistant" and _item_text(i.data)
            ),
            "",
        )
        found = {
            "first_ask": _item_text(asks_first[0].data) if asks_first else "",
            "last_ask": _item_text(asks_recent[0].data) if asks_recent else "",
            "said": said,
        }
        details[session_id] = (updated_at, found)
        return found

    def _offered(conv: Any) -> bool:
        labels = getattr(conv, "labels", None) or {}
        return (
            conv.id not in exclude
            and labels.get("omnigent.hub") != "1"
            and labels.get("omnigent.hub_terminal") != "1"
        )

    async def listing() -> list[HubCandidate]:
        page = await asyncio.to_thread(
            conversation_store.list_conversations,
            limit=LISTING_LIMIT + len(exclude) + 1,
            owned_by=user_id,
            project="",
            sort_by="updated_at",
            order="desc",
        )
        return [
            HubCandidate(
                id=conv.id,
                title=getattr(conv, "title", None) or "",
                updated_at=float(getattr(conv, "updated_at", 0) or 0),
                provider=hub_models.PROVIDERS.get(
                    str((getattr(conv, "labels", None) or {}).get("omnigent.wrapper") or ""), ""
                ),
            )
            for conv in page.data
            if _offered(conv)
        ][:LISTING_LIMIT]

    async def sessions(session_ids: Sequence[str]) -> list[HubSession]:
        picked = []
        for session_id in session_ids:
            conv = await asyncio.to_thread(conversation_store.get_conversation, session_id)
            if conv is not None and _offered(conv) and not getattr(conv, "archived", False):
                picked.append(conv)
        run_of = await asyncio.gather(*(_run(conv) for conv in picked))
        out: list[HubSession] = []
        for conv, run in zip(picked, run_of, strict=True):
            updated = float(getattr(conv, "updated_at", 0) or 0)
            found = await asyncio.to_thread(_details, conv.id, updated)
            status = live_status(conv.id, getattr(conv, "live_status", None))
            now = ""
            if (status or "") in voice_hub.WORKING_STATUSES:
                items = await asyncio.to_thread(
                    conversation_store.list_items, conv.id, limit=12, order="desc"
                )
                now = _current_step(items.data)
            summary = None
            companion = companions().peek(conv.id)
            if companion is not None:
                for entry in reversed(companion.context):
                    if entry.kind == "summary":
                        summary = entry.text
                        break
            out.append(
                HubSession(
                    id=conv.id,
                    title=getattr(conv, "title", None) or "",
                    status=status,
                    updated_at=updated,
                    first_ask=found["first_ask"],
                    last_ask=found["last_ask"],
                    said=found["said"],
                    summary=summary,
                    now=now,
                    pending_approvals=int(getattr(conv, "pending_elicitation_count", 0) or 0),
                    run=run,
                    project_id=str(getattr(conv, "project_id", None) or ""),
                    lane=str(
                        (getattr(conv, "labels", None) or {}).get(hub_lanes.LANE_LABEL) or ""
                    ),
                )
            )
        return out

    async def _chat_ids(name: str) -> tuple[str, ...]:
        page = await asyncio.to_thread(
            conversation_store.list_conversations,
            limit=CHATS_PER_PROJECT,
            owned_by=user_id,
            project=name,
            sort_by="updated_at",
            order="desc",
        )
        return tuple(conv.id for conv in page.data if _offered(conv))

    def _record(row: dict[str, Any], chat_ids: tuple[str, ...]) -> ProjectRecord:
        config = row.get("config") or {}
        return ProjectRecord(
            id=str(row["id"]),
            name=str(row["name"]),
            icon=str(config.get("icon") or ""),
            folder=str(config.get("workspace") or ""),
            chat_ids=chat_ids,
            created_at=float(row.get("created_at") or 0),
        )

    async def projects() -> list[ProjectRecord]:
        async with _client() as client:
            response = await client.get("/v1/projects", headers=headers)
        if response.status_code == 404:
            # A server without a project store: nothing to run yet.
            return []
        response.raise_for_status()
        rows = list(response.json().get("data") or [])
        chat_ids = await asyncio.gather(*(_chat_ids(str(row["name"])) for row in rows))
        return [_record(row, ids) for row, ids in zip(rows, chat_ids, strict=True)]

    async def file_chat(session_id: str, project_id: str) -> None:
        async with _client() as client:
            response = await client.patch(
                f"/v1/sessions/{session_id}", json={"project_id": project_id}, headers=headers
            )
        response.raise_for_status()

    async def create_project(name: str, from_chat: str | None) -> ProjectRecord:
        # A chat it starts from sets where its new chats start: same folder,
        # machine and agent.
        config: dict[str, Any] = {}
        if from_chat:
            conv = await asyncio.to_thread(conversation_store.get_conversation, from_chat)
            for key in ("workspace", "host_id", "agent_id"):
                value = getattr(conv, key, None)
                if value:
                    config[key] = str(value)
        async with _client() as client:
            response = await client.post(
                "/v1/projects", json={"name": name, "config": config}, headers=headers
            )
        response.raise_for_status()
        row = response.json()
        if from_chat:
            await file_chat(from_chat, str(row["id"]))
        return _record(row, (from_chat,) if from_chat else ())

    async def rename_project(project_id: str, name: str) -> None:
        async with _client() as client:
            response = await client.patch(
                f"/v1/projects/{project_id}", json={"name": name}, headers=headers
            )
        response.raise_for_status()

    async def delete_project(project_id: str) -> int:
        # Its chats leave it first: the projects route deletes only the row.
        found = next((r for r in await projects() if r.id == project_id), None)
        chat_ids = found.chat_ids if found is not None else ()
        for chat_id in chat_ids:
            await file_chat(chat_id, "")
        async with _client() as client:
            response = await client.delete(f"/v1/projects/{project_id}", headers=headers)
        response.raise_for_status()
        return len(chat_ids)

    async def _copy_file(client: Any, file: HubFile, session_id: str) -> dict[str, Any]:
        """Copy a file from the hub into a chat: a file id only works in its own chat."""
        got = await client.get(
            f"/v1/sessions/{file.session_id}/resources/files/{file.file_id}/content",
            headers=headers,
        )
        got.raise_for_status()
        from omnigent.runner.identity import OMNIGENT_INTERNAL_WS_ORIGIN

        # The server's own upload: the reader's Origin passed its check when
        # they attached the file, and may not be loopback.
        made = await client.post(
            f"/v1/sessions/{session_id}/resources/files",
            files={
                "file": (
                    file.filename,
                    got.content,
                    got.headers.get("content-type", "application/octet-stream"),
                )
            },
            headers={**headers, "origin": OMNIGENT_INTERNAL_WS_ORIGIN},
        )
        made.raise_for_status()
        return {"type": file.kind, "file_id": made.json()["id"], "filename": file.filename}

    async def send(session_id: str, text: str, files: Sequence[HubFile] = ()) -> None:
        async with _client() as client:
            attached = [await _copy_file(client, f, session_id) for f in files]
            body = {
                "type": "message",
                "data": {
                    "role": "user",
                    "content": [
                        *attached,
                        {"type": "input_text", "text": text},
                        {"type": "force_claude"},
                    ],
                },
            }
            response = await client.post(
                f"/v1/sessions/{session_id}/events", json=body, headers=headers
            )
        response.raise_for_status()

    async def status(session_id: str) -> str | None:
        conv = await asyncio.to_thread(conversation_store.get_conversation, session_id)
        if conv is None or getattr(conv, "archived", False):
            return None
        return live_status(session_id, getattr(conv, "live_status", None)) or ""

    agent_ids: dict[str, str] = {}

    async def _agent_id(client: Any, name: str) -> str:
        if name not in agent_ids:
            response = await client.get("/v1/agents", params={"limit": 100}, headers=headers)
            response.raise_for_status()
            for agent in response.json().get("data") or []:
                agent_ids.setdefault(str(agent.get("name")), str(agent.get("id")))
        if name not in agent_ids:
            raise LookupError(f"no {name} agent on this server")
        return agent_ids[name]

    async def open_chat(project_id: str, spec: hub_lanes.LaneSpec) -> str:
        # Same machine and folder as the chat it opens beside; the CLI keeps the
        # machine's own permission settings, as a chat made with the defaults does.
        like = await asyncio.to_thread(conversation_store.get_conversation, spec.like)
        host_id, workspace = getattr(like, "host_id", None), getattr(like, "workspace", None)
        if not host_id or not workspace:
            raise LookupError("the chat it opens beside has no machine or folder")
        provider = spec.run.provider
        async with _client() as client:
            body: dict[str, Any] = {
                "agent_id": await _agent_id(client, hub_lanes.AGENT_NAMES[provider]),
                "project_id": project_id,
                "host_id": str(host_id),
                "workspace": str(workspace),
                "title": spec.topic,
                "labels": {
                    "omnigent.ui": "terminal",
                    "omnigent.wrapper": hub_lanes.WRAPPERS[provider],
                    hub_lanes.LANE_LABEL: spec.topic,
                },
            }
            if spec.run.model:
                body["model_override"] = spec.run.model
            if spec.run.effort:
                body["reasoning_effort"] = spec.run.effort
            if spec.branch:
                body["git"] = {"branch_name": spec.branch}
            response = await client.post("/v1/sessions", json=body, headers=headers)
        response.raise_for_status()
        return str(response.json()["id"])

    async def catalog(provider: str, like_id: str) -> list[str]:
        conv = await asyncio.to_thread(conversation_store.get_conversation, like_id)
        host_id = getattr(conv, "host_id", None)
        harness = hub_lanes.HARNESSES.get(provider)
        if not host_id or harness is None:
            return []
        async with _client() as client:
            response = await client.get(
                f"/v1/hosts/{host_id}/harnesses/{harness}/model-options", headers=headers
            )
        if response.status_code != 200:
            return []
        return [str(m["id"]) for m in response.json().get("models") or [] if m.get("id")]

    async def paths_in_use(session_id: str) -> list[str]:
        calls = await asyncio.to_thread(
            conversation_store.list_items,
            session_id,
            limit=_TURN_CALLS,
            order="desc",
            type="function_call",
        )
        return touched_paths(calls.data)

    async def rename_chat(session_id: str, name: str) -> None:
        async with _client() as client:
            response = await client.patch(
                f"/v1/sessions/{session_id}",
                json={"title": name, "labels": {hub_lanes.LANE_LABEL: name}},
                headers=headers,
            )
        response.raise_for_status()

    async def close_chat(session_id: str) -> None:
        async with _client() as client:
            response = await client.patch(
                f"/v1/sessions/{session_id}", json={"archived": True}, headers=headers
            )
        response.raise_for_status()

    async def configure(session_id: str, model: str | None, effort: str | None) -> None:
        # One change per request, model first: a Claude Code session types
        # each into its terminal, and two at once would interleave.
        async with _client() as client:
            for field_name, value in (("model_override", model), ("reasoning_effort", effort)):
                if value is None:
                    continue
                response = await client.patch(
                    f"/v1/sessions/{session_id}", json={field_name: value}, headers=headers
                )
                response.raise_for_status()
        runs.pop(session_id, None)

    async def check(session_id: str, question: str) -> str:
        conv = await asyncio.to_thread(conversation_store.get_conversation, session_id)
        companion = await companions().get(session_id)
        result = await companion.check(question, cwd=getattr(conv, "workspace", None))
        if result.answer:
            return result.answer
        return (
            f"I couldn't find out with read-only commands: {result.needs_claude or 'no answer'}."
        )

    async def turn(session_id: str, since: float, until: float | None = None) -> TurnState:
        conv = await asyncio.to_thread(conversation_store.get_conversation, session_id)
        status = live_status(session_id, getattr(conv, "live_status", None))
        # A past turn (*until* given) may sit behind newer work: read further back.
        recent = await asyncio.to_thread(
            conversation_store.list_items,
            session_id,
            limit=30 if until is None else 400,
            order="desc",
            type="message",
        )

        def in_window(item: Any) -> bool:
            return until is None or float(getattr(item, "created_at", 0) or 0) <= until + 1

        # Newest first. The answer is the newest message with answer text: a
        # spoken summary or an attached chart's caption comes after it.
        reply, reply_response = "", None
        files: list[ReplyFile] = []
        summaries: list[tuple[Any, dict[str, Any]]] = []
        woke_itself = typed = False
        for item in recent.data:
            if float(getattr(item, "created_at", 0) or 0) + 1 < since:
                break
            if not in_window(item):
                continue
            if _role(item.data) == "user":
                said = _item_text(item.data).lstrip()
                if said.startswith(_SELF_WAKE_PREFIXES):
                    woke_itself = True
                elif said:
                    typed = True
                continue
            if _role(item.data) != "assistant":
                continue
            files[:0] = reply_files(item.data)
            summary = spoken_summary(item.data)
            if summary is not None:
                summaries.append((getattr(item, "response_id", None), summary))
            if not reply and _field(item.data, "agent") != "attachment":
                reply = reply_text(item.data)
                reply_response = getattr(item, "response_id", None) if reply else None
        calls = await asyncio.to_thread(
            conversation_store.list_items,
            session_id,
            limit=_TURN_CALLS if until is None else 4 * _TURN_CALLS,
            order="desc",
            type="function_call",
        )
        this_turn = [
            item
            for item in calls.data
            if float(getattr(item, "created_at", 0) or 0) + 1 >= since and in_window(item)
        ]
        # The newest summary of this reply (a summary is revised once its audio is made).
        chosen = next((s for r, s in summaries if r == reply_response), None) if reply else None
        show = tuple(
            b
            for b in (chosen or {}).get("show") or []
            if isinstance(b, dict) and b.get("kind") in SHOWN_KINDS
        )
        return TurnState(
            status=status if until is None else "idle",
            reply=reply,
            pending_approvals=int(getattr(conv, "pending_elicitation_count", 0) or 0),
            files=tuple(files),
            workspace=getattr(conv, "workspace", None),
            touched=tuple(touched_paths(this_turn)),
            show=show,
            summary=str((chosen or {}).get("text") or "").strip(),
            summary_lang=str((chosen or {}).get("lang") or ""),
            summary_due=bool(reply) and chosen is None and await _summaries_on(session_id),
            since=since,
            woke_itself=woke_itself,
            typed=typed,
        )

    async def _summaries_on(session_id: str) -> bool:
        from omnigent.server.spoken_summary import resolve_spoken_summary_settings_async

        try:
            enabled, _language, _conv = await resolve_spoken_summary_settings_async(
                session_id, conversation_store
            )
        except Exception:  # noqa: BLE001 - unknown: report without waiting
            return False
        return bool(enabled)

    async def import_files(
        source_id: str, target_id: str, files: Sequence[ReplyFile]
    ) -> list[dict[str, Any]]:
        if _file_stores is None:
            return []
        file_store, artifact_store = _file_stores

        def copy(file: ReplyFile) -> dict[str, Any] | None:
            meta = file_store.get(file.file_id, session_id=source_id)
            if meta is None or (meta.bytes or 0) > MAX_REPLY_FILE_BYTES:
                return None
            data = artifact_store.get(file.file_id)
            made = file_store.create(
                meta.filename or file.filename, len(data), meta.content_type, target_id
            )
            artifact_store.put(made.id, data)
            return {
                "type": "output_file",
                "file_id": made.id,
                "filename": made.filename,
                "mime_type": meta.content_type or file.mime_type,
            }

        copied = []
        for file in list(files)[:MAX_REPLY_FILES]:
            try:
                block = await asyncio.to_thread(copy, file)
            except Exception:  # noqa: BLE001 - one unreadable file must not drop the rest
                _logger.warning("hub could not copy file %s", file.file_id, exc_info=True)
                continue
            if block is not None:
                copied.append(block)
        return copied

    return HubBackend(
        sessions=sessions,
        listing=listing,
        activity=lambda sid: live_activity(conversation_store, sid),
        send=send,
        check=check,
        turn=turn,
        projects=projects,
        create_project=create_project,
        rename_project=rename_project,
        delete_project=delete_project,
        file_chat=file_chat,
        configure=configure,
        import_files=import_files,
        status=status,
        open_chat=open_chat,
        catalog=catalog,
        paths_in_use=paths_in_use,
        rename_chat=rename_chat,
        close_chat=close_chat,
    )
