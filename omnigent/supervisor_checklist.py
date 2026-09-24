"""The supervisor's checklist check when a worker reports back.

The orchestrator ends a brief with a ``CHECKLIST:`` block: behaviours the change
must have, including facts only it knows. When the worker reports, this asks the
supervisor one yes/no question per item, reading the code the worker changed
(``git diff`` plus new files) and the runner's evidence of its last test run --
never the worker's own report, which argues for itself. It runs where every
worker's result enters the orchestrator's inbox, so it covers every harness.

An item judged unmet (``P(met)`` at or below :func:`send_back_below`) sends the
worker back with those items, at most :data:`MAX_ROUNDS` times per checklist;
otherwise the orchestrator gets the result with the verdict per item, so its
review starts from the checklist. Every error lets the result through unchanged.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import re
import time
from dataclasses import dataclass, field
from typing import Any, Final

import httpx

from omnigent import supervisor
from omnigent.runner.supervisor_tool import _items, _message_text
from omnigent.runner.worker_evidence import (
    _CONTINUATION_PREFIXES,
    _git,
    extract_evidence,
    git_changes,
    repo_roots,
    worker_dirs,
)

#: Times one checklist may send a worker back before the orchestrator takes over.
MAX_ROUNDS: Final[int] = 2

#: ``P(met)`` at or below which an item sends the worker back. Jev rarely goes
#: below 0.4 even on an unmet item, so a low bar never fires; replayed on real
#: trees, 0.5 sent back only items the hidden tests confirmed unmet.
DEFAULT_SEND_BACK_BELOW: Final[float] = 0.5

MAX_ITEMS: Final[int] = 12
_ITEM_CHARS: Final[int] = 300
_WORKER_ITEMS: Final[int] = 500
_DIFF_CHARS: Final[int] = 45_000
_FILE_CHARS: Final[int] = 8_000
_NEW_FILE_BYTES: Final[int] = 200_000
_SKIPPED_FILES: Final[re.Pattern[str]] = re.compile(
    r"(^|/)(uv\.lock|package-lock\.json|pnpm-lock\.yaml|yarn\.lock|poetry\.lock)$"
    r"|(^|/)(\.[^/]+-tmp|\.venv|node_modules|__pycache__)(/|$)|\.min\.(js|css)$"
)
_HEADER: Final[re.Pattern[str]] = re.compile(r"^\W*checklist\W*:?\W*$", re.IGNORECASE)
_ITEM: Final[re.Pattern[str]] = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(?:\[[ xX]\]\s*)?(.+?)\s*$")

#: How many times each checklist has sent each worker back, by ``(child, checklist)``.
_rounds: dict[tuple[str, str], int] = {}
#: The items each checklist last sent its worker back on, by ``(child, checklist)``.
_last_unmet: dict[tuple[str, str], tuple[str, ...]] = {}


@dataclass(frozen=True)
class ChecklistVerdict:
    """The supervisor's reading of one checklist.

    :param p_yes: ``P(met)`` per item, in checklist order; empty when unanswered.
    :param unmet: Items at or below :func:`send_back_below`, in checklist order.
    :param unsure: Items between that bar and the supervisor's confidence bar.
    :param backend: Which backend answered, or ``"none"``.
    :param latency_ms: Wall time of the call that answered.
    :param error: Why no backend answered, else ``""``.
    """

    p_yes: dict[str, float] = field(default_factory=dict)
    unmet: tuple[str, ...] = ()
    unsure: tuple[str, ...] = ()
    backend: str = "none"
    latency_ms: int = 0
    error: str = ""


def enabled() -> bool:
    """Whether worker results get the checklist check.

    ``OMNIGENT_SUPERVISOR_CHECKLIST=on|off`` wins; otherwise the
    ``checklist`` flag file decides (see ``supervisor.switched_on``).
    A backend must be configured either way.
    """
    return supervisor.switched_on("OMNIGENT_SUPERVISOR_CHECKLIST", "checklist") and bool(
        supervisor.configured_backends()
    )


def send_back_below() -> float:
    """``P(met)`` at or below which an item sends the worker back.

    From ``OMNIGENT_SUPERVISOR_CHECKLIST_BELOW``; values outside ``[0, 0.75)`` fall back.
    """
    raw = os.environ.get("OMNIGENT_SUPERVISOR_CHECKLIST_BELOW", "").strip()
    try:
        value = float(raw) if raw else DEFAULT_SEND_BACK_BELOW
    except ValueError:
        return DEFAULT_SEND_BACK_BELOW
    return value if 0.0 <= value < 0.75 else DEFAULT_SEND_BACK_BELOW


def parse_checklist(brief: str) -> tuple[str, ...]:
    """Return the items of the brief's ``CHECKLIST:`` block, or ``()`` if it has none.

    Items are the list lines right after the header (``-``, ``*``, ``1.``,
    ``- [ ]``); the block ends at the first line that is neither an item nor blank.

    :param brief: The orchestrator's order, e.g. ``"...\\nCHECKLIST:\\n- a\\n- b"``.
    :returns: Up to :data:`MAX_ITEMS` distinct items.
    """
    lines = brief.splitlines()
    start = next((i for i, line in enumerate(lines) if _HEADER.match(line)), None)
    if start is None:
        return ()
    items: list[str] = []
    for line in lines[start + 1 :]:
        if not line.strip():
            if items:
                break
            continue
        match = _ITEM.match(line)
        if match is None:
            break
        item = match.group(1)[:_ITEM_CHARS]
        if item not in items:
            items.append(item)
    return tuple(items[:MAX_ITEMS])


def latest_checklist(items: list[dict[str, Any]]) -> tuple[str, ...]:
    """The newest checklist the orchestrator gave this worker.

    A fix-round order often carries none, so earlier orders count too.

    :param items: The worker session's items, oldest first.
    """
    for item in reversed(items):
        if item.get("type") != "message" or item.get("role") != "user":
            continue
        text = _message_text(item)
        if text.lstrip().startswith(_CONTINUATION_PREFIXES):
            continue
        found = parse_checklist(text)
        if found:
            return found
    return ()


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n[...rest of this file cut]\n"


async def collect_diff(roots: list[str]) -> str:
    """The code change in ``roots``: ``git diff HEAD`` and every new file in full.

    :param roots: Repository top-levels, e.g. from ``repo_roots``.
    :returns: The change, at most :data:`_DIFF_CHARS` characters, or ``""``.
    """
    parts: list[str] = []
    for root in roots:
        listed = await _git("-C", root, "diff", "HEAD", "--name-only")
        for name in (listed or "").splitlines():
            if not name or _SKIPPED_FILES.search(name):
                continue
            body = await _git("-C", root, "diff", "HEAD", "--no-color", "--", name)
            if body:
                parts.append(_clip(body, _FILE_CHARS))
        untracked = await _git("-C", root, "ls-files", "--others", "--exclude-standard")
        for name in (untracked or "").splitlines():
            path = pathlib.Path(root, name)
            if not name or _SKIPPED_FILES.search(name) or path.is_symlink():
                continue
            try:
                if path.stat().st_size > _NEW_FILE_BYTES:
                    continue
                raw = path.read_bytes()
            except OSError:
                continue
            if b"\0" in raw[:4096]:
                continue
            parts.append(_clip(f"new file {name}:\n{raw.decode(errors='replace')}", _FILE_CHARS))
    text, used = [], 0
    for index, part in enumerate(parts):
        if used + len(part) > _DIFF_CHARS:
            text.append(f"[... {len(parts) - index} more changed files cut]")
            break
        text.append(part)
        used += len(part)
    return "\n".join(text)


def render_checklist_state(diff: str, evidence: str) -> str:
    """What the supervisor reads: the code change and the runner's evidence.

    :param diff: From :func:`collect_diff`.
    :param evidence: The runner's evidence block for the worker's turn.
    """
    return (
        "## The code the worker changed (git diff; new files in full)\n"
        f"{diff.strip() or 'No code change was found.'}\n\n"
        "## What the worker's tools did (collected by the runner, not written by the worker)\n"
        f"{evidence.strip() or 'No tool calls were found in this turn.'}"
    )


def _question(item: str) -> str:
    return (
        "Reading only the code change and the test output above, is this requirement met"
        f" by the change? Requirement: {item}"
    )


async def decide(
    state: str,
    checklist: tuple[str, ...],
    *,
    backends: list[supervisor.Backend] | None = None,
    session_id: str = "",
) -> ChecklistVerdict:
    """Ask one yes/no per checklist item in a single call; never raises.

    :param state: From :func:`render_checklist_state`.
    :param checklist: The items, e.g. from :func:`latest_checklist`.
    :param backends: Override for tests; defaults to the supervisor's backends.
    :param session_id: The worker's session, for the ledger.
    """
    chosen = supervisor.configured_backends() if backends is None else backends
    questions = {
        f"c{i}": {"type": "noul", "instructions": _question(c)} for i, c in enumerate(checklist)
    }
    errors: list[str] = []
    verdict = ChecklistVerdict()
    for backend in chosen:
        started = time.perf_counter()
        try:
            answers, _usage = await supervisor.post_questions(backend, state, questions)
            p_yes = {c: round(float(answers[f"c{i}"]["noul"]), 4) for i, c in enumerate(checklist)}
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"{backend.name}: {exc}")
            continue
        low, high = send_back_below(), supervisor.min_confidence()
        verdict = ChecklistVerdict(
            p_yes=p_yes,
            unmet=tuple(c for c in checklist if p_yes[c] <= low),
            unsure=tuple(c for c in checklist if low < p_yes[c] < high),
            backend=backend.name,
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
        break
    else:
        verdict = ChecklistVerdict(
            error="; ".join(errors) or "no supervisor backend is configured"
        )
    supervisor.append_ledger(
        {
            "at": time.time(),
            "session": session_id,
            "kind": "checklist",
            "state_chars": len(state),
            "p_yes": verdict.p_yes,
            "unmet": list(verdict.unmet),
            "backend": verdict.backend,
            "latency_ms": verdict.latency_ms,
            "error": verdict.error,
        }
    )
    return verdict


def format_verdict(verdict: ChecklistVerdict) -> str:
    """The per-item verdict the orchestrator reads under the worker's result."""
    marks = []
    for item, p in verdict.p_yes.items():
        mark = (
            "not met" if item in verdict.unmet else "unsure" if item in verdict.unsure else "met"
        )
        marks.append(f"- {mark} ({p:.2f}): {item}")
    return (
        "[Supervisor checklist, judged from the diff and the runner's test evidence, not the"
        " worker's report. Unsure items need your own check.]\n" + "\n".join(marks)
    )


def send_back_text(unmet: tuple[str, ...]) -> str:
    """The order that sends a worker back with its unmet items."""
    lines = "\n".join(f"- {item}" for item in unmet)
    return (
        "IMPLEMENT (supervisor check before your report goes to the orchestrator). Same"
        " worktree, same rules. Reading your diff and your last test output, these checklist"
        f" items are not met yet:\n{lines}\n\nMake each one true in the code, run the tests"
        " that cover it, and report again with the real test output. If one is already met,"
        " say where in the code, in one line."
    )


@dataclass(frozen=True)
class Review:
    """What to do with a worker's result.

    :param payload: The payload to deliver, with the verdict per item appended.
    :param send_back: The order that sends the worker back, or ``None`` to deliver.
    :param notice: The payload to deliver instead once the worker was sent back.
    :param round_key: Pass to :func:`count_round` once the order was sent.
    :param unmet: The items the order sends the worker back on.
    """

    payload: dict[str, Any]
    send_back: str | None = None
    notice: dict[str, Any] | None = None
    round_key: tuple[str, str] | None = None
    unmet: tuple[str, ...] = ()


def count_round(key: tuple[str, str] | None, unmet: tuple[str, ...] = ()) -> None:
    """Record that a checklist sent its worker back once more, on ``unmet``."""
    if key is not None:
        _rounds[key] = _rounds.get(key, 0) + 1
        _last_unmet[key] = unmet


async def review(
    payload: dict[str, Any],
    *,
    server_client: httpx.AsyncClient | None,
    child_id: str | None,
    backends: list[supervisor.Backend] | None = None,
) -> Review:
    """Check a finished worker's result against its checklist; never raises.

    :param payload: The inbox payload, e.g. ``{"type": "sub_agent", "status": "completed", ...}``.
    :param server_client: HTTP client pointed at the Omnigent server.
    :param child_id: The worker session id.
    :param backends: Override for tests.
    :returns: The payload to deliver, and the order to send back when items are unmet.
    """
    if (
        payload.get("type") != "sub_agent"
        or payload.get("status") != "completed"
        or server_client is None
        or not child_id
        or not enabled()
    ):
        return Review(payload)
    try:
        items = await _items(server_client, child_id, _WORKER_ITEMS)
        checklist = latest_checklist(items)
        if not checklist:
            return Review(payload)
        dirs = worker_dirs(items)
        diff = await collect_diff(await repo_roots(dirs))
        evidence = extract_evidence(items, await git_changes(dirs)) or ""
    except (httpx.HTTPError, OSError, ValueError):
        return Review(payload)
    verdict = await decide(
        render_checklist_state(diff, evidence), checklist, backends=backends, session_id=child_id
    )
    if not verdict.p_yes:
        return Review(payload)
    output = payload.get("output")
    base = output if isinstance(output, str) else ""
    annotated = {
        **payload,
        "output": f"{base}\n\n{format_verdict(verdict)}"
        if base.strip()
        else format_verdict(verdict),
    }
    key = (child_id, hashlib.sha1("\n".join(checklist).encode()).hexdigest())
    done = _rounds.get(key, 0)
    if not verdict.unmet or done >= MAX_ROUNDS:
        return Review(annotated)
    if done and verdict.unmet == _last_unmet.get(key):
        # The worker was sent back on these exact items and reports them done
        # again: the supervisor may simply be unable to see them in a diff, so
        # a second identical round only burns the worker's quota.
        disputed = (
            "\n[Supervisor] It was already sent back on these same items and reported"
            " them done again. Check them against the code yourself."
        )
        return Review({**annotated, "output": annotated["output"] + disputed})
    who = f"{payload.get('agent') or 'worker'}/{payload.get('title') or ''}".rstrip("/")
    notice = (
        f"[Supervisor] {who} reported done, but its diff and test output do not meet"
        f" {len(verdict.unmet)} checklist item(s), so it was sent back to fix them (round"
        f" {done + 1} of {MAX_ROUNDS}). You will be woken when it reports again; nothing to"
        " review yet.\n" + format_verdict(verdict)
    )
    return Review(
        annotated, send_back_text(verdict.unmet), {**payload, "output": notice}, key, verdict.unmet
    )
