"""REST routes for the hub (``/v1/hub``).

The hub is a regular conversation, one per user: its messages travel the
normal session routes (the events route hands them to the hub). This router
finds or makes that conversation and manages the projects it runs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from omnigent.server import budget, hub_chat
from omnigent.server import hub as hub_module
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
    """A model or effort picked for a project in the hub's Projects panel."""

    project_id: str = Field(min_length=1, max_length=200)
    #: Which of its chats; empty for the newest, the one the hub sends to.
    session_id: str = Field(default="", max_length=200)
    #: A model id its chat offers, ``"default"``, or empty to keep the model.
    model: str = Field(default="", max_length=256)
    #: An effort its chat offers, ``"default"``, or empty to keep the effort.
    effort: str = Field(default="", max_length=32)


class HubProjectIn(BaseModel):
    """A project to make."""

    name: str = Field(min_length=1, max_length=200)
    #: A chat of theirs outside every project to be its first, if any.
    from_chat: str | None = Field(default=None, max_length=200)


class HubNameIn(BaseModel):
    """A project's new name."""

    name: str = Field(min_length=1, max_length=200)


class HubChatIn(BaseModel):
    """A chat to put in a project."""

    session_id: str = Field(min_length=1, max_length=200)


class HubLaneIn(BaseModel):
    """A new lane: what it works on and its first request."""

    topic: str = Field(default="", max_length=200)
    message: str = Field(min_length=1, max_length=20_000)
    #: A model or provider to run it on, e.g. ``"Sonnet"``; empty lets the hub pick.
    use: str = Field(default="", max_length=100)


class HubCloseLaneIn(BaseModel):
    """How to close a lane."""

    #: Ask it for its handoff note first, and archive it once that is written.
    handoff: bool = True


class HubPlaceIn(BaseModel):
    """Where a message the hub placed beside a busy lane should have gone."""

    right: str = Field(pattern="^(steer|after|new)$")


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
        """The caller's hub conversation, made on first use, and its projects."""
        hub, session_id = await _bound(request)
        return {
            "session_id": session_id,
            "projects": [p.as_dict() for p in await hub.projects()],
            "model": hub_module.hub_model(),
            "last_id": hub.state.last_id,
        }

    @router.get("/hub/projects")
    async def hub_projects(request: Request) -> dict[str, Any]:
        """Their projects, each with its newest chats, their models and live status."""
        hub, _ = await _bound(request)
        projects = await hub.projects(shown=hub_module.CHATS_SHOWN)
        return {"projects": [p.as_dict() for p in projects]}

    @router.get("/hub/budgets")
    async def hub_budgets(request: Request) -> dict[str, Any]:
        """Each provider's budget for today, on its own week."""
        await _bound(request)
        return {
            "providers": [
                b.as_dict() for b in await asyncio.to_thread(budget.today, hub_module.HUB_DIR)
            ]
        }

    async def _project(hub: hub_module.Hub, project_id: str) -> hub_module.HubProject:
        project = next((p for p in await hub.projects() if p.id == project_id), None)
        if project is None:
            raise HTTPException(status_code=404, detail="not one of your projects")
        return project

    @router.post("/hub/projects")
    async def hub_create_project(request: Request, body: HubProjectIn) -> dict[str, Any]:
        """Make a project, empty or with one of their chats as its first."""
        hub, _ = await _bound(request)
        try:
            entry, record = await hub.create_project(body.name, from_chat=body.from_chat)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"text": entry.text, "id": record.id, "name": record.name, "link": entry.link}

    @router.patch("/hub/projects/{project_id}")
    async def hub_rename_project(
        request: Request, project_id: str, body: HubNameIn
    ) -> dict[str, Any]:
        """Give a project another name, in the sidebar too."""
        hub, _ = await _bound(request)
        project = await _project(hub, project_id)
        try:
            entry = await hub.rename_project(project, body.name)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"text": entry.text}

    @router.delete("/hub/projects/{project_id}")
    async def hub_delete_project(request: Request, project_id: str) -> dict[str, Any]:
        """Delete a project; its chats are kept, outside any project."""
        hub, _ = await _bound(request)
        entry = await hub.delete_project(await _project(hub, project_id))
        return {"text": entry.text}

    @router.get("/hub/candidates")
    async def hub_candidates(request: Request) -> dict[str, Any]:
        """Their chats outside every project, newest first."""
        hub, _ = await _bound(request)
        return {"chats": [c.as_dict() for c in await hub.candidates()]}

    @router.post("/hub/projects/{project_id}/chats")
    async def hub_add_chat(request: Request, project_id: str, body: HubChatIn) -> dict[str, Any]:
        """Put one of their chats outside every project into this one."""
        hub, _ = await _bound(request)
        project = await _project(hub, project_id)
        try:
            entry = await hub.add_chat(project, body.session_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"text": entry.text}

    async def _lane(
        hub: hub_module.Hub, session_id: str
    ) -> tuple[hub_module.HubProject, hub_module.HubSession]:
        for project in await hub.projects(shown=hub_module.CHATS_SHOWN):
            for lane in project.lanes:
                if lane.id == session_id:
                    return project, lane
        raise HTTPException(status_code=404, detail="not a lane of your projects")

    @router.post("/hub/projects/{project_id}/lanes")
    async def hub_open_lane(request: Request, project_id: str, body: HubLaneIn) -> dict[str, Any]:
        """Open a new lane in a project with its first request, on the model the hub picks."""
        hub, _ = await _bound(request)
        project = next(
            (p for p in await hub.projects(shown=hub_module.CHATS_SHOWN) if p.id == project_id),
            None,
        )
        if project is None:
            raise HTTPException(status_code=404, detail="not one of your projects")
        entry = await hub.open_lane(project, body.topic, body.message, via="panel", use=body.use)
        return {"opened": entry.kind == "sent", "text": entry.text, "session_id": entry.session_id}

    @router.patch("/hub/lanes/{session_id}")
    async def hub_rename_lane(
        request: Request, session_id: str, body: HubNameIn
    ) -> dict[str, Any]:
        """Give a lane another name."""
        hub, _ = await _bound(request)
        _, lane = await _lane(hub, session_id)
        try:
            entry = await hub.rename_lane(lane, body.name)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"text": entry.text}

    @router.post("/hub/lanes/{session_id}/close")
    async def hub_close_lane(
        request: Request, session_id: str, body: HubCloseLaneIn
    ) -> dict[str, Any]:
        """Close a lane: archived now, or once it has written its handoff note."""
        hub, _ = await _bound(request)
        _, lane = await _lane(hub, session_id)
        try:
            entry = await hub.close_lane(lane, handoff=body.handoff)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"text": entry.text, "closing": entry.kind == "sent"}

    @router.post("/hub/placements/{placement_id}/feedback")
    async def hub_place_feedback(
        request: Request, placement_id: str, body: HubPlaceIn
    ) -> dict[str, Any]:
        """Their word on a placement: kept as a lesson, and fixed where it still can be."""
        hub, _ = await _bound(request)
        try:
            entry = await hub.correct_place(placement_id, body.right, via="button")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="no such placement") from exc
        return {"right": body.right, "text": entry.text if entry is not None else "Noted."}

    @router.post("/hub/model")
    async def hub_model(request: Request, body: HubModelIn) -> dict[str, Any]:
        """Change a project's model or effort, by the same rules as asking for it.

        The change is checked against what the project's chat offers and
        written into the hub chat, so a pick made here reads like one asked for.
        """
        hub, _ = await _bound(request)
        if body.session_id:
            projects = await hub.projects(shown=hub_module.CHATS_SHOWN)
            project = next((p for p in projects if p.id == body.project_id), None)
            if project is None:
                raise HTTPException(status_code=404, detail="not one of your projects")
            chat = next((c for c in project.lanes if c.id == body.session_id), None)
            if chat is None:
                raise HTTPException(status_code=404, detail=f"not a recent chat of {project.name}")
        else:
            project = await _project(hub, body.project_id)
            chat = project.chat
        if chat is None:
            raise HTTPException(status_code=409, detail=f"{project.name} has no chat yet")
        entry = await hub.set_model(chat, body.model, body.effort, via="panel")
        return {"changed": entry.kind == "model", "text": entry.text}

    @router.post("/hub/voice")
    async def hub_voice(request: Request, body: HubVoiceIn) -> dict[str, Any]:
        """Act on what a voice handed over and return what the voice should say.

        For a voice that cannot run the hub's tools itself (GPT-Live, Gemini
        Live): the request and the hub's answer land in the hub conversation as
        for a typed message. ``sent`` says it went to a project, so the call can
        end the way a project chat's call ends once Claude has the request.
        """
        hub, _ = await _bound(request)
        # Shielded: a slow answer must still land in the chat after the voice
        # stops waiting for it.
        work = asyncio.ensure_future(hub.ask(body.text, via="voice", context=body.context))
        try:
            said = await asyncio.wait_for(asyncio.shield(work), VOICE_TIMEOUT_S)
        except TimeoutError:
            return {"say": VOICE_TOO_SLOW, "sent": False, "last_id": hub.state.last_id}
        return {
            "say": "\n".join(e.text for e in said) or "Nothing came back from the hub.",
            "sent": any(e.kind in ("sent", "held") for e in said),
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
