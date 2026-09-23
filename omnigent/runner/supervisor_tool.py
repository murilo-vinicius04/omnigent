"""Runner side of ``sys_ask_supervisor``: gather what the orchestrator knows, then ask.

The supervisor reads what the worker's orchestrator knows, not what the worker
chose to include: the human's request (the orchestrator's last message from a
person), the orchestrator's latest messages, the brief that started this
turn, and every step the worker has taken since. All of it comes over the
same REST API the rest of the runner uses, so no harness has to cooperate.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from omnigent import supervisor
from omnigent.runner.worker_evidence import (
    _CONTINUATION_PREFIXES,
    _arguments,
    _command_text,
    _edited_paths,
    _message_text,
    _output_text,
)

_WORKER_ITEMS: int = 500
_PARENT_ITEMS: int = 200
#: The orchestrator's latest messages carry its plan and what it has learnt.
_NOTE_MESSAGES: int = 3


async def _items(client: httpx.AsyncClient, session_id: str, limit: int) -> list[dict[str, Any]]:
    """Return a session's items, oldest first; empty on any failure."""
    try:
        resp = await client.get(
            f"/v1/sessions/{session_id}/items",
            params={"limit": limit, "order": "desc"},
            timeout=15.0,
        )
        data = resp.json().get("data", []) if resp.status_code == 200 else []
    except (httpx.HTTPError, ValueError, AttributeError):
        return []
    return [i for i in reversed(data) if isinstance(i, dict)] if isinstance(data, list) else []


async def _parent_of(client: httpx.AsyncClient, session_id: str) -> str:
    """Return the session's parent id, or ``""`` for a top-level session."""
    try:
        resp = await client.get(
            f"/v1/sessions/{session_id}",
            params={"include_items": "false", "include_liveness": "false"},
            timeout=15.0,
        )
        parent = resp.json().get("parent_session_id") if resp.status_code == 200 else None
    except (httpx.HTTPError, ValueError, AttributeError):
        return ""
    return parent if isinstance(parent, str) else ""


def _is_user(item: dict[str, Any]) -> bool:
    return item.get("type") == "message" and item.get("role") == "user"


def split_turn(items: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Split a worker's history into the brief that started this turn and the turn's items.

    :param items: The worker's items, oldest first.
    :returns: ``(brief, items after it)``; the brief is ``""`` if none is found.
    """
    for index in range(len(items) - 1, -1, -1):
        item = items[index]
        if _is_user(item) and not _message_text(item).lstrip().startswith(_CONTINUATION_PREFIXES):
            return _message_text(item), items[index + 1 :]
    return "", items


def render_progress(turn: list[dict[str, Any]]) -> str:
    """One line per step the worker took this turn, with a clipped result.

    :param turn: The turn's items, oldest first.
    :returns: The progress text.
    """
    lines: list[str] = []
    for item in turn:
        kind = item.get("type")
        if kind == "function_call":
            args = _arguments(item)
            what = _command_text(args) or ", ".join(_edited_paths(args)) or json.dumps(args)[:200]
            lines.append(f"- called {item.get('name')}: {what[:300]}")
        elif kind == "function_call_output":
            out = _output_text(item.get("output")).strip()
            if out:
                lines.append(f"  result: {out[-400:]}")
        elif kind == "message" and item.get("role") == "assistant":
            text = _message_text(item).strip()
            if text:
                lines.append(f"- said: {text[:600]}")
    return "\n".join(lines)


def orchestrator_view(parent_items: list[dict[str, Any]]) -> tuple[str, str]:
    """What the orchestrator knows: the person's last request and its latest messages.

    Framework notices (``[System: ...]``, ``[Omnigent] ...``) are not requests.

    :param parent_items: The orchestrator's items, oldest first.
    :returns: ``(request, notes)``.
    """
    request = next(
        (
            _message_text(i)
            for i in reversed(parent_items)
            if _is_user(i) and not _message_text(i).lstrip().startswith("[")
        ),
        "",
    )
    said = [
        _message_text(i).strip()
        for i in parent_items
        if i.get("type") == "message" and i.get("role") == "assistant" and _message_text(i).strip()
    ]
    return request, "\n\n".join(said[-_NOTE_MESSAGES:])


async def gather_knowledge(
    client: httpx.AsyncClient, session_id: str
) -> tuple[supervisor.Knowledge, str]:
    """Collect the supervisor's reading for one worker session.

    :returns: ``(knowledge, parent session id or "")``.
    """
    parent = await _parent_of(client, session_id)
    brief, turn = split_turn(await _items(client, session_id, _WORKER_ITEMS))
    request, notes = (
        orchestrator_view(await _items(client, parent, _PARENT_ITEMS)) if parent else (brief, "")
    )
    return (
        supervisor.Knowledge(
            request=request, brief=brief, notes=notes, progress=render_progress(turn)
        ),
        parent,
    )


def _question_from(args: dict[str, Any]) -> supervisor.Question:
    """Build a validated question from the tool arguments.

    :raises supervisor.SupervisorError: With a message the worker can act on.
    """
    kind = args.get("kind")
    text = args.get("question")
    options = args.get("options") or []
    evidence = args.get("evidence") or ""
    if kind not in ("yes_no", "choice", "score") or not isinstance(text, str):
        raise supervisor.SupervisorError(
            "Give `kind` (yes_no, choice or score) and a `question` string."
        )
    if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
        raise supervisor.SupervisorError("`options` must be a list of strings.")
    if not isinstance(evidence, str):
        raise supervisor.SupervisorError("`evidence` must be a string.")
    question = supervisor.Question(
        kind=kind, question=text, options=tuple(options), evidence=evidence
    )
    supervisor.validate(question)
    return question


async def ask_via_rest(
    args: dict[str, Any],
    conversation_id: str | None,
    server_client: httpx.AsyncClient | None,
) -> str:
    """Dispatch ``sys_ask_supervisor``; every failure is a readable tool result.

    :param args: The tool arguments.
    :param conversation_id: The asking session.
    :param server_client: The runner's client for the server API.
    :returns: The answer, worded for the worker.
    """
    try:
        question = _question_from(args)
    except supervisor.SupervisorError as exc:
        return f"sys_ask_supervisor: {exc}"
    if server_client is None or not conversation_id:
        knowledge, parent = supervisor.Knowledge(), ""
    else:
        knowledge, parent = await gather_knowledge(server_client, conversation_id)
    verdict = await supervisor.ask(
        question, knowledge, session_id=conversation_id or "", parent_id=parent
    )
    return supervisor.format_for_worker(verdict)
