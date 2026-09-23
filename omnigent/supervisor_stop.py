"""The supervisor's check when a worker tries to finish: code asks, not the worker.

A weak worker rarely notices when it should consult the supervisor, so a rule in
its brief is not enough. Jev is meant to be called from a hook at a fixed
decision point instead, so this runs from Hermes' ``pre_verify`` hook when a
worker that edited files is about to end its turn.

Jev reads only what the check needs -- the brief, the worker's final answer and
the runner's evidence (files edited, git status, last test command and its real
output) -- because its accuracy drops when the state carries irrelevant detail.
Fixed yes/no questions go in one call. A confident "no" (``P(yes)`` at or below
:func:`block_below`) keeps the worker going with that check's instruction;
anything else, and every error, lets it finish: the check must never trap a
worker, and Hermes caps the nudges per turn anyway.
"""

from __future__ import annotations

import os
import pathlib
import time
from dataclasses import dataclass, field
from typing import Any, Final

import httpx

from omnigent import supervisor
from omnigent.runner.supervisor_tool import _items, _parent_of, split_turn
from omnigent.runner.worker_evidence import extract_evidence, git_changes, worker_dirs

#: Touch to run the check at Hermes workers' turn ends (also needs a backend).
ENABLED_FLAG: Final[pathlib.Path] = supervisor.ENABLED_FLAG.with_name("stop_check")

#: The OpenRouter cookbook gates tool calls at 0.1 block / 0.9 approve: only a
#: confident "no" acts, the unsure middle is left to the orchestrator's review.
DEFAULT_BLOCK_BELOW: Final[float] = 0.1

#: Hermes' ``agent.max_verify_nudges`` while the check is on (Hermes' default is 3).
MAX_NUDGES: Final[int] = 2

#: Seconds Hermes waits for the hook: two backends at ``REQUEST_TIMEOUT_S`` each.
HOOK_TIMEOUT_S: Final[int] = 60

_BRIEF_HEAD_CHARS: Final[int] = 4_000
_BRIEF_TAIL_CHARS: Final[int] = 4_000
_ANSWER_CHARS: Final[int] = 3_000
_WORKER_ITEMS: Final[int] = 500


@dataclass(frozen=True)
class Check:
    """One yes/no question about a finishing worker, and what to tell it on a "no".

    :param key: Question id; TypeSafe never shows it to the model.
    :param question: The noul instruction, answered yes or no.
    :param instruction: Sent to the worker when the answer is a confident "no".
    """

    key: str
    question: str
    instruction: str


CHECKS: Final[tuple[Check, ...]] = (
    Check(
        "ran_tests",
        "Did the worker run the tests or checks that the brief asks for?",
        "Run the tests or checks the brief names now, and put their real output in your report.",
    ),
    Check(
        "tests_pass",
        "Does the last test output in the evidence show the tests passing, with no failures"
        " or errors?",
        "Your last test run is not clean. Fix what fails, run the tests again, and report"
        " the real output.",
    ),
    Check(
        "claims_backed",
        "Is every claim of success in the worker's final answer supported by the evidence of"
        " what its tools did?",
        "Your report claims things the evidence does not show. Check each claim against the"
        " files and the test output, fix what is missing, and report only what you verified.",
    ),
    Check(
        "brief_done",
        "Has the worker done everything the brief asks for?",
        "Part of the brief is not done yet. Re-read the brief, finish the missing parts, and"
        " verify them before you report.",
    ),
)


@dataclass(frozen=True)
class StopVerdict:
    """The outcome of one check.

    :param p_yes: ``P(yes)`` per check key; empty when no backend answered.
    :param failed: Keys answered with a confident "no", in :data:`CHECKS` order.
    :param message: What the worker is told, or ``""`` to let it finish.
    :param backend: Which backend answered, or ``"none"``.
    :param latency_ms: Wall time of the call that answered.
    :param cost_usd: What that call cost, as the backend reported it.
    :param error: Why no backend answered, else ``""``.
    """

    p_yes: dict[str, float] = field(default_factory=dict)
    failed: tuple[str, ...] = ()
    message: str = ""
    backend: str = "none"
    latency_ms: int = 0
    cost_usd: float = 0.0
    error: str = ""


def enabled() -> bool:
    """Whether Hermes workers get the check at their turn ends.

    ``OMNIGENT_SUPERVISOR_STOP_CHECK=on|off`` wins; otherwise the
    :data:`ENABLED_FLAG` file decides. A backend must be configured either way.

    :returns: True when the ``pre_verify`` hook should be registered.
    """
    raw = os.environ.get("OMNIGENT_SUPERVISOR_STOP_CHECK", "").strip().lower()
    wanted = raw in ("1", "on", "true", "yes") if raw else ENABLED_FLAG.exists()
    return wanted and bool(supervisor.configured_backends())


def block_below() -> float:
    """Return the ``P(yes)`` at or below which a check stops the worker finishing.

    From ``OMNIGENT_SUPERVISOR_BLOCK_BELOW``; values outside ``[0, 0.5)`` fall back.
    """
    raw = os.environ.get("OMNIGENT_SUPERVISOR_BLOCK_BELOW", "").strip()
    try:
        value = float(raw) if raw else DEFAULT_BLOCK_BELOW
    except ValueError:
        return DEFAULT_BLOCK_BELOW
    return value if 0.0 <= value < 0.5 else DEFAULT_BLOCK_BELOW


def _clip_middle(text: str, head: int, tail: int) -> str:
    """Keep the start and the end: briefs put the task first and "done when" last."""
    if len(text) <= head + tail:
        return text
    return f"{text[:head]}\n[...middle of the brief cut]\n{text[-tail:]}"


def render_stop_state(brief: str, final_answer: str, evidence: str) -> str:
    """Lay out what the check reads: only the brief, the answer and the evidence.

    :param brief: The order that started the worker's turn.
    :param final_answer: The text the worker is about to finish with.
    :param evidence: The runner's evidence block, or ``""`` if the turn had no tool calls.
    :returns: The state text.
    """
    brief = brief.replace(supervisor.WORKER_RULE, "").strip()
    answer = final_answer.strip()
    if len(answer) > _ANSWER_CHARS:
        answer = answer[:_ANSWER_CHARS] + "\n[...rest of the answer cut]"
    parts = (
        (
            "The brief the worker was given",
            _clip_middle(brief, _BRIEF_HEAD_CHARS, _BRIEF_TAIL_CHARS),
        ),
        ("The worker's final answer", answer or "(empty)"),
        (
            "What the worker's tools actually did (collected by the runner, not written by"
            " the worker)",
            evidence.strip() or "No tool calls were found in this turn.",
        ),
    )
    return "\n\n".join(f"## {title}\n{body}" for title, body in parts)


def _p_yes(answer: dict[str, Any]) -> float:
    return round(float(answer["noul"]), 4)


async def decide(
    state: str,
    *,
    checks: tuple[Check, ...] = CHECKS,
    backends: list[supervisor.Backend] | None = None,
    session_id: str = "",
    parent_id: str = "",
) -> StopVerdict:
    """Ask every check in one call and turn confident "no"s into one instruction.

    Never raises for backend trouble: an unanswered check lets the worker finish.

    :param state: From :func:`render_stop_state`.
    :param checks: The questions to ask.
    :param backends: Override for tests; defaults to the supervisor's backends.
    :param session_id: The worker's session, for the ledger.
    :param parent_id: Its orchestrator's session, for the ledger.
    :returns: The verdict; ``message`` is empty unless the worker must go on.
    """
    chosen = supervisor.configured_backends() if backends is None else backends
    questions = {c.key: {"type": "noul", "instructions": c.question} for c in checks}
    errors: list[str] = []
    verdict = StopVerdict()
    for backend in chosen:
        started = time.perf_counter()
        try:
            answers, usage = await supervisor.post_questions(backend, state, questions)
            p_yes = {c.key: _p_yes(answers[c.key]) for c in checks}
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"{backend.name}: {exc}")
            continue
        bar = block_below()
        failed = tuple(c.key for c in checks if p_yes[c.key] <= bar)
        lines = [c.instruction for c in checks if c.key in failed]
        message = (
            "[Supervisor check before you finish]\n"
            + "\n".join(f"- {line}" for line in lines)
            + "\nThen finish with your report."
            if lines
            else ""
        )
        verdict = StopVerdict(
            p_yes=p_yes,
            failed=failed,
            message=message,
            backend=backend.name,
            latency_ms=round((time.perf_counter() - started) * 1000),
            cost_usd=float(usage.get("cost") or 0.0),
        )
        break
    else:
        verdict = StopVerdict(error="; ".join(errors) or "no supervisor backend is configured")
    supervisor.append_ledger(
        {
            "at": time.time(),
            "session": session_id,
            "parent": parent_id,
            "kind": "stop_check",
            "state_chars": len(state),
            "p_yes": verdict.p_yes,
            "failed": list(verdict.failed),
            "blocked": bool(verdict.message),
            "backend": verdict.backend,
            "latency_ms": verdict.latency_ms,
            "cost_usd": verdict.cost_usd,
            "error": verdict.error,
        }
    )
    return verdict


async def check_worker_stop(
    client: httpx.AsyncClient,
    session_id: str,
    final_answer: str,
    *,
    backends: list[supervisor.Backend] | None = None,
) -> dict[str, str]:
    """Run the check for a worker session about to finish; the hook's stdout object.

    :param client: HTTP client pointed at the Omnigent server.
    :param session_id: The worker's Omnigent session id.
    :param final_answer: The answer Hermes is about to finish the turn with.
    :param backends: Override for tests.
    :returns: ``{"decision": "block", "reason": ...}`` to keep the worker going,
        else ``{}``.
    """
    items = await _items(client, session_id, _WORKER_ITEMS)
    brief, _turn = split_turn(items)
    if not brief:
        return {}
    parent_id = await _parent_of(client, session_id)
    evidence = extract_evidence(items, await git_changes(worker_dirs(items))) or ""
    verdict = await decide(
        render_stop_state(brief, final_answer, evidence),
        backends=backends,
        session_id=session_id,
        parent_id=parent_id,
    )
    return {"decision": "block", "reason": verdict.message} if verdict.message else {}
