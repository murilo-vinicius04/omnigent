"""GPT-Live voice session support: credentials and the WebRTC handshake.

A live voice session is a full-duplex conversation with OpenAI's
``gpt-live-1``, reached over WebRTC. Unlike the spoken summary (one-shot
text in, an MP3 out) this is a *conversation*, so the browser holds the
peer connection and this module only brokers the handshake.

Why the server is in the path at all: the browser must never hold the
OpenAI key. It produces an SDP offer, posts it here, and this module
forwards it with the key attached and hands back the answer. That is the
whole exchange — afterwards audio flows browser-to-OpenAI directly.

**Cost shape, which drives the design.** Sessions bill on wall-clock, not
on speech: silence costs the same as talking. There is no way to *list*
open sessions, so a session whose page vanished is unreachable; ending
one cleanly means sending ``session.close`` over the data channel, which
answers ``session.closed`` with ``reason: close_requested``. Dropping the
peer connection also ends it. Session lifetime is therefore the client's
responsibility, and :data:`MAX_SESSION_S` is a runaway guard rather than
a UX timeout.
"""

from __future__ import annotations

import logging
import os
import pathlib
from typing import Any, Final

import httpx

_logger = logging.getLogger(__name__)

#: OpenAI's live-session endpoint. Creation is WebRTC-only: the POST body
#: carries the caller's SDP offer and the response carries the answer.
LIVE_SESSIONS_URL: Final[str] = "https://api.openai.com/v1/live/sessions"

#: Default conversational model.
DEFAULT_MODEL: Final[str] = "gpt-live-1"

#: Default voice. Lives under ``session.audio.output.voice``; a top-level
#: ``session.voice`` is rejected by the API.
DEFAULT_VOICE: Final[str] = "marin"

#: Where the API key is read from when ``OPENAI_API_KEY`` is unset. Kept
#: outside the repo so it cannot be committed.
KEY_PATH: Final[pathlib.Path] = pathlib.Path.home() / ".omnigent" / "openai-key"

#: Model that reads a finished summary aloud. It decides nothing: the words
#: are already written, and its whole job is to voice them. Pinned small on
#: purpose -- the voice clock runs while the backend thinks, so a slow reader
#: is billed twice, once in its own tokens and again in dead air. Measured:
#: gpt-4o-mini starts speaking in 1.4s, gpt-5 in 27.8s.
DEFAULT_READER_MODEL: Final[str] = "gpt-4o-mini"


def reader_model() -> str:
    """Return the model used to read finished text aloud."""
    return os.environ.get("OMNIGENT_LIVE_READER_MODEL", "").strip() or DEFAULT_READER_MODEL


#: Session prompt for reading, as opposed to conversing.
#:
#: The last clause is load-bearing. A live session generates an opening turn
#: by itself the moment the connection establishes, before any text arrives,
#: and a narrator with nothing to narrate invents something to say -- in
#: testing it fabricated a whole conversation and spoke it confidently.
NARRATOR_INSTRUCTIONS: Final[str] = (
    "You are a narrator with exactly one job: read aloud the text you are given. "
    "Read it naturally, as if telling the listener what happened. Never greet, "
    "never add commentary, never ask a question, never omit anything. "
    "If you have not been given text to read, say absolutely nothing and produce "
    "empty output. Never invent content. "
    "The very first word you speak must be the first word of the text itself. "
    "Never put anything in front of it: no sigh, exhale, hum or throat-clearing, "
    "and no interjection or reaction such as oh, ah, well, so, okay or right."
)


def frame_for_reading(text: str) -> str:
    """Wrap finished prose in the instruction that makes it read, not reply.

    Handed a bare message the reader answers it, which is how a summary
    became unsolicited advice in testing. Told to read it, it reads it.

    :param text: The finished summary, already written by the rewriter.
    :returns: The text framed as a read-aloud command.
    """
    return (
        "Read the following status update aloud to the listener, in your own "
        "speaking voice, keeping every fact and adding nothing. Do not reply to "
        "it, do not advise, do not continue it. Just say it:\n\n"
        f"{text.strip()}"
    )


#: Billed rate, for the client's running-cost estimate. The usage API
#: needs an org-scoped key, so the browser estimates from elapsed time.
USD_PER_MINUTE: Final[float] = 0.05

#: Runaway guard, not a UX timeout: the reader closes sessions by hand.
#: A session that somehow outlives its page still stops billing here.
MAX_SESSION_S: Final[int] = 30 * 60

#: The handshake is one round trip; a slow one means a broken session.
_HANDSHAKE_TIMEOUT_S: Final[float] = 30.0


class LiveVoiceUnavailable(RuntimeError):
    """No usable OpenAI credential, so live voice cannot be offered."""


def api_key() -> str:
    """Return the OpenAI API key.

    :raises LiveVoiceUnavailable: When no key is configured.
    """
    from_env = os.environ.get("OPENAI_API_KEY", "").strip()
    if from_env:
        return from_env
    try:
        from_file = KEY_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        from_file = ""
    if not from_file:
        raise LiveVoiceUnavailable(f"no OpenAI key: set OPENAI_API_KEY or write one to {KEY_PATH}")
    return from_file


def available() -> bool:
    """Whether a live voice session could be created right now."""
    try:
        api_key()
    except LiveVoiceUnavailable:
        return False
    return True


def session_config(
    *,
    instructions: str,
    model: str | None,
    voice: str | None,
    backend: str | None = None,
) -> dict[str, Any]:
    """Build the ``session`` half of a live-session request.

    Field names are load-bearing and were confirmed against the API:
    ``model`` is required, ``instructions`` is accepted, the voice lives
    at ``audio.output.voice``, and unknown keys are rejected outright.

    :param backend: Model to delegate turn content to. ``None`` leaves the
        session in its default ``client`` delegation, where the live model
        answers natively. Naming one switches it to ``responses``
        delegation, the only mode that accepts text pushed in from here.
    """
    config: dict[str, Any] = {
        "model": model or DEFAULT_MODEL,
        "instructions": instructions,
        "audio": {"output": {"voice": voice or DEFAULT_VOICE}},
    }
    if backend:
        config["delegation"] = {"type": "responses", "responses": {"model": backend}}
    return config


async def open_session(
    *,
    sdp_offer: str,
    instructions: str,
    model: str | None = None,
    voice: str | None = None,
    backend: str | None = None,
) -> tuple[str, str]:
    """Exchange a browser SDP offer for a live session.

    :param sdp_offer: The browser's SDP offer.
    :param instructions: System prompt for the conversation.
    :param model: Model override; defaults to :data:`DEFAULT_MODEL`.
    :param voice: Voice override; defaults to :data:`DEFAULT_VOICE`.
    :param backend: Model to delegate turn content to, when text will be
        pushed into the session rather than spoken at it.
    :returns: ``(session_id, sdp_answer)``.
    :raises LiveVoiceUnavailable: When no key is configured.
    :raises httpx.HTTPStatusError: When OpenAI rejects the handshake.
    """
    body = {
        "transport": {"type": "webrtc", "sdp": sdp_offer},
        "session": session_config(
            instructions=instructions, model=model, voice=voice, backend=backend
        ),
    }
    async with httpx.AsyncClient(timeout=_HANDSHAKE_TIMEOUT_S) as client:
        response = await client.post(
            LIVE_SESSIONS_URL,
            headers={"Authorization": f"Bearer {api_key()}"},
            json=body,
        )
    response.raise_for_status()
    payload = response.json()
    session_id = str(payload.get("session", {}).get("id") or "")
    answer = str(payload.get("transport", {}).get("sdp") or "")
    if not answer:
        raise RuntimeError(f"live session returned no SDP answer: {payload!r}")
    _logger.info("live voice session opened: %s", session_id)
    return session_id, answer
