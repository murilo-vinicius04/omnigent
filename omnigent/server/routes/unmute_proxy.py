"""Serve a local Kyutai Unmute voice stack under Omnigent's own origin.

Unmute runs as its own compose stack on ``127.0.0.1:8089`` with its pages
under ``/unmute``. The browser only reaches Omnigent (and the microphone only
works on a secure origin, which here means the forwarded localhost port), so
this relays ``/unmute/*`` -- pages, API calls and the live-audio WebSocket --
to that stack unchanged.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any, Final

import httpx
import websockets
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketException
from starlette import status
from starlette.background import BackgroundTask
from starlette.responses import Response, StreamingResponse
from websockets.typing import Subprotocol

from omnigent.server.auth import AuthProvider
from omnigent.server.unmute_live import unmute_base_url

#: Connection-scoped headers that must not be relayed, plus ``host`` (the
#: upstream sets its own) and ``content-length`` (the body is re-streamed).
_HOP_BY_HOP: Final[frozenset[str]] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)

_METHODS: Final[list[str]] = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

#: Opens the upstream socket: ``(url, subprotocols) -> connection``.
UpstreamConnect = Callable[[str, list[str]], Awaitable[Any]]


async def _default_connect(url: str, subprotocols: list[str]) -> Any:
    # Audio frames exceed the library's 1 MiB default message cap.
    offered = [Subprotocol(p) for p in subprotocols] or None
    return await websockets.connect(url, subprotocols=offered, max_size=None)


def _target(base: str, path: str, query: str) -> str:
    url = f"{base}/unmute" + (f"/{path}" if path else "")
    return f"{url}?{query}" if query else url


def create_unmute_proxy_router(
    *,
    auth_provider: AuthProvider | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    upstream_connect: UpstreamConnect | None = None,
) -> APIRouter:
    """Build the router that relays ``/unmute`` to the local Unmute stack.

    :param auth_provider: Omnigent's auth; a request without a user is refused.
    :param transport: HTTP transport override, for tests.
    :param upstream_connect: WebSocket connect override, for tests.
    :returns: The router, to be included without a prefix.
    """
    router = APIRouter()
    connect = upstream_connect or _default_connect

    @router.api_route("/unmute", methods=_METHODS, include_in_schema=False)
    @router.api_route("/unmute/{path:path}", methods=_METHODS, include_in_schema=False)
    async def unmute_http(request: Request, path: str = "") -> Response:
        if auth_provider is not None and auth_provider.get_user_id(request) is None:
            raise HTTPException(status_code=401, detail="authentication required")
        url = _target(unmute_base_url(), path, request.url.query)
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
        client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(120.0, connect=5.0))
        # GET/HEAD carry no body; streaming an empty one sends a chunked GET.
        body = None if request.method in ("GET", "HEAD") else request.stream()
        upstream_request = client.build_request(request.method, url, headers=headers, content=body)
        try:
            upstream = await client.send(upstream_request, stream=True)
        except httpx.HTTPError:
            await client.aclose()
            return Response(
                "Unmute is not running (expected on " + unmute_base_url() + ").",
                status_code=status.HTTP_502_BAD_GATEWAY,
                media_type="text/plain",
            )

        async def close() -> None:
            await upstream.aclose()
            await client.aclose()

        return StreamingResponse(
            # Raw bytes: any content-encoding is passed through untouched.
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            headers={k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP},
            background=BackgroundTask(close),
        )

    @router.websocket("/unmute/{path:path}")
    async def unmute_ws(websocket: WebSocket, path: str) -> None:
        if auth_provider is not None and auth_provider.get_user_id(websocket) is None:
            raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
        base = unmute_base_url().replace("http://", "ws://", 1).replace("https://", "wss://", 1)
        # Unmute's realtime socket negotiates the "realtime" subprotocol.
        offered = list(websocket.scope.get("subprotocols") or [])
        try:
            upstream = await connect(_target(base, path, websocket.url.query), offered)
        except Exception:  # noqa: BLE001 - any upstream failure ends the client socket
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
            return
        await websocket.accept(subprotocol=getattr(upstream, "subprotocol", None))

        async def client_to_upstream() -> None:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                if message.get("text") is not None:
                    await upstream.send(message["text"])
                elif message.get("bytes") is not None:
                    await upstream.send(message["bytes"])

        async def upstream_to_client() -> None:
            async for message in upstream:
                if isinstance(message, str):
                    await websocket.send_text(message)
                else:
                    await websocket.send_bytes(message)

        tasks = [
            asyncio.create_task(client_to_upstream()),
            asyncio.create_task(upstream_to_client()),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            with contextlib.suppress(Exception):
                await upstream.close()
            with contextlib.suppress(Exception):
                await websocket.close()

    return router
