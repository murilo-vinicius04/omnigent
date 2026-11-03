"""Routes for the board where agents take turns on shared resources."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from omnigent.server import duet_router
from omnigent.server.auth import AuthProvider
from omnigent.server.resource_board import DEFAULT_TTL_MIN, MAX_TTL_MIN, Event, ResourceBoard
from omnigent.server.routes._auth_helpers import require_access, require_user
from omnigent.stores import ConversationStore
from omnigent.stores.permission_store import PermissionStore

_EDIT = 2


class AcquireIn(BaseModel):
    """Body of ``POST /resources/{name}/acquire``."""

    session_id: str
    purpose: str = Field(default="", max_length=160)
    ttl_minutes: int = Field(default=DEFAULT_TTL_MIN, ge=1, le=MAX_TTL_MIN)


class ReleaseIn(BaseModel):
    """Body of ``POST /resources/{name}/release``."""

    session_id: str | None = None
    force: bool = False
    yield_to_next: bool = False


def create_resources_router(
    conversation_store: ConversationStore,
    *,
    permission_store: PermissionStore | None = None,
    auth_provider: AuthProvider | None = None,
    board: ResourceBoard | None = None,
    notes: duet_router.DuetStore | None = None,
) -> APIRouter:
    """Build the resources router.

    :param conversation_store: Used to name the lane that claims a resource.
    :param permission_store: Session grants; ``None`` disables the checks.
    :param auth_provider: Auth provider; ``None`` means single-user.
    :param board: The board; defaults to the state-directory file.
    :param notes: Duet note store the board's notices go to; share it with the duet router.
    :returns: The router, to mount under ``/v1``.
    """
    router = APIRouter()
    shelf = board or ResourceBoard()
    inbox = notes or duet_router.DuetStore()

    def _tell(events: list[Event]) -> None:
        now = time.time()
        inbox.add(
            [
                duet_router.Note(
                    id=uuid.uuid4().hex[:12],
                    created_at=now,
                    from_session="resource-board",
                    from_user="system",
                    from_title="resource board",
                    from_project="",
                    topic=f"{e.resource}: {e.kind}",
                    text=e.text,
                    urgency="normal",
                    to_session=e.session_id,
                    to_title="",
                )
                for e in events
            ]
        )

    @router.get("/resources")
    async def list_resources(request: Request) -> dict[str, Any]:
        """List every shared resource with its holder and queue."""
        require_user(request, auth_provider)
        views, events = await asyncio.to_thread(shelf.status)
        await asyncio.to_thread(_tell, events)
        return {"resources": views}

    @router.get("/resources/{name}")
    async def get_resource(request: Request, name: str) -> dict[str, Any]:
        """Show one resource."""
        require_user(request, auth_provider)
        views, events = await asyncio.to_thread(shelf.status, name)
        await asyncio.to_thread(_tell, events)
        return views[0]

    @router.post("/resources/{name}/acquire")
    async def acquire(request: Request, name: str, body: AcquireIn) -> dict[str, Any]:
        """Claim a resource for a lane, renew or confirm its claim, or queue it."""
        user_id = require_user(request, auth_provider)
        await require_access(user_id, body.session_id, _EDIT, permission_store, conversation_store)
        conv = await asyncio.to_thread(conversation_store.get_conversation, body.session_id)
        lane = (conv.title if conv is not None else "") or body.session_id[:8]
        try:
            view, events = await asyncio.to_thread(
                lambda: shelf.acquire(
                    name,
                    session_id=body.session_id,
                    user=user_id or "",
                    lane=lane,
                    purpose=body.purpose,
                    ttl_min=body.ttl_minutes,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await asyncio.to_thread(_tell, events)
        return view

    @router.post("/resources/{name}/release")
    async def release(request: Request, name: str, body: ReleaseIn) -> dict[str, Any]:
        """Give a resource up or leave its queue; ``force`` lets a person free it."""
        user_id = require_user(request, auth_provider)
        if body.yield_to_next and (body.force or body.session_id is None):
            raise HTTPException(status_code=422, detail="yield requires the holder's session_id")
        if body.session_id is not None:
            await require_access(
                user_id, body.session_id, _EDIT, permission_store, conversation_store
            )
        elif not body.force:
            raise HTTPException(status_code=422, detail="give a session_id, or force=true")
        view, events = await asyncio.to_thread(
            lambda: shelf.release(
                name,
                session_id=body.session_id,
                force=body.force,
                yield_to_next=body.yield_to_next,
            )
        )
        await asyncio.to_thread(_tell, events)
        return view

    return router
