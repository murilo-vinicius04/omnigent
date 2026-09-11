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


def create_discussion_router(
    *,
    auth_provider: AuthProvider | None = None,
    registry_provider: Callable[[], DiscussionRegistry] | None = None,
) -> APIRouter:
    """Build the router carrying the companion routes.

    :param auth_provider: Optional provider used to authenticate callers.
        ``None`` preserves single-user/dev behavior (open).
    :param registry_provider: Registry factory override for tests.
        Defaults to the process-wide :func:`registry`.
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
