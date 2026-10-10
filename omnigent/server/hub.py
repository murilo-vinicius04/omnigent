"""The hub: one chat, with no name, that runs your sessions for you.

You talk to the hub -- typed on its page, or aloud through the voice hub --
and it works out which of your sessions a message is for, sends it there,
and tells you in this same chat when that session's Claude finishes. There
is nothing to set up: the sessions are your recent ones, read fresh on every
message, so a session started a minute ago is reachable, and the hub learns
what you call each one ("amarras", "the audio one") as you go.

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
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Final, Literal

from omnigent.process_logging import data_dir
from omnigent.server import voice_hub
from omnigent.server.discussion import DiscussionSession, DiscussionUnavailable

_logger = logging.getLogger(__name__)

#: Where each user's hub log, nicknames and conversation id are kept: beside
#: the server's database, since the conversation id belongs to that database.
HUB_DIR: Final[pathlib.Path] = data_dir() / "hub"

#: The router's model when ``OMNIGENT_HUB_MODEL`` is unset.
DEFAULT_HUB_MODEL: Final[str] = "gemini-3.7-flash-medium"

#: Sessions the hub offers: top-level, not archived, touched this recently.
RECENT_S: Final[float] = 7 * 24 * 3600.0

#: At most this many sessions, newest first.
MAX_SESSIONS: Final[int] = 8

#: Log entries kept on disk and served to the page.
MAX_LOG: Final[int] = 400

#: How often a session the hub sent work to is looked at.
WATCH_POLL_S: Final[float] = 4.0

#: A watch gives up after this long; the reply is still in that session.
WATCH_MAX_S: Final[float] = 6 * 3600.0

#: A session that has not started on what it was sent after this long.
NOT_STARTED_S: Final[float] = 120.0

#: Budget for one routing turn, a cold start included.
ROUTE_TIMEOUT_S: Final[float] = 45.0

#: Longest text quoted per session in the router's list.
_QUOTE_CHARS: Final[int] = 180

HUB_ROUTER_ROLE: Final[str] = (
    "You are the hub: the one chat an engineer uses to run several Claude Code "
    "sessions on their machine. Each session is a separate Claude working on its "
    "own task; you are not Claude and cannot see code or files yourself. With "
    "every message you get the list of their sessions, what each was started "
    "with, what it was last asked and what it is doing now. Your job is to work "
    "out which session a message is for and pass it on, to say what the "
    "sessions are doing, and to answer small things yourself. Be brief: one or "
    "two plain sentences, no lists unless asked."
)

#: The routing turn. One JSON line keeps parsing trivial and every failure loud.
_DECIDE_PROMPT: Final[str] = """\
[Their sessions right now, newest first. Each starts with its handle in brackets.]
{sessions}

[They {how}]
{message}

Reply with ONE line of JSON and nothing else, exactly one of:
{{"answer": "..."}} -- you reply yourself: greetings, small talk, and anything \
the list above or this chat already answers.
{{"send": "<handle>", "message": "...", "say": "..."}} -- a session's Claude has \
to do or answer it. "message" is their request in plain English for that Claude, \
faithful and complete: Claude cannot see this chat, so carry what the request \
depends on from earlier here, and keep code, paths and names exactly. "say" is a \
short confirmation for them that names the session.
{{"status": "<handle>"}} -- they ask what a session is doing now or whether it \
finished, and its line above does not say enough.
{{"check": "<handle>", "question": "..."}} -- a fact on their machine (disk, \
processes, services, logs, files, git) that read-only commands in that session's \
folder can show. Nothing is changed by a check.
{{"ask": "..."}} -- you cannot tell which session they mean: one short question \
that names the likely ones.

Add "names": {{"<handle>": "<what they call it>"}} to any reply when they tell \
you, or make plain, what they call a session.
Reply to them in the language they wrote in; "message" for Claude is always \
English. Never say a session did, found or finished anything the list or this \
chat does not show. When it is unclear whether they want something sent, ask.\
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
code, no lists.

{reply}\
"""

EntryKind = Literal["message", "sent", "finished", "status", "check", "attention", "error"]

#: Entries an open voice hub call is told about as they happen.
SPOKEN_KINDS: Final[frozenset[str]] = frozenset({"finished", "attention"})


@dataclass(frozen=True)
class HubSession:
    """One session as the hub sees it.

    :param id: The session id.
    :param title: Its title, often generated, e.g. ``"Project Analysis"``.
    :param nickname: What they call it, once the hub has learned it.
    :param status: Live status, e.g. ``"running"`` or ``"idle"``.
    :param handle: Short unique id prefix the router uses, e.g. ``"d14b"``.
    :param first_ask: The first thing it was asked.
    :param last_ask: The latest thing it was asked.
    :param said: The latest thing its Claude said.
    :param summary: The companion's latest spoken summary of it.
    :param now: Its current step while working, e.g. ``"Bash: pytest"``.
    :param pending_approvals: Approvals it is waiting on.
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

    def as_dict(self) -> dict[str, Any]:
        """The JSON shape the page and the voice relay read."""
        return asdict(self)


@dataclass(frozen=True)
class TurnState:
    """Where a session is with the work the hub sent it."""

    status: str | None
    reply: str = ""
    pending_approvals: int = 0

    @property
    def working(self) -> bool:
        return (self.status or "") in voice_hub.WORKING_STATUSES


@dataclass(frozen=True)
class HubDecision:
    """What the router decided about one message."""

    action: Literal["answer", "send", "status", "check", "ask", "none"]
    text: str = ""
    handle: str | None = None
    message: str = ""
    names: dict[str, str] = field(default_factory=dict)


def hub_model() -> str:
    """The router's model: ``OMNIGENT_HUB_MODEL`` or the default."""
    return os.environ.get("OMNIGENT_HUB_MODEL", "").strip() or DEFAULT_HUB_MODEL


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def assign_handles(session_ids: Sequence[str]) -> dict[str, str]:
    """Shortest unique id prefix per session, at least four characters."""
    handles: dict[str, str] = {}
    for sid in session_ids:
        size = 4
        while size < len(sid) and any(
            other != sid and other.startswith(sid[:size]) for other in session_ids
        ):
            size += 1
        handles[sid] = sid[:size]
    return handles


def session_line(session: HubSession) -> str:
    """One session as the router reads it."""
    name = f"{session.nickname} · " if session.nickname else ""
    state = "WORKING" if session.working else "idle"
    if session.working and session.now:
        state += f" (now: {_short(session.now, 90)})"
    if session.pending_approvals:
        state += f", waiting for {session.pending_approvals} approval(s)"
    parts = [f'[{session.handle}] {name}"{session.title or "untitled"}" · {state}']
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
    """Every session, one line each, or a note that there are none."""
    if not sessions:
        return "(No sessions in the last week. They start one from the sidebar.)"
    return "\n".join(session_line(s) for s in sessions)


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
    raw_names = parsed.get("names")
    names = (
        {str(k).strip(): str(v).strip() for k, v in raw_names.items() if str(v).strip()}
        if isinstance(raw_names, dict)
        else {}
    )

    def text(key: str) -> str:
        value = parsed.get(key)
        return value.strip() if isinstance(value, str) else ""

    if text("send"):
        return HubDecision(
            action="send",
            handle=text("send"),
            message=text("message"),
            text=text("say"),
            names=names,
        )
    if text("check"):
        return HubDecision(
            action="check", handle=text("check"), message=text("question"), names=names
        )
    if text("status"):
        return HubDecision(action="status", handle=text("status"), names=names)
    if text("ask"):
        return HubDecision(action="ask", text=text("ask"), names=names)
    if text("answer"):
        return HubDecision(action="answer", text=text("answer"), names=names)
    return HubDecision(action="none", names=names)


def find_session(sessions: Sequence[HubSession], spoken: str) -> HubSession | None:
    """The session a handle, nickname or title means, forgiving speech."""
    want = voice_hub.normalize(spoken)
    if not want:
        return None
    for session in sessions:
        if session.handle == spoken.strip() or session.id == spoken.strip():
            return session
    keyed: dict[str, HubSession] = {}
    for session in sessions:
        for key in (session.nickname, session.title):
            if key and voice_hub.normalize(key) and voice_hub.normalize(key) not in keyed:
                keyed[voice_hub.normalize(key)] = session
    if want in keyed:
        return keyed[want]
    containing = {s.id: s for key, s in keyed.items() if key and (key in want or want in key)}
    if len(containing) == 1:
        return next(iter(containing.values()))
    close = difflib.get_close_matches(want, list(keyed), n=1, cutoff=0.6)
    return keyed[close[0]] if close else None


class HubRouter(DiscussionSession):
    """The hub's warm Gemini: a companion whose job is routing, not chatting."""

    role = HUB_ROUTER_ROLE

    @classmethod
    def _default_model(cls) -> str:
        return hub_model()

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
            try:
                await self._ensure_process()
                blocks = self._preamble()
                reply = await self._turn(
                    "\n\n".join([*blocks, prompt]) if blocks else prompt, timeout_s=timeout_s
                )
            except DiscussionUnavailable:
                await self._kill()
                self._record("question", said)
                raise
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
                reply = await self._turn(prompt, timeout_s=timeout_s)
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
        self.names: dict[str, str] = {}
        self._waiters: set[asyncio.Future[None]] = set()
        self._load()

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
        """Remember what they call a session."""
        self.names[session_id] = nickname
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = self._dir / "names.json.tmp"
            tmp.write_text(json.dumps(self.names), encoding="utf-8")
            tmp.replace(self._dir / "names.json")
        except OSError:
            _logger.warning("hub names not written", exc_info=True)

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

    :param sessions: The recent sessions, nicknames not yet applied.
    :param activity: A session's live status, in full.
    :param send: Post a message into a session, as if typed there.
    :param check: Answer a question with read-only commands in its folder.
    :param turn: Where a session is with work sent at a given time.
    """

    sessions: Callable[[], Awaitable[list[HubSession]]]
    activity: Callable[[str], Awaitable[str]]
    send: Callable[[str, str], Awaitable[None]]
    check: Callable[[str, str], Awaitable[str]]
    turn: Callable[[str, float], Awaitable[TurnState]]


class Hub:
    """One user's hub: their chat, their router, and the sessions it watches."""

    def __init__(
        self,
        key: str,
        *,
        router_factory: Callable[[], Awaitable[HubRouter]],
        state: HubState | None = None,
        poll_s: float = WATCH_POLL_S,
    ) -> None:
        self.key = key
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

    def bind(self, backend: HubBackend) -> None:
        """Use this backend from now on; the newest request's wins."""
        self._backend = backend

    def require_backend(self) -> HubBackend:
        """The bound backend; raises when no request has bound one yet."""
        if self._backend is None:
            raise RuntimeError("hub has no backend bound")
        return self._backend

    async def sessions(self) -> list[HubSession]:
        """The recent sessions, with handles and nicknames applied."""
        raw = await self.require_backend().sessions()
        handles = assign_handles([s.id for s in raw])
        return [
            replace(s, handle=handles[s.id], nickname=self.state.names.get(s.id) or s.nickname)
            for s in raw
        ]

    def submit(self, text: str, *, via: str = "typed") -> HubEntry:
        """Record what they said and answer it in the background."""
        entry = self.state.append(role="you", kind="message", text=text.strip(), via=via)
        self._spawn(self._handle(text.strip(), via))
        return entry

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

    async def _handle(self, text: str, via: str) -> None:
        async with self._busy:
            if self.chat is not None:
                self.chat.busy(True)
            try:
                await self._answer(text, via)
            except Exception:  # noqa: BLE001 - the chat must always get a reply
                _logger.warning("hub message failed", exc_info=True)
                self.say("Something went wrong on my side; nothing was sent.", kind="error")
            finally:
                if self.chat is not None:
                    self.chat.busy(False)

    async def _answer(self, text: str, via: str) -> None:
        sessions = await self.sessions()
        router = await self._router_factory()
        how = "said, aloud" if via == "voice" else "wrote"
        prompt = _DECIDE_PROMPT.format(sessions=sessions_block(sessions), how=how, message=text)
        try:
            reply = await router.decide(prompt, said=text, timeout_s=ROUTE_TIMEOUT_S)
        except DiscussionUnavailable as exc:
            _logger.warning("hub router unavailable: %s", exc)
            self.say(
                "I can't think right now (the Gemini router is not answering), so "
                "nothing was sent. Try again in a moment, or type it in the session.",
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
        by_handle = {s.handle: s for s in sessions}
        for handle, nickname in decision.names.items():
            named = by_handle.get(handle)
            if named is not None:
                self.state.name(named.id, nickname)
                by_handle[handle] = replace(named, nickname=nickname)
        sessions = list(by_handle.values())
        target = (
            by_handle.get(decision.handle or "") or find_session(sessions, decision.handle or "")
            if decision.handle
            else None
        )
        if decision.action in ("send", "status", "check") and target is None:
            names = ", ".join(s.name for s in sessions) or "none"
            said = f"I couldn't tell which session you mean. Your sessions: {names}."
            self.say(said)
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
        said = decision.text or (
            "I didn't understand that well enough to act on it; nothing was sent."
        )
        self.say(said)
        router.remember(said)

    async def send_to(
        self, session: HubSession, message: str, *, via: str, say: str = ""
    ) -> HubEntry:
        """Post into a session, log it here, and watch for its answer."""
        sent_at = time.time()
        try:
            await self.require_backend().send(session.id, message)
        except Exception as exc:  # noqa: BLE001 - say it failed, never pretend
            _logger.warning("hub send to %s failed", session.id, exc_info=True)
            return self.say(
                f"Sending to {session.name} failed ({type(exc).__name__}); nothing was sent.",
                kind="error",
                session_id=session.id,
                session_name=session.name,
            )
        entry = self.say(
            say or f"Sent to {session.name}.",
            kind="sent",
            via=via,
            session_id=session.id,
            session_name=session.name,
            detail=message,
        )
        router = await self._router_factory()
        router.remember(f"sent to {session.name}: {message}")
        self.watch(session, sent_at)
        return entry

    def watch(self, session: HubSession, sent_at: float) -> None:
        """Report back when the session finishes; one watch per session."""
        running = self._watches.get(session.id)
        if running is not None and not running.done():
            return
        task = asyncio.ensure_future(self._watch(session, sent_at))
        self._watches[session.id] = task

    async def _watch(self, session: HubSession, sent_at: float) -> None:
        deadline = time.monotonic() + WATCH_MAX_S
        started = time.monotonic()
        seen_working = False
        idle_polls = 0
        told_approval = False
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
                if state.reply and idle_polls >= 2:
                    await self._report(session, state.reply)
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
            raise
        except Exception:  # noqa: BLE001 - a broken watch must not take the hub down
            _logger.warning("hub watch on %s failed", session.id, exc_info=True)
        finally:
            self._watches.pop(session.id, None)

    async def _report(self, session: HubSession, reply: str) -> None:
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
        self.say(
            f"{session.name} finished: {told}",
            kind="finished",
            session_id=session.id,
            session_name=session.name,
            detail=reply[:8000],
        )
        router.remember(f"{session.name} finished: {told}")

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
            made = await registry().get(f"hub-{key}", factory=HubRouter)
            assert isinstance(made, HubRouter)
            return made

        hub = Hub(key, router_factory=router)
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

    async def sessions() -> list[HubSession]:
        page = await asyncio.to_thread(
            conversation_store.list_conversations,
            limit=MAX_SESSIONS + len(exclude),
            owned_by=user_id,
            sort_by="updated_at",
            order="desc",
        )
        cutoff = time.time() - RECENT_S
        out: list[HubSession] = []
        for conv in page.data:
            labels = getattr(conv, "labels", None) or {}
            if conv.id in exclude or labels.get("omnigent.hub") == "1":
                continue
            updated = float(getattr(conv, "updated_at", 0) or 0)
            if updated and updated < cutoff:
                continue
            if len(out) >= MAX_SESSIONS:
                break
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
                )
            )
        return out

    async def send(session_id: str, text: str) -> None:
        import httpx

        body = {
            "type": "message",
            "data": {
                "role": "user",
                "content": [{"type": "input_text", "text": text}, {"type": "force_claude"}],
            },
        }
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://hub", timeout=60
        ) as client:
            response = await client.post(
                f"/v1/sessions/{session_id}/events", json=body, headers=headers
            )
        response.raise_for_status()

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
        reply = ""
        for item in recent.data:
            if float(getattr(item, "created_at", 0) or 0) + 1 < since:
                break
            if _role(item.data) == "assistant" and _item_text(item.data):
                reply = _item_text(item.data)
                break
        return TurnState(
            status=status,
            reply=reply,
            pending_approvals=int(getattr(conv, "pending_elicitation_count", 0) or 0),
        )

    return HubBackend(
        sessions=sessions,
        activity=lambda sid: live_activity(conversation_store, sid),
        send=send,
        check=check,
        turn=turn,
    )
