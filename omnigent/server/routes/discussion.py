"""Companion routes: feed it, ask it, and see what it knows.

- ``GET  /v1/discussion/{session_id}`` — the ledger, for the UI panel.
- ``POST /v1/discussion/{session_id}/note`` — record context. Cheap: it
  never reaches the model until the next question.
- ``POST /v1/discussion/{session_id}/ask`` — one question, one answer.
- ``POST /v1/discussion/{session_id}/prewarm`` — pay the cold start now.
- ``POST /v1/discussion/{session_id}/close`` — drop the process. The
  ledger survives, so this is safe to call from a ``pagehide`` beacon.

Every mutating route answers with the whole ledger rather than a bare
acknowledgement, so the caller's context panel can never drift from what
the companion actually knows.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from omnigent.server.auth import AuthProvider
from omnigent.server.discussion import (
    DiscussionRegistry,
    DiscussionUnavailable,
    EntryKind,
    registry,
)
from omnigent.stores.conversation_store import ConversationStore

_logger = logging.getLogger(__name__)


class NoteRequest(BaseModel):
    """Something the companion should know, recorded without asking it."""

    text: str = Field(min_length=1)
    kind: EntryKind = "activity"


class AskRequest(BaseModel):
    """A question for the companion."""

    text: str = Field(min_length=1)
    timeout_s: float | None = Field(default=None, gt=0, le=120)


class AskResponse(BaseModel):
    """The answer, plus the ledger it was produced from."""

    answer: str
    state: dict[str, Any]


class RouteRequest(BaseModel):
    """Something the reader said aloud, for the companion to decide about."""

    text: str = Field(min_length=1)


class RouteResponse(BaseModel):
    """What to do with what they said.

    ``forward`` is the whole point: the reader spoke, and this says whether
    Claude has to see it. When the companion cannot decide, the
    conversation keeps it.
    """

    #: Whether this is work for Claude rather than something to talk about.
    forward: bool
    #: The message as Claude should receive it, when it differs from *text*.
    #: Honours the session's language setting exactly as a typed message does.
    english: str | None = None
    #: What the companion would say back, when it is not forwarding.
    answer: str | None = None


def create_discussion_router(
    *,
    auth_provider: AuthProvider | None = None,
    registry_provider: Callable[[], DiscussionRegistry] | None = None,
    conversation_store: ConversationStore | None = None,
) -> APIRouter:
    """Build the router carrying the companion routes.

    :param auth_provider: Optional provider used to authenticate callers.
        ``None`` preserves single-user/dev behavior (open).
    :param registry_provider: Registry factory override for tests.
        Defaults to the process-wide :func:`registry`.
    :param conversation_store: Store used to resolve a session's language
        when routing what was said aloud. Without it that route forwards
        everything, which is the safe answer rather than a broken one.
    :returns: An :class:`APIRouter` carrying the companion API.
    """
    router = APIRouter()
    companions = registry_provider or registry

    def _require_user(request: Request) -> None:
        if auth_provider is not None and auth_provider.get_user_id(request) is None:
            raise HTTPException(status_code=401, detail="authentication required")

    @router.get("/discussion/{session_id}")
    async def discussion_state(request: Request, session_id: str) -> dict[str, Any]:
        """Return what the companion for this session knows."""
        _require_user(request)
        session = await companions().get(session_id)
        return session.as_dict()

    @router.post("/discussion/{session_id}/note")
    async def discussion_note(
        request: Request, session_id: str, body: NoteRequest
    ) -> dict[str, Any]:
        """Record context without spending a turn on it."""
        _require_user(request)
        session = await companions().get(session_id)
        session.note(body.kind, body.text)
        return session.as_dict()

    @router.post("/discussion/{session_id}/ask", response_model=AskResponse)
    async def discussion_ask(request: Request, session_id: str, body: AskRequest) -> AskResponse:
        """Ask the companion one question and wait for the answer."""
        _require_user(request)
        session = await companions().get(session_id)
        try:
            answer = await session.ask(body.text, timeout_s=body.timeout_s)
        except DiscussionUnavailable as exc:
            # 503, not 500: the companion is optional, and the caller is
            # expected to carry on without it.
            _logger.warning("companion unavailable for %s: %s", session_id, exc)
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return AskResponse(answer=answer, state=session.as_dict())

    @router.post("/discussion/{session_id}/route", response_model=RouteResponse)
    async def discussion_route(
        request: Request, session_id: str, body: RouteRequest
    ) -> RouteResponse:
        """Decide whether something said aloud is work for Claude.

        The spoken counterpart of what already happens when the reader
        presses enter, and deliberately the same code path: the session's
        language setting decides what may happen to their words, and only
        the routing decision is unconditional.

        Never fails the caller. Unlike a typed message, a companion that
        cannot decide keeps what was said: the voice is already answering
        it, and forwarding every failure sent small talk to Claude.
        """
        _require_user(request)
        from omnigent.server.routes._sessions.orchestration import _route_through_companion

        if conversation_store is None:
            return RouteResponse(forward=False)
        try:
            routing = await _route_through_companion(
                session_id,
                [{"type": "input_text", "text": body.text}],
                conversation_store,
                spoken=True,
            )
        except Exception:  # noqa: BLE001 - the voice still has what they said
            _logger.warning("spoken routing failed for %s", session_id, exc_info=True)
            routing = None
        if routing is None:
            # Routing is off, or it could not decide. The conversation keeps it.
            return RouteResponse(forward=False)
        return RouteResponse(
            forward=routing.forward, english=routing.english, answer=routing.answer
        )

    @router.post("/discussion/{session_id}/prewarm")
    async def discussion_prewarm(request: Request, session_id: str) -> dict[str, Any]:
        """Start the process now so the first question is a warm one."""
        _require_user(request)
        session = await companions().get(session_id)
        try:
            await session.prewarm(timeout_s=None)
        except DiscussionUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return session.as_dict()

    @router.post("/discussion/{session_id}/close")
    async def discussion_close(request: Request, session_id: str) -> dict[str, Any]:
        """Stop the process. The ledger survives and replays on the next ask."""
        _require_user(request)
        session = await companions().get(session_id)
        await session.close()
        return session.as_dict()

    return router
