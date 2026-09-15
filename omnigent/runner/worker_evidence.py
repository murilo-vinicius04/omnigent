"""Evidence a finished worker left in its own tool calls, for the orchestrator's review.

A worker's final message is its own account and can be wrong or useless (a raw
directory listing, a pass it never ran). The orchestrator reviews faster and
cheaper when the result also carries what the worker's tools actually did: the
files it edited, and its last test command with that command's real output.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

_ITEMS_LIMIT = 500
_OUTPUT_TAIL_CHARS = 1500
_MAX_FILES_LISTED = 30
_EVIDENCE_STATUSES = frozenset({"completed", "failed"})

_COMMAND_KEYS = ("command", "CommandLine", "cmd", "code")
_PATH_KEYS = ("path", "file_path", "TargetFile", "AbsolutePath")
# Edit tools across the native workers: hermes, codex, antigravity, claude.
_EDIT_TOOLS = frozenset(
    {
        "patch",
        "write_file",
        "apply_patch",
        "replace_file_content",
        "multi_replace_file_content",
        "write_to_file",
        "Edit",
        "MultiEdit",
        "Write",
    }
)
_TEST_COMMAND = re.compile(
    r"\b(pytest|unittest|jest|vitest|go test|cargo test|npm (run )?test|pnpm test|yarn test"
    r"|just test|mvn test|gradle test|rspec|phpunit)\b"
)

HEADER = "[Runner evidence from the worker's own tool calls, not its summary]"


def _arguments(item: dict[str, Any]) -> dict[str, Any]:
    raw = item.get("arguments")
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else {}
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _current_turn(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Items after the last user message: a reused worker keeps earlier turns."""
    for index in range(len(items) - 1, -1, -1):
        item = items[index]
        if item.get("type") == "message" and item.get("role") == "user":
            return items[index + 1 :]
    return items


def _edited_paths(args: dict[str, Any]) -> list[str]:
    changes = args.get("changes")
    if isinstance(changes, list):
        return [
            c["path"] for c in changes if isinstance(c, dict) and isinstance(c.get("path"), str)
        ]
    for key in _PATH_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value:
            return [value]
    return []


def _command_text(args: dict[str, Any]) -> str | None:
    for key in _COMMAND_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _output_text(raw: Any) -> str:
    """Unwrap tool output that some harnesses store as ``{"output": "..."}`` JSON."""
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return raw
        if isinstance(parsed, dict) and isinstance(parsed.get("output"), str):
            return parsed["output"]
        return raw
    return "" if raw is None else str(raw)


def extract_evidence(items: list[dict[str, Any]]) -> str | None:
    """
    Summarize the worker's latest turn from its chronological history items.

    :param items: The worker session's items, oldest first.
    :returns: The evidence block, or ``None`` when the turn made no tool calls.
    """
    turn = _current_turn(items)
    calls = [i for i in turn if i.get("type") == "function_call"]
    if not calls:
        return None
    outputs = {
        i.get("call_id"): i.get("output") for i in turn if i.get("type") == "function_call_output"
    }
    files: list[str] = []
    last_test: tuple[str, Any] | None = None
    for call in calls:
        name = str(call.get("name") or "")
        args = _arguments(call)
        if name in _EDIT_TOOLS:
            for path in _edited_paths(args):
                if path not in files:
                    files.append(path)
        command = _command_text(args)
        if command and _TEST_COMMAND.search(command):
            last_test = (command, outputs.get(call.get("call_id")))

    shown = files[:_MAX_FILES_LISTED]
    more = f" (+{len(files) - len(shown)} more)" if len(files) > len(shown) else ""
    lines = [HEADER, f"Files edited ({len(files)}): {', '.join(shown) if shown else 'none'}{more}"]
    if last_test is None:
        lines.append("Last test command: none found in this turn; the worker ran no tests.")
    else:
        command, raw = last_test
        text = _output_text(raw).rstrip()
        lines.append(f"Last test command: {command}")
        if not text:
            lines.append("Its output: (no output captured)")
        else:
            lines.append(f"Its output (last {_OUTPUT_TAIL_CHARS} chars):")
            lines.append(text[-_OUTPUT_TAIL_CHARS:])
    return "\n".join(lines)


async def attach_worker_evidence(
    payload: dict[str, Any], *, server_client: httpx.AsyncClient | None, child_id: str | None
) -> dict[str, Any]:
    """
    Return a copy of a finished sub-agent inbox payload with evidence appended.

    Any failure (no client, unreadable history, no tool calls) returns the
    payload unchanged: evidence helps a review but must never block a result.

    :param payload: The inbox payload, e.g. ``{"type": "sub_agent", "status": "completed", ...}``.
    :param server_client: HTTP client pointed at the Omnigent server.
    :param child_id: The worker session id.
    :returns: The payload, with the evidence block after its ``output`` when found.
    """
    if (
        payload.get("type") != "sub_agent"
        or payload.get("status") not in _EVIDENCE_STATUSES
        or server_client is None
        or not child_id
    ):
        return payload
    try:
        resp = await server_client.get(
            f"/v1/sessions/{child_id}/items",
            params={"limit": _ITEMS_LIMIT, "order": "desc"},
            timeout=15.0,
        )
        data = resp.json().get("data", []) if resp.status_code == 200 else []
    except (httpx.HTTPError, ValueError, AttributeError):
        return payload
    if not isinstance(data, list):
        return payload
    evidence = extract_evidence([i for i in reversed(data) if isinstance(i, dict)])
    if evidence is None:
        return payload
    output = payload.get("output")
    base = output if isinstance(output, str) else ""
    return {**payload, "output": f"{base}\n\n{evidence}" if base.strip() else evidence}
