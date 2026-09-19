"""The Pro-equivalent readout: a Max reading scaled to what it would be on Pro."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnigent import pro_equivalent
from omnigent.server.routes import plan_limits


def test_max_5x_reading_scales_to_five_pro_allowances() -> None:
    """20% of a Max 5x 5-hour window is a full Pro window."""
    windows = [
        {"kind": "session", "label": "5h", "percent": 20.0},
        {"kind": "weekly", "label": "week", "percent": 3.0},
    ]
    out = pro_equivalent.pro_equivalent(windows, 5)
    assert out is not None
    by_kind = {w["kind"]: w for w in out["windows"]}
    assert by_kind["session"]["used_pct"] == 100.0
    assert by_kind["session"]["weighted_tokens_used"] == 5_500_000
    assert by_kind["weekly"]["used_pct"] == 15.0
    assert by_kind["weekly"]["weighted_tokens_used"] == 6_000_000


def test_over_the_pro_limit_is_shown_not_clipped() -> None:
    """A session Pro could not have held reads above 100%, so the overrun shows."""
    out = pro_equivalent.pro_equivalent([{"kind": "session", "percent": 60}], 5)
    assert out is not None
    assert out["windows"][0]["used_pct"] == 300.0


@pytest.mark.parametrize(
    ("tier", "sub", "expected"),
    [
        ("default_claude_max_5x", "max", 5),
        ("default_claude_max_20x", "max", 20),
        (None, "pro", 1),
        ("some_future_tier", "max", None),
        (None, None, None),
    ],
)
def test_plan_multiplier(tier: str | None, sub: str | None, expected: int | None) -> None:
    """Unknown tiers give no multiple rather than a guessed one."""
    assert pro_equivalent.plan_multiplier(tier, sub) == expected


def test_windows_without_a_reading_are_skipped() -> None:
    """A window with no percent, or one this table has no budget for, is left out."""
    assert pro_equivalent.pro_equivalent([{"kind": "session"}], 5) is None
    assert pro_equivalent.pro_equivalent([{"kind": "daily", "percent": 10}], 5) is None


def test_route_attaches_pro_equivalent_from_the_credential_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Claude row carries the readout when the signed-in plan's tier is known."""
    creds = tmp_path / "creds.json"
    creds.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "rateLimitTier": "default_claude_max_5x",
                    "subscriptionType": "max",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(plan_limits, "CLAUDE_CREDENTIALS_PATH", creds)
    row = {"id": "claude", "windows": [{"kind": "session", "label": "5h", "percent": 10}]}
    out = plan_limits._with_pro_equivalent(row)
    assert out["pro_equivalent"]["multiplier"] == 5
    assert out["pro_equivalent"]["windows"][0]["used_pct"] == 50.0
    # The original row is left untouched.
    assert "pro_equivalent" not in row


def test_route_leaves_the_row_alone_when_the_tier_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No credential file means no readout, not an error."""
    monkeypatch.setattr(plan_limits, "CLAUDE_CREDENTIALS_PATH", tmp_path / "missing.json")
    row = {"id": "claude", "windows": [{"kind": "session", "percent": 10}]}
    assert plan_limits._with_pro_equivalent(row) is row
