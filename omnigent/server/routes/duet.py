"""Routes for notes that agents volunteer to other agents (the duet).

An agent posts what it learned to ``/duet/say``; the router in
:mod:`omnigent.server.duet_router` decides which lanes the caller can reach
should hear it. Lanes read their queued notes from ``/duet/inbox/{id}``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from omnigent.entities import Conversation
from omnigent.server import duet_router
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_access, require_user
from omnigent.stores import ConversationStore
from omnigent.stores.permission_store import PermissionStore
from omnigent.stores.project_store import ProjectStore

#: Level an agent needs on its own lane to speak, and on a lane to read its inbox.
_EDIT = 2
_PEER_LIMIT = 300

logger = logging.getLogger(__name__)


class DuetSay(BaseModel):
    """Body of ``POST /duet/say``."""

    from_session_id: str
    topic: str = Field(default="", max_length=200)
    text: str = Field(max_length=6000)
    urgency: str = Field(default="normal", pattern="^(normal|urgent)$")
    to: str = Field(default="", max_length=200)


class DuetAck(BaseModel):
    ids: list[str] = Field(max_length=5)


def _out_of_quota(conv: Conversation) -> bool:
    """Whether the lane's last turn failed on a usage or quota limit."""
    msg = (getattr(conv, "labels", None) or {}).get("omnigent.last_task_error_message", "")
    return any(k in msg.lower() for k in ("usage limit", "weekly limit", "quota"))


def create_duet_router(
    conversation_store: ConversationStore,
    *,
    permission_store: PermissionStore | None = None,
    project_store: ProjectStore | None = None,
    auth_provider: AuthProvider | None = None,
    store: duet_router.DuetStore | None = None,
) -> APIRouter:
    """Build the duet router.

    :param conversation_store: Source of the lanes a caller can reach.
    :param permission_store: Session grants; ``None`` disables the checks.
    :param project_store: Resolves a lane's project name and owner.
    :param auth_provider: Auth provider; ``None`` means single-user.
    :param store: Note store; defaults to the state-directory file.
    :returns: The router, to mount under ``/v1``.
    """
    router = APIRouter()
    notes = store or duet_router.DuetStore()

    def _labels(convs: list[Conversation]) -> dict[str, tuple[str, str | None]]:
        ids = sorted({c.project_id for c in convs if c.project_id})
        return project_store.owners_and_names(ids) if project_store is not None else {}

    def _peer(
        conv: Conversation,
        user_id: str | None,
        labels: dict[str, tuple[str, str | None]],
    ) -> duet_router.Peer:
        project, owner = labels.get(conv.project_id or "", ("", None))
        return duet_router.Peer(
            session_id=conv.id,
            title=conv.title or "",
            project=project,
            owner=owner or (user_id or ""),
            context=conv.workspace or "",
            status=conv.live_status or "",
            task_summary=getattr(conv, "task_summary", None) or "",
            exhausted=_out_of_quota(conv),
        )

    def _reachable(user_id: str | None, *, explicit: bool) -> list[duet_router.Peer]:
        convs: list[Conversation] = []
        after: str | None = None
        seen: set[str] = set()
        while True:
            page = conversation_store.list_conversations(
                limit=_PEER_LIMIT,
                after=after,
                accessible_by=user_id,
                has_agent_id=True,
                kind=None if explicit else "default",
                order="desc",
                sort_by="updated_at",
            )
            convs.extend(page.data)
            if not page.has_more or not page.last_id or page.last_id in seen:
                break
            after = page.last_id
            seen.add(after)
        labels = _labels(convs)
        return [_peer(c, user_id, labels) for c in convs]

    @router.post("/duet/say")
    async def say(request: Request, body: DuetSay) -> dict[str, Any]:
        """Route a volunteered note to the lanes worth telling.

        :returns: Where the note went and how (``queue`` or ``wake``), or why it went nowhere.
        """
        user_id = require_user(request, auth_provider)
        await require_access(
            user_id, body.from_session_id, _EDIT, permission_store, conversation_store
        )
        peers = await asyncio.to_thread(_reachable, user_id, explicit=bool(body.to.strip()))
        sender = next((p for p in peers if p.session_id == body.from_session_id), None)
        if sender is None:
            conv = await asyncio.to_thread(
                conversation_store.get_conversation, body.from_session_id
            )
            sender = (
                _peer(conv, user_id, _labels([conv]))
                if conv is not None
                else duet_router.Peer(body.from_session_id)
            )
        history = await asyncio.to_thread(notes.all)
        decisions, reason = duet_router.route(
            sender=sender,
            sender_user=user_id or "",
            topic=body.topic,
            text=body.text,
            urgency=body.urgency,
            peers=peers,
            history=history,
            to=body.to,
            fallback=True,
        )
        await asyncio.to_thread(notes.add, [d.note for d in decisions])
        if not decisions:
            logger.warning(
                "duet say from %s (%s) went nowhere: to=%r topic=%r reason=%s",
                body.from_session_id,
                user_id,
                body.to,
                body.topic,
                reason,
            )
        candidates = []
        if not decisions:
            words = duet_router._words(f"{body.to} {body.topic} {body.text[:1500]}")
            possible = [
                (len(words & peer.keywords()), peer)
                for peer in peers
                if peer.session_id != sender.session_id
            ]
            possible.sort(key=lambda item: (-item[0], item[1].title, item[1].session_id))
            candidates = [
                {"session_id": peer.session_id, "title": peer.title, "project": peer.project}
                for score, peer in possible[:5]
                if score > 0
            ]
        return {
            "routed": [
                {
                    "note_id": d.note.id,
                    "to_session_id": d.peer.session_id,
                    "to_title": d.peer.title,
                    "to_project": d.peer.project,
                    "to_owner": d.peer.owner,
                    "delivery": d.delivery,
                    "score": d.score,
                    # Always render: the runner uses it for best-effort push
                    # (idle lanes start a turn, busy lanes buffer + inject
                    # mid-turn). Queue vs wake only signals urgency now.
                    "message": duet_router.render(d.note),
                }
                for d in decisions
            ],
            "reason": reason,
            "candidates": candidates,
        }

    @router.get("/duet/inbox/{session_id}")
    async def inbox(request: Request, session_id: str) -> dict[str, Any]:
        """Return and clear a lane's queued notes."""
        user_id = require_user(request, auth_provider)
        await require_access(user_id, session_id, _EDIT, permission_store, conversation_store)
        taken = await asyncio.to_thread(notes.take, session_id)
        return {
            "notes": [
                {
                    "note_id": n.id,
                    "from_session_id": n.from_session,
                    "from_user": n.from_user,
                    "from_title": n.from_title,
                    "from_project": n.from_project,
                    "topic": n.topic,
                    "text": duet_router.render(n),
                    "urgency": n.urgency,
                }
                for n in taken
            ]
        }

    @router.get("/duet/pending/{session_id}")
    async def pending(request: Request, session_id: str) -> dict[str, Any]:
        """Peek at a bounded batch for delivery to the session's runner."""
        user_id = require_user(request, auth_provider)
        await require_access(user_id, session_id, _EDIT, permission_store, conversation_store)
        batch = await asyncio.to_thread(notes.pending, session_id)
        return {
            "notes": [
                {"id": n.id, "created_at": n.created_at, "text": duet_router.render(n)}
                for n in batch
            ]
        }

    @router.post("/duet/pending/{session_id}/ack")
    async def acknowledge(request: Request, session_id: str, body: DuetAck) -> dict[str, bool]:
        """Acknowledge notes only after their content was handed to the runner hook."""
        user_id = require_user(request, auth_provider)
        await require_access(user_id, session_id, _EDIT, permission_store, conversation_store)
        await asyncio.to_thread(notes.acknowledge, session_id, set(body.ids))
        return {"ok": True}

    @router.get("/duet/activity")
    async def activity(request: Request) -> dict[str, Any]:
        """Show notes between sessions the viewer can read, without consuming them."""
        user_id = require_user(request, auth_provider)
        peers = await asyncio.to_thread(_reachable, user_id, explicit=True)
        by_id = {peer.session_id: peer for peer in peers}
        history = await asyncio.to_thread(notes.all)
        visible = [n for n in history if n.from_session in by_id and n.to_session in by_id][-100:]
        shared = [
            {
                "id": p.session_id,
                "title": p.title,
                "project": p.project,
                "owner": p.owner,
                "status": p.status,
                "task_summary": p.task_summary,
            }
            for p in peers
            if user_id is not None and p.owner and p.owner != user_id
        ]
        return {
            "notes": [
                {
                    "id": n.id,
                    "created_at": n.created_at,
                    "from_user": n.from_user,
                    "from_session": n.from_session,
                    "from_title": by_id[n.from_session].title,
                    "to_session": n.to_session,
                    "to_title": by_id[n.to_session].title,
                    "topic": n.topic,
                    "text": n.text,
                    "status": n.status,
                }
                for n in reversed(visible)
            ],
            "shared_sessions": shared[:100],
        }

    return router
