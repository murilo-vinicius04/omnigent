"""Hidden tests for H4: a video in a chat that sat idle.

The chat's runner has gone away (its tunnel is gone, the host that launched it
is still connected), which is what the server sees for a chat nobody has
touched for a while. The person opens a video the agent produced; the viewer
fetches it with ``?download=true``.

Everything below the HTTP route is real except the two ends of the wire:

* the host daemon is simulated at the frame level -- it answers every host
  frame the server sends (runner status, launch, stop, filesystem reads) the
  way ``omnigent host`` does, using the real ``HostProcess`` filesystem
  handler for reads;
* a runner that connects is registered in the real ``TunnelRegistry``; the
  only fake is the transport, which reaches a stub runner app over ASGI
  instead of a WebSocket.

So any fix that brings a runner back for the chat passes, whichever code path
it uses to do so. Serving a truncated, empty or host-read body does not.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from omnigent.errors import OmnigentError
from omnigent.host.connect import HostProcess
from omnigent.host.frames import (
    HostFsRequestFrame,
    HostHelloFrame,
    HostLaunchRunnerFrame,
    HostRunnerStatusFrame,
    HostStopRunnerFrame,
    decode_host_frame,
)
from omnigent.runner.identity import token_bound_runner_id
from omnigent.runner.routing import RunnerRouter
from omnigent.runner.transports.ws_tunnel.frames import HelloFrame
from omnigent.runner.transports.ws_tunnel.registry import TunnelRegistry
from omnigent.runtime import (
    _globals,
    set_runner_client,
    set_runner_direct_attach_resolver,
    set_runner_router,
)
from omnigent.server.host_registry import HostRegistry, RunnerExitReports
from omnigent.server.routes.sessions import create_sessions_router
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore

_HOST_ID = "7f3a2c9d41b84e0fa6c15e2d9b0f4c83"
_IDLE_RUNNER = token_bound_runner_id("the-binding-the-idle-runner-was-launched-with")
_FS_ROUTE = (
    "/v1/sessions/{session_id}/resources/environments/{environment_id}"
    "/filesystem/{relative_path:path}"
)
# Bigger than one host-tunnel filesystem reply may carry (the workspace
# reader caps a read at 10 MiB), as a screen recording or render easily is.
_VIDEO_BYTES = 12 * 1024 * 1024 + 4099


class _Ws:
    """Stands in for a WebSocket; the frames never touch it."""

    async def send_text(self, data: str) -> None:
        del data

    async def receive_text(self) -> str:
        await asyncio.Event().wait()
        return ""


class _Runners(RunnerRouter):
    """The real router; a connected runner is reached over ASGI, not a tunnel."""

    def __init__(self, runner_http: httpx.AsyncClient, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._runner_http = runner_http

    def _client_for_runner(self, runner_id: str) -> httpx.AsyncClient:
        del runner_id
        return self._runner_http


class _HostDaemon:
    """The machine the chat lives on: connected, answering server frames.

    Replies are resolved on the connection's pending futures exactly as the
    server's host-tunnel receive loop does when the real daemon answers.
    """

    def __init__(self, host_registry: HostRegistry, tunnels: TunnelRegistry) -> None:
        self.conn = host_registry.register(
            _HOST_ID,
            _Ws(),
            HostHelloFrame(version="0.1.0-test", frame_protocol_version=1, name="laptop"),
            owner=None,
        )
        self._tunnels = tunnels
        self.launches: list[HostLaunchRunnerFrame] = []
        self._tasks: set[asyncio.Task[None]] = set()
        self._task = asyncio.create_task(self._serve())

    async def close(self) -> None:
        self._task.cancel()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(self._task, *self._tasks, return_exceptions=True)

    async def _serve(self) -> None:
        while True:
            text = await self.conn.outbound_queue.get()
            if text is None:
                return
            try:
                frame = decode_host_frame(text)
            except Exception:  # noqa: BLE001 -- a frame this fake has no answer for
                continue
            await self._answer(frame)

    async def _answer(self, frame: Any) -> None:
        conn = self.conn
        if isinstance(frame, HostRunnerStatusFrame):
            alive = self._tunnels.get(frame.runner_id) is not None
            _settle(
                conn.pending_runner_status,
                frame.request_id,
                {"status": "alive" if alive else "dead"},
            )
        elif isinstance(frame, HostLaunchRunnerFrame):
            self.launches.append(frame)
            runner_id = token_bound_runner_id(frame.binding_token)
            _settle(
                conn.pending_launches,
                frame.request_id,
                {"status": "launched", "runner_id": runner_id, "error": None, "error_code": None},
            )
            task = asyncio.create_task(self._connect_runner(runner_id))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        elif isinstance(frame, HostStopRunnerFrame):
            self._tunnels.deregister(frame.runner_id)
            _settle(conn.pending_stops, frame.request_id, {"status": "stopped", "error": None})
        elif isinstance(frame, HostFsRequestFrame):
            host = SimpleNamespace(_dispatch_fs_op=HostProcess._dispatch_fs_op)
            result = await asyncio.to_thread(HostProcess._handle_fs_request, host, frame)  # type: ignore[arg-type]
            _settle(
                conn.pending_fs_requests,
                frame.request_id,
                {
                    "status": result.status,
                    "payload": result.payload,
                    "error_status": result.error_status,
                    "error_code": result.error_code,
                    "error": result.error,
                },
            )

    async def _connect_runner(self, runner_id: str) -> None:
        # A spawned runner dials back in a moment later, as a real one does.
        await asyncio.sleep(0.05)
        self._tunnels.register(
            runner_id,
            _Ws(),
            HelloFrame(runner_version="0.0.0-test", frame_protocol_version=1),
        )


def _settle(
    pending: dict[str, asyncio.Future[Any]], request_id: str, result: dict[str, Any]
) -> None:
    future = pending.pop(request_id, None)
    if future is not None and not future.done():
        future.set_result(result)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    (root / "renders").mkdir(parents=True)
    return root


@pytest.fixture
def video(workspace: Path) -> bytes:
    header = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
    body = bytes((i * 7 + 3) % 251 for i in range(_VIDEO_BYTES - len(header)))
    payload = header + body
    (workspace / "renders" / "compare_1x.mp4").write_bytes(payload)
    return payload


@pytest.fixture
def runner_app(workspace: Path) -> FastAPI:
    """What the chat's runner serves: the workspace, as an attachment on download."""
    runner = FastAPI()

    @runner.get(_FS_ROUTE)
    async def _serve(
        session_id: str,
        environment_id: str,
        relative_path: str,
        download: bool = False,
    ) -> Any:
        del session_id, environment_id
        target = workspace / relative_path
        if not target.is_file():
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "path_not_found", "message": "no such file"}},
            )
        if not download:
            return JSONResponse({"object": "session.environment.filesystem.file_content"})
        return FileResponse(target, filename=target.name)

    return runner


@contextlib.asynccontextmanager
async def _idle_chat(
    db_uri: str,
    workspace: Path,
    runner_app: FastAPI,
) -> AsyncIterator[tuple[httpx.AsyncClient, _HostDaemon, str]]:
    """A server with one chat whose runner is gone and whose host is connected."""
    prior = (
        _globals._runner_client,
        _globals._runner_router,
        _globals._runner_direct_attach_resolver,
    )
    store = SqlAlchemyConversationStore(db_uri)
    conv = store.create_conversation(
        agent_id="087b7cb7ac30abf4debfaa578d052ec6",
        runner_id=_IDLE_RUNNER,
        host_id=_HOST_ID,
        workspace=str(workspace),
    )
    tunnels = TunnelRegistry()
    host_registry = HostRegistry()
    exit_reports = RunnerExitReports()
    host = _HostDaemon(host_registry, tunnels)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=runner_app), base_url="http://runner"
    ) as runner_http:
        router = _Runners(
            runner_http,
            registry=tunnels,
            conversation_store=store,
            host_registry=host_registry,
        )
        set_runner_client(None)
        set_runner_direct_attach_resolver(None)
        set_runner_router(router)
        app = FastAPI()

        @app.exception_handler(OmnigentError)
        async def _handle(request: Request, exc: OmnigentError) -> JSONResponse:
            del request
            return JSONResponse(
                status_code=exc.http_status,
                content={"error": {"code": exc.code, "message": exc.message}},
            )

        app.state.conversation_store = store
        app.state.tunnel_registry = tunnels
        app.state.runner_router = router
        app.state.host_registry = host_registry
        app.state.runner_exit_reports = exit_reports
        app.state.host_store = None
        app.state.sandbox_config = None
        app.include_router(
            create_sessions_router(
                store,
                SimpleNamespace(get=lambda _agent_id: None),  # type: ignore[arg-type]
                runner_router=router,
                runner_exit_reports=exit_reports,
                host_registry=host_registry,
            ),
            prefix="/v1",
        )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://server"
            ) as client:
                yield client, host, conv.id
        finally:
            await host.close()
            set_runner_client(prior[0])
            set_runner_router(prior[1])
            set_runner_direct_attach_resolver(prior[2])


def _download_url(session_id: str) -> str:
    return (
        f"/v1/sessions/{session_id}/resources/environments/default"
        "/filesystem/renders/compare_1x.mp4?download=true"
    )


async def _fetch(client: httpx.AsyncClient, url: str, *, attempts: int = 20) -> httpx.Response:
    """GET until the server stops answering "not yet" (at most ~5 s).

    A fix may wake the runner inside the request or ask the caller to come
    back; either is fine as long as the file then arrives.
    """
    resp = await client.get(url)
    for _ in range(attempts - 1):
        if resp.status_code not in (502, 503, 504):
            break
        await asyncio.sleep(0.25)
        resp = await client.get(url)
    return resp


@pytest.mark.asyncio
async def test_video_in_an_idle_chat_downloads_whole(
    db_uri: str,
    workspace: Path,
    runner_app: FastAPI,
    video: bytes,
) -> None:
    """The viewer's request for a video in the idle chat gets the complete file."""
    async with _idle_chat(db_uri, workspace, runner_app) as (client, _host, session_id):
        resp = await asyncio.wait_for(_fetch(client, _download_url(session_id)), timeout=30)

    assert resp.status_code == 200, resp.text[:300]
    received = len(resp.content)
    assert received == len(video)
    assert resp.content == video
    assert "attachment" in resp.headers.get("content-disposition", "")


@pytest.mark.asyncio
async def test_after_the_first_video_plays_the_next_requests_need_no_new_runner(
    db_uri: str,
    workspace: Path,
    runner_app: FastAPI,
    video: bytes,
) -> None:
    """Once the chat can serve its files again, reopening the video just works.

    A player asks for the same file again (seeking, replay, reopening the
    tab); those requests are answered straight away and do not start the
    chat's agent over again each time.
    """
    async with _idle_chat(db_uri, workspace, runner_app) as (client, host, session_id):
        first = await asyncio.wait_for(_fetch(client, _download_url(session_id)), timeout=30)
        assert first.status_code == 200, first.text[:300]
        launches_after_first = len(host.launches)

        again = [await client.get(_download_url(session_id)) for _ in range(3)]

    assert launches_after_first >= 1
    assert [r.status_code for r in again] == [200, 200, 200]
    assert all(r.content == video for r in again)
    assert len(host.launches) == launches_after_first
