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
from typing import Any, Final

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

#: Default conversational model. Google lists 3.1 Flash Live as a legacy
#: preview. Measured 2026-10-09 over 20 interleaved sessions with identical
#: audio and our real briefing: asked "are you hearing me?" during a 10 s check,
#: 3.8 answered in ~1.3 s where 3.1 sat silent ~12 s (it cancels the call), and
#: spoke the result 1.6 s after it came vs 7.5-8.6 s. Plain replies tied (~1.3 s).
MODEL: Final[str] = "models/gemini-3.8-live"

#: Narration reads a given text aloud and calls no tools.
NARRATOR_MODEL: Final[str] = "models/gemini-3.1-flash-live-preview"


def tools_block(model: str | None = None) -> bool:
    """Whether the model stays deaf and mute until a tool call is answered.

    3.1 Flash Live calls functions sequentially only. 3.8 Live runs them beside
    the conversation (``NON_BLOCKING``), so it can talk while a check runs.
    """
    name = (model or MODEL).removeprefix("models/")
    return not name.startswith("gemini-3.8-live")


#: Default prebuilt voice for narration and speech output.
DEFAULT_VOICE: Final[str] = "Aoede"
VOICE: Final[str] = DEFAULT_VOICE


#: The handoff tool, as every live voice declares it (Unmute's brain too).
ASK_CLAUDE_DESCRIPTION: Final[str] = (
    "Send a request to Claude once the user wants it sent: they "
    "told you to send it or to ask Claude, or they said yes when "
    "you asked once. Calling this ends the call, so never call it "
    "on your own initiative, and never ask twice: once they want "
    "it sent, then call it at once. This is the only way to reach Claude: "
    "never say you are asking or waiting on Claude without "
    "calling it. "
    "What needs Claude: a fact the session notes do not cover "
    "(code, files, data, measurements) or an action on their "
    "machine. Not for what the notes answer, and not for a brief "
    "clarification."
)
ASK_CLAUDE_QUESTION: Final[str] = "The question or task to ask Claude."

#: The look-it-up tool: read-only commands run by the companion, call stays open.
CHECK_MACHINE_DESCRIPTION: Final[str] = (
    "Find out a fact on their machine without bothering Claude: disk or memory "
    "use, running processes and services, whether a run or job is still going, "
    "log tails, files and folders, git status and history. The companion runs "
    "read-only commands and returns what it found. It can never change "
    "anything, so call it whenever such a fact would answer them, without "
    "asking first; the call stays open. Not for changing code, files or "
    "settings, or starting, stopping or deleting anything: that is ask_claude."
)
CHECK_MACHINE_QUESTION: Final[str] = (
    "What to find out, in plain words, with any names, paths or numbers they mentioned."
)


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
    tools: list[dict[str, Any]] | None = None,
    extra_tools: list[dict[str, Any]] | None = None,
    resume_handle: str | None = None,
) -> dict:
    """Return the proven setup frame for the WebSocket's first message.

    :param tools: Declarations to use instead of the per-chat pair (the hub's).
    :param extra_tools: Declarations added to the per-chat pair.
    :param resume_handle: Continue the session this handle names, after
        Google closed its connection.
    """
    name = model or (NARRATOR_MODEL if mode == "narrate" else MODEL)
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
        # Low start sensitivity: its own voice leaking back through speakers,
        # or room noise, read as the reader talking and cut every reply after a
        # word or two ("Opa! O", "Tô sim,"). Real speech still interrupts.
        "realtimeInputConfig": {
            "automaticActivityDetection": {"startOfSpeechSensitivity": "START_SENSITIVITY_LOW"}
        },
        # A connection lasts about ten minutes and an audio session fifteen.
        # Handles let the relay carry the session onto a new connection, and
        # a sliding window lifts the length cap.
        "sessionResumption": {"handle": resume_handle} if resume_handle else {},
        "contextWindowCompression": {"slidingWindow": {}},
    }
    if system_instruction and system_instruction.strip():
        setup["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        # Spelled out although it is 3.8's default: a check that leaves the
        # voice deaf for its 10-30 s is the dead air this exists to remove.
        behavior = {} if tools_block(name) else {"behavior": "NON_BLOCKING"}
        if tools is not None:
            setup["tools"] = [
                {
                    "functionDeclarations": [
                        {k: v for k, v in d.items() if k != "behavior" or not tools_block(name)}
                        for d in tools
                    ]
                }
            ]
            return {"setup": setup}
        setup["tools"] = [
            {
                "functionDeclarations": [
                    {
                        "name": "ask_claude",
                        **behavior,
                        "description": ASK_CLAUDE_DESCRIPTION,
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "question": {
                                    "type": "STRING",
                                    "description": ASK_CLAUDE_QUESTION,
                                }
                            },
                            "required": ["question"],
                        },
                    },
                    {
                        "name": "check_machine",
                        **behavior,
                        "description": CHECK_MACHINE_DESCRIPTION,
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "question": {
                                    "type": "STRING",
                                    "description": CHECK_MACHINE_QUESTION,
                                }
                            },
                            "required": ["question"],
                        },
                    },
                    *({**behavior, **d} for d in extra_tools or []),
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
