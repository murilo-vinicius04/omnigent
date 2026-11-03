"""Runner side of the resource tools: call the server's resource board."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx


async def _call(
    method: str,
    path: str,
    body: dict[str, Any] | None,
    client: httpx.AsyncClient | None,
    label: str,
) -> str:
    if client is None:
        return json.dumps({"error": f"{label} requires server access"})
    try:
        resp = await client.request(method, path, json=body, timeout=30.0)
    except httpx.HTTPError as exc:
        return json.dumps({"error": f"{label} failed: {exc}"})
    if resp.status_code >= 400:
        return json.dumps(
            {"error": f"{label} returned {resp.status_code}", "detail": resp.text[:200]}
        )
    return json.dumps(resp.json())


async def acquire_via_rest(
    args: dict[str, Any], conversation_id: str | None, client: httpx.AsyncClient | None
) -> str:
    """Claim, renew or queue for a resource on behalf of this session."""
    name = str(args.get("resource") or "").strip()
    if not name or conversation_id is None:
        return json.dumps({"error": "sys_resource_acquire requires a 'resource' name"})
    body = {
        "session_id": conversation_id,
        "purpose": str(args.get("purpose") or ""),
        "ttl_minutes": int(args.get("ttl_minutes") or 30),
    }
    return await _call(
        "POST",
        f"/v1/resources/{quote(name, safe='')}/acquire",
        body,
        client,
        "sys_resource_acquire",
    )


async def release_via_rest(
    args: dict[str, Any], conversation_id: str | None, client: httpx.AsyncClient | None
) -> str:
    """Release a resource this session holds, or leave its queue."""
    name = str(args.get("resource") or "").strip()
    if not name or conversation_id is None:
        return json.dumps({"error": "sys_resource_release requires a 'resource' name"})
    body: dict[str, Any] = {"session_id": conversation_id}
    if args.get("yield_to_next") is True:
        body["yield_to_next"] = True
    return await _call(
        "POST",
        f"/v1/resources/{quote(name, safe='')}/release",
        body,
        client,
        "sys_resource_release",
    )


async def status_via_rest(args: dict[str, Any], client: httpx.AsyncClient | None) -> str:
    """Show one resource or all of them."""
    name = str(args.get("resource") or "").strip()
    path = f"/v1/resources/{quote(name, safe='')}" if name else "/v1/resources"
    return await _call("GET", path, None, client, "sys_resource_status")
