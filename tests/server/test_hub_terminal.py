"""The hub's shell: one session on the user's machine, shells opened in it."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, Request

from omnigent.server import hub as hub_module
from omnigent.server import hub_terminal


class _Server:
    """The routes the hub's shell calls, faked: one machine, one agent."""

    def __init__(self, *, online: bool = True) -> None:
        self.online = online
        self.sessions: dict[str, dict[str, Any]] = {}
        self.terminals: dict[str, list[dict[str, Any]]] = {}
        self.app = FastAPI()
        app = self.app

        @app.get("/v1/hosts")
        async def hosts() -> dict[str, Any]:
            status = "online" if self.online else "offline"
            return {"hosts": [{"host_id": "h1", "name": "nexus", "status": status}]}

        @app.get("/v1/hosts/{host_id}/filesystem")
        async def root(host_id: str) -> dict[str, Any]:
            return {"data": [{"name": ".bashrc", "path": "/home/me/.bashrc"}]}

        @app.get("/v1/agents")
        async def agents() -> dict[str, Any]:
            return {
                "data": [{"id": "a0", "name": "other"}, {"id": "a1", "name": "claude-native-ui"}]
            }

        @app.post("/v1/sessions", status_code=201)
        async def create(request: Request) -> dict[str, Any]:
            body = await request.json()
            session_id = f"s{len(self.sessions) + 1}"
            self.sessions[session_id] = body
            return {"id": session_id}

        @app.get("/v1/sessions/{session_id}/resources/terminals")
        async def listed(session_id: str) -> dict[str, Any]:
            return {"data": self.terminals.get(session_id, [])}

        @app.post("/v1/sessions/{session_id}/resources/terminals")
        async def made(session_id: str, request: Request) -> dict[str, Any]:
            body = await request.json()
            name, key = body["terminal"], body["session_key"]
            resource = {
                "id": f"terminal_{name}_{key}",
                "metadata": {"terminal_name": name, "session_key": key, "running": True},
            }
            self.terminals.setdefault(session_id, []).append(resource)
            return resource


class _Store:
    def __init__(self) -> None:
        self.archived: set[str] = set()

    def get_conversation(self, session_id: str) -> Any:
        return SimpleNamespace(id=session_id, archived=session_id in self.archived)


@pytest.fixture(autouse=True)
def _hub_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "HUB_DIR", tmp_path)


async def _open(server: _Server, store: _Store, *, new: bool = False) -> dict[str, Any]:
    return await hub_terminal.open_terminal(
        "u1", conversation_store=store, app=server.app, headers={}, new=new
    )


async def test_the_first_shell_makes_a_session_in_the_home_folder():
    server, store = _Server(), _Store()
    opened = await _open(server, store)
    assert opened["session_id"] == "s1"
    assert opened["terminal"]["metadata"]["terminal_name"] == "bash"
    made = server.sessions["s1"]
    assert made["host_id"] == "h1"
    assert made["workspace"] == "/home/me"
    assert made["agent_id"] == "a1"
    assert made["labels"] == {hub_terminal.TERMINAL_LABEL: "1"}


async def test_the_button_returns_the_shell_already_open():
    server, store = _Server(), _Store()
    first = await _open(server, store)
    again = await _open(server, store)
    assert again == first
    assert len(server.sessions) == 1
    assert len(server.terminals["s1"]) == 1


async def test_the_shortcut_opens_another_shell_in_the_same_session():
    server, store = _Server(), _Store()
    first = await _open(server, store)
    second = await _open(server, store, new=True)
    assert second["session_id"] == first["session_id"]
    assert second["terminal"]["id"] != first["terminal"]["id"]
    assert len(server.terminals["s1"]) == 2


async def test_an_archived_session_is_replaced():
    server, store = _Server(), _Store()
    await _open(server, store)
    store.archived.add("s1")
    opened = await _open(server, store)
    assert opened["session_id"] == "s2"


async def test_no_machine_online_says_so():
    with pytest.raises(hub_terminal.TerminalUnavailable, match="online"):
        await _open(_Server(online=False), _Store())
