"""Gemini Live voice session support: credentials and the WebSocket URL.

Gemini Live runs beside the OpenAI live voice (``live_voice``), not
instead of it: it bills differently and the swap must stay reversible.
It is a WebSocket, not WebRTC -- the server opens the upstream socket
to Google and relays frames both ways on the browser's behalf, so the
key never leaves the server; ``socket_url`` is server-side only because
the key rides in its query string. The endpoint and frame shape below were smoke
tested against the real API on 2026-09-14; do not redesign them.
"""

from __future__ import annotations

import os
import pathlib
from typing import Final

from omnigent.server.live_voice import NARRATOR_INSTRUCTIONS


class GeminiLiveUnavailable(RuntimeError):
    """No usable Gemini credential, so live voice cannot be offered."""


#: Where the API key is read from when ``GEMINI_API_KEY`` is unset. Kept
#: outside the repo so it cannot be committed.
KEY_PATH: Final[pathlib.Path] = pathlib.Path.home() / ".omnigent" / "gemini.env"

#: Gemini's bidirectional streaming endpoint. The key rides in the query
#: string; the socket is opened by this server and the frames are
#: relayed to/from the browser, so this URL never reaches the page.
ENDPOINT: Final[str] = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)

#: Default conversational model, pinned to the smoke-tested one.
#: Measured 2026-09-18 over 20 interleaved sessions with identical audio: the
#: 12-2025 native-audio preview left 3 of 10 sessions silent — connected, still
#: transcribing, never answering — while this one answered 13 of 13, faster.
MODEL: Final[str] = "models/gemini-3.1-flash-live-preview"

#: Default prebuilt voice for narration and speech output.
DEFAULT_VOICE: Final[str] = "Aoede"
VOICE: Final[str] = DEFAULT_VOICE


def api_key() -> str:
    """Return the Gemini API key.

    :raises GeminiLiveUnavailable: When no key is configured.
    """
    from_env = os.environ.get("GEMINI_API_KEY", "").strip()
    if from_env:
        return from_env
    try:
        from_file = KEY_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        from_file = ""
    for line in from_file.splitlines():
        line = line.strip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() != "GEMINI_API_KEY":
            continue
        value = value.strip().strip("'\"").strip()
        if value:
            return value
    raise GeminiLiveUnavailable(f"no Gemini key: set GEMINI_API_KEY or write one to {KEY_PATH}")


def available() -> bool:
    """Whether a Gemini live voice session could be created right now."""
    try:
        api_key()
    except GeminiLiveUnavailable:
        return False
    return True


def setup_frame(
    *,
    model: str | None = None,
    system_instruction: str | None = None,
    mode: str | None = None,
    voice: str | None = None,
) -> dict:
    """Return the proven setup frame for the WebSocket's first message."""
    name = model or MODEL
    if not name.startswith("models/"):
        name = f"models/{name}"
    if mode == "narrate":
        setup: dict[str, object] = {
            "model": name,
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice or DEFAULT_VOICE}}
                },
            },
            "systemInstruction": {"parts": [{"text": NARRATOR_INSTRUCTIONS}]},
        }
        return {"setup": setup}

    setup: dict[str, object] = {
        "model": name,
        # Ask for the voice by name here too. Narration does, and a setup frame
        # that omits speechConfig gets Google's own default instead, so the
        # same companion answered in one voice when it read and a different
        # one when it talked.
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice or DEFAULT_VOICE}}
            },
        },
        "inputAudioTranscription": {},
        "outputAudioTranscription": {},
    }
    if system_instruction and system_instruction.strip():
        setup["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        setup["tools"] = [
            {
                "functionDeclarations": [
                    {
                        "name": "ask_claude",
                        "description": (
                            "Delegate to Claude when the user asks about something the "
                            "session notes do not cover (code, files, data, measurements) "
                            "or asks for actions on their machine; do not delegate when notes "
                            "answer it or for brief clarifications."
                        ),
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "question": {
                                    "type": "STRING",
                                    "description": "The question or task to ask Claude.",
                                }
                            },
                            "required": ["question"],
                        },
                    }
                ]
            }
        ]
    return {"setup": setup}


def socket_url(key: str, *, model: str | None = None) -> str:
    """Return the WebSocket URL with the key in the query string.

    ``model`` is accepted for symmetry with :func:`setup_frame`; the
    endpoint itself takes the model from the setup frame, not the URL.
    """
    _ = model
    return f"{ENDPOINT}?key={key}"
