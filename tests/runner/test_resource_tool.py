"""Tests for the runner side of the resource tools."""

from __future__ import annotations

import json

import httpx

from omnigent.runner import resource_tool


def _client(seen: list[httpx.Request], reply: dict | None = None, status: int = 200):
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=reply or {"ok": True})

    return httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url="http://server")


async def test_acquire_posts_the_session_purpose_and_ttl() -> None:
    seen: list[httpx.Request] = []
    async with _client(seen, {"result": "granted"}) as client:
        out = await resource_tool.acquire_via_rest(
            {"resource": "Franka Robot", "purpose": "grasp test", "ttl_minutes": 45},
            "sess1",
            client,
        )

    assert json.loads(out) == {"result": "granted"}
    assert seen[0].method == "POST"
    assert seen[0].url.raw_path == b"/v1/resources/Franka%20Robot/acquire"
    assert json.loads(seen[0].content) == {
        "session_id": "sess1",
        "purpose": "grasp test",
        "ttl_minutes": 45,
    }


async def test_release_and_status_use_the_right_routes() -> None:
    seen: list[httpx.Request] = []
    async with _client(seen) as client:
        await resource_tool.release_via_rest({"resource": "lab-gpu"}, "sess1", client)
        await resource_tool.status_via_rest({}, client)
        await resource_tool.status_via_rest({"resource": "lab-gpu"}, client)

    assert [(r.method, r.url.path) for r in seen] == [
        ("POST", "/v1/resources/lab-gpu/release"),
        ("GET", "/v1/resources"),
        ("GET", "/v1/resources/lab-gpu"),
    ]
    assert json.loads(seen[0].content) == {"session_id": "sess1"}


async def test_release_can_yield_without_dropping_the_waiting_place() -> None:
    seen: list[httpx.Request] = []
    async with _client(seen) as client:
        await resource_tool.release_via_rest(
            {"resource": "robot", "yield_to_next": True}, "sess1", client
        )
    assert json.loads(seen[0].content) == {"session_id": "sess1", "yield_to_next": True}


async def test_missing_name_server_errors_and_no_client_come_back_as_readable_errors() -> None:
    seen: list[httpx.Request] = []
    async with _client(seen, status=403) as client:
        denied = json.loads(await resource_tool.acquire_via_rest({"resource": "x"}, "s", client))
        nameless = json.loads(await resource_tool.release_via_rest({}, "s", client))
    offline = json.loads(await resource_tool.status_via_rest({}, None))

    assert "403" in denied["error"] and "requires a 'resource'" in nameless["error"]
    assert "server access" in offline["error"] and len(seen) == 1
