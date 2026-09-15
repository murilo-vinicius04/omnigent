"""Evidence a finished worker left in its own tool calls, for the orchestrator's review.

A worker's final message is its own account and can be wrong or useless (a raw
directory listing, a pass it never ran). The orchestrator reviews faster and
cheaper when the result also carries what the worker's tools actually did: the
files it edited, and its last test command with that command's real output.

Edit-tool calls miss files a worker writes through its shell (a ``python3 - <<``
script, ``sed -i``, a redirect), so the evidence also lists ``git status`` for
every repository the worker worked in.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

import httpx

_ITEMS_LIMIT = 500
_OUTPUT_TAIL_CHARS = 1500
_MAX_FILES_LISTED = 30
_MAX_DIRS_CHECKED = 10
_MAX_REPOS = 3
_GIT_TIMEOUT_S = 5.0
_EVIDENCE_STATUSES = frozenset({"completed", "failed"})

_COMMAND_KEYS = ("command", "CommandLine", "cmd", "code")
_CWD_KEYS = ("workdir", "cwd", "Cwd")
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
_CD_COMMAND = re.compile(r"(?:^|&&|;|\n)\s*cd\s+(?P<q>['\"]?)(?P<dir>/[^'\"\s;&|]+)(?P=q)")
# Hermes injects these as user messages when it compacts a worker's context
# (hermes-agent agent/context_compressor.py). They continue the same task, so
# they must not cut the turn and hide the edits and tests made before them.
_CONTINUATION_PREFIXES = ("[STILL IN PROGRESS", "[CONTEXT COMPACTION", "[CONTEXT SUMMARY")

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


def _message_text(item: dict[str, Any]) -> str:
    content = item.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part["text"]
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def _current_turn(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Items after the last real user message: a reused worker keeps earlier turns."""
    for index in range(len(items) - 1, -1, -1):
        item = items[index]
        if (
            item.get("type") == "message"
            and item.get("role") == "user"
            and not _message_text(item).lstrip().startswith(_CONTINUATION_PREFIXES)
        ):
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


def worker_dirs(items: list[dict[str, Any]]) -> list[str]:
    """
    Absolute directories the worker's latest turn worked in, first seen first.

    Taken from ``cd /dir`` in its commands, working-directory arguments, and the
    parents of files it edited with edit tools.

    :param items: The worker session's items, oldest first.
    :returns: Directory paths, e.g. ``["/repo"]``.
    """
    dirs: list[str] = []
    for call in _current_turn(items):
        if call.get("type") != "function_call":
            continue
        args = _arguments(call)
        found = [m.group("dir") for m in _CD_COMMAND.finditer(_command_text(args) or "")]
        for key in _CWD_KEYS:
            value = args.get(key)
            if isinstance(value, str) and value.startswith("/"):
                found.append(value)
        if str(call.get("name") or "") in _EDIT_TOOLS:
            found += [os.path.dirname(p) for p in _edited_paths(args) if p.startswith("/")]
        for directory in found:
            if directory not in dirs:
                dirs.append(directory)
    return dirs


async def _git(*args: str) -> str | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return None
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), _GIT_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return None
    return out.decode(errors="replace") if proc.returncode == 0 else None


async def git_changes(dirs: list[str]) -> list[str]:
    """
    One ``git status`` line per repository among ``dirs``.

    :param dirs: Directories the worker worked in, e.g. from :func:`worker_dirs`.
    :returns: Evidence lines; empty when none of the directories is in a git repository.
    """
    roots: list[str] = []
    for directory in dirs[:_MAX_DIRS_CHECKED]:
        if not os.path.isdir(directory):
            continue
        top = await _git("-C", directory, "rev-parse", "--show-toplevel")
        root = top.strip() if top else ""
        if root and root not in roots:
            roots.append(root)
            if len(roots) >= _MAX_REPOS:
                break
    lines = []
    for root in roots:
        status = await _git("-C", root, "status", "--porcelain=v1", "--untracked-files=normal")
        if status is None:
            continue
        entries = [line.strip() for line in status.splitlines() if line.strip()]
        shown = entries[:_MAX_FILES_LISTED]
        more = f" (+{len(entries) - len(shown)} more)" if len(entries) > len(shown) else ""
        lines.append(
            f"Uncommitted changes in {root} per git status, including any made before this"
            f" worker ran ({len(entries)}): {', '.join(shown) if shown else 'none'}{more}"
        )
    return lines


def extract_evidence(
    items: list[dict[str, Any]], repo_changes: list[str] | None = None
) -> str | None:
    """
    Summarize the worker's latest turn from its chronological history items.

    :param items: The worker session's items, oldest first.
    :param repo_changes: Lines from :func:`git_changes`, listed after the edited files.
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
    lines.extend(repo_changes or [])
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
    items = [i for i in reversed(data) if isinstance(i, dict)]
    evidence = extract_evidence(items, await git_changes(worker_dirs(items)))
    if evidence is None:
        return payload
    output = payload.get("output")
    base = output if isinstance(output, str) else ""
    return {**payload, "output": f"{base}\n\n{evidence}" if base.strip() else evidence}
