"""A warm ``agy`` process per session: the companion you can talk to.

The spoken summary spawns a fresh ``agy --print`` for every rewrite and
pays 4-5s of process startup each time. That is tolerable after a turn
has already ended; it is not tolerable in a *conversation*, where the
same 4-5s is dead air on a wall-clock-billed live voice session. One
warm process answers in ~1.0s with its history intact, so this module
keeps one alive per Omnigent session instead of one per message.

What the companion knows
------------------------

Deliberately not much. It sees short notes about what Claude is doing
and the spoken summaries of what Claude said — never the transcript,
never the code. It is the person sitting next to you who has been
half-listening, not a second engineer with the repo open. That is a
product decision, and it is also what keeps replay cheap.

The ledger is the memory; the process is a cache
------------------------------------------------

Every note, question and answer lands in :class:`ContextEntry` rows on
the session — and *that* is the conversation's memory. The subprocess
holds the same history, but only as a warm cache of it. So any process
death is recoverable: a crash, a timeout, or an idle reap simply drops
the cache, and the next question restarts ``agy`` and replays the ledger
into it. Nothing the reader said is lost by killing the process, which
is why this module is free to kill it whenever the state is in doubt.

The ledger is also exactly what the UI shows, so "what does it know?"
has one answer rather than one per surface.

Routing: the companion sees the message first
---------------------------------------------

:meth:`DiscussionSession.route` is the composer's path. Every message the
reader types goes to the companion before Claude sees it, and the same
turn does two jobs: it restates the message in English for the harness
(the job the cold inbound repair pass used to do, at 4-5s) and it says
whether Claude is needed at all. Trivia it can answer from what it
already knows, it answers; everything else forwards.

The bias is heavily toward forwarding, and every failure mode forwards:
an unparseable reply, a dead process, a missing CLI, a timeout. A slow
answer from Claude is a cost; a confident wrong answer from something
that cannot see the code is a trap.

Notes cost nothing until they are needed
----------------------------------------

:meth:`DiscussionSession.note` does not talk to the process. It appends
to the ledger and returns. Undelivered notes are folded into the next
question as a briefing block, so telling the companion what Claude is
doing never burns a round trip of its own — the context arrives at the
moment it becomes relevant.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Final, Literal

from omnigent.model_fallbacks import SPOKEN_SUMMARY_AGY_DEFAULT_MODEL

_logger = logging.getLogger(__name__)

#: The CLI. Overridable for tests and non-PATH installs.
DISCUSSION_AGY_BIN: Final[str] = "agy"

#: How long a question may take before the process is assumed wedged.
#: Warm turns measure ~1.0s and a cold first turn ~3.1s, so this is
#: loose enough to absorb a restart-and-replay and still bound the wait.
DEFAULT_TIMEOUT_S: Final[float] = 30.0

#: Budget for a routing turn. A warm one lands in ~1s; this leaves room
#: for one cold start before the message goes to Claude unrouted.
DEFAULT_ROUTE_TIMEOUT_S: Final[float] = 8.0

#: Idle time after which the process is reaped. The ledger survives, so
#: the only cost of reaping early is one cold start later.
DEFAULT_IDLE_S: Final[float] = 15 * 60.0

#: Ledger entries kept. Caps what a replay has to re-send, and enforces
#: "nothing really deep" by construction.
MAX_ENTRIES: Final[int] = 60

#: stderr lines retained for diagnostics when a process misbehaves.
_STDERR_LINES: Final[int] = 20

#: What the companion is told it is. There is no ``--system-prompt``
#: flag on the CLI, so the role rides as the head of the first message —
#: folded into a real question when nobody prewarmed, which is why it
#: carries no "reply with" instruction of its own.
ROLE_INSTRUCTIONS: Final[str] = (
    "You are a companion talking with someone who is working alongside Claude Code, "
    "an AI that is writing and running code for them. You are not that AI and you "
    "cannot see the code, the files, or the full conversation. What you get is short "
    "notes about what Claude is doing and short summaries of what it said.\n\n"
    "Talk like a colleague leaning over from the next desk: two or three sentences, "
    "plain words, no lists and no code. If you do not know something, say so and "
    "suggest asking Claude — do not invent what the code does. When the person is "
    "just thinking out loud, react like a person would rather than answering like a "
    "manual."
)

#: Appended when the role is sent on its own, ahead of any question, so
#: the turn has something to answer.
_PREWARM_SUFFIX: Final[str] = "\n\nReply to this message with just: ready"

EntryKind = Literal["activity", "summary", "question", "answer", "note"]


@dataclass(frozen=True, slots=True)
class ContextEntry:
    """One thing the companion knows, and when it learned it.

    :param kind: What sort of knowledge this is; drives the UI's label.
    :param text: The text itself, already trimmed to size.
    :param at: Unix timestamp when it was recorded.
    :param id: Position in the session's ledger, from 1. A question and
        its answer are recorded in the same instant, so ``at`` does not
        identify an entry and the UI needs something that does.
    """

    kind: EntryKind
    text: str
    at: float
    id: int = 0

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON shape the UI consumes."""
        return {"id": self.id, "kind": self.kind, "text": self.text, "at": self.at}


class DiscussionUnavailable(RuntimeError):
    """The companion cannot be reached, so the caller should degrade."""


#: What the routing turn should do to the reader's text, mirroring the
#: inbound pass's own two jobs: a reader working in another language gets
#: their message restated in English, a reader already writing English gets
#: dictation slips repaired, and an unset language means hands off entirely.
Restatement = Literal["translate", "repair", "off"]

#: The routing turn. Asks for both jobs at once because they need the same
#: context and a second turn would double the latency on the typing path.
_ROUTE_PROMPT: Final[str] = """\
[The person just typed this to Claude. It goes to Claude unless you can \
answer it completely yourself.]
{message}

Reply with ONE line of JSON and nothing else:
{{"forward": true, "english": {english_example}, "answer": ""}}

{english_rule}
- "forward": false ONLY when you can answer completely from what you already \
know -- small talk, a question about what has been happening, something you \
were just told. If it needs the code, the files, a tool, a change, or any \
fact you were not given, forward it.
- "answer": your reply when forward is false, in the language they wrote in. \
Empty otherwise.

When in doubt, forward. Making them wait for Claude costs seconds; a \
confident wrong answer from someone who cannot see the code costs much more.\
"""

#: Restate the message in English. The reader is not writing English, so the
#: harness would otherwise pay the tokenization premium on every turn.
_RULE_TRANSLATE: Final[str] = (
    '- "english": their message restated in plain English for Claude. Faithful, '
    "same meaning, no commentary, no answering it. Always fill this in, even when "
    "you are answering yourself. Reproduce code, commands, paths, identifiers and "
    "quoted output exactly as given."
)

#: The reader already writes English, so this is a repair, not an edit. The
#: bar is deliberately high: their own words are the default, and only a
#: word the sentence plainly settles may change.
_RULE_REPAIR: Final[str] = (
    '- "english": their message with speech-to-text slips fixed, and NOTHING else '
    "changed. They already write English, so this is a repair, not a rewrite: keep "
    "their words, register and phrasing, including wording you find clumsy. Fix a "
    "word only when the surrounding sentence makes the intended one obvious. "
    "Reproduce code, commands, paths, identifiers and quoted output exactly. If "
    "nothing is clearly mis-heard, repeat the message unchanged."
)

#: Hands off. The field is still asked for so the shape stays constant, but
#: the caller ignores it and the reader's own words are what forward.
_RULE_VERBATIM: Final[str] = (
    '- "english": repeat their message back exactly as written, unchanged.'
)

_RESTATEMENT_RULES: Final[dict[str, str]] = {
    "translate": _RULE_TRANSLATE,
    "repair": _RULE_REPAIR,
    "off": _RULE_VERBATIM,
}


@dataclass(frozen=True, slots=True)
class Routing:
    """What the companion decided about one typed message.

    :param forward: Whether Claude should see it. Every failure yields
        ``True``: forwarding is always safe, answering is not.
    :param english: The message restated for the harness, or ``None``
        when the companion could not produce one.
    :param answer: The companion's own reply when it kept the message.
    """

    forward: bool
    english: str | None = None
    answer: str | None = None


def _model() -> str:
    """Return the configured companion model."""
    return (
        os.environ.get("OMNIGENT_DISCUSSION_MODEL", "").strip() or SPOKEN_SUMMARY_AGY_DEFAULT_MODEL
    )


def _binary() -> str:
    """Return the configured ``agy`` binary."""
    return os.environ.get("OMNIGENT_DISCUSSION_AGY_BIN", "").strip() or DISCUSSION_AGY_BIN


def route_timeout_s() -> float:
    """Return the budget for a routing turn, in seconds.

    Deliberately tight: this sits between the reader pressing enter and
    Claude seeing the message, so a companion that is thinking too long
    must get out of the way rather than hold the composer.
    """
    raw = os.environ.get("OMNIGENT_COMPANION_ROUTE_TIMEOUT_S", "").strip()
    if raw:
        with contextlib.suppress(ValueError):
            parsed = float(raw)
            if parsed > 0:
                return parsed
    return DEFAULT_ROUTE_TIMEOUT_S


def routing_enabled() -> bool:
    """Whether typed messages are routed through the companion first.

    On by default: the composer is the feature. Set
    ``OMNIGENT_COMPANION_ROUTING=0`` to send everything straight to Claude
    (the companion still listens and can still be asked directly).
    """
    raw = os.environ.get("OMNIGENT_COMPANION_ROUTING", "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def idle_timeout_s() -> float:
    """Return the configured idle reap threshold, in seconds."""
    raw = os.environ.get("OMNIGENT_DISCUSSION_IDLE_S", "").strip()
    if raw:
        with contextlib.suppress(ValueError):
            parsed = float(raw)
            if parsed > 0:
                return parsed
    return DEFAULT_IDLE_S


class DiscussionSession:
    """One warm ``agy`` process, plus the ledger of what it was told.

    Not thread-safe and not re-entrant: an internal lock serializes
    start, ask and close, so concurrent questions queue rather than
    interleaving on the one stdin pipe.
    """

    def __init__(self, session_id: str, *, model: str | None = None, binary: str | None = None):
        """
        :param session_id: Omnigent session this companion belongs to.
        :param model: Model override; defaults to the configured one.
        :param binary: CLI override; defaults to the configured one.
        """
        self.session_id = session_id
        self._model = model or _model()
        self._binary = binary or _binary()
        self._entries: deque[ContextEntry] = deque(maxlen=MAX_ENTRIES)
        self._next_id = 1
        self._undelivered: list[ContextEntry] = []
        self._process: asyncio.subprocess.Process | None = None
        self._stderr: deque[str] = deque(maxlen=_STDERR_LINES)
        self._stderr_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._last_used = time.time()
        self._started_at: float | None = None
        # Whether the process now held has been given the role and the
        # ledger. False after any respawn, which is what makes a replay
        # get folded into the next message.
        self._briefed = False

    # -- state the UI asks about ------------------------------------

    @property
    def context(self) -> list[ContextEntry]:
        """Everything the companion knows, oldest first."""
        return list(self._entries)

    @property
    def running(self) -> bool:
        """Whether a warm process is currently held."""
        return self._process is not None and self._process.returncode is None

    @property
    def idle_s(self) -> float:
        """Seconds since this session was last used."""
        return time.time() - self._last_used

    def as_dict(self) -> dict[str, Any]:
        """Return the session's state for the UI's context panel."""
        return {
            "session_id": self.session_id,
            "model": self._model,
            "running": self.running,
            "idle_s": round(self.idle_s, 1),
            "warm_since": self._started_at if self._briefed else None,
            "pending_notes": len(self._undelivered),
            "context": [entry.as_dict() for entry in self._entries],
        }

    # -- feeding it ---------------------------------------------------

    def note(self, kind: EntryKind, text: str) -> None:
        """Record something the companion should know, without asking it.

        Cheap by design: this never touches the process. The note is
        folded into the next question's briefing instead, so a busy
        session can narrate freely without paying a turn per note.

        :param kind: ``"activity"`` for what Claude is doing,
            ``"summary"`` for what it said, ``"note"`` for anything else.
        :param text: The note; blank text is ignored.
        """
        cleaned = text.strip()
        if not cleaned:
            return
        self._undelivered.append(self._record(kind, cleaned[:2000]))

    def _record(self, kind: EntryKind, text: str) -> ContextEntry:
        """Append one entry to the ledger and return it.

        :param kind: The entry's kind.
        :param text: Already-trimmed text.
        :returns: The stored entry, carrying its ledger id.
        """
        entry = ContextEntry(kind=kind, text=text, at=time.time(), id=self._next_id)
        self._next_id += 1
        self._entries.append(entry)
        return entry

    # -- process lifecycle --------------------------------------------

    async def _spawn(self) -> asyncio.subprocess.Process:
        """Start one ``agy`` process in warm streaming mode.

        :raises DiscussionUnavailable: When the binary is not on PATH.
        """
        args = [
            # An empty --print is what puts the CLI in print mode without
            # supplying the prompt inline; the turns arrive on stdin.
            "--print=",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--model",
            self._model,
            # The reader's speech is untrusted text. No slash-command or
            # skill expansion, ever.
            "--disable-slash-commands",
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                self._binary,
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise DiscussionUnavailable(f"{self._binary!r} not found on PATH") from exc
        self._process = process
        self._started_at = time.time()
        self._stderr_task = asyncio.create_task(self._drain_stderr(process))
        return process

    async def _drain_stderr(self, process: asyncio.subprocess.Process) -> None:
        """Keep stderr flowing so a chatty CLI cannot block on a full pipe."""
        stream = process.stderr
        if stream is None:
            return
        with contextlib.suppress(Exception):
            while True:
                line = await stream.readline()
                if not line:
                    return
                self._stderr.append(line.decode(errors="replace").rstrip())

    async def _ensure_process(self) -> None:
        """Spawn the process if it is not running. Sends nothing.

        Deliberately silent: a fresh process needs the role and a ledger
        replay, and paying for those as separate turns would make the
        first question slower (measured 8.3s) than the cold one-shot
        this module exists to replace. :meth:`_preamble` folds them into
        the question instead, so a cold ask costs one turn.
        """
        if self.running:
            return
        await self._spawn()
        self._briefed = False

    def _preamble(self) -> list[str]:
        """Return the blocks that must ride ahead of the next message.

        A fresh process gets the role plus the whole ledger. A warm one
        gets only the notes recorded since its last turn.
        """
        if not self._briefed:
            blocks = [ROLE_INSTRUCTIONS]
            replay = self._replay_block()
            if replay:
                blocks.append(replay)
            return blocks
        if self._undelivered:
            return [
                "[Since we last spoke, this happened. Background only — "
                "do not summarize it back to me.]\n"
                + "\n".join(_render(entry) for entry in self._undelivered)
            ]
        return []

    def _replay_block(self) -> str:
        """Render the whole ledger as a catch-up block, or ``""``.

        Used when a process starts fresh and has to be told everything
        the ledger already holds.
        """
        if not self._entries:
            return ""
        return (
            "[What has happened so far, for your memory. Background only — "
            "do not summarize it back to me.]\n"
            + "\n".join(_render(entry) for entry in self._entries)
        )

    async def prewarm(self, *, timeout_s: float | None = None) -> None:
        """Pay the cold start now so the first question does not.

        Call this when a voice channel opens: the process starts and
        swallows the role and the ledger while nobody is waiting, which
        turns the reader's first question into a warm ~1s turn.

        :param timeout_s: Budget for the warm-up turn.
        :raises DiscussionUnavailable: When the CLI is missing or dies.
        """
        budget = timeout_s if timeout_s is not None else DEFAULT_TIMEOUT_S
        async with self._lock:
            if self.running and self._briefed:
                return
            try:
                await self._ensure_process()
                blocks = self._preamble()
                await self._turn("\n\n".join(blocks) + _PREWARM_SUFFIX, timeout_s=budget)
            except DiscussionUnavailable:
                await self._kill()
                raise
            self._briefed = True
            self._undelivered.clear()
            self._last_used = time.time()

    async def close(self) -> None:
        """Stop the process. The ledger survives and can be replayed."""
        async with self._lock:
            await self._kill()

    async def _kill(self) -> None:
        """Terminate the process and drop the stderr drain. Lock held."""
        process, self._process = self._process, None
        task, self._stderr_task = self._stderr_task, None
        self._started_at = None
        self._briefed = False
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if process is None or process.returncode is not None:
            return
        with contextlib.suppress(Exception):
            if process.stdin is not None:
                process.stdin.close()
        with contextlib.suppress(Exception):
            process.kill()
        with contextlib.suppress(Exception):
            await process.wait()

    # -- asking it ----------------------------------------------------

    async def ask(self, text: str, *, timeout_s: float | None = None) -> str:
        """Put a question to the companion and wait for its answer.

        Everything the companion still needs — its role if the process
        is fresh, a ledger replay, and any notes recorded since the last
        question — rides on this same turn, so even a cold ask is one
        round trip rather than three.

        :param text: What the reader said.
        :param timeout_s: Per-question budget; covers a cold start.
        :returns: The companion's reply.
        :raises DiscussionUnavailable: When the CLI is missing, dies, or
            fails to answer in time. The ledger is intact either way.
        """
        question = text.strip()
        if not question:
            raise DiscussionUnavailable("nothing to ask")
        budget = timeout_s if timeout_s is not None else DEFAULT_TIMEOUT_S
        async with self._lock:
            self._last_used = time.time()
            try:
                await self._ensure_process()
                blocks = self._preamble()
                message = (
                    "\n\n".join([*blocks, f"[They said]\n{question}"]) if blocks else question
                )
                answer = await self._turn(message, timeout_s=budget)
            except DiscussionUnavailable:
                # The process is the only thing in doubt, so drop it and
                # let the next question rebuild from the ledger.
                await self._kill()
                self._record("question", question)
                raise
            self._briefed = True
            self._undelivered.clear()
            self._record("question", question)
            self._record("answer", answer)
            self._last_used = time.time()
            return answer

    async def route(
        self,
        text: str,
        *,
        restate: Restatement = "translate",
        timeout_s: float | None = None,
    ) -> Routing:
        """Decide whether Claude is needed, and restate the message for it.

        One warm turn doing the work the cold inbound repair pass used to
        do, plus the decision. Never raises: anything that goes wrong
        returns a forwarding :class:`Routing`, because the composer must
        stay usable when the companion is not.

        :param text: What the reader typed.
        :param restate: What may happen to their words -- ``"translate"``
            for a reader working in another language, ``"repair"`` for one
            already writing English, ``"off"`` to forward them untouched.
        :param timeout_s: Budget. Short by default -- this sits on the
            typing path, so a slow companion must get out of the way.
        :returns: The decision. ``english`` is ``None`` when *restate* is
            ``"off"``, whatever the model replied.
        """
        message = text.strip()
        if not message:
            return Routing(forward=True)
        budget = timeout_s if timeout_s is not None else route_timeout_s()
        async with self._lock:
            self._last_used = time.time()
            try:
                await self._ensure_process()
                blocks = self._preamble()
                prompt = _ROUTE_PROMPT.format(
                    message=message,
                    english_rule=_RESTATEMENT_RULES[restate],
                    english_example='"..."' if restate != "off" else '"<their words>"',
                )
                reply = await self._turn(
                    "\n\n".join([*blocks, prompt]) if blocks else prompt,
                    timeout_s=budget,
                )
            except DiscussionUnavailable as exc:
                _logger.info("companion routing unavailable, forwarding: %s", exc)
                await self._kill()
                self._record("question", message)
                return Routing(forward=True)
            self._briefed = True
            self._undelivered.clear()
            self._last_used = time.time()

        decision = _parse_routing(reply)
        if restate == "off":
            # The session asked for no rewriting. A model that restated
            # anyway must not be allowed to put words in the reader's mouth.
            decision = Routing(forward=decision.forward, answer=decision.answer)
        self._record("question", message)
        if not decision.forward and decision.answer:
            self._record("answer", decision.answer)
        return decision

    async def _turn(self, message: str, *, timeout_s: float) -> str:
        """Run exactly one NDJSON turn against the warm process. Lock held.

        :param message: The user message to send.
        :param timeout_s: Budget for writing and reading the reply.
        :returns: The reply text.
        :raises DiscussionUnavailable: On a dead process or a timeout.
        """
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise DiscussionUnavailable("companion process is not running")
        # The "event" key is load-bearing: a "type" key is rejected with
        # `stream input message is missing the "event" field`.
        line = json.dumps({"event": "user", "message": {"role": "user", "content": message}})
        try:
            process.stdin.write(line.encode() + b"\n")
            await process.stdin.drain()
            return await asyncio.wait_for(self._read_result(process), timeout=timeout_s)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise DiscussionUnavailable(f"no answer within {timeout_s:g}s") from exc
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise DiscussionUnavailable(f"companion process died: {self._stderr_tail()}") from exc

    async def _read_result(self, process: asyncio.subprocess.Process) -> str:
        """Read NDJSON until this turn's ``result`` event.

        Non-result events (progress, warnings) are skipped; the CLI emits
        exactly one result per input message, so reading to the next one
        is what pairs an answer with its question.
        """
        stdout = process.stdout
        assert stdout is not None
        while True:
            raw = await stdout.readline()
            if not raw:
                raise DiscussionUnavailable(f"companion process ended: {self._stderr_tail()}")
            text = raw.decode(errors="replace").strip()
            if not text:
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("event") != "result":
                continue
            result = event.get("result")
            if not isinstance(result, dict):
                raise DiscussionUnavailable(f"unreadable result: {text[:200]}")
            answer = str(result.get("response") or "").strip()
            if not answer:
                raise DiscussionUnavailable(f"empty answer: {text[:200]}")
            return answer

    def _stderr_tail(self) -> str:
        """Return the last stderr lines, for an error message."""
        return " | ".join(self._stderr) or "no stderr"


def _parse_routing(reply: str) -> Routing:
    """Read the model's routing reply, defaulting to forwarding.

    The model is asked for one line of JSON, and mostly obliges, but a
    stray sentence or a code fence must not strand a message. Anything
    unreadable forwards -- the only safe failure.

    :param reply: Raw text of the routing turn.
    :returns: The decision.
    """
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end <= start:
        _logger.info("companion routing reply was not JSON, forwarding: %r", reply[:200])
        return Routing(forward=True)
    try:
        parsed = json.loads(reply[start : end + 1])
    except json.JSONDecodeError:
        _logger.info("companion routing reply did not parse, forwarding: %r", reply[:200])
        return Routing(forward=True)
    if not isinstance(parsed, dict):
        return Routing(forward=True)
    english = str(parsed.get("english") or "").strip() or None
    answer = str(parsed.get("answer") or "").strip() or None
    # Only an explicit false keeps the message, and only with something to
    # say: "forward": "no" or a missing answer both mean forward.
    forward = parsed.get("forward") is not False or not answer
    return Routing(forward=forward, english=english, answer=None if forward else answer)


def _render(entry: ContextEntry) -> str:
    """Render one ledger entry for the model's eyes.

    :param entry: The entry to render.
    :returns: A single labelled line.
    """
    label = {
        "activity": "Claude is",
        "summary": "Claude said",
        "question": "They asked",
        "answer": "You said",
        "note": "Note",
    }.get(entry.kind, "Note")
    return f"- {label}: {entry.text}"


class DiscussionRegistry:
    """The live companions, one per Omnigent session."""

    def __init__(self) -> None:
        self._sessions: dict[str, DiscussionSession] = {}
        self._lock = asyncio.Lock()

    async def get(self, session_id: str) -> DiscussionSession:
        """Return the companion for a session, creating it if needed.

        Creation does not spawn a process: that waits for the first
        question, so a session nobody talks to costs nothing.

        :param session_id: Omnigent session id.
        """
        async with self._lock:
            existing = self._sessions.get(session_id)
            if existing is None:
                existing = DiscussionSession(session_id)
                self._sessions[session_id] = existing
            return existing

    def peek(self, session_id: str) -> DiscussionSession | None:
        """Return an existing companion without creating one."""
        return self._sessions.get(session_id)

    def note(self, session_id: str, kind: EntryKind, text: str) -> None:
        """Record context for a session, creating its companion if needed.

        Synchronous on purpose: the callers are turn hot paths that must
        not await on the companion's account. Safe without the lock —
        creation neither awaits nor blocks, so no other coroutine can
        interleave between the lookup and the insert.

        :param session_id: Omnigent session id.
        :param kind: See :meth:`DiscussionSession.note`.
        :param text: The note.
        """
        session = self._sessions.get(session_id)
        if session is None:
            session = DiscussionSession(session_id)
            self._sessions[session_id] = session
        session.note(kind, text)

    async def close(self, session_id: str) -> None:
        """Close and forget one session's companion."""
        async with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is not None:
            await session.close()

    async def close_all(self) -> None:
        """Close every companion. Called on server shutdown."""
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            await session.close()

    async def sweep(self, *, idle_s: float | None = None) -> int:
        """Reap processes idle beyond the threshold.

        The ledger is kept, so a reaped session still knows everything
        it knew; it just pays a cold start on the next question.

        :param idle_s: Threshold override, in seconds.
        :returns: How many processes were stopped.
        """
        threshold = idle_s if idle_s is not None else idle_timeout_s()
        async with self._lock:
            candidates = [s for s in self._sessions.values() if s.running and s.idle_s > threshold]
        for session in candidates:
            _logger.info("reaping idle companion for session %s", session.session_id)
            await session.close()
        return len(candidates)


_REGISTRY: Final[DiscussionRegistry] = DiscussionRegistry()


def registry() -> DiscussionRegistry:
    """Return the process-wide companion registry."""
    return _REGISTRY


def note(session_id: str, kind: EntryKind, text: str) -> None:
    """Tell the session's companion something, and never raise.

    This is what the turn hot paths call. The companion is a convenience
    hanging off the side of a session: a failure here must be invisible
    to the turn that was being served, so everything is swallowed.

    :param session_id: Omnigent session id.
    :param kind: ``"activity"`` for what is happening, ``"summary"`` for
        what Claude said.
    :param text: The note.
    """
    try:
        _REGISTRY.note(session_id, kind, text)
    except Exception:  # noqa: BLE001 - a companion must never break a turn
        _logger.debug("companion note dropped for %s", session_id, exc_info=True)


async def sweep_idle_companions(*, interval_s: float = 60.0) -> None:
    """Reap idle companion processes forever. Started by the server lifespan.

    A warm process costs nothing to bill but does hold a subprocess, and
    a session the reader wandered away from should not keep one alive
    until the server restarts. Reaping is cheap precisely because the
    ledger is the memory: the session keeps everything it knew.

    :param interval_s: How often to check.
    """
    while True:
        try:
            await asyncio.sleep(interval_s)
            await _REGISTRY.sweep()
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - a sweep must never kill the loop
            _logger.exception("companion sweep failed")
