"""Each provider's daily budget on its own week, and which provider takes a task."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from omnigent.server import budget
from omnigent.server.budget import BudgetSettings, Meter, ProviderBudget

RESET = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
WEEK_START = RESET - timedelta(days=7)
SETTINGS = BudgetSettings()
DAILY = SETTINGS.daily_pct


def _meter(weekly: float, *, five_hour: float = 10.0, as_of: datetime | None = None) -> Meter:
    return Meter("claude", weekly, five_hour, RESET, as_of or RESET - timedelta(days=2))


def test_a_day_is_counted_from_the_providers_own_reset() -> None:
    now = WEEK_START + timedelta(days=3, hours=5)
    readings = [(WEEK_START + timedelta(days=3, hours=1), 20.0)]

    got = budget.provider_budget(_meter(24.0, as_of=now), readings, SETTINGS, now)

    assert got.day == 3
    # 20% used by today's start against 3 days' worth: the carry is capped at one day.
    assert got.allowed == round(2 * DAILY, 2) and got.spent == 4.0
    assert got.remaining == round(2 * DAILY - 4.0, 2) and got.blocked == ""


def test_a_heavy_week_leaves_less_for_today() -> None:
    now = WEEK_START + timedelta(days=2, hours=2)
    readings = [(WEEK_START + timedelta(days=1, hours=20), 30.0)]

    got = budget.provider_budget(_meter(30.0, as_of=now), readings, SETTINGS, now)

    # By today's start 30% was used against 2 days' worth (24.3%): today pays it back.
    assert got.allowed == round(3 * DAILY - 30.0, 2)


def test_todays_spend_is_the_meter_now_minus_its_reading_at_the_days_start() -> None:
    now = WEEK_START + timedelta(days=1, hours=8)
    readings = [
        (WEEK_START + timedelta(hours=23), 5.0),
        (WEEK_START + timedelta(days=1, hours=2), 9.0),
    ]

    got = budget.provider_budget(_meter(12.0, as_of=now), readings, SETTINGS, now)

    assert got.spent == 7.0


def test_until_its_date_the_whole_leftover_may_be_spent() -> None:
    now = WEEK_START + timedelta(days=5)
    free = BudgetSettings(spend_freely_until={"claude": RESET})

    got = budget.provider_budget(_meter(45.0, as_of=now), [], free, now)

    assert got.remaining == 55.0 and got.free_until is not None


def test_a_full_five_hour_window_or_an_old_reading_blocks_it() -> None:
    now = WEEK_START + timedelta(days=1)
    busy = budget.provider_budget(_meter(10.0, five_hour=90.0, as_of=now), [], SETTINGS, now)
    old = budget.provider_budget(_meter(10.0, as_of=now - timedelta(hours=7)), [], SETTINGS, now)

    assert busy.blocked == "5-hour window at 90%"
    assert old.blocked == "reading is old" and old.stale


def test_a_reading_from_before_the_reset_says_nothing_about_this_week() -> None:
    now = RESET + timedelta(hours=3)

    got = budget.provider_budget(_meter(13.0, as_of=RESET - timedelta(days=6)), [], SETTINGS, now)

    assert got.stale and got.blocked == "no reading since its reset"


def _budget(provider: str, remaining: float, **kw) -> ProviderBudget:
    return ProviderBudget(
        provider=provider,
        week_used=10.0,
        resets_at=None,
        daily=DAILY,
        allowed=remaining,
        remaining=remaining,
        **kw,
    )


def test_the_provider_with_the_most_of_todays_budget_left_takes_the_task() -> None:
    budgets = [_budget("claude", 5.0), _budget("codex", 20.0), _budget("gemini", 30.0)]

    top = budget.pick("top", "high", budgets)
    cheap = budget.pick("economical", "xhigh", budgets)

    # Gemini has no top tier until agy lists Argon.
    assert (top.provider, top.model, top.effort) == ("codex", "Sol", "high")
    assert (cheap.provider, cheap.model, cheap.effort) == ("gemini", "Flash", "high")
    assert budget.pick("top", "high", budgets, agy_models=("gemini-4-argon",)).model == "Argon"
    # Only Claude has a middle tier.
    assert budget.pick("middle", "medium", budgets).provider == "claude"


def test_an_old_meter_is_used_only_when_nothing_current_can_take_it() -> None:
    old = _budget("gemini", 30.0, stale=True, blocked="reading is old")
    spent = _budget("claude", 0.0, blocked="today's budget is spent")
    codex = _budget("codex", 0.0, blocked="today's budget is spent")

    got = budget.pick("economical", "low", [spent, codex, old])
    none = budget.pick("middle", "low", [spent, codex, old])

    assert got.provider == "gemini" and "not current" in got.reason
    assert none.provider == "" and "Claude: today's budget is spent" in none.reason


def test_settings_come_from_the_hubs_budget_file(tmp_path) -> None:
    (tmp_path / budget.SETTINGS_NAME).write_text(
        json.dumps({"buffer_pct": 20, "spend_freely_until": {"claude": "2026-10-16T12:00:00Z"}})
    )

    got = budget.load_settings(tmp_path)

    assert got.buffer_pct == 20.0 and got.daily_pct == 80 / 7
    assert got.spend_freely_until == {"claude": RESET}
    assert budget.load_settings(tmp_path / "missing") == BudgetSettings()


def test_meters_and_history_are_read_from_the_data_dir(tmp_path) -> None:
    (tmp_path / "plan-limits-codex.json").write_text(
        json.dumps(
            {
                "windows": [
                    {"kind": "session", "percent": 4, "resets_at": "2026-10-14T21:00:00Z"},
                    {"kind": "weekly", "percent": 9, "resets_at": "2026-10-20T15:00:00Z"},
                ],
                "as_of": "2026-10-14T16:00:00Z",
            }
        )
    )
    rows = [
        {
            "at": "2026-10-14T10:00:00Z",
            "kind": "plan_limits",
            "provider": "codex",
            "state": "ok",
            "windows": {"weekly": 7},
        },
        {
            "at": "2026-10-14T11:00:00Z",
            "kind": "plan_limits",
            "provider": "codex",
            "state": "stale",
            "windows": {"weekly": 99},
        },
    ]
    (tmp_path / "usage-history.jsonl").write_text("\n".join(json.dumps(r) for r in rows))

    meter = budget.read_meter("codex", tmp_path)
    readings = budget.weekly_readings(tmp_path)

    assert (meter.weekly, meter.five_hour) == (9.0, 4.0)
    # A stale replay is not a reading.
    assert readings["codex"] == [(datetime(2026, 10, 14, 10, tzinfo=UTC), 7.0)]
