"""Correlated read-only requests over an owner's host tunnel."""

import asyncio
import secrets
from typing import Any

from omnigent.host.frames import HostInspectFrame, encode_host_frame
from omnigent.server.host_registry import HostRegistry
from omnigent.server.routes._host_launch import host_absent_error, resolve_host_owner
from omnigent.stores.host_store import HostStore


async def request_inspection(
    registry: HostRegistry,
    store: HostStore,
    user_id: str | None,
    host_id: str,
    payload: dict[str, Any],
    *,
    timeout: float = 35,
) -> dict[str, Any]:
    host = await asyncio.to_thread(
        resolve_host_owner, user_id=user_id, host_id=host_id, host_store=store
    )
    conn = registry.get(host.host_id)
    if conn is None:
        raise host_absent_error(host)
    # A co-located daemon may predate inspection frames. Only its loopback
    # connection AND this OS user's persisted identity allow local execution.
    from ipaddress import ip_address

    import yaml

    from omnigent.host.identity import CONFIG_PATH

    peer = getattr(conn.ws, "client", None)
    try:
        local_id = (yaml.safe_load(CONFIG_PATH.read_text()) or {}).get("host", {}).get("host_id")
        colocated = (
            peer is not None and ip_address(peer.host).is_loopback and local_id == host.host_id
        )
    except (OSError, ValueError, AttributeError, yaml.YAMLError):
        colocated = False
    if colocated:
        from omnigent.host.inspection import inspect

        result = await inspect(payload)
        if result.get("error"):
            raise ValueError(result["error"])
        return result
    request_id = secrets.token_hex(16)
    future = asyncio.get_running_loop().create_future()
    conn.pending_inspections[request_id] = future
    try:
        registry.send_text(conn, encode_host_frame(HostInspectFrame(request_id, payload)))
        result = await asyncio.wait_for(future, timeout)
        if result.get("error"):
            raise ValueError(result["error"])
        return result
    finally:
        conn.pending_inspections.pop(request_id, None)
