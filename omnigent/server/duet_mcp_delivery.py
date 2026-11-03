"""Attach recent duet notes to a native agent's existing MCP shell response."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

from starlette.background import BackgroundTasks
from starlette.responses import JSONResponse, Response

from omnigent.server.duet_router import DuetStore, render

_FIELD = "_omnigent_duet_notes"
_MAX_AGE_SECONDS = 30 * 60


async def attach_notes(response: Response, session_id: str, notes: DuetStore) -> Response:
    """Add notes only to a successful JSON object without changing its existing fields."""
    if response.status_code != 200:
        return response
    try:
        envelope: Any = json.loads(bytes(response.body))
        result = envelope["result"]
        if not isinstance(result, dict) or result.get("isError") or result.get("resultType"):
            return response
        content = result["content"]
        if not isinstance(content, list) or len(content) != 1:
            return response
        block = content[0]
        if not isinstance(block, dict) or block.get("type") != "text":
            return response
        payload = json.loads(block["text"])
        if not isinstance(payload, dict) or _FIELD in payload or payload.get("error"):
            return response
    except (KeyError, TypeError, ValueError):
        return response
    batch = await asyncio.to_thread(
        notes.pending, session_id, since=time.time() - _MAX_AGE_SECONDS
    )
    if not batch:
        return response
    payload[_FIELD] = [
        {
            "id": n.id,
            "created_at": datetime.fromtimestamp(n.created_at, tz=timezone.utc).isoformat(),
            "text": render(n),
        }
        for n in batch
    ]
    block["text"] = json.dumps(payload, ensure_ascii=False)
    background = BackgroundTasks()
    if response.background is not None:
        background.add_task(response.background)
    background.add_task(notes.acknowledge, session_id, {n.id for n in batch})
    headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in {"content-length", "content-type"}
    }
    return JSONResponse(
        envelope, status_code=response.status_code, headers=headers, background=background
    )
