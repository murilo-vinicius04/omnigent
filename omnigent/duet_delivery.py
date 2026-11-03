"""Deliver queued duet notes at a native agent's next tool boundary."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx


async def pending(client: httpx.AsyncClient, session_id: str) -> list[dict[str, Any]]:
    """Peek, leaving notes queued if the hook cannot deliver them."""
    try:
        response = await client.get(f"/v1/duet/pending/{quote(session_id, safe='')}", timeout=1.0)
        response.raise_for_status()
        notes = response.json().get("notes", [])
        if not isinstance(notes, list):
            return []
        return [n for n in notes[:5] if isinstance(n, dict) and n.get("id") and n.get("text")]
    except (httpx.HTTPError, ValueError, AttributeError):
        return []


async def acknowledge(client: httpx.AsyncClient, session_id: str, ids: list[str]) -> None:
    """A failed acknowledgement leaves the notes available for retry."""
    with suppress(httpx.HTTPError):
        await client.post(
            f"/v1/duet/pending/{quote(session_id, safe='')}/ack", json={"ids": ids}, timeout=1.0
        )


def pending_sync(base_url: str, headers: dict[str, str], session_id: str) -> list[dict[str, Any]]:
    """Peek from short-lived hook processes, failing open on network errors."""
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}/v1/duet/pending/{quote(session_id, safe='')}",
            headers=headers,
            timeout=1.0,
        )
        response.raise_for_status()
        notes = response.json().get("notes", [])
        if not isinstance(notes, list):
            return []
        return [n for n in notes[:5] if isinstance(n, dict) and n.get("id") and n.get("text")]
    except (httpx.HTTPError, ValueError, AttributeError):
        return []


def acknowledge_sync(
    base_url: str, headers: dict[str, str], session_id: str, ids: list[str]
) -> None:
    with suppress(httpx.HTTPError):
        httpx.post(
            f"{base_url.rstrip('/')}/v1/duet/pending/{quote(session_id, safe='')}/ack",
            headers=headers,
            json={"ids": ids},
            timeout=1.0,
        )


def append_to_post_tool_hook(
    output: dict[str, Any] | None, notes: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Add context without replacing a policy warning from the same hook."""
    if not notes:
        return output
    blocks = []
    for note in notes:
        try:
            stamp = datetime.fromtimestamp(float(note.get("created_at", 0)), tz=timezone.utc)
            when = stamp.strftime("%Y-%m-%d %H:%M:%S UTC")
        except (ValueError, TypeError, OverflowError, OSError):
            when = "unknown time"
        blocks.append(f"Received note created {when}:\n{note['text']}")
    content = "\n\n".join(blocks)
    result = dict(output or {})
    detail = dict(result.get("hookSpecificOutput") or {})
    old = str(detail.get("additionalContext") or "")
    detail.update(
        hookEventName="PostToolUse",
        additionalContext="\n\n".join(part for part in (old, content) if part),
    )
    result["hookSpecificOutput"] = detail
    return result
