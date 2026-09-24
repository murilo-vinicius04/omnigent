"""The supervisor's next-move check while a worker runs: Omnigent builds the options.

Every few tool calls, Hermes' ``pre_tool_call`` hook asks Jev to pick the worker's
next move from five fixed options, given only the brief and the turn's recent
steps. When Jev is confident and the call about to run is a different kind of
move, the call is skipped and the worker reads a one-line redirect instead, so
a worker that keeps reading, never tests, or edits past a failing test is
steered without having to ask. Every error lets the call run.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Final

import httpx

from omnigent import supervisor
from omnigent.runner.supervisor_tool import _items, render_progress, split_turn
from omnigent.runner.worker_evidence import (
    _EDIT_TOOLS,
    _TEST_COMMAND,
    _arguments,
    _command_text,
)

#: Only a confident pick redirects; the unsure middle lets the worker carry on.
DEFAULT_ACT_ABOVE: Final[float] = 0.8

#: Seconds Hermes waits for the hook: two backends at ``REQUEST_TIMEOUT_S`` each.
HOOK_TIMEOUT_S: Final[int] = 60

_BRIEF_HEAD_CHARS: Final[int] = 3_000
_BRIEF_TAIL_CHARS: Final[int] = 3_000
_PROGRESS_CHARS: Final[int] = 8_000
_NEXT_CALL_CHARS: Final[int] = 400
_WORKER_ITEMS: Final[int] = 500

_READ_TOOLS: Final[frozenset[str]] = frozenset(
    {"read_file", "search_files", "list_files", "file_search", "list_dir", "view_file"}
    | {"Read", "Grep", "Glob", "LS", "grep_search", "find_by_name", "view_file_outline"}
)
_READ_COMMAND: Final[re.Pattern[str]] = re.compile(
    r"^(cat|head|tail|less|sed -n|grep|rg|ag|ls|find|tree|wc|file|stat|pwd"
    r"|git (show|log|diff|status|grep|blame))\b"
)
_LEADING_CD: Final[re.Pattern[str]] = re.compile(r"^\s*cd\s+\S+\s*(&&|;)\s*")


@dataclass(frozen=True)
class Move:
    """One option Jev picks from, and what the worker reads when it is the pick.

    :param key: Option id sent to Jev as the criterion's name.
    :param criterion: When this is the right next move; Jev reads only this text.
    :param redirect: The instruction the worker gets when a call is skipped for it.
    """

    key: str
    criterion: str
    redirect: str


MOVES: Final[tuple[Move, ...]] = (
    Move(
        "investigate",
        "Keep reading or searching the code: the worker does not yet know what to change"
        " or where.",
        "You do not know enough yet: read the code the brief points to before changing anything.",
    ),
    Move(
        "edit",
        "Change the code: the worker knows what to change and has not finished the changes"
        " the brief asks for.",
        "You have enough context: make the change the brief asks for now instead of reading more.",
    ),
    Move(
        "test",
        "Run the tests or checks the brief names: code changed since the last test run, or"
        " no test has run yet.",
        "Run the tests or checks the brief names now: code changed since the last test run.",
    ),
    Move(
        "fix",
        "Fix what failed: the last test run or check shows failures or errors caused by the"
        " worker's changes.",
        "Your last test run failed: fix those failures before anything else.",
    ),
    Move(
        "report",
        "Stop and report: everything the brief asks for is done and the last test run passed.",
        "The brief looks done and tested: stop here and write your report.",
    ),
)
_MOVES_BY_KEY: Final[dict[str, Move]] = {m.key: m for m in MOVES}
QUESTION: Final[str] = (
    "Given the brief and what the worker has done so far this turn, what should its next move be?"
)


@dataclass(frozen=True)
class MoveVerdict:
    """The outcome of one next-move check.

    :param probabilities: ``P(move)`` per move key; empty when no backend answered.
    :param pick: Jev's most likely move, or ``""``.
    :param worker_move: The kind of move the pending call is, or ``None`` if unknown.
    :param message: The redirect for the worker, or ``""`` to let the call run.
    :param backend: Which backend answered, or ``"none"``.
    :param latency_ms: Wall time of the call that answered.
    :param cost_usd: What that call cost, as the backend reported it.
    :param error: Why no backend answered, else ``""``.
    """

    probabilities: dict[str, float] = field(default_factory=dict)
    pick: str = ""
    worker_move: str | None = None
    message: str = ""
    backend: str = "none"
    latency_ms: int = 0
    cost_usd: float = 0.0
    error: str = ""


def enabled() -> bool:
    """Whether Hermes workers get the next-move check.

    ``OMNIGENT_SUPERVISOR_NEXT_MOVE=on|off`` wins; otherwise the
    ``next_move`` flag file decides (see ``supervisor.switched_on``).
    A backend must be configured either way.

    :returns: True when the ``pre_tool_call`` hook should be registered.
    """
    return supervisor.switched_on("OMNIGENT_SUPERVISOR_NEXT_MOVE", "next_move") and bool(
        supervisor.configured_backends()
    )


def act_above() -> float:
    """Return the pick probability at or above which a mismatched call is skipped.

    From ``OMNIGENT_SUPERVISOR_ACT_ABOVE``; values outside ``(0.5, 1]`` fall back.
    """
    raw = os.environ.get("OMNIGENT_SUPERVISOR_ACT_ABOVE", "").strip()
    try:
        value = float(raw) if raw else DEFAULT_ACT_ABOVE
    except ValueError:
        return DEFAULT_ACT_ABOVE
    return value if 0.5 < value <= 1.0 else DEFAULT_ACT_ABOVE


def classify_call(tool_name: str, args: dict[str, Any]) -> str | None:
    """Name the kind of move a pending tool call is.

    :param tool_name: E.g. ``"terminal"`` or ``"patch"``.
    :param args: The call's arguments.
    :returns: ``"edit"``, ``"test"``, ``"investigate"``, or ``None`` when unclear.
    """
    if tool_name in _EDIT_TOOLS:
        return "edit"
    if tool_name in _READ_TOOLS:
        return "investigate"
    command = _command_text(args)
    if not command:
        return None
    if _TEST_COMMAND.search(command):
        return "test"
    bare = _LEADING_CD.sub("", command.strip())
    return "investigate" if _READ_COMMAND.match(bare) else None


def _clip_middle(text: str, head: int, tail: int) -> str:
    if len(text) <= head + tail:
        return text
    return f"{text[:head]}\n[...middle of the brief cut]\n{text[-tail:]}"


def render_move_state(
    brief: str, turn: list[dict[str, Any]], tool_name: str, args: dict[str, Any]
) -> str:
    """Lay out what the check reads: the brief, this turn's steps, and the pending call.

    :param brief: The order that started the worker's turn.
    :param turn: The turn's items after the brief, oldest first.
    :param tool_name: The call about to run.
    :param args: Its arguments.
    :returns: The state text.
    """
    brief = brief.replace(supervisor.WORKER_RULE, "").strip()
    calls = [i for i in turn if i.get("type") == "function_call"]
    edits = sum(1 for c in calls if str(c.get("name")) in _EDIT_TOOLS)
    tests = sum(
        1
        for c in calls
        if (cmd := _command_text(_arguments(c))) is not None and _TEST_COMMAND.search(cmd)
    )
    progress = render_progress(turn).strip() or "Nothing yet."
    if len(progress) > _PROGRESS_CHARS:
        progress = "[...earlier steps cut]\n" + progress[-_PROGRESS_CHARS:]
    detail = _command_text(args) or str(args)
    parts = (
        (
            "The brief the worker was given",
            _clip_middle(brief, _BRIEF_HEAD_CHARS, _BRIEF_TAIL_CHARS),
        ),
        (
            f"What the worker did this turn, oldest first ({len(calls)} tool calls,"
            f" {edits} edits, {tests} test runs)",
            progress,
        ),
        ("The call the worker is about to make", f"{tool_name}: {detail[:_NEXT_CALL_CHARS]}"),
    )
    return "\n\n".join(f"## {title}\n{body}" for title, body in parts)


async def decide_move(
    state: str,
    worker_move: str | None,
    tool_name: str,
    *,
    backends: list[supervisor.Backend] | None = None,
    session_id: str = "",
) -> MoveVerdict:
    """Ask Jev for the next move and turn a confident mismatch into a redirect.

    Never raises for backend trouble: an unanswered check lets the call run.

    :param state: From :func:`render_move_state`.
    :param worker_move: :func:`classify_call` of the pending call.
    :param tool_name: The pending call's tool, for the redirect and the ledger.
    :param backends: Override for tests; defaults to the supervisor's backends.
    :param session_id: The worker's session, for the ledger.
    :returns: The verdict; ``message`` is empty unless the call should be skipped.
    """
    chosen = supervisor.configured_backends() if backends is None else backends
    question = {
        "type": "choice",
        "instructions": QUESTION,
        "criteria": {m.key: m.criterion for m in MOVES},
    }
    errors: list[str] = []
    verdict = MoveVerdict(worker_move=worker_move)
    for backend in chosen:
        started = time.perf_counter()
        try:
            answers, usage = await supervisor.post_questions(backend, state, {"move": question})
            raw = answers["move"].get("probabilities") or {}
            probs = {m.key: round(float(raw[m.key]), 4) for m in MOVES if m.key in raw}
            if not probs:
                raise ValueError(f"no probabilities: {answers['move']!r}")
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"{backend.name}: {exc}")
            continue
        pick = max(probs, key=lambda k: probs[k])
        acts = worker_move is not None and pick != worker_move and probs[pick] >= act_above()
        message = (
            f"[Supervisor] Skipped this {tool_name} call. {_MOVES_BY_KEY[pick].redirect}"
            if acts
            else ""
        )
        verdict = MoveVerdict(
            probabilities=probs,
            pick=pick,
            worker_move=worker_move,
            message=message,
            backend=backend.name,
            latency_ms=round((time.perf_counter() - started) * 1000),
            cost_usd=float(usage.get("cost") or 0.0),
        )
        break
    else:
        verdict = MoveVerdict(
            worker_move=worker_move,
            error="; ".join(errors) or "no supervisor backend is configured",
        )
    supervisor.append_ledger(
        {
            "at": time.time(),
            "session": session_id,
            "kind": "next_move",
            "tool": tool_name,
            "state_chars": len(state),
            "probabilities": verdict.probabilities,
            "pick": verdict.pick,
            "worker_move": worker_move,
            "acted": bool(verdict.message),
            "backend": verdict.backend,
            "latency_ms": verdict.latency_ms,
            "cost_usd": verdict.cost_usd,
            "error": verdict.error,
        }
    )
    return verdict


async def check_next_move(
    client: httpx.AsyncClient,
    session_id: str,
    tool_name: str,
    args: dict[str, Any],
    *,
    backends: list[supervisor.Backend] | None = None,
) -> dict[str, str]:
    """Run the check for a worker's pending call; the hook's stdout object.

    :param client: HTTP client pointed at the Omnigent server.
    :param session_id: The worker's Omnigent session id.
    :param tool_name: The pending call's tool.
    :param args: Its arguments.
    :param backends: Override for tests.
    :returns: ``{"decision": "block", "reason": ...}`` to skip the call, else ``{}``.
    """
    worker_move = classify_call(tool_name, args)
    if worker_move is None:
        return {}
    items = await _items(client, session_id, _WORKER_ITEMS)
    brief, turn = split_turn(items)
    if not brief:
        return {}
    verdict = await decide_move(
        render_move_state(brief, turn, tool_name, args),
        worker_move,
        tool_name,
        backends=backends,
        session_id=session_id,
    )
    return {"decision": "block", "reason": verdict.message} if verdict.message else {}
