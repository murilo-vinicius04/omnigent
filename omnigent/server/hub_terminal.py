"""The hub's shell: a terminal on the user's machine, opened from the hub.

The hub has no runner, so it cannot hold a terminal itself. It keeps one
session on the machine for that instead, made on first use in the home
folder, and opens its shells there. That session's agent is never prompted,
so a shell costs no model quota.
"""

from __future__ import annotations

import asyncio
import json
import posixpath
import secrets
from typing import Any, Final

import httpx

from omnigent.server import hub as hub_module

#: Marks the session that holds the hub's shells.
TERMINAL_LABEL: Final[str] = "omnigent.hub_terminal"
TERMINAL_TITLE: Final[str] = "Terminal"
#: An agent whose spec declares the shell. Its own CLI starts idle with the runner.
TERMINAL_AGENT: Final[str] = "claude-native-ui"
SHELL: Final[str] = "bash"

_locks: dict[str, asyncio.Lock] = {}


class TerminalUnavailable(RuntimeError):
    """No machine can hold the shell, or the machine refused it."""


def is_hub_terminal(conv: Any) -> bool:
    """Whether a conversation is the session holding a hub's shells."""
    labels = getattr(conv, "labels", None) or {}
    return labels.get(TERMINAL_LABEL) == "1"


def _id_path(user_id: str | None):
    return hub_module.HUB_DIR / hub_module.user_key(user_id) / "terminal.json"


def _known_id(user_id: str | None) -> str | None:
    try:
        raw = json.loads(_id_path(user_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = raw.get("id") if isinstance(raw, dict) else None
    return value if isinstance(value, str) and value else None


async def open_terminal(
    user_id: str | None,
    *,
    conversation_store: Any,
    app: Any,
    headers: dict[str, str],
    new: bool,
) -> dict[str, Any]:
    """Open a shell for the hub, or return the one already open.

    :param user_id: The hub's owner.
    :param conversation_store: Where the shell's session is looked up.
    :param app: The server app, called as the user through its own routes.
    :param headers: The user's credentials to call it with.
    :param new: Open another shell even when one is running.
    :returns: ``{"session_id": ..., "terminal": <terminal resource>}``.
    :raises TerminalUnavailable: When no machine is online or the shell fails.
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://hub", timeout=60
    ) as client:
        async with _locks.setdefault(hub_module.user_key(user_id), asyncio.Lock()):
            session_id = await _session(user_id, client, headers, conversation_store)
        if not new:
            running = await _running_shell(client, headers, session_id)
            if running is not None:
                return {"session_id": session_id, "terminal": running}
        made = await client.post(
            f"/v1/sessions/{session_id}/resources/terminals",
            json={"terminal": SHELL, "session_key": f"hub-{secrets.token_hex(4)}"},
            headers=headers,
        )
    if made.status_code >= 400:
        raise TerminalUnavailable(_error_text(made))
    return {"session_id": session_id, "terminal": made.json()}


async def _running_shell(
    client: httpx.AsyncClient, headers: dict[str, str], session_id: str
) -> dict[str, Any] | None:
    """The newest shell still running in the session; none while its runner sleeps."""
    listed = await client.get(f"/v1/sessions/{session_id}/resources/terminals", headers=headers)
    if listed.status_code != 200:
        return None
    shells = [
        t
        for t in listed.json().get("data") or []
        if (t.get("metadata") or {}).get("terminal_name") == SHELL
        and (t.get("metadata") or {}).get("running")
    ]
    return shells[-1] if shells else None


async def _session(
    user_id: str | None,
    client: httpx.AsyncClient,
    headers: dict[str, str],
    conversation_store: Any,
) -> str:
    """The session holding the shells, made on the first online machine when missing."""
    known = _known_id(user_id)
    if known is not None:
        conv = await asyncio.to_thread(conversation_store.get_conversation, known)
        if conv is not None and not getattr(conv, "archived", False):
            return known
    hosts = await client.get("/v1/hosts", headers=headers)
    hosts.raise_for_status()
    online = [h for h in hosts.json().get("hosts") or [] if h.get("status") == "online"]
    if not online:
        raise TerminalUnavailable("none of your machines is online")
    host_id = str(online[0]["host_id"])
    # The root listing is the home folder's contents.
    root = await client.get(f"/v1/hosts/{host_id}/filesystem", headers=headers)
    entries = root.json().get("data") or [] if root.status_code == 200 else []
    if not entries:
        raise TerminalUnavailable(f"could not read the home folder on {online[0].get('name')}")
    home = posixpath.dirname(str(entries[0]["path"]))
    agents = await client.get("/v1/agents", params={"limit": 100}, headers=headers)
    agents.raise_for_status()
    agent_id = next(
        (str(a["id"]) for a in agents.json().get("data") or [] if a.get("name") == TERMINAL_AGENT),
        None,
    )
    if agent_id is None:
        raise TerminalUnavailable(f"no {TERMINAL_AGENT} agent on this server")
    made = await client.post(
        "/v1/sessions",
        json={
            "agent_id": agent_id,
            "host_id": host_id,
            "workspace": home,
            "title": TERMINAL_TITLE,
            "labels": {TERMINAL_LABEL: "1"},
        },
        headers=headers,
    )
    if made.status_code >= 400:
        raise TerminalUnavailable(_error_text(made))
    session_id = str(made.json()["id"])
    path = _id_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": session_id}), encoding="utf-8")
    return session_id


def _error_text(response: httpx.Response) -> str:
    try:
        error = response.json().get("error") or {}
    except ValueError:
        error = {}
    message = error.get("message") if isinstance(error, dict) else None
    return str(message or f"the server answered {response.status_code}")
