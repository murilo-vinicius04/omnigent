"""Tests for our own OpenAI daily token counter and its plan-limits row."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from omnigent import openai_token_budget as budget
from omnigent.server.routes import plan_limits


@pytest.fixture(autouse=True)
def _ledger(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the ledger at a temp file so tests never touch the real count."""
    path = tmp_path / "openai-token-ledger.json"
    monkeypatch.setattr(budget, "ledger_path", lambda: path)
    return path


def test_pools_follow_the_model_family() -> None:
    assert budget.pool_for("gpt-5.6-luna") == "small"
    assert budget.pool_for("openai/gpt-5.6-terra-2026-07-01") == "small"
    assert budget.pool_for("gpt-5.6-sol") == "large"
    # OpenAI, but in no free pool: every token is billed.
    assert budget.pool_for("gpt-5.5") == budget.UNLISTED
    assert budget.pool_for("o4") == budget.UNLISTED
    # Not OpenAI at all: never counted.
    assert budget.pool_for("claude-opus-5") is None
    assert budget.pool_for("gemini-3-pro") is None


def test_every_token_counts_including_cached_and_cache_writes() -> None:
    recorded = budget.record(
        "gpt-5.6-luna",
        {
            "input_tokens": 27,
            "cache_read_input_tokens": 107_814,
            "cache_creation_input_tokens": 15_814,
            "output_tokens": 3_298,
            "total_tokens": 999_999,  # not summed: it would double-count
        },
    )
    assert recorded == 126_953
    small = next(row for row in budget.pool_usage() if row["id"] == "small")
    assert small["tokens"] == 126_953
    assert small["models"] == {"gpt-5.6-luna": 126_953}


def test_pools_accumulate_across_models_and_calls() -> None:
    budget.record("gpt-5.6-luna", {"input_tokens": 100, "output_tokens": 50})
    budget.record("gpt-5.6-terra", {"input_tokens": 10, "output_tokens": 5})
    budget.record("gpt-5.6-luna", {"output_tokens": 45})
    budget.record("gpt-5.6-sol", {"input_tokens": 1_000})
    budget.record("gpt-5.5", {"output_tokens": 7})
    rows = {row["id"]: row for row in budget.pool_usage()}
    assert rows["small"]["tokens"] == 210
    assert rows["small"]["models"] == {"gpt-5.6-luna": 195, "gpt-5.6-terra": 15}
    assert rows["large"]["tokens"] == 1_000
    assert rows[budget.UNLISTED]["tokens"] == 7
    assert rows[budget.UNLISTED]["daily_tokens"] is None


def test_non_openai_and_junk_usage_record_nothing(_ledger: Path) -> None:
    assert budget.record("claude-opus-5", {"input_tokens": 500}) == 0
    assert budget.record("gpt-5.6-luna", {"input_tokens": -5, "output_tokens": float("nan")}) == 0
    assert budget.record("gpt-5.6-luna", {"input_tokens": True}) == 0
    assert not _ledger.exists()


def test_days_are_utc_and_do_not_carry_over() -> None:
    late = datetime(2026, 9, 14, 23, 59, tzinfo=UTC)
    budget.record("gpt-5.6-luna", {"output_tokens": 100}, now=late)
    assert budget.pool_usage(late)[0]["tokens"] == 100
    next_day = datetime(2026, 9, 15, 0, 1, tzinfo=UTC)
    assert budget.pool_usage(next_day)[0]["tokens"] == 0
    assert budget.next_reset(late) == datetime(2026, 9, 15, tzinfo=UTC)


def test_usage_delta_records_only_openai_models() -> None:
    delta = {
        "input_tokens": 999,
        "by_model": {
            "gpt-5.6-terra": {"input_tokens": 40, "cache_read_input_tokens": 60},
            "claude-opus-5": {"input_tokens": 1_000},
            "broken": "not a bucket",
        },
    }
    assert budget.record_usage_delta(delta) == 100
    assert budget.record_usage_delta({"input_tokens": 5}) == 0


def test_usage_delta_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_args: Any, **_kwargs: Any) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(budget, "record", explode)
    assert budget.record_usage_delta({"by_model": {"gpt-5.6-luna": {"input_tokens": 1}}}) == 0


def test_manual_add_is_tagged_by_source(capsys: pytest.CaptureFixture[str]) -> None:
    assert budget._main(["add", "gpt-5.6-terra", "--cached", "37598", "--output", "2004"]) == 0
    bucket = budget.read_day()["gpt-5.6-terra"]
    assert bucket["tokens"] == 39_602
    assert bucket["by_source"] == {"manual": 39_602}
    assert "Luna/Terra: 39.6k of 2.5M (1.6%)" in capsys.readouterr().out


def test_plan_limits_row_shows_each_free_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plan_limits, "_cache", None)
    budget.record("gpt-5.6-luna", {"input_tokens": 1_000_000, "output_tokens": 250_000})
    budget.record("gpt-5.6-sol", {"output_tokens": 249_999})
    budget.record("gpt-5.5", {"output_tokens": 12_000})

    row = plan_limits._openai_provider()

    assert row["state"] == "ok"
    windows = {w["kind"]: w for w in row["windows"]}
    assert windows["daily-small"]["percent"] == 50
    assert windows["daily-small"]["label"] == "Luna/Terra 1.25M/2.5M today"
    # Floored: one token short of the cap must not read as 100%.
    assert windows["daily-large"]["percent"] == 99
    assert windows["daily-large"]["resets_at"].endswith("T00:00:00Z")
    assert "12k billed (not free)" in row["tier"]


def test_plan_limits_row_survives_an_unreadable_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("corrupt")

    monkeypatch.setattr(budget, "pool_usage", explode)
    row = plan_limits._openai_provider()
    assert row["state"] == "error"
    assert row["windows"] == []


def test_zero_usage_still_renders_a_live_row() -> None:
    row = plan_limits._openai_provider()
    assert row["state"] == "ok"
    assert [w["percent"] for w in row["windows"]] == [0, 0]
    assert asyncio.iscoroutinefunction(plan_limits.collect_plan_limits)
