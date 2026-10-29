"""Host inspections keep identical paths on different machines separate."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from omnigent.errors import OmnigentError
from omnigent.host.frames import (
    HostHelloFrame,
    HostInspectFrame,
    decode_host_frame,
    encode_host_frame,
)
from omnigent.host.inspection import inspect
from omnigent.server.host_inspection import request_inspection
from omnigent.server.host_registry import HostRegistry
from omnigent.server.routes.host_credentials import create_host_credentials_router
from omnigent.server.routes.hosts import create_hosts_router
from omnigent.stores.host_store import HostStore

A = "8661c0d6534c4314923985bdaa550a10"
B = "9eef91528c694216bcc7954112cd6856"


async def test_two_host_routing_is_owner_checked_and_never_uses_local_files(db_uri):
    store, registry = HostStore(db_uri), HostRegistry()
    for host_id, name in [(A, "local"), (B, "remote")]:
        store.upsert_on_connect(host_id, name, "owner")
        registry.register(
            host_id,
            SimpleNamespace(),
            HostHelloFrame(version="test", frame_protocol_version=1, name=name),
            owner="owner",
        )

    async def reply(host_id):
        conn = registry.get(host_id)
        raw = await conn.outbound_queue.get()
        frame = decode_host_frame(raw)
        assert isinstance(frame, HostInspectFrame)
        assert frame.payload["workspace"] == "/same/path"
        conn.pending_inspections[frame.request_id].set_result({"machine": host_id})

    replies = [asyncio.create_task(reply(h)) for h in (A, B)]
    results = await asyncio.gather(
        *[
            request_inspection(
                registry, store, "owner", h, {"action": "repos", "workspace": "/same/path"}
            )
            for h in (A, B)
        ]
    )
    await asyncio.gather(*replies)
    assert [r["machine"] for r in results] == [A, B]
    assert all(not registry.get(h).pending_inspections for h in (A, B))
    with pytest.raises(HTTPException, match="not your host"):
        await request_inspection(registry, store, "intruder", B, {"action": "identity"})
    registry.deregister(B)
    with pytest.raises(OmnigentError):
        await request_inspection(registry, store, "owner", B, {"action": "identity"})


async def test_inspection_missing_directory_never_falls_back_to_home(tmp_path):
    result = await inspect(
        {"action": "command", "workspace": str(tmp_path / "missing"), "command": "pwd"}
    )
    assert "error" in result


async def test_remote_reports_and_repositories_are_resolved_on_the_executor(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "result.md").write_text("report")
    result = await inspect(
        {
            "action": "report",
            "workspace": str(tmp_path),
            "reply": "[Report](result.md)",
            "touched": [],
        }
    )
    assert result["links"] == [("Report", "result.md")]
    repos = await inspect(
        {"action": "repos", "workspace": str(tmp_path), "paths": [str(tmp_path / "result.md")]}
    )
    assert repos["repos"][str(tmp_path / "result.md")] == str(tmp_path)
    frame = HostInspectFrame("r", {"action": "identity"})
    assert decode_host_frame(encode_host_frame(frame)) == frame


async def test_enrollment_api_returns_only_host_credential_and_rejects_hijack(db_uri):
    store, registry = HostStore(db_uri), HostRegistry()
    auth = SimpleNamespace(get_user_id=lambda req: req.headers.get("x-user"))
    app = FastAPI()
    app.include_router(
        create_hosts_router(registry, store, SimpleNamespace(), auth_provider=auth), prefix="/v1"
    )
    app.include_router(create_host_credentials_router(store), prefix="/v1")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        made = await client.post(
            "/v1/hosts/enroll", headers={"x-user": "owner"}, json={"host_id": B, "name": "remote"}
        )
        assert made.status_code == 200
        assert made.headers["cache-control"] == "no-store"
        credential = made.json()["token"]
        assert store.resolve_launch_token(B, credential).user_id == "owner"
        secrets = await client.get(
            f"/v1/hosts/{B}/credentials/github", headers={"X-Omnigent-Host-Token": credential}
        )
        assert secrets.status_code == 401
        refused = await client.post(
            "/v1/hosts/enroll",
            headers={"x-user": "intruder"},
            json={"host_id": B, "name": "stolen"},
        )
        assert refused.status_code == 409
        renamed = await client.patch(
            f"/v1/hosts/{B}", headers={"x-user": "owner"}, json={"name": "renamed"}
        )
        assert renamed.status_code == 200
        store.upsert_on_connect(B, "old-name", "owner", managed_token=credential)
        assert store.get_host(B).name == "renamed"
        revoked = await client.delete(f"/v1/hosts/{B}/credential", headers={"x-user": "owner"})
        assert revoked.status_code == 200
        assert store.resolve_launch_token(B, credential) is None


async def test_inspection_timeout_cleans_pending_request(db_uri):
    store, registry = HostStore(db_uri), HostRegistry()
    store.upsert_on_connect(B, "remote", "owner")
    conn = registry.register(
        B,
        SimpleNamespace(),
        HostHelloFrame(version="test", frame_protocol_version=1, name="remote"),
        owner="owner",
    )
    with pytest.raises(TimeoutError):
        await request_inspection(registry, store, "owner", B, {"action": "identity"}, timeout=0.01)
    assert conn.pending_inspections == {}
