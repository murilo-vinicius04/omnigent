"""Each provider's daily budget on its own week, and which provider takes a task."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

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

    # Argon is explicit-only: Flash's meter must never authorize its separate quota.
    assert (top.provider, top.model, top.effort) == ("codex", "Sol", "high")
    assert (cheap.provider, cheap.model, cheap.effort) == ("gemini", "Flash", "high")
    assert budget.pick("top", "high", budgets, agy_models=("gemini-4-argon",)).model == "Sol"
    assert "top" not in budget.offered_tiers("gemini", ("gemini-4-argon-high",))
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


def test_a_mocked_smaller_plan_counts_from_its_start_at_the_plans_multiple() -> None:
    since = WEEK_START + timedelta(days=5, hours=9)
    five_reset = since + timedelta(hours=2)
    mock = budget.PlanMock(
        since=since,
        multiplier=5,
        weekly_start=52,
        weekly_at_start=52,
        weekly_resets_at=RESET,
        five_hour_at_start=36,
        five_hour_resets_at=five_reset,
    )
    settings = BudgetSettings(spend_freely_until={"claude": RESET}, plan_mocks={"claude": mock})
    now = since + timedelta(hours=1)
    # The meter's reset times jitter by fractions of a second between readings.
    jitter = timedelta(microseconds=15180)
    meter = Meter("claude", 54.0, 40.0, RESET + jitter, now, five_reset - jitter)
    readings = [(since - timedelta(hours=3), 50.0), (since + timedelta(minutes=30), 53.0)]

    got = budget.provider_budget(meter, readings, settings, now)

    # Two more Max points are ten Pro points; the 5-hour window grows the same way.
    assert got.week_used == 62.0 and got.five_hour == 56.0
    # Paced like the smaller plan: the leftover is not spent freely, and today
    # counts only what was used since the mock began.
    assert got.free_until is None and got.spent == 10.0
    # A new 5-hour window and a new week are the plain multiple.
    later = Meter(
        "claude", 3.0, 10.0, RESET + timedelta(days=7), now, five_reset + timedelta(hours=5)
    )
    after = budget.provider_budget(later, [], settings, RESET + timedelta(hours=1))
    assert (after.week_used, after.five_hour) == (15.0, 50.0)


def test_a_busy_five_hour_window_loses_to_a_quieter_provider() -> None:
    claude = _budget("claude", 30.0, five_hour=70.0)  # lots of week left, window nearly full
    codex = _budget("codex", 8.0, five_hour=0.0)  # less of the week, an empty window

    got = budget.pick("top", "high", [claude, codex])

    assert claude.capacity < codex.capacity
    assert got.provider == "codex" and "5-hour window 0% used" in got.reason


def test_local_one_am_renews_hub_day_without_resetting_subscription() -> None:
    settings = BudgetSettings(daily_reset_hour=1, daily_timezone="America/Sao_Paulo")
    reset = datetime(2026, 10, 22, 20, 22, 43, tzinfo=UTC)
    before = datetime(2026, 10, 16, 3, 59, tzinfo=UTC)
    after = before + timedelta(minutes=1)
    readings = [(reset - timedelta(days=7) + timedelta(minutes=1), 1), (before, 14)]
    old = budget.provider_budget(Meter("codex", 14, 21, reset, before), readings, settings, before)
    new = budget.provider_budget(Meter("codex", 14, 21, reset, after), readings, settings, after)
    assert old.blocked == "today's budget is spent"
    assert new.blocked == "" and new.remaining == 10.29
    assert new.spent == 0 and new.week_used == 14 and new.five_hour == 21
    assert new.daily_resets_at == "2026-10-17T04:00:00Z"
    assert new.resets_at == "2026-10-22T20:22:43Z"


def test_local_day_still_respects_the_weekly_reserve_and_five_hour_limit() -> None:
    now = RESET - timedelta(hours=4)
    settings = BudgetSettings(daily_reset_hour=1)
    readings = [(now - timedelta(hours=1), 84)]
    got = budget.provider_budget(_meter(84, as_of=now), readings, settings, now)
    assert got.remaining == 1 and got.allowed == 1
    full = budget.provider_budget(_meter(85, as_of=now), readings, settings, now)
    assert full.remaining <= 0 and full.blocked
    busy = budget.provider_budget(_meter(10, five_hour=90, as_of=now), [], settings, now)
    assert busy.blocked == "5-hour window at 90%"


def test_provider_week_reset_mid_day_does_not_inherit_last_weeks_spend() -> None:
    now = RESET + timedelta(hours=2)
    settings = BudgetSettings(daily_reset_hour=1)
    meter = Meter("claude", 2, 5, RESET + timedelta(days=7), now)
    got = budget.provider_budget(
        meter, [(RESET - timedelta(minutes=1), 85), (RESET, 0)], settings, now
    )
    assert got.day == 0 and got.spent == 2 and got.remaining == 10.14
    assert got.daily_started_at == "2026-10-16T12:00:00Z"
    assert got.daily_resets_at == "2026-10-17T04:00:00Z"


@pytest.mark.parametrize("hour,zone", [(25, "UTC"), (True, "UTC"), (1, "invalid-zone")])
def test_invalid_daily_clock_keeps_legacy_boundaries(tmp_path, hour, zone) -> None:
    (tmp_path / budget.SETTINGS_NAME).write_text(
        json.dumps({"daily_reset_hour": hour, "daily_timezone": zone})
    )
    assert budget.load_settings(tmp_path).daily_reset_hour is None


def test_subscription_budget_ignores_api_pool_ledger_and_clock(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path))
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / "budget.json").write_text(
        json.dumps({"daily_reset_hour": 1, "daily_timezone": "America/Sao_Paulo"})
    )
    now = datetime(2026, 10, 16, 15, tzinfo=UTC)
    (tmp_path / "plan-limits-codex.json").write_text(
        json.dumps(
            {
                "as_of": now.isoformat(),
                "windows": [
                    {"kind": "weekly", "percent": 14, "resets_at": "2026-10-22T20:22:43Z"},
                    {"kind": "session", "percent": 21, "resets_at": "2026-10-16T19:13:05Z"},
                ],
            }
        )
    )
    original = budget.today(hub, now=now)
    (tmp_path / "openai-token-ledger.json").write_text('{"tokens":999999999999,"reset":"00:00Z"}')
    (tmp_path / "plan-limits-openai.json").write_text(
        '{"windows":[{"kind":"daily","percent":100}]}'
    )
    assert budget.today(hub, now=now) == original
    assert [row.provider for row in original] == ["claude", "codex", "gemini", "muse"]


def test_avoid_models_excludes_sol_and_argon_and_prioritizes_luna_and_flash(tmp_path) -> None:
    settings_data = {
        "avoid_models": ["Argon", "Sol"],
        "priority_models": ["Luna", "Flash"],
    }
    (tmp_path / budget.SETTINGS_NAME).write_text(json.dumps(settings_data))
    settings = budget.load_settings(tmp_path)
    assert settings.avoid_models == ("Argon", "Sol")
    assert settings.priority_models == ("Luna", "Flash")

    # Sol and Argon excluded from offered tiers
    codex_tiers = budget.offered_tiers("codex", avoid_models=settings.avoid_models)
    assert "top" not in codex_tiers
    assert codex_tiers == {"economical": "Luna"}

    gemini_tiers = budget.offered_tiers("gemini", avoid_models=settings.avoid_models)
    assert "top" not in gemini_tiers
    assert gemini_tiers == {"economical": "Flash"}

    budgets = [
        _budget("claude", 50.0, five_hour=2.0),
        _budget("codex", 10.0, five_hour=2.0),
        _budget("gemini", 15.0, five_hour=2.0),
    ]

    # Top tier must pick Claude Opus since Sol and Argon are avoided
    top = budget.pick("top", "high", budgets, avoid_models=settings.avoid_models)
    assert top.provider == "claude" and top.model == "Opus"

    # Economical tier must prioritize Luna and Flash over Claude Haiku
    # even with Claude having more remaining budget
    cheap = budget.pick(
        "economical",
        "medium",
        budgets,
        avoid_models=settings.avoid_models,
        priority_models=settings.priority_models,
    )
    assert cheap.provider == "gemini" and cheap.model == "Flash"


def test_muse_joins_the_quota_rules_with_spark_preferred() -> None:
    assert budget.METERS["muse"] == ("plan-limits-muse.json", "muse", "weekly", "session")
    assert budget.offered_tiers("muse") == {
        "top": "Wonder",
        "middle": "Spark",
        "economical": "Spark",
    }
    assert budget.fit_effort("muse", "xhigh") == "xhigh"
    assert budget.PROVIDER_LABELS["muse"] == "Muse"


def test_a_spent_provider_fails_over_to_muse_spark() -> None:
    budgets = [
        _budget("claude", 0.0, five_hour=100.0, blocked="5-hour window at 100%"),
        _budget("muse", 10.0, five_hour=3.0),
    ]
    proposal = budget.failover_proposal(
        "claude", "claude-sonnet-5-5", "xhigh", budgets, priority_models=("Spark",)
    )
    assert proposal is not None
    assert (proposal.provider, proposal.model, proposal.effort) == ("muse", "Spark", "xhigh")


def test_failover_has_nowhere_to_go_when_every_provider_is_spent() -> None:
    budgets = [
        _budget("claude", 0.0, blocked="today's budget is spent"),
        _budget("muse", 0.0, blocked="today's budget is spent"),
    ]
    assert budget.failover_proposal("claude", "opus", "high", budgets) is None
