"""What the current Claude usage would be on the Pro plan.

The composer tray shows how full the plan you are on is. When that plan is Max,
the question it cannot answer is the one that decides whether you can go back
to Pro: would this session have fit there? Max plans are sold as a fixed
multiple of Pro -- Max 5x is five Pro allowances, Max 20x twenty -- for both
the 5-hour and the weekly window, so a Max reading scales straight to Pro.

The token figures put that percentage into a unit you can reason about. The
limiter does not count raw tokens equally: a cache read costs a tenth of fresh
input, output five times it. So the budgets are in *weighted* tokens -- fresh
input x1, output x5, cache read x0.1, cache write x1.25, the same ratios as
Opus list prices -- and were calibrated on 2026-09-18 from a Max 5x window
where 5.7M weighted tokens moved the 5-hour reading 20% and the weekly 3%.
Treat them as estimates: Anthropic does not publish the formula, and the
calibration spans 5.3M-8.5M per 5-hour window depending on whether the
long-context premium counts.
"""

from __future__ import annotations

from typing import Any

#: Pro allowance, in weighted tokens, per window. See the module docstring.
PRO_WEIGHTED_TOKEN_BUDGET: dict[str, int] = {
    "session": 5_500_000,
    "weekly": 40_000_000,
}

#: How many Pro allowances each plan tier is sold as.
_TIER_MULTIPLIERS: dict[str, int] = {
    "default_claude_max_5x": 5,
    "default_claude_max_20x": 20,
}


def plan_multiplier(rate_limit_tier: str | None, subscription_type: str | None) -> int | None:
    """Return how many Pro allowances the plan holds, or ``None`` if unknown.

    :param rate_limit_tier: Claude Code's ``rateLimitTier``, e.g.
        ``"default_claude_max_5x"``.
    :param subscription_type: Claude Code's ``subscriptionType``, e.g.
        ``"max"`` or ``"pro"``.
    :returns: ``1`` on Pro, the tier's multiple on Max, ``None`` when the tier
        is one this table does not know -- guessing would show a confident
        wrong number, which is worse than showing none.
    """
    if subscription_type == "pro":
        return 1
    if isinstance(rate_limit_tier, str):
        return _TIER_MULTIPLIERS.get(rate_limit_tier)
    return None


def pro_equivalent(windows: list[dict[str, Any]], multiplier: int) -> dict[str, Any] | None:
    """Scale the plan's window readings to what they would be on Pro.

    :param windows: The Claude row's windows, e.g.
        ``[{"kind": "session", "percent": 20.0, ...}]``.
    :param multiplier: Pro allowances in the current plan, from
        :func:`plan_multiplier`.
    :returns: ``{"multiplier": 5, "windows": [...]}`` with one entry per known
        window, or ``None`` when no window carries a reading.
    """
    out: list[dict[str, Any]] = []
    for window in windows:
        kind = window.get("kind")
        used = window.get("percent")
        budget = PRO_WEIGHTED_TOKEN_BUDGET.get(str(kind))
        if budget is None or not isinstance(used, (int, float)) or isinstance(used, bool):
            continue
        pro_pct = float(used) * multiplier
        out.append(
            {
                "kind": kind,
                "label": window.get("label"),
                "used_pct": round(pro_pct, 1),
                "weighted_tokens_used": round(budget * pro_pct / 100),
                "weighted_token_budget": budget,
            }
        )
    if not out:
        return None
    return {"multiplier": multiplier, "windows": out}
