"""Private note transport over an existing native session's MCP relay."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from starlette.responses import JSONResponse

from omnigent.server.duet_router import render


async def handle_relay_request(
    rpc_id: Any, session_id: str, arguments: Any, state: Any, conversation: Any
) -> JSONResponse:
    """Handle edit-authorized peek/ack; no arbitrary tool or policy execution."""
    notes = getattr(state, "duet_notes", None)
    wrapper = (conversation.labels or {}).get("omnigent.wrapper") if conversation else None
    result: dict[str, Any] = {"error": "unavailable"}
    if (
        notes is not None
        and isinstance(wrapper, str)
        and "native" in wrapper
        and isinstance(arguments, dict)
    ):
        op = arguments.get("op")
        if op == "peek":
            batch = await asyncio.to_thread(notes.pending, session_id, since=time.time() - 1800)
            result = {
                "notes": [
                    {"id": note.id, "created_at": note.created_at, "text": render(note)}
                    for note in batch
                ]
            }
        elif op == "ack":
            ids = arguments.get("ids")
            if (
                isinstance(ids, list)
                and len(ids) <= 5
                and all(isinstance(item, str) and 0 < len(item) <= 128 for item in ids)
            ):
                await asyncio.to_thread(notes.acknowledge, session_id, set(ids))
                result = {"ok": True}
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {"content": [{"type": "text", "text": json.dumps(result)}]},
        }
    )
