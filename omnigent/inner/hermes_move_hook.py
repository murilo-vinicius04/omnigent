"""Hermes ``pre_tool_call`` shell hook: the supervisor's next-move check.

Hermes runs it before every tool call with ``{tool_name, tool_input, extra:
{turn_id, ...}}`` on stdin. Most calls only bump a per-turn counter and exit
before importing Omnigent; every ``CHECK_EVERY``-th call (up to ``MAX_NUDGES``
redirects a turn) asks the supervisor. It prints ``{"decision": "block",
"reason": ...}`` to skip the call, or ``{}`` to let it run; any failure lets it run.

Environment variables (set by the wrapper shell script, as for the policy hook):
    _OMNIGENT_SERVER_URL  : Base URL of the Omnigent server.
    _OMNIGENT_SESSION_ID  : The worker's Omnigent session id.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

CHECK_EVERY = 8
MAX_NUDGES = 2


def _state_path(session_id: str) -> Path:
    return (
        Path(tempfile.gettempdir())
        / f"omnigent-{os.getuid()}"
        / "hermes-moves"
        / f"{session_id}.json"
    )


def _load(path: Path, turn: str) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict) or state.get("turn") != turn:
        return {"turn": turn, "calls": 0, "nudges": 0}
    return state


def _save(path: Path, state: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state))
    except OSError:
        pass


async def _check(
    server_url: str, session_id: str, tool_name: str, args: dict[str, Any]
) -> dict[str, str]:
    import httpx

    from omnigent.native_policy_hook import policy_hook_request_headers
    from omnigent.supervisor_moves import check_next_move

    async with httpx.AsyncClient(
        base_url=server_url, headers=policy_hook_request_headers(), timeout=15.0
    ) as client:
        return await check_next_move(client, session_id, tool_name, args)


def decide(payload: dict[str, Any], server_url: str, session_id: str) -> dict[str, str]:
    """Count the call and, when one is due, run the check.

    :param payload: Hermes' hook payload.
    :param server_url: The Omnigent server.
    :param session_id: The worker's Omnigent session id.
    :returns: The object to print.
    """
    tool_name = str(payload.get("tool_name") or "")
    if (
        not server_url
        or not session_id
        or tool_name.startswith(("mcp_omnigent_", "mcp__omnigent__"))
    ):
        return {}
    extra = payload.get("extra") if isinstance(payload.get("extra"), dict) else {}
    path = _state_path(session_id)
    state = _load(path, str(extra.get("turn_id") or ""))
    state["calls"] += 1
    _save(path, state)
    if state["calls"] % CHECK_EVERY or state["nudges"] >= MAX_NUDGES:
        return {}
    args = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    try:
        result = asyncio.run(_check(server_url, session_id, tool_name, args))
    except Exception:  # noqa: BLE001 -- a broken check must never block the worker
        return {}
    if result:
        state["nudges"] += 1
        _save(path, state)
    return result


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError, ValueError):
        payload = {}
    result = decide(
        payload if isinstance(payload, dict) else {},
        os.environ.get("_OMNIGENT_SERVER_URL", ""),
        os.environ.get("_OMNIGENT_SESSION_ID", ""),
    )
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
