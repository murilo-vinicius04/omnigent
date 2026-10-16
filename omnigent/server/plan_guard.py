"""Stop a working chat before its plan window runs out, and write it up then.

The checks at a turn end are too late for a long turn: Codex went from 90% to
100% of its 5-hour window inside one 46-call turn, and a session blocked at
100% can neither write anything down nor compact. So while any chat is
working this reads the plan meters every few seconds and, when one crosses the
interrupt ceiling (95% of 5h, 98% of the week, by default), interrupts that
chat while the plan still answers, so it can write its handoff and be
compacted. It does not resume the task: that would spend the last of the
window; the chat is left idle and lean.

The ceilings live in ``<hub dir>/budget.json`` under ``"handoff"``
(``interrupt_five_hour_pct``, ``interrupt_weekly_pct``, ``interrupt``).
``OMNIGENT_PLAN_GUARD=0`` turns the guard off.

Delivery is injected, so the decision is testable without a server.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import pathlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from omnigent.server import auto_compact, quota_handoff

_logger = logging.getLogger(__name__)

#: How often working chats are checked. The plan-limits reading behind it is
#: cached for 60 s, so this is the quickest a new reading is seen.
POLL_S: Final[float] = 20.0

ENABLED_ENV: Final[str] = "OMNIGENT_PLAN_GUARD"

#: Providers whose native harness can really interrupt a running turn.
#: Antigravity's cannot yet, so an "interrupt" there would stop nothing.
INTERRUPTIBLE: Final[frozenset[str]] = frozenset({"claude", "codex"})


def enabled() -> bool:
    """On unless ``OMNIGENT_PLAN_GUARD=0``."""
    return os.environ.get(ENABLED_ENV, "").strip().lower() not in {"0", "false", "no", "off"}


@dataclass(frozen=True)
class Deps:
    """What the guard needs from the server.

    :param working: Ids of the chats with a turn running now.
    :param labels: A chat's labels; ``None`` once it is gone.
    :param refresh: Read the plan meters again (cached briefly).
    :param act: Interrupt a chat, write it up and compact it. Runs in the
        background; a chat is acted on once per plan window.
    :param data_dir: Where the plan-limits cache files live.
    :param hub_dir: Where ``budget.json`` lives.
    """

    working: Callable[[], list[str]]
    labels: Callable[[str], Awaitable[dict[str, str] | None]]
    refresh: Callable[[], Awaitable[None]]
    act: Callable[[str, auto_compact.Pressure], Awaitable[None]]
    data_dir: pathlib.Path
    hub_dir: pathlib.Path


#: Handoffs in flight, held so the event loop cannot drop them.
_tasks: set[asyncio.Task[None]] = set()


async def _act(deps: Deps, session_id: str, pressure: auto_compact.Pressure) -> None:
    try:
        await deps.act(session_id, pressure)
    except Exception:  # noqa: BLE001 - one chat's failure must not stop the guard
        _logger.warning("plan guard: handoff of %s failed", session_id, exc_info=True)


async def tick(deps: Deps, *, now: datetime | None = None) -> list[str]:
    """Check every working chat once; start a handoff for those past the ceiling.

    :returns: The chats a handoff was started for.
    """
    ids = deps.working()
    if not ids or not quota_handoff.load_ceilings(deps.hub_dir).interrupt:
        return []
    try:
        await deps.refresh()
    except Exception:  # noqa: BLE001 - an old reading is still checked, and ignored if stale
        _logger.debug("plan guard: meter refresh failed", exc_info=True)
    started: list[str] = []
    for session_id in ids:
        if session_id in auto_compact._CLAIMED:
            continue
        labels = await deps.labels(session_id)
        if not labels or quota_handoff.provider_of(labels) not in INTERRUPTIBLE:
            continue
        if auto_compact.context_tokens(labels) < auto_compact.MIN_CONTEXT_TOKENS:
            continue
        pressure = quota_handoff.pressure_for(
            labels,
            data_dir=deps.data_dir,
            hub_dir=deps.hub_dir,
            now=now,
            interrupting=True,
        )
        # Written up already this window (at a turn end, or by an earlier tick).
        if pressure is None or auto_compact._QUOTA_HANDLED.get(session_id) == pressure.key:
            continue
        auto_compact._QUOTA_HANDLED[session_id] = pressure.key
        task = asyncio.ensure_future(_act(deps, session_id, pressure))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
        started.append(session_id)
    return started


async def keep_guard(deps: Deps, *, poll_s: float = POLL_S) -> None:
    """Run :func:`tick` for as long as the server does."""
    _logger.info("plan guard: watching working chats every %.0fs", poll_s)
    while True:
        try:
            await tick(deps)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the guard must outlive any one bad tick
            _logger.warning("plan guard tick failed", exc_info=True)
        await asyncio.sleep(poll_s)


async def drain() -> None:
    """Wait for handoffs in flight (tests, shutdown)."""
    if _tasks:
        with contextlib.suppress(Exception):
            await asyncio.gather(*list(_tasks))
