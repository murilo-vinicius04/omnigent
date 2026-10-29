from datetime import UTC, datetime, timedelta

import pytest

from omnigent.claude_pro_transition import plan_metadata, prepare

NOW = datetime(2026, 10, 16, 15, tzinfo=UTC)


def settings():
    return {
        "buffer_pct": 15,
        "five_hour_guard_pct": 85,
        "carry_days": 1,
        "spend_freely_until": {"claude": "2099-01-01T00:00:00Z", "codex": "kept"},
        "plan_mocks": {"claude": {"multiplier": 5}, "gemini": {"multiplier": 2}},
    }


def meter(at):
    return {
        "as_of": at.isoformat(),
        "windows": [
            {"kind": kind, "percent": 5, "resets_at": (NOW + timedelta(days=1)).isoformat()}
            for kind in ("session", "weekly")
        ],
    }


def test_max_keeps_conservative_simulation_and_removes_only_claude_exception():
    config = settings()
    updated, state = prepare(config, {"subscriptionType": "max"}, {}, {}, NOW)
    assert state["phase"] == "waiting_for_pro"
    assert updated["plan_mocks"] == config["plan_mocks"]
    assert updated["spend_freely_until"] == {"codex": "kept"}
    assert "claude" in config["spend_freely_until"]


@pytest.mark.parametrize(
    "sample", [meter(NOW - timedelta(seconds=1)), {}, meter(NOW + timedelta(seconds=1))]
)
def test_first_pro_detection_waits_for_a_meter_after_detection(sample):
    updated, state = prepare(settings(), {"subscriptionType": "pro"}, sample, {}, NOW)
    assert state["phase"] == "pro_waiting_for_fresh_meter"
    assert updated["plan_mocks"]["claude"]["multiplier"] == 5


def test_new_pro_meter_removes_fivefold_scaling_without_touching_other_providers():
    updated, state = prepare(
        settings(),
        {"subscriptionType": "pro"},
        meter(NOW),
        {"pro_observed_at": (NOW - timedelta(minutes=1)).isoformat()},
        NOW,
    )
    assert state["phase"] == "pro_active"
    assert state["weekly_percent"] == 5
    assert updated["plan_mocks"] == {"gemini": {"multiplier": 2}}
    assert state["daily_budget_pct"] == 85 / 7


@pytest.mark.parametrize("kind", ["session", "weekly"])
def test_incomplete_or_expired_windows_do_not_activate_pro(kind):
    sample = meter(NOW)
    next(row for row in sample["windows"] if row["kind"] == kind)["resets_at"] = NOW.isoformat()
    _, state = prepare(settings(), {"subscriptionType": "pro"}, sample, {}, NOW)
    assert state["phase"] == "pro_waiting_for_fresh_meter"


def test_metadata_never_returns_authentication(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text('{"claudeAiOauth":{"subscriptionType":"pro","accessToken":"SECRET"}}')
    assert plan_metadata(path) == {"subscriptionType": "pro", "rateLimitTier": None}
    path.write_text("malformed")
    assert plan_metadata(path) == {"subscriptionType": None, "rateLimitTier": None}
