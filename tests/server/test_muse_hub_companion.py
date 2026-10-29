"""Muse Hub messages pass through Companion before the ACP transport."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from omnigent.server.routes._sessions import orchestration


@pytest.mark.parametrize("forward", [True, False])
async def test_muse_companion_can_restate_or_answer_without_losing_original(monkeypatch, forward):
    original = [
        {"type": "input_text", "text": "corrige isso"},
        {"type": "input_file", "file_id": "attachment"},
    ]
    body = SimpleNamespace(type="message", data={"role": "user", "content": original})
    conv = SimpleNamespace()
    route = AsyncMock(
        return_value=SimpleNamespace(forward=forward, english="Fix this.", answer="Already fixed.")
    )
    send = AsyncMock(return_value="forwarded")
    persist_answer = AsyncMock(return_value="answered")
    monkeypatch.setattr(orchestration, "_is_native_terminal_session", lambda conv: False)
    monkeypatch.setattr(orchestration, "_resolve_harness", lambda conv: "muse-code")
    monkeypatch.setattr(orchestration, "_route_through_companion", route)
    monkeypatch.setattr(orchestration, "_forward_event_to_runner", send)
    monkeypatch.setattr(orchestration, "_persist_companion_answer", persist_answer)

    result = await orchestration._dispatch_session_event_to_runner_impl(
        "muse-session",
        conv,
        body,
        SimpleNamespace(),
        SimpleNamespace(),
        agent_name="Muse Code",
        file_store=None,
        artifact_store=None,
    )

    route.assert_awaited_once()
    assert body.data["content"] == original
    if forward:
        assert result.item_id == "forwarded"
        dispatched = send.await_args.kwargs["forward_content"]
        assert dispatched[0]["text"] == "Fix this."
        assert dispatched[1] == original[1]
        persist_answer.assert_not_awaited()
    else:
        assert result.item_id == "answered"
        send.assert_not_awaited()
        persist_answer.assert_awaited_once()
