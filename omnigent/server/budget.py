"""Each provider's budget for today, and which provider takes a task: plain arithmetic.

Each provider's week starts at its own reset, so "day 3" is three days after
that provider's reset, not a calendar day. The weekly meter is spread evenly
over the week, keeping a buffer for the end: a day's budget is
``(100% - buffer) / 7`` of the week. A quiet day leaves room for the next and
a heavy day takes it from the next, with the carry-over capped. A provider
near the top of its 5-hour window waits. Until a date set per provider, the
whole leftover of the week may be spent (Claude's Max week before it drops to
Pro), since unused quota is lost at the reset.

The tier and effort come from the decision model (``model_router``); this
module only picks which provider runs them: the one with the most of today's
budget left, among those that have that tier.
"""

from __future__ import annotations

import json
import logging
import pathlib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from omnigent.install_ledger import state_dir

_logger = logging.getLogger(__name__)

WEEK: Final[timedelta] = timedelta(days=7)
DAY: Final[timedelta] = timedelta(days=1)

#: Settings beside the hub's logs; every key is optional.
SETTINGS_NAME: Final[str] = "budget.json"

#: Each provider's plan meter: the cache file the plan-limits route writes,
#: its name in the usage history, and its weekly and 5-hour window kinds.
METERS: Final[dict[str, tuple[str, str, str, str]]] = {
    "claude": ("plan-limits-claude.json", "claude", "weekly", "session"),
    "codex": ("plan-limits-codex.json", "codex", "weekly", "session"),
    "gemini": ("plan-limits-antigravity.json", "antigravity", "gemini-weekly", "gemini-5h"),
}

#: What each provider runs at each tier, as words its model picker matches.
#: Google's top tier is offered only while ``agy models`` lists Argon.
TIER_MODELS: Final[dict[str, dict[str, str]]] = {
    "claude": {"top": "Opus", "middle": "Sonnet", "economical": "Haiku"},
    "codex": {"top": "Sol", "economical": "Luna"},
    "gemini": {"top": "Argon", "economical": "Flash"},
}

#: Efforts each provider takes, lowest first; Gemini through agy stops at high.
PROVIDER_EFFORTS: Final[dict[str, tuple[str, ...]]] = {
    "claude": ("low", "medium", "high", "xhigh", "max"),
    "codex": ("low", "medium", "high", "xhigh"),
    "gemini": ("low", "medium", "high"),
}

PROVIDER_LABELS: Final[dict[str, str]] = {"claude": "Claude", "codex": "Codex", "gemini": "Gemini"}


@dataclass(frozen=True)
class BudgetSettings:
    """The governor's knobs, from ``<hub dir>/budget.json`` when it has them."""

    buffer_pct: float = 15.0
    carry_days: float = 1.0
    five_hour_guard_pct: float = 85.0
    #: A reading older than this is not trusted to decide anything.
    stale_after_s: float = 6 * 3600.0
    #: Provider -> the instant up to which its week's whole leftover may be spent.
    spend_freely_until: dict[str, datetime] = field(default_factory=dict)

    @property
    def daily_pct(self) -> float:
        return (100.0 - self.buffer_pct) / 7.0


def _parse_time(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def load_settings(hub_dir: pathlib.Path) -> BudgetSettings:
    """The settings file's values over the defaults; a broken file is ignored."""
    try:
        raw = json.loads((hub_dir / SETTINGS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return BudgetSettings()
    if not isinstance(raw, dict):
        return BudgetSettings()
    defaults = BudgetSettings()
    numbers = {}
    for key in ("buffer_pct", "carry_days", "five_hour_guard_pct", "stale_after_s"):
        value = raw.get(key)
        if isinstance(value, int | float) and value >= 0:
            numbers[key] = float(value)
    free = {
        str(provider): when
        for provider, stamp in (raw.get("spend_freely_until") or {}).items()
        if (when := _parse_time(stamp)) is not None
    }
    return BudgetSettings(**{**asdict(defaults), **numbers, "spend_freely_until": free})


@dataclass(frozen=True)
class Meter:
    """A provider's latest plan reading, percentages of the plan used."""

    provider: str
    weekly: float | None = None
    five_hour: float | None = None
    resets_at: datetime | None = None
    as_of: datetime | None = None


def read_meter(provider: str, data_dir: pathlib.Path) -> Meter:
    """The latest reading the plan-limits route cached for one provider."""
    cache, _history, weekly_kind, five_hour_kind = METERS[provider]
    try:
        blob = json.loads((data_dir / cache).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Meter(provider)
    weekly = five_hour = None
    resets_at = None
    for window in blob.get("windows") or []:
        if not isinstance(window, dict):
            continue
        if window.get("kind") == weekly_kind:
            weekly = float(window.get("percent") or 0)
            resets_at = _parse_time(window.get("resets_at"))
        elif window.get("kind") == five_hour_kind:
            five_hour = float(window.get("percent") or 0)
    return Meter(provider, weekly, five_hour, resets_at, _parse_time(blob.get("as_of")))


_history_cache: dict[str, Any] = {}


def weekly_readings(data_dir: pathlib.Path) -> dict[str, list[tuple[datetime, float]]]:
    """Every provider's weekly readings from the usage history, oldest first."""
    path = data_dir / "usage-history.jsonl"
    try:
        stat = path.stat()
    except OSError:
        return {}
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if _history_cache.get("key") == key:
        return _history_cache["readings"]
    by_name = {history: provider for provider, (_c, history, _w, _f) in METERS.items()}
    readings: dict[str, list[tuple[datetime, float]]] = {p: [] for p in METERS}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if '"plan_limits"' not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            provider = by_name.get(str(row.get("provider")))
            if provider is None or row.get("state") == "stale":
                continue
            weekly = (row.get("windows") or {}).get(METERS[provider][2])
            at = _parse_time(row.get("at"))
            if isinstance(weekly, int | float) and at is not None:
                readings[provider].append((at, float(weekly)))
    _history_cache.update(key=key, readings=readings)
    return readings


@dataclass(frozen=True)
class ProviderBudget:
    """One provider's budget today, in percent of its week.

    :param day: Days since its week began, 0 to 6.
    :param allowed: What today may spend, carry-over included.
    :param spent: What today has spent so far.
    :param free_until: Set while its whole leftover may be spent.
    :param blocked: Why it cannot take work now; empty when it can.
    """

    provider: str
    week_used: float | None
    resets_at: str | None
    day: int = 0
    daily: float = 0.0
    allowed: float = 0.0
    spent: float = 0.0
    remaining: float = 0.0
    five_hour: float | None = None
    stale: bool = False
    free_until: str | None = None
    blocked: str = ""

    @property
    def headroom(self) -> float:
        """Today's remaining budget in days of budget, to compare providers."""
        return self.remaining / self.daily if self.daily > 0 else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "label": PROVIDER_LABELS.get(self.provider, self.provider)}


def _iso(when: datetime | None) -> str | None:
    return when.astimezone(UTC).isoformat().replace("+00:00", "Z") if when else None


def provider_budget(
    meter: Meter,
    readings: list[tuple[datetime, float]],
    settings: BudgetSettings,
    now: datetime,
) -> ProviderBudget:
    """Today's budget for one provider from its meter and its readings this week."""
    daily = settings.daily_pct
    base = {
        "provider": meter.provider,
        "week_used": meter.weekly,
        "resets_at": _iso(meter.resets_at),
        "daily": round(daily, 2),
        "five_hour": meter.five_hour,
    }
    if meter.weekly is None or meter.resets_at is None or meter.as_of is None:
        return ProviderBudget(**base, stale=True, blocked="no reading")
    if now >= meter.resets_at:
        # The reading is from a week that has ended; this week's is unknown.
        return ProviderBudget(**base, stale=True, blocked="no reading since its reset")
    stale = (now - meter.as_of).total_seconds() > settings.stale_after_s
    week_start = meter.resets_at - WEEK
    day = min(6, max(0, int((now - week_start) / DAY)))
    day_start = week_start + day * DAY
    this_week = [(at, pct) for at, pct in readings if week_start <= at <= now]
    before = [pct for at, pct in this_week if at <= day_start]
    after = [pct for at, pct in this_week if at > day_start]
    at_day_start = before[-1] if before else (after[0] if after else meter.weekly)
    spent = max(0.0, meter.weekly - at_day_start)
    free = settings.spend_freely_until.get(meter.provider)
    if free is not None and now < free:
        remaining = max(0.0, 100.0 - meter.weekly)
        allowed = spent + remaining
        free_until = _iso(free)
    else:
        carry = min(day * daily - at_day_start, settings.carry_days * daily)
        allowed = max(0.0, min(daily + carry, 100.0 - settings.buffer_pct - at_day_start))
        remaining = allowed - spent
        free_until = None
    blocked = ""
    if stale:
        blocked = "reading is old"
    elif meter.five_hour is not None and meter.five_hour >= settings.five_hour_guard_pct:
        blocked = f"5-hour window at {meter.five_hour:.0f}%"
    elif remaining <= 0:
        blocked = "today's budget is spent"
    return ProviderBudget(
        **base,
        day=day,
        allowed=round(allowed, 2),
        spent=round(spent, 2),
        remaining=round(remaining, 2),
        stale=stale,
        free_until=free_until,
        blocked=blocked,
    )


def today(hub_dir: pathlib.Path, *, now: datetime | None = None) -> list[ProviderBudget]:
    """Every provider's budget right now."""
    data_dir = state_dir()
    settings = load_settings(hub_dir)
    readings = weekly_readings(data_dir)
    moment = now or datetime.now(UTC)
    return [
        provider_budget(read_meter(p, data_dir), readings.get(p, []), settings, moment)
        for p in METERS
    ]


@dataclass(frozen=True)
class Proposal:
    """Which provider, model and effort the rules pick for a tier and effort."""

    provider: str = ""
    model: str = ""
    effort: str = ""
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def offered_tiers(provider: str, agy_models: tuple[str, ...] = ()) -> dict[str, str]:
    """The tiers a provider has now; Google's top only while agy lists Argon."""
    tiers = dict(TIER_MODELS.get(provider, {}))
    if provider == "gemini" and not any("argon" in m.lower() for m in agy_models):
        tiers.pop("top", None)
    return tiers


def fit_effort(provider: str, effort: str) -> str:
    """The effort, or the provider's nearest one below it when it lacks it."""
    efforts = PROVIDER_EFFORTS.get(provider, ())
    if not effort or effort in efforts:
        return effort
    order = ("low", "medium", "high", "xhigh", "max")
    wanted = order.index(effort) if effort in order else len(order)
    lower = [e for e in efforts if e in order and order.index(e) <= wanted]
    return lower[-1] if lower else (efforts[0] if efforts else effort)


def pick(
    tier: str,
    effort: str,
    budgets: list[ProviderBudget],
    *,
    agy_models: tuple[str, ...] = (),
) -> Proposal:
    """The provider with the most of today's budget left among those with this tier.

    A provider whose reading is old is used only when no provider with a
    current reading can take the task.
    """
    having = [b for b in budgets if tier in offered_tiers(b.provider, agy_models)]
    if not having:
        return Proposal(reason=f"no provider has the {tier} tier")
    ready = [b for b in having if not b.blocked]
    if ready:
        best = max(ready, key=lambda b: b.headroom)
        why = (
            "spending the week's leftover before its reset"
            if best.free_until
            else f"{best.remaining:.1f}% of its week left today"
        )
    else:
        old = [b for b in having if b.blocked in ("reading is old", "no reading since its reset")]
        if not old:
            reasons = "; ".join(f"{PROVIDER_LABELS[b.provider]}: {b.blocked}" for b in having)
            return Proposal(reason=f"no provider can take it now ({reasons})")
        best = old[0]
        why = "the only one with this tier left, though its meter is not current"
    label = PROVIDER_LABELS.get(best.provider, best.provider)
    return Proposal(
        provider=best.provider,
        model=offered_tiers(best.provider, agy_models)[tier],
        effort=fit_effort(best.provider, effort),
        reason=f"{label}: {why}",
    )


def snapshot(budgets: list[ProviderBudget]) -> dict[str, dict[str, Any]]:
    """The few numbers the router log keeps of each provider's budget."""
    return {
        b.provider: {
            "remaining": b.remaining,
            "allowed": b.allowed,
            "spent": b.spent,
            "day": b.day,
            **({"blocked": b.blocked} if b.blocked else {}),
        }
        for b in budgets
    }
