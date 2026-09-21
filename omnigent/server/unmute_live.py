"""Omnigent's live voice on a local Kyutai Unmute stack.

Unmute hears (speech-to-text) and speaks (text-to-speech) on the local GPU and
asks an OpenAI-compatible model what to say. Omnigent plays that model -- the
*brain* -- so the voice gets the same briefing and the same ``ask_claude``
handoff as Gemini Live, while the browser relay speaks Gemini Live's wire
format so the page drives both with the same client code.

Unmute has one model address for every session, and the system prompt is the
only per-session thing it forwards, so the relay writes a call token into the
instructions and the brain finds the call by it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Final

import httpx

from omnigent.server.gemini_live import ASK_CLAUDE_DESCRIPTION, ASK_CLAUDE_QUESTION

_logger = logging.getLogger(__name__)

#: Where the Unmute stack listens (pages, API and realtime socket under /unmute).
UPSTREAM_ENV: Final[str] = "OMNIGENT_UNMUTE_URL"
DEFAULT_UPSTREAM: Final[str] = "http://127.0.0.1:8089"

#: Unmute voice for Omnigent calls: a studio narration clip from the Expresso
#: dataset, so no accent is cloned into it.
VOICE_ENV: Final[str] = "OMNIGENT_UNMUTE_VOICE"
DEFAULT_VOICE: Final[str] = "unmute-prod-website/ex04_narration_longform_00001.wav"

#: Where the brain sends model calls. Defaults to this server's own OpenAI
#: budget proxy, so every call is admitted against and recorded in the free pool.
BRAIN_UPSTREAM_ENV: Final[str] = "OMNIGENT_UNMUTE_BRAIN_UPSTREAM"

#: Speech limits of the Kyutai stack, added to the shared live voice role.
SPOKEN_RULES: Final[str] = (
    "Your words are turned into speech by a voice that only speaks English, so "
    "always answer in English, even when they use another language. Write the way "
    "people talk: plain sentences, no lists, markdown, emojis or symbols, nothing "
    "in brackets. What they say reaches you through speech recognition and can "
    "contain mistakes; when a word makes no sense, guess what they meant from how "
    "it sounds rather than asking."
)

#: Longest wait for the page to answer a tool call. Its own routing gives up
#: after 15s and still answers, so this only fires on a page that went away.
TOOL_RESULT_TIMEOUT_S: Final[float] = 45.0

#: Tool round trips in one reply before the brain stops calling tools.
MAX_TOOL_ROUNDS: Final[int] = 4

#: Spoken replies are short; a small cap keeps the pool's admission estimate low.
MAX_REPLY_TOKENS: Final[int] = 800

#: Returned to the model when the page never answered its tool call.
NO_TOOL_RESULT: Final[str] = (
    "Nothing came back from the page, so nothing was sent. Tell them briefly."
)

#: Spoken when the model itself cannot be reached, so silence is never the answer.
BRAIN_DOWN: Final[str] = "Sorry, I can't reach my language model right now."

#: The narration frame the page wraps a summary in (see ``frameForReading``);
#: the brain reads what follows it verbatim.
_NARRATION_LEAD: Final[str] = "Just say it:\n\n"

_CALL_RE: Final[re.Pattern[str]] = re.compile(r"omnigent-call:([A-Za-z0-9_-]{16,})")

ASK_CLAUDE_TOOL: Final[dict[str, Any]] = {
    "type": "function",
    "function": {
        "name": "ask_claude",
        "description": ASK_CLAUDE_DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": ASK_CLAUDE_QUESTION}},
            "required": ["question"],
        },
    },
}


def unmute_base_url() -> str:
    """Return the Unmute stack's base URL, without a trailing slash."""
    return os.environ.get(UPSTREAM_ENV, DEFAULT_UPSTREAM).rstrip("/")


def brain_upstream(server: tuple[str, int] | None) -> str:
    """Return the model base URL: the override, else this server's budget proxy.

    :param server: The ASGI ``server`` of the request being served, which
        names the port this Omnigent listens on.
    """
    override = os.environ.get(BRAIN_UPSTREAM_ENV, "").strip()
    if override:
        return override.rstrip("/")
    port = server[1] if server else 6767
    return f"http://127.0.0.1:{port}/v1/openai-budget/v1"


def voice() -> str:
    """Return the Unmute voice Omnigent calls speak with."""
    return os.environ.get(VOICE_ENV, "").strip() or DEFAULT_VOICE


class CallEnded(Exception):
    """The page hung up while the brain was waiting on it."""


@dataclass
class UnmuteCall:
    """One open Unmute session, shared by its browser relay and the brain.

    :param call_id: Token written into Unmute's instructions.
    :param session_id: Omnigent session the call belongs to.
    :param instructions: System prompt for a conversation; ``None`` narrates.
    """

    call_id: str
    session_id: str
    instructions: str | None
    #: Text to read aloud, once the page has sent it (narration only).
    narration: str | None = None
    #: Gemini-shaped frames the brain wants the page to see (tool calls).
    outbox: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    pending: dict[str, asyncio.Future[str]] = field(default_factory=dict)
    #: Tool calls whose reply Unmute dropped because the reader talked over it.
    orphaned: set[str] = field(default_factory=set)
    #: Their results, carried into the next reply instead of being lost.
    late_results: list[str] = field(default_factory=list)
    tool_calls: int = 0
    closed: bool = False

    async def ask_page(self, tool_call_id: str, question: str) -> str:
        """Hand an ``ask_claude`` call to the page and wait for its answer."""
        self.tool_calls += 1
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self.pending[tool_call_id] = future
        await self.outbox.put(
            {
                "toolCall": {
                    "functionCalls": [
                        {"id": tool_call_id, "name": "ask_claude", "args": {"question": question}}
                    ]
                }
            }
        )
        try:
            return await asyncio.wait_for(future, TOOL_RESULT_TIMEOUT_S)
        except asyncio.TimeoutError:
            return NO_TOOL_RESULT
        except asyncio.CancelledError:
            self.orphaned.add(tool_call_id)
            raise
        finally:
            self.pending.pop(tool_call_id, None)

    def resolve(self, tool_call_id: str, output: str) -> None:
        """Deliver the page's answer to a tool call, or keep it for later."""
        future = self.pending.get(tool_call_id)
        if future is not None and not future.done():
            future.set_result(output)
        elif tool_call_id in self.orphaned:
            self.orphaned.discard(tool_call_id)
            self.late_results.append(output)

    def close(self) -> None:
        """End the call: anything still waiting on the page gives up."""
        self.closed = True
        for future in self.pending.values():
            if not future.done():
                future.set_exception(CallEnded())


_CALLS: dict[str, UnmuteCall] = {}


def open_call(session_id: str, instructions: str | None) -> UnmuteCall:
    """Register a call; ``instructions=None`` opens a narration."""
    call = UnmuteCall(secrets.token_urlsafe(18), session_id, instructions)
    _CALLS[call.call_id] = call
    return call


def close_call(call: UnmuteCall) -> None:
    """Forget a call and release anything waiting on it."""
    call.close()
    _CALLS.pop(call.call_id, None)


def find_call(system_prompt: str) -> UnmuteCall | None:
    """Return the call whose token appears in Unmute's system prompt."""
    match = _CALL_RE.search(system_prompt)
    return _CALLS.get(match.group(1)) if match else None


def narration_text(framed: str) -> str:
    """Strip the read-aloud frame the page wraps a summary in."""
    return framed.split(_NARRATION_LEAD, 1)[-1].strip()


def session_update(call: UnmuteCall) -> dict[str, Any]:
    """Return the Unmute ``session.update`` that opens *call*.

    Both directions carry PCM16 so the page reuses its Gemini audio path. A
    conversation waits for the reader instead of greeting, and never fills a
    silence on its own, as Gemini does; a narration "greets" with its text.
    """
    narrating = call.instructions is None
    return {
        "type": "session.update",
        "session": {
            "instructions": {"type": "constant", "text": f"omnigent-call:{call.call_id}"},
            "voice": voice(),
            "allow_recording": False,
            "audio_format": "pcm16",
            # The page captures at 16 kHz; a narration feeds its own silence.
            "input_sample_rate": 24_000 if narrating else 16_000,
            "greet": narrating,
            "nudge_on_silence": False,
            # Only a recognized word interrupts: a noise or echo flicker in the
            # voice-activity score right after a pause cancelled whole replies.
            "interrupt_on_vad": False,
        },
    }


def conversation_messages(
    call: UnmuteCall, messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Swap Unmute's system prompt for the call's own, keeping the dialogue.

    :param call: The conversation the request belongs to.
    :param messages: Unmute's chat history as it sent it.
    :returns: Messages for the model, with any late tool result folded in.
    """
    dialogue = [m for m in messages if m.get("role") != "system"]
    out: list[dict[str, Any]] = [{"role": "system", "content": call.instructions or ""}]
    if call.late_results:
        note = (
            "[Results of your earlier ask_claude call, which arrived after they "
            "interrupted you. Tell them if it is still relevant.]\n" + "\n".join(call.late_results)
        )
        call.late_results.clear()
        dialogue = [*dialogue[:-1], {"role": "system", "content": note}, *dialogue[-1:]]
    return out + dialogue


async def _stream_round(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    tool_calls: dict[int, dict[str, str]],
) -> AsyncIterator[str]:
    """Stream one model call's text, collecting its tool calls into *tool_calls*."""
    async with client.stream("POST", url, headers=headers, json=payload) as response:
        if response.status_code >= 400:
            detail = (await response.aread()).decode("utf-8", "replace")[:300]
            _logger.warning("unmute brain: model refused %s: %s", response.status_code, detail)
            yield BRAIN_DOWN
            return
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except ValueError:
                continue
            for choice in event.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    yield delta["content"]
                for part in delta.get("tool_calls") or []:
                    slot = tool_calls.setdefault(
                        int(part.get("index", 0)), {"id": "", "name": "", "arguments": ""}
                    )
                    function = part.get("function") or {}
                    slot["id"] = part.get("id") or slot["id"]
                    slot["name"] = function.get("name") or slot["name"]
                    slot["arguments"] += function.get("arguments") or ""


async def speak(
    call: UnmuteCall,
    messages: list[dict[str, Any]],
    *,
    model: str,
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
) -> AsyncIterator[str]:
    """Yield what the voice says next, running any ``ask_claude`` through the page.

    A narration yields its text verbatim, with no model call. A conversation
    streams the model's words as they come; a tool call waits for the page's
    answer and the model then continues with it, all in the same reply.
    """
    if call.instructions is None:
        if call.narration:
            yield call.narration
            call.narration = ""
        return
    history = conversation_messages(call, messages)
    for _ in range(MAX_TOOL_ROUNDS):
        tool_calls: dict[int, dict[str, str]] = {}
        said = ""
        payload = {
            "model": model,
            "messages": history,
            "stream": True,
            "tools": [ASK_CLAUDE_TOOL],
            # The only effort OpenAI allows with function tools on chat
            # completions, and the fastest to a first word.
            "reasoning_effort": "none",
            "max_completion_tokens": MAX_REPLY_TOKENS,
        }
        try:
            async for text in _stream_round(client, url, headers, payload, tool_calls):
                said += text
                yield text
        except httpx.HTTPError as exc:
            _logger.warning("unmute brain: model unreachable: %s", exc)
            yield BRAIN_DOWN
            return
        if not tool_calls:
            return
        calls = [tool_calls[i] for i in sorted(tool_calls)]
        history.append(
            {
                "role": "assistant",
                "content": said or None,
                "tool_calls": [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {"name": c["name"], "arguments": c["arguments"]},
                    }
                    for c in calls
                ],
            }
        )
        for c in calls:
            if c["name"] != "ask_claude":
                result = f"Tool '{c['name']}' is not available."
            else:
                try:
                    args = json.loads(c["arguments"] or "{}")
                except ValueError:
                    args = {}
                question = str(args.get("question", "")) if isinstance(args, dict) else ""
                try:
                    result = await call.ask_page(c["id"], question)
                except CallEnded:
                    return
            history.append({"role": "tool", "tool_call_id": c["id"], "content": result})
