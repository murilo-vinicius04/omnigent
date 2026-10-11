"""REST routes for the hub (``/v1/hub``).

The hub is a regular conversation, one per user: its messages travel the
normal session routes (the events route hands them to the hub). This router
finds or makes that conversation and keeps the list of sessions it reaches.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from omnigent.server import hub as hub_module
from omnigent.server import hub_chat
from omnigent.server.auth import AuthProvider
from omnigent.server.discussion import DiscussionRegistry, registry

#: Longest an updates long-poll may hold the request.
MAX_WAIT_S = 25.0

#: Longest a voice waits for the hub to act on what it handed over.
VOICE_TIMEOUT_S = 60.0

#: Said by the voice when the hub took too long.
VOICE_TOO_SLOW = (
    "The hub is still working on that. Tell them it is taking a while and that the "
    "result will show in the hub chat."
)


class HubVoiceIn(BaseModel):
    """What a voice call handed to the hub."""

    text: str = Field(min_length=1, max_length=20_000)
    #: The call so far, oldest first, so the request can refer back to it.
    context: str = Field(default="", max_length=8_000)


class HubModelIn(BaseModel):
    """A model or effort picked for a session in the hub's Sessions panel."""

    session_id: str = Field(min_length=1, max_length=200)
    #: A model id it offers, ``"default"``, or empty to keep the model.
    model: str = Field(default="", max_length=256)
    #: An effort it offers, ``"default"``, or empty to keep the effort.
    effort: str = Field(default="", max_length=32)


class HubMemberIn(BaseModel):
    """A session to put in the hub."""

    session_id: str = Field(min_length=1, max_length=200)


class HubNameIn(BaseModel):
    """What to call a session in the hub."""

    #: Its new name; empty goes back to its title.
    name: str = Field(default="", max_length=200)


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
            "last_id": hub.state.last_id,
        }

    @router.get("/hub/sessions")
    async def hub_sessions(request: Request) -> dict[str, Any]:
        """The sessions the hub can reach, with their live status."""
        hub, _ = await _bound(request)
        return {"sessions": [s.as_dict() for s in await hub.sessions()]}

    async def _member(hub: hub_module.Hub, session_id: str) -> hub_module.HubSession:
        session = next((s for s in await hub.sessions() if s.id == session_id), None)
        if session is None:
            raise HTTPException(status_code=404, detail="not one of the hub's sessions")
        return session

    @router.get("/hub/candidates")
    async def hub_candidates(request: Request) -> dict[str, Any]:
        """Their sessions not in the hub, newest first, to add one."""
        hub, _ = await _bound(request)
        return {"sessions": [c.as_dict() for c in await hub.candidates()]}

    @router.post("/hub/members")
    async def hub_add(request: Request, body: HubMemberIn) -> dict[str, Any]:
        """Put one of their sessions in the hub."""
        hub, _ = await _bound(request)
        entry = await hub.add(body.session_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="not one of your sessions outside the hub")
        return {"text": entry.text}

    @router.delete("/hub/members/{session_id}")
    async def hub_remove(request: Request, session_id: str) -> dict[str, Any]:
        """Take a session out of the hub; the session itself is untouched."""
        hub, _ = await _bound(request)
        entry = await hub.remove(await _member(hub, session_id))
        return {"text": entry.text}

    @router.patch("/hub/members/{session_id}")
    async def hub_rename(request: Request, session_id: str, body: HubNameIn) -> dict[str, Any]:
        """Call a session something else in the hub, by chat and voice too."""
        hub, _ = await _bound(request)
        session = await _member(hub, session_id)
        try:
            entry = await hub.rename(session, body.name)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"text": entry.text}

    @router.post("/hub/model")
    async def hub_model(request: Request, body: HubModelIn) -> dict[str, Any]:
        """Change a session's model or effort, by the same rules as asking for it.

        The change is checked against what that session offers and written
        into the hub chat, so a pick made here reads like one asked for.
        """
        hub, _ = await _bound(request)
        session = await _member(hub, body.session_id)
        entry = await hub.set_model(session, body.model, body.effort, via="panel")
        return {"changed": entry.kind == "model", "text": entry.text}

    @router.post("/hub/voice")
    async def hub_voice(request: Request, body: HubVoiceIn) -> dict[str, Any]:
        """Act on what a voice handed over and return what the voice should say.

        For a voice that cannot run the hub's tools itself (GPT-Live): the
        request and the hub's answer land in the hub conversation as for a
        typed message, and the call stays open.
        """
        hub, _ = await _bound(request)
        # Shielded: a slow answer must still land in the chat after the voice
        # stops waiting for it.
        work = asyncio.ensure_future(hub.ask(body.text, via="voice", context=body.context))
        try:
            said = await asyncio.wait_for(asyncio.shield(work), VOICE_TIMEOUT_S)
        except TimeoutError:
            return {"say": VOICE_TOO_SLOW, "last_id": hub.state.last_id}
        return {
            "say": "\n".join(e.text for e in said) or "Nothing came back from the hub.",
            "last_id": hub.state.last_id,
        }

    @router.get("/hub/updates")
    async def hub_updates(request: Request, after: int = 0, wait: float = 0.0) -> dict[str, Any]:
        """News an open call should tell: a session finished or needs them."""
        hub, _ = await _bound(request)
        entries = await hub.state.wait_after(after, max(0.0, min(wait, MAX_WAIT_S)))
        return {
            "updates": [e.as_dict() for e in entries if e.kind in hub_module.SPOKEN_KINDS],
            "last_id": max([after, *(e.id for e in entries)]),
        }

    return router
