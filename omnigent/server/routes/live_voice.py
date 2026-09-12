"""Live voice routes: the WebRTC handshake broker and a standalone test page.

- ``POST /v1/live/offer`` — forwards a browser SDP offer to OpenAI with the
  server's key attached and returns the answer. The key never reaches the
  browser.
- ``GET /v1/live/test`` — a self-contained probe page (stage one: no backend
  model, just conversation) for judging latency and whether the thing is
  pleasant to talk to.

The page is served from here rather than from ``static/web-ui`` because a
``vite build --emptyOutDir`` wipes that directory; an experiment that
disappears on the next frontend build is worse than useless.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Final, Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from omnigent.server.auth import AuthProvider
from omnigent.server.discussion import DiscussionRegistry, registry, voice_briefing
from omnigent.server.live_voice import (
    DEFAULT_MODEL,
    DEFAULT_VOICE,
    MAX_SESSION_S,
    NARRATOR_INSTRUCTIONS,
    USD_PER_MINUTE,
    LiveVoiceUnavailable,
    available,
    frame_for_reading,
    open_session,
    reader_model,
)
from omnigent.server.routes.live_voice_page import render_test_page

_logger = logging.getLogger(__name__)

#: Stage-one prompt. No tools and no backend: the point is to judge the
#: voice itself, so it is told to behave like a colleague, not an
#: assistant reading documentation.
DEFAULT_INSTRUCTIONS: Final[str] = (
    "You are a terse, friendly engineering colleague talking over voice. "
    "Keep answers to a couple of sentences unless asked to go deeper. "
    "Never read code or tables aloud. If you are unsure, say so plainly. "
    "Expect the other person to interrupt you; stop immediately when they do."
)


class LiveOfferRequest(BaseModel):
    """A browser's SDP offer plus optional session overrides."""

    sdp: str = Field(min_length=1)
    instructions: str | None = None
    model: str | None = None
    voice: str | None = None
    #: ``"narrate"`` reads *text* aloud and stops; ``"converse"`` talks back.
    mode: Literal["narrate", "converse"] = "converse"
    #: The finished prose to read. Only meaningful when narrating.
    text: str | None = None
    #: Conversation this belongs to. When conversing, the session is briefed
    #: from that session's companion ledger, which is the only way the live
    #: model learns what Claude has been doing.
    session_id: str | None = None


class LiveOfferResponse(BaseModel):
    """The SDP answer and the id of the session it belongs to."""

    session_id: str
    sdp: str
    #: What the browser must push over the data channel to start narration.
    #: Composed here so the read-aloud framing stays next to the rest of the
    #: summary logic rather than being reinvented in the client.
    speak: str | None = None


def create_live_voice_router(
    *,
    auth_provider: AuthProvider | None = None,
    registry_provider: Callable[[], DiscussionRegistry] | None = None,
) -> APIRouter:
    """Build the router carrying the live voice routes.

    :param auth_provider: Optional provider used to authenticate callers.
        ``None`` preserves single-user/dev behavior (open).
    :param registry_provider: Companion registry factory override for tests.
        Defaults to the process-wide registry.
    :returns: An :class:`APIRouter` carrying the handshake and test page.
    """
    router = APIRouter()
    companions = registry_provider or registry

    def _require_user(request: Request) -> None:
        if auth_provider is not None and auth_provider.get_user_id(request) is None:
            raise HTTPException(status_code=401, detail="authentication required")

    @router.post("/live/offer", response_model=LiveOfferResponse)
    async def live_offer(request: Request, body: LiveOfferRequest) -> LiveOfferResponse:
        """Broker one WebRTC handshake with OpenAI's live endpoint."""
        _require_user(request)
        narrating = body.mode == "narrate"
        if narrating and not (body.text or "").strip():
            raise HTTPException(status_code=400, detail="narrate mode requires text to read")
        # Reading needs responses delegation: it is the only mode that accepts
        # text pushed in from outside. Conversing leaves the live model to
        # answer natively, which costs no backend tokens at all.
        backend = reader_model() if narrating else None
        if body.instructions:
            instructions = body.instructions
        elif narrating:
            instructions = NARRATOR_INSTRUCTIONS
        else:
            # Conversing: the live model answers natively, so everything it
            # knows about the work has to be in its prompt. Brief it from the
            # companion's ledger rather than leaving it to invent context.
            companion = companions().peek(body.session_id) if body.session_id else None
            instructions = voice_briefing(companion)
        try:
            session_id, answer = await open_session(
                sdp_offer=body.sdp,
                instructions=instructions,
                model=body.model,
                voice=body.voice,
                backend=backend,
            )
        except LiveVoiceUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            # Surface OpenAI's own message: "no credits remaining" and an
            # unknown voice name are both things the reader must see.
            detail = exc.response.text[:500]
            _logger.warning("live handshake rejected: %s %s", exc.response.status_code, detail)
            raise HTTPException(status_code=502, detail=detail) from exc
        return LiveOfferResponse(
            session_id=session_id,
            sdp=answer,
            speak=frame_for_reading(body.text or "") if narrating else None,
        )

    @router.get("/live/test", response_class=HTMLResponse)
    async def live_test(request: Request) -> HTMLResponse:
        """Serve the standalone live-voice probe page."""
        _require_user(request)
        return HTMLResponse(
            render_test_page(
                model=DEFAULT_MODEL,
                voice=DEFAULT_VOICE,
                usd_per_minute=USD_PER_MINUTE,
                max_session_s=MAX_SESSION_S,
                configured=available(),
            )
        )

    return router
