"""REST routes for the hub chat (``/v1/hub``).

The hub is one chat per user with no name: the page reads its log, posts
what they type, and long-polls for what the hub says back. Sending into a
session goes through the app's own events route with the caller's
credentials, so the hub needs no access of its own.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from omnigent.server import hub as hub_module
from omnigent.server.auth import AuthProvider
from omnigent.server.discussion import DiscussionRegistry, registry

#: Longest a long-poll may hold the request.
MAX_WAIT_S = 25.0

#: Entries the first read returns.
FIRST_READ = 200

#: Request headers a send carries on, so it acts as the caller.
_FORWARDED = ("cookie", "authorization", "x-forwarded-email", "origin")


class HubMessageIn(BaseModel):
    """What they typed into the hub."""

    text: str = Field(min_length=1, max_length=20_000)
    via: str = "typed"


def create_hub_router(
    *,
    auth_provider: AuthProvider | None = None,
    conversation_store: Any = None,
    registry_provider: Callable[[], DiscussionRegistry] | None = None,
) -> APIRouter:
    """Build the hub chat's router.

    :param auth_provider: Identifies the caller; ``None`` is single-user.
    :param conversation_store: The sessions the hub reads and sends to.
    :param registry_provider: Companion registry override for tests.
    :returns: An :class:`APIRouter` for ``/hub`` under the ``/v1`` prefix.
    """
    router = APIRouter()
    companions = registry_provider or registry

    def _bound(request: Request) -> hub_module.Hub:
        user_id = auth_provider.get_user_id(request) if auth_provider is not None else None
        if auth_provider is not None and user_id is None:
            raise HTTPException(status_code=401, detail="authentication required")
        if conversation_store is None:
            raise HTTPException(status_code=503, detail="hub unavailable")
        hub = hub_module.hub_for(user_id)
        hub.bind(
            hub_module.store_backend(
                conversation_store,
                user_id=user_id,
                app=request.app,
                headers={k: v for k, v in request.headers.items() if k in _FORWARDED},
                companions=companions,
            )
        )
        return hub

    @router.get("/hub")
    async def get_hub(request: Request) -> dict[str, Any]:
        """The hub chat so far, and the sessions it can reach."""
        hub = _bound(request)
        sessions = await hub.sessions()
        return {
            "entries": [e.as_dict() for e in hub.state.entries[-FIRST_READ:]],
            "last_id": hub.state.last_id,
            "sessions": [s.as_dict() for s in sessions],
            "model": hub_module.hub_model(),
        }

    @router.get("/hub/entries")
    async def hub_entries(request: Request, after: int = 0, wait: float = 0.0) -> dict[str, Any]:
        """Entries after *after*, holding up to *wait* seconds for a new one."""
        hub = _bound(request)
        entries = await hub.state.wait_after(after, max(0.0, min(wait, MAX_WAIT_S)))
        return {"entries": [e.as_dict() for e in entries], "last_id": hub.state.last_id}

    @router.get("/hub/sessions")
    async def hub_sessions(request: Request) -> dict[str, Any]:
        """The sessions the hub can reach, with their live status."""
        hub = _bound(request)
        return {"sessions": [s.as_dict() for s in await hub.sessions()]}

    @router.post("/hub/messages", status_code=202)
    async def post_message(request: Request, body: HubMessageIn) -> dict[str, Any]:
        """Record what they typed; the hub's answer arrives as later entries."""
        text = body.text.strip()
        if not text:
            raise HTTPException(status_code=422, detail="empty message")
        hub = _bound(request)
        via = "voice" if body.via == "voice" else "typed"
        return hub.submit(text, via=via).as_dict()

    return router
