"""REST routes for the hub (``/v1/hub``).

The hub is a regular conversation, one per user: its messages travel the
normal session routes (the events route hands them to the hub). This router
only finds or makes that conversation and lists the sessions it reaches.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from omnigent.server import hub as hub_module
from omnigent.server import hub_chat
from omnigent.server.auth import AuthProvider
from omnigent.server.discussion import DiscussionRegistry, registry


def create_hub_router(
    *,
    auth_provider: AuthProvider | None = None,
    conversation_store: Any = None,
    artifact_store: Any = None,
    permission_store: Any = None,
    registry_provider: Callable[[], DiscussionRegistry] | None = None,
) -> APIRouter:
    """Build the hub's router.

    :param auth_provider: Identifies the caller; ``None`` is single-user.
    :param conversation_store: The sessions the hub reads and sends to.
    :param artifact_store: Where the hub agent's bundle is kept.
    :param permission_store: Grants the user ownership of their hub.
    :param registry_provider: Companion registry override for tests.
    :returns: An :class:`APIRouter` for ``/hub`` under the ``/v1`` prefix.
    """
    router = APIRouter()
    companions = registry_provider or registry

    async def _bound(request: Request) -> tuple[hub_module.Hub, str]:
        user_id = auth_provider.get_user_id(request) if auth_provider is not None else None
        if auth_provider is not None and user_id is None:
            raise HTTPException(status_code=401, detail="authentication required")
        if conversation_store is None or artifact_store is None:
            raise HTTPException(status_code=503, detail="hub unavailable")
        session_id = await asyncio.to_thread(
            hub_chat.ensure_hub_conversation,
            user_id,
            conversation_store=conversation_store,
            artifact_store=artifact_store,
            permission_store=permission_store,
        )
        hub = hub_module.hub_for(user_id)
        hub_chat.attach(hub, conversation_store, session_id)
        hub_chat.bind_request(
            hub,
            conversation_store=conversation_store,
            user_id=user_id,
            app=request.app,
            headers=request.headers,
            companions=companions,
        )
        return hub, session_id

    @router.get("/hub")
    async def get_hub(request: Request) -> dict[str, Any]:
        """The caller's hub conversation, made on first use, and its sessions."""
        hub, session_id = await _bound(request)
        return {
            "session_id": session_id,
            "sessions": [s.as_dict() for s in await hub.sessions()],
            "model": hub_module.hub_model(),
        }

    @router.get("/hub/sessions")
    async def hub_sessions(request: Request) -> dict[str, Any]:
        """The sessions the hub can reach, with their live status."""
        hub, _ = await _bound(request)
        return {"sessions": [s.as_dict() for s in await hub.sessions()]}

    return router
