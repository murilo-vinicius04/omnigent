"""The /unmute relay to a local Kyutai Unmute stack."""

from __future__ import annotations

import asyncio

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent.server.routes.unmute_proxy import create_unmute_proxy_router


def _app(**kwargs: object) -> TestClient:
    app = FastAPI()
    app.include_router(create_unmute_proxy_router(**kwargs))  # type: ignore[arg-type]
    return TestClient(app)


class _Body(httpx.AsyncByteStream):
    """A streamed body, like a real upstream; text= responses arrive pre-read."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def __aiter__(self):  # type: ignore[override]
        yield self._data


def test_pages_and_api_calls_reach_unmute_with_path_and_query() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(
            200, stream=_Body(b"unmute page"), headers={"content-type": "text/html"}
        )

    client = _app(transport=httpx.MockTransport(handler))

    root = client.get("/unmute")
    assert root.status_code == 200
    assert root.text == "unmute page"
    client.get("/unmute/api/v1/health?x=1")

    assert seen == [
        "http://127.0.0.1:8089/unmute",
        "http://127.0.0.1:8089/unmute/api/v1/health?x=1",
    ]


def test_a_stopped_stack_says_so_instead_of_hanging() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    response = _app(transport=httpx.MockTransport(handler)).get("/unmute")

    assert response.status_code == 502
    assert "Unmute is not running" in response.text


class _FakeUpstream:
    """Echoes what it receives, the way a relay test needs it to."""

    subprotocol = "realtime"

    def __init__(self) -> None:
        self.sent: list[str | bytes] = []
        self._queue: asyncio.Queue[str | bytes | None] = asyncio.Queue()
        self.closed = False

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)
        await self._queue.put(message)

    async def close(self) -> None:
        self.closed = True
        await self._queue.put(None)

    def __aiter__(self) -> _FakeUpstream:
        return self

    async def __anext__(self) -> str | bytes:
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item


def test_the_realtime_socket_is_relayed_with_its_subprotocol() -> None:
    upstream = _FakeUpstream()
    urls: list[tuple[str, list[str]]] = []

    async def connect(url: str, subprotocols: list[str]) -> _FakeUpstream:
        urls.append((url, subprotocols))
        return upstream

    client = _app(upstream_connect=connect)
    with client.websocket_connect("/unmute/api/v1/realtime", subprotocols=["realtime"]) as ws:
        # Unmute only accepts its own subprotocol; the relay must pass it on.
        assert ws.accepted_subprotocol == "realtime"
        ws.send_text('{"type":"session.update"}')
        assert ws.receive_text() == '{"type":"session.update"}'
        ws.send_bytes(b"\x00\x01")
        assert ws.receive_bytes() == b"\x00\x01"

    assert urls == [("ws://127.0.0.1:8089/unmute/api/v1/realtime", ["realtime"])]
    assert upstream.sent == ['{"type":"session.update"}', b"\x00\x01"]
