from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from omnigent.runtime import inflight_text
from omnigent.runtime.subagent_loop_notifier import SubagentLoopNotifier

pytestmark = pytest.mark.asyncio


def _tool_event(name: str = "Read", arguments: str = '{"path":"a.py"}') -> dict[str, Any]:
    return {
        "type": "external_conversation_item",
        "data": {
            "item_type": "function_call",
            "item_data": {"name": name, "arguments": arguments},
        },
    }


def _standard_tool_event(
    name: str = "Read", arguments: str = '{"path":"a.py"}', call_id: str = "c"
) -> dict[str, Any]:
    return {
        "type": "response.output_item.done",
        "item": {
            "type": "function_call",
            "name": name,
            "arguments": arguments,
            "call_id": call_id,
        },
    }


async def _exercise(events: list[dict[str, Any]], parent_id: str | None = "parent") -> list[str]:
    delivered: list[str] = []
    notifier = SubagentLoopNotifier(
        conversation_store=SimpleNamespace(
            get_conversation=lambda _conversation_id: SimpleNamespace(
                id="child", parent_conversation_id=parent_id, title="codex:child"
            )
        ),
        wake_dispatch=lambda _parent, _child, notice: _capture(delivered, notice),
        loop=asyncio.get_running_loop(),
    )
    for event in events:
        notifier.observe("child", event)
    await asyncio.sleep(0.05)
    notifier.close()
    return delivered


async def _capture(delivered: list[str], notice: str) -> bool:
    delivered.append(notice)
    return True


async def test_five_identical_calls_wake_parent_once() -> None:
    delivered = await _exercise([_tool_event()] * 5)
    assert len(delivered) == 1
    assert "Read" in delivered[0]
    assert "5" in delivered[0]


async def test_four_identical_then_different_call_wakes_nobody() -> None:
    delivered = await _exercise([_tool_event()] * 4 + [_tool_event("Write")])
    assert delivered == []


async def test_breaking_and_reforming_streak_wakes_twice() -> None:
    events = [_tool_event()] * 5 + [_tool_event("Write")] + [_tool_event()] * 5
    delivered = await _exercise(events)
    assert len(delivered) == 2


async def test_parentless_session_never_wakes() -> None:
    delivered = await _exercise([_tool_event()] * 6, parent_id=None)
    assert delivered == []


async def test_standard_output_item_shape_counts() -> None:
    delivered = await _exercise([_standard_tool_event(call_id=str(i)) for i in range(5)])
    assert len(delivered) == 1


async def test_long_arguments_are_capped() -> None:
    delivered = await _exercise([_tool_event(arguments="x" * 300)] * 5)
    assert len(delivered) == 1
    assert "x" * 197 + "..." in delivered[0]
    assert len(delivered[0].split("(", 1)[1].split(")", 1)[0]) == 200


async def test_call_id_prevents_double_count() -> None:
    events = []
    for index in range(5):
        event = _tool_event()
        event["data"]["item_data"]["call_id"] = str(index)
        events.append(event)
    events.append(events[-1])
    delivered = await _exercise(events)
    assert len(delivered) == 1


async def test_record_publish_wires_observer() -> None:
    delivered: list[str] = []
    notifier = SubagentLoopNotifier(
        SimpleNamespace(
            get_conversation=lambda _id: SimpleNamespace(
                id="child", parent_conversation_id="parent", title="child"
            )
        ),
        lambda _parent, _child, notice: _capture(delivered, notice),
        asyncio.get_running_loop(),
    )
    inflight_text.set_publish_observer(notifier.observe)
    try:
        for _ in range(5):
            inflight_text.record_publish("child", _tool_event())
        await asyncio.sleep(0.05)
    finally:
        inflight_text.set_publish_observer(None)
        notifier.close()
    assert len(delivered) == 1
