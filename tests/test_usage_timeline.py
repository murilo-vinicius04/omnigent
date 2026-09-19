"""Reading the usage-history log back: per-provider tokens and plan curves.

The log is append-only debug data written by four unrelated producers, so the
reader's job is to survive what they disagree about — timestamp precision,
token-field names, a half-written last line — and still attribute every call to
the right vendor.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnigent import usage_history, usage_timeline


@pytest.fixture(autouse=True)
def _state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the log (and the read cache) at a throwaway state dir."""
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path))
    usage_history._last_snapshot.clear()
    usage_timeline._cache.clear()
    return tmp_path


def _write(path: Path, *events: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


@pytest.mark.parametrize(
    ("model", "family"),
    [
        ("claude-opus-5", "claude"),
        ("system.ai.claude-sonnet-4-6[1m]", "claude"),
        ("Gemini 3.8 Flash (High)", "gemini"),
        ("gemini-3.8-flash-low", "gemini"),
        ("gpt-5.6-luna", "openai"),
        ("grok-4.6-build", "grok"),
        ("llama-3-70b", "other"),
        (None, "other"),
    ],
)
def test_provider_family_is_read_off_the_model_id(model: str | None, family: str) -> None:
    # Harnesses report model ids in whatever shape they like — a display name
    # with spaces, a prefixed alias, a plain slug — and all of them have to land
    # on the same vendor row.
    assert usage_timeline.provider_for_model(model) == family


def test_tokens_are_grouped_by_vendor_day_and_model(tmp_path: Path) -> None:
    log = tmp_path / "usage-history.jsonl"
    _write(
        log,
        # Three writers, three field spellings for the same idea.
        {
            "at": "2026-09-14T10:00:00Z",
            "kind": "openai_call",
            "model": "gpt-5.6-luna",
            "tokens": 1_000,
            "input_tokens": 200,
            "output_tokens": 800,
        },
        {
            "at": "2026-09-15T11:30:00.123456789+00:00",
            "kind": "grok_call",
            "model": "grok-4.6-build",
            "tokens": 500,
            "cached_read_tokens": 400,
            "output_tokens": 100,
            "cost_usd": 0.25,
        },
        {
            "at": "2026-09-15T12:00:00Z",
            "kind": "model_call",
            "model": "claude-opus-5",
            "tokens": 300,
            "input_tokens": 100,
            "cache_read_input_tokens": 150,
            "output_tokens": 50,
            "cost_usd": 0.5,
        },
        {
            "at": "2026-09-15T13:00:00Z",
            "kind": "model_call",
            "model": "claude-opus-5",
            "tokens": 100,
            "output_tokens": 100,
        },
    )

    report = usage_timeline.build_token_usage(path=log)
    rows = {provider["id"]: provider for provider in report["providers"]}

    assert set(rows) == {"claude", "openai", "grok"}
    assert rows["claude"]["tokens"] == 400
    assert rows["claude"]["calls"] == 2
    assert rows["claude"]["cost_usd"] == 0.5
    assert rows["claude"]["cached_tokens"] == 150
    # Both Claude turns happened on the same UTC day, so they share a bucket.
    assert rows["claude"]["days"] == [
        {
            "day": "2026-09-15",
            "tokens": 400,
            "input_tokens": 100,
            "output_tokens": 150,
            "cached_tokens": 150,
            "cost_usd": 0.5,
            "calls": 2,
        }
    ]
    assert [model["model"] for model in rows["claude"]["models"]] == ["claude-opus-5"]
    # A nanosecond-precision stamp from the Grok ingester still resolves.
    assert rows["grok"]["days"][0]["day"] == "2026-09-15"
    assert rows["grok"]["cached_tokens"] == 400
    assert report["totals"]["tokens"] == 1_900
    assert report["totals"]["calls"] == 4
    # Vendor order is stable regardless of what the log happened to record first.
    assert [provider["id"] for provider in report["providers"]] == ["claude", "openai", "grok"]


def test_day_bounds_are_inclusive(tmp_path: Path) -> None:
    log = tmp_path / "usage-history.jsonl"
    _write(
        log,
        {
            "at": "2026-09-13T23:59:59Z",
            "kind": "model_call",
            "model": "claude-opus-5",
            "tokens": 1,
        },
        {
            "at": "2026-09-14T00:00:01Z",
            "kind": "model_call",
            "model": "claude-opus-5",
            "tokens": 2,
        },
        {
            "at": "2026-09-15T12:00:00Z",
            "kind": "model_call",
            "model": "claude-opus-5",
            "tokens": 4,
        },
        {
            "at": "2026-09-16T00:00:00Z",
            "kind": "model_call",
            "model": "claude-opus-5",
            "tokens": 8,
        },
    )

    report = usage_timeline.build_token_usage(path=log, since="2026-09-14", until="2026-09-15")
    assert report["providers"][0]["tokens"] == 6
    assert report["since"] == "2026-09-14"
    assert report["until"] == "2026-09-15"


def test_a_torn_or_unreadable_line_does_not_cost_the_rest_of_the_log(tmp_path: Path) -> None:
    # The writer appends without locking and the process can die mid-line, so a
    # truncated tail is normal — it must not blank the chart.
    log = tmp_path / "usage-history.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "at": "2026-09-15T10:00:00Z",
                        "kind": "model_call",
                        "model": "claude-opus-5",
                        "tokens": 10,
                    }
                ),
                "",
                "not json at all",
                json.dumps({"kind": "model_call", "model": "claude-opus-5", "tokens": 5}),  # no at
                json.dumps({"at": "never", "kind": "model_call", "tokens": 5}),  # unparseable at
                '{"at": "2026-09-15T10:01:00Z", "kind": "model_ca',  # torn
            ]
        ),
        encoding="utf-8",
    )

    report = usage_timeline.build_token_usage(path=log)
    assert report["totals"]["tokens"] == 10


def test_rotated_log_is_read_before_the_live_one(tmp_path: Path) -> None:
    log = tmp_path / "usage-history.jsonl"
    _write(
        log.with_suffix(".jsonl.1"),
        {
            "at": "2026-09-10T10:00:00Z",
            "kind": "model_call",
            "model": "claude-opus-5",
            "tokens": 7,
        },
    )
    _write(
        log,
        {
            "at": "2026-09-15T10:00:00Z",
            "kind": "model_call",
            "model": "claude-opus-5",
            "tokens": 3,
        },
    )

    report = usage_timeline.build_token_usage(path=log)
    assert [day["day"] for day in report["providers"][0]["days"]] == ["2026-09-10", "2026-09-15"]
    assert report["totals"]["tokens"] == 10


def test_missing_log_reads_as_an_empty_report(tmp_path: Path) -> None:
    report = usage_timeline.build_token_usage(path=tmp_path / "nope.jsonl")
    assert report["providers"] == []
    assert report["limits"] == []
    assert report["totals"]["tokens"] == 0


def test_plan_readings_become_one_curve_per_window(tmp_path: Path) -> None:
    log = tmp_path / "usage-history.jsonl"
    _write(
        log,
        {
            "at": "2026-09-15T10:00:00Z",
            "kind": "plan_limits",
            "provider": "claude",
            "windows": {"session": 10, "weekly": 40},
        },
        {
            "at": "2026-09-15T10:05:00Z",
            "kind": "plan_limits",
            "provider": "claude",
            "windows": {"session": 12, "weekly": 41},
        },
        {
            "at": "2026-09-15T10:05:00Z",
            "kind": "plan_limits",
            "provider": "antigravity",
            "windows": {"gemini-5h": 55, "bad": None},
        },
    )

    report = usage_timeline.build_token_usage(path=log)
    curves = {row["provider"]: row for row in report["limits"]}

    assert curves["claude"]["label"] == "Claude"
    assert [window["kind"] for window in curves["claude"]["windows"]] == ["session", "weekly"]
    assert curves["claude"]["windows"][0]["label"] == "5h"
    assert curves["claude"]["windows"][0]["points"] == [
        {"at": "2026-09-15T10:00:00Z", "percent": 10},
        {"at": "2026-09-15T10:05:00Z", "percent": 12},
    ]
    # Antigravity is the client, Gemini is the vendor whose tokens it burns.
    assert curves["antigravity"]["label"] == "Gemini"
    # A null percentage is dropped, not charted as zero.
    assert [window["kind"] for window in curves["antigravity"]["windows"]] == ["gemini-5h"]


def test_plan_curves_are_thinned_but_keep_the_newest_reading(tmp_path: Path) -> None:
    # The tray records every few minutes; a month of that is thousands of
    # points. The last one is the number the pill is showing right now, so a
    # thinned curve that dropped it would disagree with the tray beside it.
    log = tmp_path / "usage-history.jsonl"
    _write(
        log,
        *[
            {
                "at": f"2026-09-15T10:{minute:02d}:00Z",
                "kind": "plan_limits",
                "provider": "claude",
                "windows": {"session": minute},
            }
            for minute in range(60)
        ],
    )

    report = usage_timeline.build_token_usage(path=log, max_points=10)
    points = report["limits"][0]["windows"][0]["points"]
    assert len(points) == 10
    assert points[0]["percent"] == 0
    assert points[-1] == {"at": "2026-09-15T10:59:00Z", "percent": 59}


def test_the_tray_read_skips_the_plan_curves_it_would_throw_away(tmp_path: Path) -> None:
    log = tmp_path / "usage-history.jsonl"
    _write(
        log,
        {
            "at": "2026-09-15T10:00:00Z",
            "kind": "model_call",
            "model": "claude-opus-5",
            "tokens": 4,
        },
        {
            "at": "2026-09-15T10:00:00Z",
            "kind": "plan_limits",
            "provider": "claude",
            "windows": {"session": 10},
        },
    )

    report = usage_timeline.build_token_usage(path=log, include_limits=False)
    assert report["limits"] == []
    assert report["totals"]["tokens"] == 4


def test_tokens_today_only_counts_today(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    log = tmp_path / "usage-history.jsonl"
    _write(
        log,
        {
            "at": "2026-09-15T10:00:00Z",
            "kind": "model_call",
            "model": "claude-opus-5",
            "tokens": 100,
        },
        {
            "at": "2026-09-16T09:00:00Z",
            "kind": "model_call",
            "model": "claude-opus-5",
            "tokens": 20,
            "cost_usd": 0.75,
        },
        {"at": "2026-09-16T09:30:00Z", "kind": "grok_call", "model": "grok-4.6", "tokens": 5},
    )

    today = usage_timeline.tokens_today(
        now=datetime(2026, 9, 16, 23, 0, tzinfo=UTC),
        path=log,
    )
    assert today == {
        "claude": {"tokens": 20, "cost_usd": 0.75, "calls": 1},
        "grok": {"tokens": 5, "cost_usd": 0.0, "calls": 1},
    }


def test_repeated_reads_are_served_from_the_short_lived_cache(tmp_path: Path) -> None:
    # Every open tab polls the tray once a minute; re-parsing a multi-megabyte
    # log for each of those is the cost this cache exists to avoid.
    log = tmp_path / "usage-history.jsonl"
    _write(
        log,
        {
            "at": "2026-09-15T10:00:00Z",
            "kind": "model_call",
            "model": "claude-opus-5",
            "tokens": 1,
        },
    )

    first = usage_timeline.cached_token_usage(path=log)
    _write(
        log,
        {
            "at": "2026-09-15T10:00:00Z",
            "kind": "model_call",
            "model": "claude-opus-5",
            "tokens": 9,
        },
    )
    assert usage_timeline.cached_token_usage(path=log) is first

    usage_timeline._cache.clear()
    assert usage_timeline.cached_token_usage(path=log)["totals"]["tokens"] == 9


def test_model_calls_are_logged_with_their_token_split() -> None:
    usage_history.append_model_call(
        "claude-opus-5",
        input_tokens=100,
        output_tokens=50,
        cache_read_input_tokens=800,
        cache_creation_input_tokens=20,
        cost_usd=0.125,
        session_id="conv_abc",
        source="relay",
    )
    line = json.loads(usage_history.history_path().read_text(encoding="utf-8").strip())

    assert line["kind"] == "model_call"
    assert line["model"] == "claude-opus-5"
    # ``tokens`` is the whole turn, cache included — that is what a vendor meter
    # counts, and what the provider row totals.
    assert line["tokens"] == 970
    assert line["cost_usd"] == 0.125
    assert line["session_id"] == "conv_abc"


def test_empty_model_calls_are_not_logged() -> None:
    # Native harnesses re-post their cumulative totals several times a turn;
    # the un-grown ones would otherwise flood the log.
    usage_history.append_model_call("claude-opus-5", cost_usd=None)
    usage_history.append_model_call("claude-opus-5", cost_usd=0.0)
    assert not usage_history.history_path().exists()
