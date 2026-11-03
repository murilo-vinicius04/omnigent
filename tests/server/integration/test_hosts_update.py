"""Integration tests for ``POST /v1/hosts/{id}/update`` and the state it reports."""

from __future__ import annotations

import asyncio

import pytest
from asgiref.testing import ApplicationCommunicator
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from omnigent import code_version
from omnigent.host.frames import (
    HostHelloFrame,
    HostUpdateFrame,
    HostUpdateResultFrame,
    decode_host_frame,
    encode_host_frame,
)
from omnigent.server.host_registry import HostRegistry
from omnigent.server.routes.host_tunnel import create_host_tunnel_router
from omnigent.server.routes.hosts import create_hosts_router
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.host_store import HostStore

pytestmark = [pytest.mark.asyncio, pytest.mark.flaky(reruns=2, reruns_delay=1)]

_HOST_ID = "a6d1c0e5b2f94c3d8e7a1b0c9d8e7f60"
_SERVER = code_version.CodeVersion(id="aaaa", label="614f4cd8+35", root="/srv/omnigent")


def _scope(path: str) -> dict[str, object]:
    return {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "ws",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "subprotocols": [],
    }


@pytest.fixture()
def wired(db_uri: str, monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, HostRegistry]:
    monkeypatch.setattr(code_version, "loaded", lambda: _SERVER)
    monkeypatch.setattr(code_version, "on_disk", lambda *_a, **_k: _SERVER)
    registry = HostRegistry()
    host_store = HostStore(db_uri)
    app = FastAPI()
    app.include_router(create_host_tunnel_router(registry, host_store), prefix="/v1")
    app.include_router(
        create_hosts_router(registry, host_store, SqlAlchemyConversationStore(db_uri)),
        prefix="/v1",
    )
    return app, registry


async def _connect(app: FastAPI, registry: HostRegistry) -> ApplicationCommunicator:
    comm = ApplicationCommunicator(app, _scope(f"/v1/hosts/{_HOST_ID}/tunnel"))
    await comm.send_input({"type": "websocket.connect"})
    assert (await comm.receive_output(timeout=1.0))["type"] == "websocket.accept"
    hello = HostHelloFrame(
        version="0.1.0-test",
        frame_protocol_version=1,
        name="lab-pc",
        code_id="zzzz",
        code_label="92692e5e",
        code_root="/home/u/omnigent",
    )
    await comm.send_input({"type": "websocket.receive", "text": encode_host_frame(hello)})
    while registry.get(_HOST_ID) is None:
        await asyncio.sleep(0.01)
    return comm


async def _next_update_frame(comm: ApplicationCommunicator) -> HostUpdateFrame:
    while True:
        out = await comm.receive_output(timeout=2.0)
        text = out.get("text")
        if out.get("type") == "websocket.send" and isinstance(text, str):
            frame = decode_host_frame(text)
            if isinstance(frame, HostUpdateFrame):
                return frame


async def test_update_reaches_the_host_and_its_outcome_is_reported(
    wired: tuple[FastAPI, HostRegistry],
) -> None:
    app, registry = wired
    comm = await _connect(app, registry)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = next(
            h for h in (await client.get("/v1/hosts")).json()["hosts"] if h["host_id"] == _HOST_ID
        )
        assert listed["update"]["state"] == "outdated"
        assert listed["code"]["id"] == "zzzz"

        resp = await client.post(f"/v1/hosts/{_HOST_ID}/update", json={"force": True})
        assert resp.status_code == 202, resp.text
        frame = await _next_update_frame(comm)
        assert frame.request_id == resp.json()["request_id"]
        assert frame.bundle_path == "/v1/code/bundle" and frame.force is True

        reply = HostUpdateResultFrame(
            request_id=frame.request_id, status="ok", message="updated 41 files", restarting=True
        )
        await comm.send_input({"type": "websocket.receive", "text": encode_host_frame(reply)})
        for _ in range(100):
            last = registry.update_result(_HOST_ID)
            if last:
                break
            await asyncio.sleep(0.02)
        assert last is not None and last["status"] == "ok" and last["restarting"] is True

        again = next(
            h for h in (await client.get("/v1/hosts")).json()["hosts"] if h["host_id"] == _HOST_ID
        )
        assert again["update"]["last"]["message"] == "updated 41 files"


async def test_update_needs_a_known_connected_host(
    wired: tuple[FastAPI, HostRegistry], db_uri: str
) -> None:
    app, _registry = wired
    HostStore(db_uri).upsert_on_connect("b7e2d1f6c3a05d4e9f8b2c1d0e9f8a71", "offline-pc", "local")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.post("/v1/hosts/00000000000000000000000000000000/update")
        offline = await client.post("/v1/hosts/b7e2d1f6c3a05d4e9f8b2c1d0e9f8a71/update")

    assert missing.status_code == 404
    assert offline.status_code == 409 and "offline" in offline.json()["detail"]
