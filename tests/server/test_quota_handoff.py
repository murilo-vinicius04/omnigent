"""Whether a session's plan window is close enough to its end to write it up."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from omnigent.server import quota_handoff

NOW = datetime(2026, 10, 16, 15, 0, tzinfo=UTC)
CODEX = {"omnigent.wrapper": "codex-native-ui"}
CLAUDE = {"omnigent.wrapper": "claude-code-native-ui"}


def _meter(tmp_path, name: str, five: float, week: float, *, age_s: float = 60.0) -> None:
    (tmp_path / name).write_text(
        json.dumps(
            {
                "windows": [
                    {
                        "kind": "session",
                        "percent": five,
                        "resets_at": (NOW + timedelta(hours=1)).isoformat(),
                    },
                    {
                        "kind": "weekly",
                        "percent": week,
                        "resets_at": (NOW + timedelta(days=5)).isoformat(),
                    },
                ],
                "as_of": (NOW - timedelta(seconds=age_s)).isoformat(),
            }
        )
    )


def _pressure(tmp_path, labels=CODEX):
    return quota_handoff.pressure_for(labels, data_dir=tmp_path, hub_dir=tmp_path, now=NOW)


def test_a_5h_window_past_the_default_ceiling_is_pressure(tmp_path) -> None:
    _meter(tmp_path, "plan-limits-codex.json", 86, 27)
    found = _pressure(tmp_path)
    assert found is not None and (found.window, found.pct, found.limit) == ("5h", 86, 80.0)
    assert found.key.startswith("codex:5h:")


def test_a_window_below_its_ceiling_is_not(tmp_path) -> None:
    _meter(tmp_path, "plan-limits-codex.json", 79, 90)
    assert _pressure(tmp_path) is None


def test_the_week_has_its_own_higher_ceiling(tmp_path) -> None:
    _meter(tmp_path, "plan-limits-codex.json", 10, 96)
    found = _pressure(tmp_path)
    assert found is not None and (found.window, found.limit) == ("week", 95.0)


def test_a_full_window_is_not_pressure_because_the_session_cannot_answer(tmp_path) -> None:
    _meter(tmp_path, "plan-limits-codex.json", 100, 27)
    assert _pressure(tmp_path) is None


def test_an_old_reading_says_nothing_about_now(tmp_path) -> None:
    _meter(tmp_path, "plan-limits-codex.json", 95, 27, age_s=3600)
    assert _pressure(tmp_path) is None


def test_a_reading_from_a_window_that_already_reset_is_ignored(tmp_path) -> None:
    _meter(tmp_path, "plan-limits-codex.json", 95, 27)
    later = NOW + timedelta(hours=2)
    assert (
        quota_handoff.pressure_for(CODEX, data_dir=tmp_path, hub_dir=tmp_path, now=later) is None
    )


def test_each_provider_reads_its_own_meter(tmp_path) -> None:
    _meter(tmp_path, "plan-limits-codex.json", 95, 27)
    _meter(tmp_path, "plan-limits-claude.json", 10, 3)
    assert _pressure(tmp_path, CODEX) is not None
    assert _pressure(tmp_path, CLAUDE) is None


def test_a_session_without_a_plan_meter_is_left_alone(tmp_path) -> None:
    _meter(tmp_path, "plan-limits-codex.json", 95, 27)
    assert _pressure(tmp_path, {"omnigent.wrapper": "cursor-native-ui"}) is None
    assert _pressure(tmp_path, {}) is None


def test_ceilings_can_be_set_in_the_budget_file(tmp_path) -> None:
    _meter(tmp_path, "plan-limits-codex.json", 70, 27)
    (tmp_path / "budget.json").write_text(json.dumps({"handoff": {"five_hour_pct": 65}}))
    found = _pressure(tmp_path)
    assert found is not None and found.limit == 65.0
    (tmp_path / "budget.json").write_text(json.dumps({"handoff": {"five_hour_pct": "x"}}))
    assert _pressure(tmp_path) is None  # nonsense falls back to 80


def _full(tmp_path, labels=CODEX, **kwargs):
    return quota_handoff.exhausted(labels, data_dir=tmp_path, now=NOW, **kwargs)


def test_a_full_5h_window_is_reported_with_when_it_ends(tmp_path) -> None:
    _meter(tmp_path, "plan-limits-codex.json", 100, 27)
    found = _full(tmp_path)
    assert found is not None and found.startswith("5h window full until ")


def test_a_full_week_is_reported_too(tmp_path) -> None:
    _meter(tmp_path, "plan-limits-codex.json", 10, 100)
    found = _full(tmp_path)
    assert found is not None and found.startswith("week window full until ")


def test_a_window_that_is_not_full_is_not_reported(tmp_path) -> None:
    _meter(tmp_path, "plan-limits-codex.json", 99, 99)
    assert _full(tmp_path) is None


def test_a_full_window_that_already_reset_is_not_reported(tmp_path) -> None:
    _meter(tmp_path, "plan-limits-codex.json", 100, 27)
    later = NOW + timedelta(hours=2)
    assert quota_handoff.exhausted(CODEX, data_dir=tmp_path, now=later) is None


def test_an_hour_old_full_reading_still_counts_but_a_day_old_one_does_not(tmp_path) -> None:
    _meter(tmp_path, "plan-limits-codex.json", 100, 27, age_s=1800)
    assert _full(tmp_path) is not None
    _meter(tmp_path, "plan-limits-codex.json", 100, 27, age_s=86400)
    assert _full(tmp_path) is None


def test_no_meter_or_no_plan_means_not_known_to_be_full(tmp_path) -> None:
    assert _full(tmp_path) is None
    _meter(tmp_path, "plan-limits-codex.json", 100, 27)
    assert _full(tmp_path, {"omnigent.wrapper": "cursor-native-ui"}) is None


def test_the_interrupt_ceilings_sit_above_the_turn_end_ones_by_default(tmp_path) -> None:
    ceilings = quota_handoff.load_ceilings(tmp_path)
    assert (ceilings.interrupt_five_hour_pct, ceilings.interrupt_weekly_pct) == (95.0, 98.0)
    assert ceilings.interrupt is True
    assert ceilings.interrupt_five_hour_pct > ceilings.five_hour_pct


def test_interrupting_uses_the_higher_ceiling(tmp_path) -> None:
    _meter(tmp_path, "plan-limits-codex.json", 90, 27)
    assert _pressure(tmp_path) is not None  # past the turn-end ceiling
    found = quota_handoff.pressure_for(
        CODEX, data_dir=tmp_path, hub_dir=tmp_path, now=NOW, interrupting=True
    )
    assert found is None  # but not past the interrupt one
    _meter(tmp_path, "plan-limits-codex.json", 96, 27)
    found = quota_handoff.pressure_for(
        CODEX, data_dir=tmp_path, hub_dir=tmp_path, now=NOW, interrupting=True
    )
    assert found is not None and found.limit == 95.0


def test_the_interrupt_settings_can_be_changed_and_ignore_nonsense(tmp_path) -> None:
    (tmp_path / "budget.json").write_text(
        json.dumps({"handoff": {"interrupt_five_hour_pct": 99, "interrupt": "yes"}})
    )
    ceilings = quota_handoff.load_ceilings(tmp_path)
    assert ceilings.interrupt_five_hour_pct == 99.0 and ceilings.interrupt is True
