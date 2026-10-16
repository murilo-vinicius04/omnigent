"""Write a chat's context down and compact it before it changes model.

A new model starts with an empty prompt cache, so everything the chat has read
is paid for again at the write rate: one switch of an 870k-token chat cost about
a ninth of a Pro 5-hour window. Compacting first shrinks what is rewritten, and
asking the chat to write down what a fresh session needs first keeps what the
summary would drop.

The sequence is the one the context threshold already uses, run on request:
wait for the chat to be idle, ask for the write-up, wait for that turn to end,
compact, wait for the compaction to land. Every wait is bounded and every
failure ends in the same place -- the switch still happens, and the reply says
what was and was not done.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final, Literal

from omnigent.server import auto_compact, voice_hub

_logger = logging.getLogger(__name__)

#: How each attempt ended.
Outcome = Literal[
    "compacted", "small", "busy", "blocked", "no-writeup", "refused", "no-confirmation"
]

#: Longest to wait for a chat that is mid-turn to finish before giving up.
IDLE_WAIT_S: Final[float] = 30 * 60.0

#: Longest to wait for the write-up turn, and for the compaction, to finish.
WRITEUP_WAIT_S: Final[float] = 15 * 60.0
COMPACT_WAIT_S: Final[float] = 10 * 60.0

#: After sending, how long a chat may still read idle before it is taken to
#: have answered already (a one-line reply can finish between two polls).
START_GRACE_S: Final[float] = 20.0

#: How often the waits look at the chat.
POLL_S: Final[float] = 3.0


@dataclass(frozen=True)
class Compaction:
    """What the hub needs from a chat's server to compact it.

    :param context_tokens: Tokens its last turn carried; 0 when unreported.
    :param compact: Ask its runner to compact; raises when refused.
    :param compacted_since: Whether a compaction landed after a moment.
    :param blocked: Why a chat's plan turns its requests away right now (e.g.
        ``"5h window full until 16:13"``), else ``None``. A blocked chat can
        neither write up nor compact, so neither is attempted.
    """

    context_tokens: Callable[[str], Awaitable[int]]
    compact: Callable[[str], Awaitable[None]]
    compacted_since: Callable[[str, float], Awaitable[bool]]
    blocked: Callable[[str], Awaitable[str | None]] | None = None

    async def why_blocked(self, session_id: str) -> str | None:
        """Why the chat cannot answer now; ``None`` when it can or this is unknown."""
        if self.blocked is None:
            return None
        try:
            return await self.blocked(session_id)
        except Exception:  # noqa: BLE001 - not knowing is not a reason to stop
            _logger.debug("handoff: block check failed for %s", session_id, exc_info=True)
            return None


async def _until(done: Callable[[], Awaitable[bool]], timeout_s: float, poll_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while True:
        if await done():
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(poll_s)


async def write_up_and_compact(
    session_id: str,
    *,
    prompt: str,
    compaction: Compaction,
    send: Callable[[str, str], Awaitable[None]],
    status: Callable[[str], Awaitable[str | None]],
    poll_s: float | None = None,
    idle_wait_s: float | None = None,
    writeup_wait_s: float | None = None,
    compact_wait_s: float | None = None,
    start_grace_s: float | None = None,
    interrupted: bool = False,
    claimed: bool = False,
) -> Outcome:
    """Ask a chat to write its context down, then compact it.

    Never raises for a chat that cannot be reached or compacted: the outcome
    says so, and the caller carries on with the switch.

    :param session_id: The chat's id.
    :param prompt: The write-up request to send.
    :param compaction: The server side of compacting it.
    :param send: Post a message into the chat.
    :param status: The chat's live status; ``None`` once it is gone.
    :param idle_wait_s: The waits, each defaulting to its module constant.
    :param interrupted: The chat's turn was just interrupted. Its status can
        lag behind the interrupt, so a chat still reading busy after the idle
        wait is asked anyway instead of being given up on.
    :param claimed: The caller already holds the chat (see
        :func:`omnigent.server.auto_compact.claim`) and releases it.
    :returns: How it ended; only ``"compacted"`` means the context shrank.
    """
    poll_s = POLL_S if poll_s is None else poll_s
    idle_wait_s = IDLE_WAIT_S if idle_wait_s is None else idle_wait_s
    writeup_wait_s = WRITEUP_WAIT_S if writeup_wait_s is None else writeup_wait_s
    compact_wait_s = COMPACT_WAIT_S if compact_wait_s is None else compact_wait_s
    start_grace_s = START_GRACE_S if start_grace_s is None else start_grace_s
    try:
        if await compaction.context_tokens(session_id) < auto_compact.MIN_CONTEXT_TOKENS:
            return "small"
    except Exception:  # noqa: BLE001 - an unknown size is treated as not worth it
        _logger.debug("handoff: context size unavailable for %s", session_id, exc_info=True)
        return "small"
    if await compaction.why_blocked(session_id):
        return "blocked"
    if not claimed and not auto_compact.claim(session_id):
        return "busy"
    compacted = False
    try:

        async def idle() -> bool:
            return (await status(session_id) or "") not in voice_hub.WORKING_STATUSES

        if not await _until(idle, idle_wait_s, poll_s) and not interrupted:
            return "busy"
        # The turn it was busy with may have used the last of the window.
        if await compaction.why_blocked(session_id):
            return "blocked"
        before = await status(session_id)
        try:
            await send(session_id, prompt)
        except Exception:  # noqa: BLE001 - a chat that cannot be reached cannot be compacted
            _logger.warning("handoff: could not ask %s to write up", session_id, exc_info=True)
            return "no-writeup"
        began = time.monotonic()
        seen_work = failed = False
        # A "failed" left over from the last turn is not this one's.
        armed = before != "failed"

        async def answered() -> bool:
            nonlocal seen_work, failed, armed
            now_status = await status(session_id) or ""
            if now_status != "failed":
                armed = True
            if now_status in voice_hub.WORKING_STATUSES:
                seen_work = True
                return False
            if now_status == "failed" and armed:
                failed = True
                return True
            return seen_work or time.monotonic() - began >= start_grace_s

        if not await _until(answered, writeup_wait_s, poll_s) or failed:
            return "no-writeup"
        # The write-up itself may have used the last of the window.
        if await compaction.why_blocked(session_id):
            return "blocked"
        compact_at = time.time()
        try:
            await compaction.compact(session_id)
        except Exception:  # noqa: BLE001 - e.g. "not enough messages to compact"
            _logger.warning("handoff: compaction of %s refused", session_id, exc_info=True)
            return "refused"

        async def landed() -> bool:
            return await compaction.compacted_since(session_id, compact_at)

        if not await _until(landed, compact_wait_s, poll_s):
            return "no-confirmation"
        # The pane is busy until the compaction has settled; give it a moment
        # to read idle before the caller types into it.
        await _until(idle, 60.0, poll_s)
        compacted = True
        return "compacted"
    finally:
        auto_compact.release(session_id, compacted=compacted)


#: What the reply adds about the compaction, by outcome.
SAID: Final[dict[str, str]] = {
    "compacted": "its context was written up and compacted first",
    "small": "",
    "busy": "it was still busy, so it was not compacted first",
    "blocked": "its plan window is full, so it could not write up or compact first",
    "no-writeup": "it did not answer the write-up request, so it was not compacted first",
    "refused": "it could not be compacted, so it was switched as it was",
    "no-confirmation": "the compaction did not confirm in time, so it was switched as it was",
}
