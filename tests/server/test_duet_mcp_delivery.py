"""An existing native runner can receive notes through a normal server MCP result."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from starlette.responses import JSONResponse, Response

from omnigent.claude_native_bridge import start_tool_relay
from omnigent.errors import OmnigentError
from omnigent.runner.proxy_mcp_manager import ProxyMcpManager
from omnigent.server import duet_router
from omnigent.server.duet_mcp_delivery import attach_notes
from omnigent.server.routes.sessions import routes_agent
from scripts import duet_hook_proxy


def _note(target: str, *, created_at: float | None = None) -> duet_router.Note:
    sender = duet_router.Peer("sender", "Source", "RAL", "morys5")
    peer = duet_router.Peer(target, "Recipient", "franka", "hipolito")
    decisions, _ = duet_router.route(
        sender=sender,
        sender_user="morys5",
        topic="Map update",
        text="Important finding",
        urgency="normal",
        peers=[sender, peer],
        history=[],
        to=target,
        now=created_at or time.time(),
    )
    return decisions[0].note


def _shell_response(payload: object) -> Response:
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": json.dumps(payload)}]},
        }
    )


@pytest.mark.asyncio
async def test_non_json_error_and_stale_notes_are_not_consumed(tmp_path: Path) -> None:
    store = duet_router.DuetStore(tmp_path / "notes.json")
    store.add([_note("receiver", created_at=time.time() - 3600)])
    original = _shell_response({"stdout": "ok"})
    assert await attach_notes(original, "receiver", store) is original
    store.add([_note("receiver")])
    for payload in ("plain text", {"error": "failed"}, {"_omnigent_duet_notes": "occupied"}):
        original = _shell_response(payload)
        assert await attach_notes(original, "receiver", store) is original
    assert len(store.pending("receiver")) == 2


@pytest.mark.asyncio
async def test_actual_mcp_proxy_adds_note_without_changing_shell_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = duet_router.DuetStore(tmp_path / "notes.json")
    store.add([_note("receiver")])

    class Conversations:
        def get_conversation(self, session_id: str) -> SimpleNamespace:
            return SimpleNamespace(labels={"omnigent.wrapper": "claude-code-native-ui"})

    async def tool_result(*args: object, **kwargs: object) -> Response:
        return _shell_response({"stdout": "hello", "stderr": "", "exit_code": 0})

    monkeypatch.setattr(routes_agent, "_handle_mcp_tools_call", tool_result)
    app = FastAPI()
    app.state.duet_notes = store
    router = APIRouter()
    routes_agent.register_agent_routes(
        router,
        conversation_store=Conversations(),  # type: ignore[arg-type]
        agent_store=SimpleNamespace(),  # type: ignore[arg-type]
        runner_router=SimpleNamespace(),  # type: ignore[arg-type]
    )
    app.include_router(router, prefix="/v1")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        other = await ProxyMcpManager("receiver", client).call_tool(None, "sys_duet_inbox", {})
        assert "_omnigent_duet_notes" not in json.loads(other)
        assert len(store.pending("receiver")) == 1
        text = await ProxyMcpManager("receiver", client).call_tool(None, "sys_os_shell", {})
    output = json.loads(text)
    assert output["stdout"] == "hello" and output["exit_code"] == 0
    assert output["_omnigent_duet_notes"][0]["id"]
    assert "Important finding" in output["_omnigent_duet_notes"][0]["text"]
    assert store.pending("receiver") == []


@pytest.mark.asyncio
async def test_non_shell_tool_result_carries_notes_mid_turn(tmp_path: Path) -> None:
    store = duet_router.DuetStore(tmp_path / "notes.json")
    store.add([_note("receiver")])

    class Conversations:
        def get_conversation(self, session_id: str) -> SimpleNamespace:
            return SimpleNamespace(labels={"omnigent.wrapper": "codex-native-ui"})

    async def tool_result(*args: object, **kwargs: object) -> Response:
        return _shell_response({"ok": True})

    import omnigent.server.routes.sessions.routes_agent as routes_agent_mod

    orig = routes_agent_mod._handle_mcp_tools_call
    routes_agent_mod._handle_mcp_tools_call = tool_result  # type: ignore[assignment]
    try:
        app = FastAPI()
        app.state.duet_notes = store
        router = APIRouter()
        routes_agent.register_agent_routes(
            router,
            conversation_store=Conversations(),  # type: ignore[arg-type]
            agent_store=SimpleNamespace(),  # type: ignore[arg-type]
            runner_router=SimpleNamespace(),  # type: ignore[arg-type]
        )
        app.include_router(router, prefix="/v1")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            text = await ProxyMcpManager("receiver", client).call_tool(
                None, "sys_resource_acquire", {}
            )
    finally:
        routes_agent_mod._handle_mcp_tools_call = orig  # type: ignore[assignment]
    output = json.loads(text)
    assert output["ok"] is True
    assert "Important finding" in output["_omnigent_duet_notes"][0]["text"]
    assert store.pending("receiver") == []


@pytest.mark.asyncio
async def test_read_only_user_cannot_consume_another_sessions_notes(tmp_path: Path) -> None:
    store = duet_router.DuetStore(tmp_path / "notes.json")
    store.add([_note("receiver")])

    class Conversations:
        def get_conversation(self, session_id: str) -> SimpleNamespace:
            return SimpleNamespace(parent_conversation_id=None)

    class Permissions:
        def is_admin(self, user_id: str) -> bool:
            return False

        def check_access(self, user_id: str, session_id: str, level: int) -> bool:
            return level <= 1

    app = FastAPI()
    app.state.duet_notes = store
    router = APIRouter()
    routes_agent.register_agent_routes(
        router,
        conversation_store=Conversations(),  # type: ignore[arg-type]
        agent_store=SimpleNamespace(),  # type: ignore[arg-type]
        runner_router=SimpleNamespace(),  # type: ignore[arg-type]
        permission_store=Permissions(),  # type: ignore[arg-type]
        auth_provider=SimpleNamespace(get_user_id=lambda request: "alice"),  # type: ignore[arg-type]
    )
    app.include_router(router, prefix="/v1")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for params in (
            {"name": "sys_os_shell", "arguments": {}},
            {
                "name": "sys_duet_inbox",
                "arguments": {
                    "op": "peek",
                    "_omnigent_hook_transport": True,
                },
            },
        ):
            with pytest.raises(OmnigentError):
                await client.post(
                    "/v1/sessions/receiver/mcp",
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params},
                )
    assert len(store.pending("receiver")) == 1


@pytest.mark.asyncio
async def test_internal_hook_transport_peeks_and_acks_only_session_notes(tmp_path: Path) -> None:
    store = duet_router.DuetStore(tmp_path / "notes.json")
    store.add([_note("receiver"), _note("other")])

    class Conversations:
        def get_conversation(self, session_id: str) -> SimpleNamespace:
            return SimpleNamespace(labels={"omnigent.wrapper": "claude-code-native-ui"})

    app = FastAPI()
    app.state.duet_notes = store
    router = APIRouter()
    routes_agent.register_agent_routes(
        router,
        conversation_store=Conversations(),  # type: ignore[arg-type]
        agent_store=SimpleNamespace(),
        runner_router=SimpleNamespace(),  # type: ignore[arg-type]
    )
    app.include_router(router, prefix="/v1")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        manager = ProxyMcpManager("receiver", client)
        result = json.loads(
            await manager.call_tool(
                None,
                "sys_duet_inbox",
                {
                    "op": "peek",
                    "_omnigent_hook_transport": True,
                },
            )
        )
        assert len(result["notes"]) == 1
        assert len(store.pending("receiver")) == 1
        result = json.loads(
            await manager.call_tool(
                None,
                "sys_duet_inbox",
                {
                    "op": "ack",
                    "_omnigent_hook_transport": True,
                    "ids": [result["notes"][0]["id"], store.pending("other")[0].id],
                },
            )
        )
        assert result == {"ok": True}
    assert not store.pending("receiver")
    assert len(store.pending("other")) == 1


@pytest.mark.asyncio
async def test_sidecar_reaches_server_through_real_native_relay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use the actual relay, proxy manager, server route and queue together."""
    store = duet_router.DuetStore(tmp_path / "notes.json")
    store.add([_note("receiver")])

    class Conversations:
        def get_conversation(self, session_id: str) -> SimpleNamespace:
            return SimpleNamespace(labels={"omnigent.wrapper": "claude-code-native-ui"})

    app = FastAPI()
    app.state.duet_notes = store
    router = APIRouter()
    routes_agent.register_agent_routes(
        router,
        conversation_store=Conversations(),  # type: ignore[arg-type]
        agent_store=SimpleNamespace(),
        runner_router=SimpleNamespace(),  # type: ignore[arg-type]
    )
    app.include_router(router, prefix="/v1")

    @app.post("/v1/sessions/receiver/policies/evaluate")
    async def policy() -> dict[str, str]:
        return {"result": "POLICY_ACTION_ALLOW"}

    # Model a relay that predates the new automatic delivery hook.
    async def no_new_relay_delivery(*args: object) -> list[object]:
        return []

    monkeypatch.setattr("omnigent.duet_delivery.pending", no_new_relay_delivery)
    monkeypatch.setattr("omnigent.claude_native_bridge._BRIDGE_ROOT", tmp_path / "claude-native")
    monkeypatch.setattr("omnigent.claude_native_bridge._TRUSTED_PARENT", tmp_path)
    bridge_dir = tmp_path / "claude-native" / "receiver"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:

        async def execute(name: str, args: dict[str, object]) -> dict[str, object]:
            return json.loads(
                await ProxyMcpManager("receiver", client).call_tool(None, name, args)
            )

        relay = start_tool_relay(
            bridge_dir=bridge_dir,
            tools=[],
            tool_executor=execute,
            loop=asyncio.get_running_loop(),
            policy_client=client,
            session_id="receiver",
        )
        env = bridge_dir / "tool_relay.env"
        original = env.read_bytes()
        process = subprocess.Popen(
            [
                sys.executable,
                duet_hook_proxy.__file__,
                "--bridge-dir",
                str(bridge_dir),
            ]
        )
        try:
            for _ in range(100):
                if env.read_bytes() != original:
                    break
                await asyncio.sleep(0.02)
            fields = dict(line.split("=", 1) for line in env.read_text().splitlines())
            url = fields["OMNIGENT_RELAY_URL"].strip("'")
            token = fields["OMNIGENT_RELAY_TOKEN"].strip("'")
            async with httpx.AsyncClient() as hook:
                response = await hook.post(
                    url + "/hook/claude/evaluate-policy",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "hook_event_name": "PostToolUse",
                        "tool_name": "Bash",
                        "tool_input": {},
                        "tool_response": "ok",
                    },
                )
            assert (
                "Important finding" in response.json()["hookSpecificOutput"]["additionalContext"]
            )
            for _ in range(100):
                if not store.pending("receiver"):
                    break
                await asyncio.sleep(0.02)
            assert not store.pending("receiver")
        finally:
            process.terminate()
            await asyncio.to_thread(process.wait, 3)
            relay.close()
