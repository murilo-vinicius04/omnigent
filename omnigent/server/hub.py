"""The hub: one chat, with no name, that runs your projects for you.

You talk to the hub -- typed in its chat, or aloud through the voice hub --
and it works out which of your projects a message is for, sends it to that
project's chat, and tells you in this same chat when its Claude finishes.
A project is one of your Omnigent projects (the sidebar's Projects); the
chat the hub sends to is the project's newest. Projects are made, renamed
and deleted in the hub's Projects panel, or made by asking the hub.

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
from omnigent.server import hub_models, voice_hub
from omnigent.server.discussion import DiscussionSession, DiscussionUnavailable
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

#: A project's chats read per listing; the newest is the one the hub sends to.
CHATS_PER_PROJECT: Final[int] = 50

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
both work.]
{projects}
{context}{files}
[They {how}]
{message}

Reply with ONE line of JSON and nothing else, exactly one of:
{{"answer": "..."}} -- you reply yourself: greetings, small talk, and anything \
the list above or this chat already answers.
{{"send": "<handle>", "message": "...", "say": "..."}} -- a project's Claude has \
to do or answer it. "message" is their request in {language} for that Claude, \
faithful and complete: Claude cannot see this chat, so carry what the request \
depends on from earlier here, and keep code, paths and names exactly. "say" is a \
short confirmation for them that names the project.
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
        }


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
    :param chat: Its newest chat, which the hub sends to; ``None`` while it
        has none yet.
    """

    id: str
    name: str
    handle: str = ""
    icon: str = ""
    folder: str = ""
    chats: int = 0
    updated_at: float = 0.0
    chat: HubSession | None = None

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
    #: Files a reply came with, copied into the hub chat as ``output_file`` blocks.
    files: list[dict[str, Any]] | None = None
    #: What the project's chat shows under its summary of the reply: tables,
    #: images, links, output (its spoken summary's ``show`` list).
    show: list[dict[str, Any]] | None = None

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
    #: The ``show`` list of the project's spoken summary of the reply.
    show: tuple[dict[str, Any], ...] = ()
    #: The project writes spoken summaries and this reply's has not landed yet.
    summary_due: bool = False

    @property
    def working(self) -> bool:
        return (self.status or "") in voice_hub.WORKING_STATUSES


@dataclass(frozen=True)
class HubDecision:
    """What the router decided about one message."""

    action: Literal["answer", "send", "status", "check", "model", "new_project", "ask", "none"]
    text: str = ""
    handle: str | None = None
    message: str = ""
    #: For ``model``: the model and effort asked for; empty leaves it as is.
    model: str = ""
    effort: str = ""


#: The server's file metadata and byte stores, set at startup: copying a
#: reply's files into the hub chat needs both.
_file_stores: tuple[Any, Any] | None = None


def use_file_stores(file_store: Any, artifact_store: Any) -> None:
    """Give the hub the server's file stores, so it can copy files between chats."""
    global _file_stores
    _file_stores = (file_store, artifact_store)


#: The Flash model ``agy models`` offered, once asked; ``None`` until then.
_listed_model: str | None = None


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
    global _listed_model
    if _listed_model is None and not os.environ.get("OMNIGENT_HUB_MODEL", "").strip():
        from omnigent.server.discussion import _binary

        rows = await _probe_agy_models(_binary())
        _listed_model = pick_router_model([str(r["id"]) for r in rows or []])
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


def _without_label(text: str, label: str) -> str:
    """The text without a leading copy of the label the hub adds itself."""
    if text.casefold().startswith(label.casefold()):
        return text[len(label) :].lstrip()
    return text


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
    """One project as the router reads it, with or without a chat yet."""
    if project.chat is not None:
        line = session_line(project.chat)
    else:
        line = f"[{project.handle}] {project.name} · no chat yet: nothing can be sent to it"
    if project.chats > 1:
        line += f" · {project.chats} chats, this is the newest"
    return line


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
            action="send", handle=text("send"), message=text("message"), text=text("say")
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
                    stalled = str(exc).startswith("no answer within")
                    if not (
                        first and stalled and deadline - time.monotonic() >= ROUTE_RETRY_MIN_S
                    ):
                        self._record("question", said)
                        raise
                    # A model call that never returns: a fresh process gets the rest.
                    _logger.warning("hub router stalled; asking a fresh process")
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
        """Note what the hub said or did, for the router's memory."""
        self._record("answer", text[:2000])


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
        self._waiters: set[asyncio.Future[None]] = set()
        self._load()

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

    def keep_watch(self, session_id: str, *, sent_at: float, name: str, **owner: Any) -> None:
        """Remember a send still to report back, so a restart does not lose it.

        :param owner: ``user_id`` and ``base_url``, for resuming it after a restart.
        """
        self.watches[session_id] = {"sent_at": sent_at, "name": name}
        self.owner = {**self.owner, **owner}
        self._write("watches.json", {**self.owner, "watches": self.watches})

    def drop_watch(self, session_id: str) -> None:
        """Forget a send: it was reported back, or will never be."""
        if self.watches.pop(session_id, None) is not None:
            self._write("watches.json", {**self.owner, "watches": self.watches})

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
    turn: Callable[[str, float], Awaitable[TurnState]]
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
    ) -> None:
        self.key = key
        self.user_id = user_id
        self.state = state or HubState(key)
        self._router_factory = router_factory
        self._backend: HubBackend | None = None
        self._busy = asyncio.Lock()
        self._watches: dict[str, asyncio.Task[None]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._poll_s = poll_s
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

    async def projects(self) -> list[HubProject]:
        """Their projects, most recently active first, each with its newest chat."""
        await self._move_sessions_to_projects()
        backend = self.require_backend()
        records = await backend.projects()
        newest = [r.chat_ids[0] for r in records if r.chat_ids]
        chats = {s.id: s for s in await backend.sessions(newest)} if newest else {}
        handles = assign_handles([r.id for r in records])
        out: list[HubProject] = []
        for record in records:
            chat = chats.get(record.chat_ids[0]) if record.chat_ids else None
            if chat is not None:
                chat = replace(
                    chat, nickname=record.name, handle=handles[record.id], project_id=record.id
                )
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
        projects = await self.projects()
        router = await self._router_factory()
        how = "said, aloud, on a voice call" if via == "voice" else "wrote"
        held = self.held_files()
        prompt = _DECIDE_PROMPT.format(
            projects=projects_block(projects),
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
        project = (
            next((p for p in projects if p.handle == decision.handle), None)
            or find_project(projects, decision.handle)
            if decision.handle
            else None
        )
        if decision.action in ("send", "status", "check", "model") and project is None:
            names = ", ".join(p.name for p in projects) or "none yet"
            said = f"I couldn't tell which project you mean. Your projects: {names}."
            self.say(said)
            router.remember(said)
            return
        target = project.chat if project is not None else None
        if project is not None and target is None and decision.action != "ask":
            said = f"{project.name} has no chat yet, so nothing was sent. Start one first."
            self.say(
                said, kind="attention", session_name=project.name, link=project_link(project.name)
            )
            router.remember(said)
            return
        if decision.action == "send" and target is not None:
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

    async def send_to(
        self, session: HubSession, message: str, *, via: str, say: str = ""
    ) -> HubEntry:
        """Post into a chat, with any files held, log it here, and watch for its answer."""
        sent_at = time.time()
        files = self.held_files()
        try:
            await self.require_backend().send(session.id, message, files)
        except Exception as exc:  # noqa: BLE001 - say it failed, never pretend
            _logger.warning("hub send to %s failed", session.id, exc_info=True)
            kept = f" The {self._count(files)} stay here for the next try." if files else ""
            return self.say(
                f"Sending to {session.name} failed ({type(exc).__name__}); nothing was sent."
                + kept,
                kind="error",
                session_id=session.id,
                session_name=session.name,
            )
        if files:
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
        )
        router = await self._router_factory()
        router.remember(
            f"sent to {session.name}: {message}" + (f" (attached: {names})" if files else "")
        )
        self.watch(session, sent_at)
        return entry

    def watch(self, session: HubSession, sent_at: float) -> None:
        """Report back when the session finishes; one watch per session.

        The watch is written down first, so a server restart resumes it
        (:meth:`resume_watches`) instead of losing the report.
        """
        running = self._watches.get(session.id)
        if running is not None and not running.done():
            return
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
        """Start again the watches a server restart stopped; returns how many."""
        resumed = 0
        for session_id, kept in list(self.state.watches.items()):
            sent_at = float(kept.get("sent_at") or 0.0)
            if time.time() - sent_at > WATCH_MAX_S:
                self.state.drop_watch(session_id)
                continue
            running = self._watches.get(session_id)
            if running is not None and not running.done():
                continue
            name = str(kept.get("name") or session_id[:8])
            session = HubSession(id=session_id, title=name, nickname=name)
            self._watches[session_id] = asyncio.ensure_future(self._watch(session, sent_at))
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
                    return
                if seen_working and idle_polls >= 6:
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

    async def _report(self, session: HubSession, state: TurnState) -> None:
        reply = state.reply
        router = await self._router_factory()
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
        self.say(
            f"{session.name} finished: {told}",
            kind="finished",
            session_id=session.id,
            session_name=session.name,
            detail=reply[:MAX_REPLY_CHARS],
            workspace=state.workspace,
            files=await self._import_files(session, _files_to_copy(state)) or None,
            show=[b for b in state.show if b.get("kind") not in ("file", None)] or None,
        )
        router.remember(f"{session.name} finished: {told}")

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
        """Stop every watch and pending answer."""
        for task in [*self._watches.values(), *self._tasks]:
            task.cancel()
        for task in [*self._watches.values(), *self._tasks]:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._watches.clear()


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

        hub = Hub(key, router_factory=router, user_id=user_id)
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
        return conv.id not in exclude and labels.get("omnigent.hub") != "1"

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

    async def turn(session_id: str, since: float) -> TurnState:
        conv = await asyncio.to_thread(conversation_store.get_conversation, session_id)
        status = live_status(session_id, getattr(conv, "live_status", None))
        recent = await asyncio.to_thread(
            conversation_store.list_items, session_id, limit=30, order="desc", type="message"
        )
        # Newest first. The answer is the newest message with answer text: a
        # spoken summary or an attached chart's caption comes after it.
        reply, reply_response = "", None
        files: list[ReplyFile] = []
        summaries: list[tuple[Any, dict[str, Any]]] = []
        for item in recent.data:
            if float(getattr(item, "created_at", 0) or 0) + 1 < since:
                break
            if _role(item.data) != "assistant":
                continue
            files[:0] = reply_files(item.data)
            summary = spoken_summary(item.data)
            if summary is not None:
                summaries.append((getattr(item, "response_id", None), summary))
            if not reply and _field(item.data, "agent") != "attachment":
                reply = reply_text(item.data)
                reply_response = getattr(item, "response_id", None) if reply else None
        # The newest summary of this reply (a summary is revised once its audio is made).
        chosen = next((s for r, s in summaries if r == reply_response), None) if reply else None
        show = tuple(
            b
            for b in (chosen or {}).get("show") or []
            if isinstance(b, dict) and b.get("kind") in SHOWN_KINDS
        )
        return TurnState(
            status=status,
            reply=reply,
            pending_approvals=int(getattr(conv, "pending_elicitation_count", 0) or 0),
            files=tuple(files),
            workspace=getattr(conv, "workspace", None),
            show=show,
            summary_due=bool(reply) and chosen is None and await _summaries_on(session_id),
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
    )
