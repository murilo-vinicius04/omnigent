"""Writing a chat up and compacting it before it changes model."""

from __future__ import annotations

import asyncio

import pytest

from omnigent.server import auto_compact, hub_handoff

SID = "conv_a"


@pytest.fixture(autouse=True)
def _clean() -> None:
    auto_compact._CLAIMED.clear()
    auto_compact._ASKED.clear()
    auto_compact._LAST_COMPACTED.clear()


class _Chat:
    """A chat that answers a write-up for a while, then is compacted."""

    def __init__(self, tokens: int = 300_000) -> None:
        self.tokens = tokens
        self.status_now = "idle"
        self.sent: list[str] = []
        self.compacted = False
        self.compact_error: Exception | None = None
        self.compaction_lands = True
        self.answers_after = 0.05
        self.blocked_now: str | None = None
        self.fails_instead = False
        self.calls: list[str] = []
        self._tasks: list[asyncio.Future[None]] = []

    async def context_tokens(self, _sid: str) -> int:
        return self.tokens

    async def status(self, _sid: str) -> str | None:
        return self.status_now

    async def send(self, _sid: str, text: str) -> None:
        self.calls.append("send")
        self.sent.append(text)
        self.status_now = "running"

        async def finish() -> None:
            await asyncio.sleep(self.answers_after)
            self.status_now = "failed" if self.fails_instead else "idle"

        self._tasks.append(asyncio.ensure_future(finish()))

    async def compact(self, _sid: str) -> None:
        self.calls.append("compact")
        if self.compact_error is not None:
            raise self.compact_error
        self.compacted = self.compaction_lands

    async def compacted_since(self, _sid: str, _since: float) -> bool:
        return self.compacted

    async def blocked(self, _sid: str) -> str | None:
        self.calls.append("blocked?")
        return self.blocked_now

    def compaction(self) -> hub_handoff.Compaction:
        return hub_handoff.Compaction(
            context_tokens=self.context_tokens,
            compact=self.compact,
            compacted_since=self.compacted_since,
            blocked=self.blocked,
        )


async def _run(chat: _Chat, **overrides) -> hub_handoff.Outcome:
    options = {"poll_s": 0.01, "idle_wait_s": 0.5, "writeup_wait_s": 0.5, "compact_wait_s": 0.3}
    options.update(overrides)
    return await hub_handoff.write_up_and_compact(
        SID,
        prompt="write it down",
        compaction=chat.compaction(),
        send=chat.send,
        status=chat.status,
        start_grace_s=0.05,
        **options,
    )


async def test_the_write_up_comes_first_then_the_compaction() -> None:
    chat = _Chat()
    assert await _run(chat) == "compacted"
    assert [c for c in chat.calls if c != "blocked?"] == ["send", "compact"]
    assert chat.sent == ["write it down"]


async def test_a_small_context_is_not_worth_it() -> None:
    chat = _Chat(tokens=10_000)
    assert await _run(chat) == "small"
    assert chat.calls == []


async def test_an_unreported_context_is_not_compacted_blind() -> None:
    chat = _Chat(tokens=0)
    assert await _run(chat) == "small"


async def test_it_waits_for_a_busy_chat_rather_than_steering_it() -> None:
    chat = _Chat()
    chat.status_now = "running"

    async def free_up() -> None:
        await asyncio.sleep(0.1)
        chat.status_now = "idle"

    task = asyncio.ensure_future(free_up())
    assert await _run(chat) == "compacted"
    assert task.done() and chat.calls.index("send") > 0 and chat.sent == ["write it down"]


async def test_a_chat_that_stays_busy_is_given_up_on() -> None:
    chat = _Chat()
    chat.status_now = "running"
    assert await _run(chat, idle_wait_s=0.1) == "busy"
    assert "send" not in chat.calls


async def test_a_write_up_that_never_finishes_means_no_compaction() -> None:
    chat = _Chat()
    chat.answers_after = 5.0
    assert await _run(chat, writeup_wait_s=0.1) == "no-writeup"
    assert "compact" not in chat.calls


async def test_a_refused_compaction_still_ends_cleanly() -> None:
    chat = _Chat()
    chat.compact_error = RuntimeError("Not enough messages to compact.")
    assert await _run(chat) == "refused"


async def test_a_compaction_that_never_lands_is_reported_not_assumed() -> None:
    chat = _Chat()
    chat.compaction_lands = False
    assert await _run(chat) == "no-confirmation"


async def test_the_chat_is_held_during_the_flow_and_released_after() -> None:
    chat = _Chat()
    seen: list[bool] = []
    original = chat.send

    async def watching(sid: str, text: str) -> None:
        seen.append(SID in auto_compact._CLAIMED)
        await original(sid, text)

    chat.send = watching  # type: ignore[method-assign]
    assert await _run(chat) == "compacted"
    assert seen == [True]
    assert SID not in auto_compact._CLAIMED
    # Compacted just now, so the context-share check holds off.
    assert (
        auto_compact.next_step(
            SID,
            {"omnigent.last_context_tokens": "900000", "omnigent.last_context_window": "1000000"},
        )
        is None
    )


async def test_a_chat_already_being_handled_is_not_handled_twice() -> None:
    auto_compact.claim(SID)
    chat = _Chat()
    assert await _run(chat) == "busy"
    assert "send" not in chat.calls


async def test_a_chat_whose_plan_is_full_is_not_asked_for_anything() -> None:
    chat = _Chat()
    chat.blocked_now = "5h window full until 16:13"
    assert await _run(chat) == "blocked"
    assert "send" not in chat.calls and "compact" not in chat.calls
    assert SID not in auto_compact._CLAIMED


async def test_a_window_that_fills_while_waiting_for_the_chat_is_caught() -> None:
    chat = _Chat()
    chat.status_now = "running"
    answers = iter([None, "5h window full until 16:13"])

    async def blocked_later(_sid: str) -> str | None:
        return next(answers, "5h window full until 16:13")

    async def finish_turn() -> None:
        await asyncio.sleep(0.05)
        chat.status_now = "idle"

    chat.blocked = blocked_later  # type: ignore[method-assign]
    task = asyncio.ensure_future(finish_turn())
    assert await _run(chat) == "blocked"
    assert task.done() and chat.sent == []


async def test_a_window_the_write_up_itself_filled_skips_the_compaction() -> None:
    chat = _Chat()
    answers = iter([None, None, "5h window full until 16:13"])

    async def blocked_after(_sid: str) -> str | None:
        return next(answers, "5h window full until 16:13")

    chat.blocked = blocked_after  # type: ignore[method-assign]
    assert await _run(chat) == "blocked"
    assert chat.sent == ["write it down"] and "compact" not in chat.calls


async def test_a_write_up_turn_that_fails_is_not_followed_by_a_compaction() -> None:
    # The plan turned the request away and the chat went to "failed": do not
    # sit out the timeout, and do not try to compact either.
    chat = _Chat()
    chat.fails_instead = True
    started = asyncio.get_running_loop().time()
    assert await _run(chat, writeup_wait_s=30.0) == "no-writeup"
    assert asyncio.get_running_loop().time() - started < 5
    assert "compact" not in chat.calls


async def test_a_failed_status_left_from_the_last_turn_is_not_this_ones() -> None:
    chat = _Chat()
    chat.status_now = "failed"  # the previous turn failed; this one is fine
    assert await _run(chat) == "compacted"
