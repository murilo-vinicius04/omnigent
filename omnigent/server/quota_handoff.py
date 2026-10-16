"""How close a session's plan window is to running out.

A session blocked at 100% of its 5-hour window sits idle for hours, its cache
expires, and the first message after the reset pays for the whole context
again. So the write-up and the compaction belong *before* the block, while the
session can still answer. This reads the plan meters the plan-limits route
already caches and says whether a session's window is past the ceiling.

The ceilings live in ``<hub dir>/budget.json`` under ``"handoff"``::

    {"handoff": {"five_hour_pct": 80, "weekly_pct": 95}}

The real meter is read, not a mocked plan: the block that matters is the one
the provider enforces.
"""

from __future__ import annotations

import json
import logging
import pathlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from omnigent.server import auto_compact, budget

_logger = logging.getLogger(__name__)

#: A session's provider, from the wrapper label its harness carries.
PROVIDER_BY_WRAPPER: Final[dict[str, str]] = {
    "claude-code-native-ui": "claude",
    "codex-native-ui": "codex",
    "antigravity-native-ui": "gemini",
}

_WRAPPER_LABEL: Final[str] = "omnigent.wrapper"

#: A reading older than this says nothing about now.
MAX_READING_AGE_S: Final[float] = 15 * 60.0

#: A full window stays full until it resets, so an older reading still counts.
MAX_FULL_READING_AGE_S: Final[float] = 3600.0

#: At or past this the provider turns requests away.
FULL_PCT: Final[float] = 100.0


@dataclass(frozen=True)
class Ceilings:
    """The readings at which a session is written up and compacted.

    :param five_hour_pct: Ceiling for the 5-hour window.
    :param weekly_pct: Ceiling for the weekly window.
    :param interrupt_five_hour_pct: Ceiling at which a chat that is still
        working is interrupted, so it can be written up while the plan still
        answers.
    :param interrupt_weekly_pct: The same for the weekly window.
    :param interrupt: Whether a working chat is interrupted at all.
    """

    five_hour_pct: float = 80.0
    weekly_pct: float = 95.0
    interrupt_five_hour_pct: float = 95.0
    interrupt_weekly_pct: float = 98.0
    interrupt: bool = True


def load_ceilings(hub_dir: pathlib.Path) -> Ceilings:
    """The ceilings in the budget file over the defaults; a broken file is ignored."""
    defaults = Ceilings()
    try:
        raw = json.loads((hub_dir / budget.SETTINGS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defaults
    block = raw.get("handoff") if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        return defaults

    def pct(key: str, fallback: float) -> float:
        value = block.get(key)
        ok = isinstance(value, int | float) and not isinstance(value, bool)
        return float(value) if ok and 1 <= value <= 100 else fallback

    switch = block.get("interrupt")
    return Ceilings(
        five_hour_pct=pct("five_hour_pct", defaults.five_hour_pct),
        weekly_pct=pct("weekly_pct", defaults.weekly_pct),
        interrupt_five_hour_pct=pct("interrupt_five_hour_pct", defaults.interrupt_five_hour_pct),
        interrupt_weekly_pct=pct("interrupt_weekly_pct", defaults.interrupt_weekly_pct),
        interrupt=switch if isinstance(switch, bool) else defaults.interrupt,
    )


def provider_of(labels: dict[str, str] | None) -> str | None:
    """The provider whose plan a session spends, or ``None`` when it has none here."""
    return PROVIDER_BY_WRAPPER.get(str((labels or {}).get(_WRAPPER_LABEL, "")))


def _when(at: datetime, now: datetime) -> str:
    local = at.astimezone()
    if (local.date() - now.astimezone().date()).days == 0:
        return f"at {local:%H:%M}"
    return f"on {local:%d/%m} at {local:%H:%M}"


def pressure_for(
    labels: dict[str, str] | None,
    *,
    data_dir: pathlib.Path,
    hub_dir: pathlib.Path,
    now: datetime | None = None,
    interrupting: bool = False,
) -> auto_compact.Pressure | None:
    """Return the window a session is past the ceiling of, or ``None``.

    :param labels: The session's labels, which name its harness.
    :param data_dir: Where the plan-limits cache files live.
    :param hub_dir: Where ``budget.json`` lives.
    :param now: Clock seam for tests.
    :param interrupting: Use the higher ceilings at which a working chat is
        interrupted, instead of the ones checked at a turn end.
    :returns: The worst window past its ceiling and not yet over, or ``None``
        when the session has no plan meter, the reading is old, or nothing is
        close.
    """
    provider = provider_of(labels)
    if provider is None:
        return None
    moment = now or datetime.now(UTC)
    try:
        meter = budget.read_meter(provider, data_dir)
    except Exception:  # noqa: BLE001 - no reading just means no handoff
        _logger.debug("handoff: no plan reading for %s", provider, exc_info=True)
        return None
    if meter.as_of is None or (moment - meter.as_of).total_seconds() > MAX_READING_AGE_S:
        return None
    ceilings = load_ceilings(hub_dir)
    five, week = (
        (ceilings.interrupt_five_hour_pct, ceilings.interrupt_weekly_pct)
        if interrupting
        else (ceilings.five_hour_pct, ceilings.weekly_pct)
    )
    found: list[auto_compact.Pressure] = []
    for window, pct, limit, resets_at in (
        ("5h", meter.five_hour, five, meter.five_hour_resets_at),
        ("week", meter.weekly, week, meter.resets_at),
    ):
        # A reading from a window that has already reset describes nothing.
        if pct is None or resets_at is None or resets_at <= moment:
            continue
        if limit <= pct < 100.0:
            found.append(
                auto_compact.Pressure(
                    window=window,
                    pct=pct,
                    limit=limit,
                    key=f"{provider}:{window}:{resets_at.isoformat()}",
                    resets=_when(resets_at, moment),
                )
            )
    return max(found, key=lambda p: p.pct - p.limit, default=None)


def exhausted(
    labels: dict[str, str] | None,
    *,
    data_dir: pathlib.Path,
    now: datetime | None = None,
) -> str | None:
    """Say which window of a session's plan is full, if one is.

    A full window turns the session's requests away, so it can neither write
    anything down nor compact. Anything that would ask it to must not.

    :param labels: The session's labels, which name its harness.
    :param data_dir: Where the plan-limits cache files live.
    :param now: Clock seam for tests.
    :returns: e.g. ``"5h window full until 16:13"``, or ``None`` when no window
        is known to be full (including when there is no reading to go by).
    """
    provider = provider_of(labels)
    if provider is None:
        return None
    moment = now or datetime.now(UTC)
    try:
        meter = budget.read_meter(provider, data_dir)
    except Exception:  # noqa: BLE001 - no reading means not known to be full
        return None
    if meter.as_of is None or (moment - meter.as_of).total_seconds() > MAX_FULL_READING_AGE_S:
        return None
    for window, pct, resets_at in (
        ("5h", meter.five_hour, meter.five_hour_resets_at),
        ("week", meter.weekly, meter.resets_at),
    ):
        if pct is not None and pct >= FULL_PCT and resets_at is not None and resets_at > moment:
            return f"{window} window full until {_when(resets_at, moment).removeprefix('at ')}"
    return None
