"""The spoken summary of a native turn describes the turn as it finished.

Hidden tests for the H3 task. They drive the server only through its public
``POST /v1/sessions/{id}/events`` route, in the order a native (terminal)
harness really posts things: the turn's transcript items, then the turn-end
``idle`` status edge, and -- because the transcript mirror is a separate writer
-- a closing reply that can reach the store a moment *after* that edge.

The rewriter (a model call) is replaced by a recorder, so the assertions are on
what the summary was written from, never on wording.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from tests.server.helpers import create_test_agent

pytestmark = pytest.mark.asyncio

#: How long a closing reply trails its turn-end edge. The observed case was
#: "the same second"; this is well inside that.
_REPLY_LAG_S = 1.0

#: Generous wall-clock bound for a summary to be written after its turn ends.
_SUMMARY_DEADLINE_S = 45.0

_QUESTION = "is the cache layer why the nightly import got slower?"
_OPENING_SHORT = "Let me check the import logs before I answer."
_OPENING_LONG = (
    "I'm checking on this right now: pulling the import timings for the last two "
    "weeks and the cache hit rates next to them, so I can tell you whether the "
    "slowdown lines up with the cache change before I say anything."
)
_FINAL = (
    "You were right on both counts. The nightly import went from 14 to 31 minutes "
    "on the day the cache layer shipped, and every extra minute is spent in cache "
    "misses on the product table, which is rebuilt from scratch each night. "
    "Warming that table before the import brings it back to 15 minutes."
)
_PREVIOUS_REPLY = (
    "The backup job is fine: it ran every night this week, the last archive is "
    "complete and restorable, and the retention policy kept exactly thirty days. "
    "Nothing there needs your attention."
)
_UNFINISHED_OPENING = (
    "Starting the full re-import with the warmed cache now; it takes about fifteen "
    "minutes, and I will compare its timings against last night's run to confirm "
    "the fix before calling it done."
)


class _Rewriter:
    """Stands in for the summary rewriter and records what it was given."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def __call__(self, text: str, **_: Any) -> tuple[dict[str, Any], None]:
        self.texts.append(text)
        return {"type": "spoken_summary", "text": "summary", "lang": "en-US"}, None

    async def wait_for(self, count: int, deadline_s: float = _SUMMARY_DEADLINE_S) -> None:
        end = time.monotonic() + deadline_s
        while len(self.texts) < count and time.monotonic() < end:
            await asyncio.sleep(0.05)


@pytest.fixture()
def rewriter(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Replace every model call on the summary path with local stand-ins."""
    from omnigent.server import voice_profile

    async def _no_refresh(*_: Any, **__: Any) -> bool:
        return False

    monkeypatch.setenv("OMNIGENT_TTS_ENABLED", "0")
    monkeypatch.setattr(voice_profile, "refresh_voice_observations", _no_refresh)
    recorder = _Rewriter()
    with patch("omnigent.server.spoken_summary.generate_spoken_summary", recorder):
        yield recorder


async def _session(client: httpx.AsyncClient) -> str:
    agent = await create_test_agent(client)
    resp = await client.post(
        "/v1/sessions",
        json={
            "agent_id": agent["id"],
            "labels": {"spoken_summary_enabled": "true", "spoken_summary_language": "en-US"},
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _post(client: httpx.AsyncClient, sid: str, event: dict[str, Any]) -> None:
    resp = await client.post(f"/v1/sessions/{sid}/events", json=event)
    assert resp.status_code in (200, 202), resp.text


def _item(item_type: str, item_data: dict[str, Any], response_id: str) -> dict[str, Any]:
    return {
        "type": "external_conversation_item",
        "data": {"item_type": item_type, "item_data": item_data, "response_id": response_id},
    }


def _user(text: str, rid: str) -> dict[str, Any]:
    # The reader's message is stored under its own response id, as a native
    # harness records it, not under the id of the turn that answers it.
    return _item(
        "message",
        {"role": "user", "content": [{"type": "input_text", "text": text}]},
        f"{rid}_prompt",
    )


def _assistant(text: str, rid: str) -> dict[str, Any]:
    return _item(
        "message",
        {
            "role": "assistant",
            "agent": "claude-native-ui",
            "content": [{"type": "output_text", "text": text}],
        },
        rid,
    )


def _tool_round(rid: str, call_id: str) -> list[dict[str, Any]]:
    return [
        _item(
            "function_call",
            {
                "agent": "claude-native-ui",
                "name": "Bash",
                "arguments": '{"command": "grep -c miss import.log"}',
                "call_id": call_id,
            },
            rid,
        ),
        _item("function_call_output", {"call_id": call_id, "output": "1842"}, rid),
    ]


def _idle(rid: str) -> dict[str, Any]:
    return {"type": "external_session_status", "data": {"status": "idle", "response_id": rid}}


async def _end_turn_then_store(
    client: httpx.AsyncClient, sid: str, rid: str, late: list[dict[str, Any]]
) -> None:
    """Post the turn-end edge, and store *late* items shortly after it was sent.

    The edge request runs concurrently: whether the server answers it at once
    or holds it, the late items still arrive while it is in flight or just after.
    """
    edge = asyncio.create_task(_post(client, sid, _idle(rid)))
    await asyncio.sleep(_REPLY_LAG_S)
    for event in late:
        await _post(client, sid, event)
    await asyncio.wait_for(edge, _SUMMARY_DEADLINE_S)


async def test_a_turn_whose_reply_is_stored_just_after_it_ends_is_summarized(
    client: httpx.AsyncClient, rewriter: _Rewriter
) -> None:
    """Short opening line + tool calls at the edge, the answer a second later."""
    sid = await _session(client)
    rid = "resp_turn_short_opening"
    for event in [_user(_QUESTION, rid), _assistant(_OPENING_SHORT, rid), *_tool_round(rid, "c1")]:
        await _post(client, sid, event)

    await _end_turn_then_store(client, sid, rid, [_assistant(_FINAL, rid)])
    await rewriter.wait_for(1)

    assert rewriter.texts, "the finished turn was never summarized"
    assert _FINAL in rewriter.texts[-1]


async def test_the_summary_is_written_from_the_answer_not_the_opening_note(
    client: httpx.AsyncClient, rewriter: _Rewriter
) -> None:
    """A long "checking on this now" opener must not be all the summary sees."""
    sid = await _session(client)
    rid = "resp_turn_long_opening"
    for event in [_user(_QUESTION, rid), _assistant(_OPENING_LONG, rid), *_tool_round(rid, "c1")]:
        await _post(client, sid, event)

    await _end_turn_then_store(client, sid, rid, [_assistant(_FINAL, rid)])
    await rewriter.wait_for(1)

    assert rewriter.texts, "the finished turn was never summarized"
    assert len(rewriter.texts) == 1, "one turn, one summary"
    assert _FINAL in rewriter.texts[0]


async def test_each_turn_is_summarized_from_its_own_messages(
    client: httpx.AsyncClient, rewriter: _Rewriter
) -> None:
    """No turn borrows another turn's reply, and a turn with no closing reply
    (it ended on a tool call) is still summarized, in bounded time."""
    sid = await _session(client)

    # An earlier, finished turn: its reply is the newest stored reply when the
    # next turn ends.
    for event in [
        _user("how did last night's backup go?", "resp_prev"),
        _assistant(_PREVIOUS_REPLY, "resp_prev"),
    ]:
        await _post(client, sid, event)

    # This turn says nothing before its tools, and its answer lands after the edge.
    rid = "resp_turn_tools_first"
    for event in [_user(_QUESTION, rid), *_tool_round(rid, "c1"), *_tool_round(rid, "c2")]:
        await _post(client, sid, event)
    await _end_turn_then_store(client, sid, rid, [_assistant(_FINAL, rid)])
    await rewriter.wait_for(1)

    assert rewriter.texts, "the finished turn was never summarized"
    assert _FINAL in rewriter.texts[0]
    assert _PREVIOUS_REPLY not in rewriter.texts[0]

    # A turn that ends on a tool call never gets a closing reply.
    rid3 = "resp_turn_ends_on_a_tool"
    for event in [
        _user("go ahead and re-run it", rid3),
        _assistant(_UNFINISHED_OPENING, rid3),
        *_tool_round(rid3, "c3"),
    ]:
        await _post(client, sid, event)
    started = time.monotonic()
    await _end_turn_then_store(client, sid, rid3, [])
    await rewriter.wait_for(2)

    assert len(rewriter.texts) >= 2, "a turn with no closing reply was never summarized"
    assert time.monotonic() - started < _SUMMARY_DEADLINE_S
    assert _UNFINISHED_OPENING in rewriter.texts[1]
    assert _FINAL not in rewriter.texts[1]
    assert _PREVIOUS_REPLY not in rewriter.texts[1]
