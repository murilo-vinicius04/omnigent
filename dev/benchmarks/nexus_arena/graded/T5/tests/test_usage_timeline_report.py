"""Reading the usage log back: tokens per vendor per day, and plan usage over time.

Lines are written here in the two formats the log already had before this
change (``openai_call`` from the OpenAI budget proxy, ``plan_limits`` from the
tray), exactly as ``omnigent.openai_token_budget.record`` and
``omnigent.usage_history.append_plan_limits`` write them, so these tests do not
depend on how a solution chooses to log session turns.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omnigent import usage_timeline


def _openai_call(at: str, model: str, tokens: int) -> dict[str, Any]:
    """An ``openai_call`` line as the OpenAI budget writes it."""
    return {
        "at": at,
        "kind": "openai_call",
        "model": model,
        "pool": "small",
        "tokens": tokens,
        "input_tokens": tokens - tokens // 4,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "output_tokens": tokens // 4,
        "source": "proxy",
    }


def _plan_limits(at: str, provider: str, windows: dict[str, int]) -> dict[str, Any]:
    """A ``plan_limits`` line as ``usage_history.append_plan_limits`` writes it."""
    return {
        "at": at,
        "kind": "plan_limits",
        "provider": provider,
        "state": "ok",
        "windows": windows,
        "tier": None,
    }


def _write(path: Path, events: list[dict[str, Any]]) -> Path:
    path.write_text(
        "".join(json.dumps(e, sort_keys=True) + "\n" for e in events), encoding="utf-8"
    )
    return path


def test_provider_family_follows_the_model_id() -> None:
    families = {
        "claude-opus-5": "claude",
        "gpt-5.6-luna": "openai",
        "databricks-gpt-5-6-luna": "openai",
        "Gemini 3.8 Flash (High)": "gemini",
        "grok-4.6-build": "grok",
        "llama-4-maverick": "other",
    }
    assert {model: usage_timeline.provider_for_model(model) for model in families} == families
    assert usage_timeline.provider_for_model(None) == "other"


def test_tokens_are_grouped_by_vendor_day_and_model(tmp_path: Path) -> None:
    log = _write(
        tmp_path / "usage-history.jsonl",
        [
            _openai_call("2026-09-14T10:00:00Z", "gpt-5.6-luna", 1_000),
            _openai_call("2026-09-14T11:00:00Z", "gpt-5.6-terra", 500),
            _plan_limits("2026-09-14T11:05:00Z", "claude", {"session": 12, "weekly": 3}),
            _openai_call("2026-09-15T09:00:00Z", "gpt-5.6-luna", 200),
        ],
    )

    report = usage_timeline.build_token_usage(path=log)

    (openai,) = report["providers"]
    assert openai["id"] == "openai"
    assert openai["tokens"] == 1_700
    assert [(d["day"], d["tokens"]) for d in openai["days"]] == [
        ("2026-09-14", 1_500),
        ("2026-09-15", 200),
    ]
    assert {m["model"]: m["tokens"] for m in openai["models"]} == {
        "gpt-5.6-luna": 1_200,
        "gpt-5.6-terra": 500,
    }
    assert report["totals"]["tokens"] == 1_700


def test_day_bounds_are_inclusive_utc_days(tmp_path: Path) -> None:
    log = _write(
        tmp_path / "usage-history.jsonl",
        [
            _openai_call("2026-09-14T23:59:59Z", "gpt-5.6-luna", 100),
            _openai_call("2026-09-15T00:00:00Z", "gpt-5.6-luna", 10),
            _openai_call("2026-09-16T23:59:59Z", "gpt-5.6-luna", 1),
            _openai_call("2026-09-17T00:00:00Z", "gpt-5.6-luna", 1_000),
        ],
    )

    def total(**bounds: str) -> int:
        return usage_timeline.build_token_usage(path=log, **bounds)["totals"]["tokens"]

    assert total(since="2026-09-15", until="2026-09-16") == 11
    assert total(since="2026-09-16") == 1_001
    assert total(until="2026-09-14") == 100
    assert total() == 1_111


def test_a_torn_or_missing_log_is_not_an_error(tmp_path: Path) -> None:
    good = [
        json.dumps(_openai_call("2026-09-15T10:00:00Z", "gpt-5.6-luna", 40)),
        "{not json",
        "",
        json.dumps(["not", "an", "object"]),
        json.dumps(_openai_call("2026-09-15T10:01:00Z", "gpt-5.6-luna", 2)),
    ]
    # The writer was interrupted mid-line: the last line has no end.
    log = tmp_path / "usage-history.jsonl"
    log.write_text("\n".join(good) + '\n{"at": "2026-09-15T10:02', encoding="utf-8")

    assert usage_timeline.build_token_usage(path=log)["totals"]["tokens"] == 42

    empty = usage_timeline.build_token_usage(path=tmp_path / "never-written.jsonl")
    assert empty["providers"] == []
    assert empty["limits"] == []
    assert empty["totals"]["tokens"] == 0


def test_plan_readings_become_one_curve_per_window(tmp_path: Path) -> None:
    log = _write(
        tmp_path / "usage-history.jsonl",
        [
            _plan_limits("2026-09-15T10:00:00Z", "claude", {"session": 10, "weekly": 5}),
            _plan_limits("2026-09-15T10:05:00Z", "antigravity", {"gemini-5h": 40}),
            _plan_limits("2026-09-15T10:10:00Z", "claude", {"session": 20, "weekly": 6}),
            _plan_limits("2026-09-15T10:20:00Z", "claude", {"session": 35, "weekly": 7}),
        ],
    )

    limits = {row["provider"]: row for row in usage_timeline.build_token_usage(path=log)["limits"]}

    claude = {w["kind"]: [p["percent"] for p in w["points"]] for w in limits["claude"]["windows"]}
    assert claude == {"session": [10, 20, 35], "weekly": [5, 6, 7]}
    agy = {
        w["kind"]: [p["percent"] for p in w["points"]] for w in limits["antigravity"]["windows"]
    }
    assert agy == {"gemini-5h": [40]}


def test_tokens_today_counts_only_the_utc_day_of_now(tmp_path: Path) -> None:
    log = _write(
        tmp_path / "usage-history.jsonl",
        [
            _openai_call("2026-09-14T23:30:00Z", "gpt-5.6-luna", 500),
            _openai_call("2026-09-15T00:10:00Z", "gpt-5.6-luna", 60),
            _openai_call("2026-09-15T12:00:00Z", "gpt-5.6-sol", 10),
        ],
    )

    today = usage_timeline.tokens_today(now=datetime(2026, 9, 15, 13, 0, tzinfo=UTC), path=log)

    assert today["openai"]["tokens"] == 70
    # [fairness] vendors with nothing today may be absent or zero.
    assert all(row["tokens"] == 0 for family, row in today.items() if family != "openai")
