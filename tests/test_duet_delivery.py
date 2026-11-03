"""Native hook delivery keeps notes available until they are acknowledged."""

from __future__ import annotations

import httpx
import pytest

from omnigent import duet_delivery


@pytest.mark.asyncio
async def test_pending_and_ack_use_target_session_and_keep_context() -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.content))
        if request.method == "GET":
            return httpx.Response(
                200, json={"notes": [{"id": "n1", "text": "hello", "created_at": 0}]}
            )
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://test"
    ) as client:
        notes = await duet_delivery.pending(client, "session1")
        output = duet_delivery.append_to_post_tool_hook(
            {"hookSpecificOutput": {"additionalContext": "policy warning"}}, notes
        )
        assert output is not None
        assert "policy warning" in output["hookSpecificOutput"]["additionalContext"]
        assert "hello" in output["hookSpecificOutput"]["additionalContext"]
        await duet_delivery.acknowledge(client, "session1", [n["id"] for n in notes])
    assert [(m, p) for m, p, _ in calls] == [
        ("GET", "/v1/duet/pending/session1"),
        ("POST", "/v1/duet/pending/session1/ack"),
    ]


@pytest.mark.asyncio
async def test_transport_failure_never_prevents_a_tool_result() -> None:
    def fail(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(fail), base_url="http://test"
    ) as client:
        assert await duet_delivery.pending(client, "session1") == []
        await duet_delivery.acknowledge(client, "session1", ["n1"])
