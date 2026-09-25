"""What a Max plan's readings would be on Pro."""

from __future__ import annotations

import pytest

from omnigent import pro_equivalent as pro


def test_plan_multiplier_knows_pro_and_both_max_tiers() -> None:
    assert pro.plan_multiplier(None, "pro") == 1
    assert pro.plan_multiplier("default_claude_max_5x", "max") == 5
    assert pro.plan_multiplier("default_claude_max_20x", "max") == 20
    # A plan whose relation to Pro is unknown must not get a guessed multiple.
    assert pro.plan_multiplier("default_claude_team_premium", "team") is None
    assert pro.plan_multiplier(None, None) is None


def test_max_readings_scale_to_pro_without_clipping() -> None:
    out = pro.pro_equivalent(
        [
            {"kind": "session", "label": "5h", "percent": 20},
            {"kind": "weekly", "label": "week", "percent": 30},
        ],
        5,
    )

    assert out is not None
    assert out["multiplier"] == 5
    windows = {w["kind"]: w for w in out["windows"]}
    assert windows["session"]["used_pct"] == pytest.approx(100)
    # 150% of Pro: the answer to "would it have fit", so it must not be capped.
    assert windows["weekly"]["used_pct"] == pytest.approx(150)
    # [fairness] the measured budgets are estimates; any figure within 5% passes.
    assert windows["session"]["weighted_token_budget"] == pytest.approx(5_500_000, rel=0.05)
    assert windows["weekly"]["weighted_token_budget"] == pytest.approx(40_000_000, rel=0.05)
    assert windows["session"]["weighted_tokens_used"] == pytest.approx(
        windows["session"]["weighted_token_budget"], rel=0.01
    )
    assert windows["weekly"]["weighted_tokens_used"] == pytest.approx(
        1.5 * windows["weekly"]["weighted_token_budget"], rel=0.01
    )
