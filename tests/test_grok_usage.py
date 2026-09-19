"""Tests for Grok session usage ingestion, ledger tracking, and plan-limits row."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from omnigent import grok_usage, usage_history


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path, Path]:
    """Redirect sessions root, ledger path, and history log to tmp_path."""
    sessions = tmp_path / "grok_sessions"
    ledger = tmp_path / "grok-usage-ledger.json"
    history = tmp_path / "usage-history.jsonl"
    sessions.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(grok_usage, "SESSIONS_ROOT", sessions)
    monkeypatch.setattr(grok_usage, "sessions_root", lambda: sessions)
    monkeypatch.setattr(grok_usage, "ledger_path", lambda: ledger)
    monkeypatch.setattr(usage_history, "history_path", lambda: history)

    return sessions, ledger, history


def _make_usage_file(
    sessions_dir: Path,
    workspace: str,
    session_id: str,
    turns: list[dict[str, Any]],
    *,
    updated_at: str = "2026-09-16T22:00:00Z",
) -> Path:
    sess_dir = sessions_dir / workspace / session_id
    sess_dir.mkdir(parents=True, exist_ok=True)
    usage_file = sess_dir / "usage.json"
    data = {
        "sessionId": session_id,
        "updatedAt": updated_at,
        "session": {
            "totalTokens": sum(t.get("totalTokens", 0) for t in turns),
            "inputTokens": sum(t.get("inputTokens", 0) for t in turns),
            "outputTokens": sum(t.get("outputTokens", 0) for t in turns),
        },
        "turns": turns,
    }
    usage_file.write_text(json.dumps(data), encoding="utf-8")
    return usage_file


def test_ingest_counts_new_turns_once_and_second_ingest_adds_zero(
    _isolate_env: tuple[Path, Path, Path],
) -> None:
    sessions, _ledger, _history = _isolate_env
    turns = [
        {
            "turnNumber": 1,
            "endedAt": "2026-09-16T15:00:00Z",
            "inputTokens": 1000,
            "outputTokens": 200,
            "cachedReadTokens": 500,
            "cacheCreationTokens": 0,
            "reasoningTokens": 50,
            "totalTokens": 1200,
            "modelCalls": 2,
            "costUsdTicks": 10_000_000_000,  # $1.00
            "primaryModelId": "grok-4.6-build",
        }
    ]
    _make_usage_file(sessions, "ws1", "sess-1", turns)

    # First ingest adds 1 turn
    added1 = grok_usage.ingest()
    assert added1 == 1

    # Second ingest finds nothing new
    added2 = grok_usage.ingest()
    assert added2 == 0

    summary = grok_usage.usage_summary(now=datetime(2026, 9, 16, 18, 0, tzinfo=UTC))
    assert summary["tokens"] == 1200
    assert summary["model_calls"] == 2
    assert summary["turns"] == 1
    assert summary["cost_usd"] == 1.0


def test_file_updated_with_new_turn_adds_exactly_one_turn_and_one_history_line(
    _isolate_env: tuple[Path, Path, Path],
) -> None:
    sessions, _ledger, history = _isolate_env
    turn1 = {
        "turnNumber": 1,
        "endedAt": "2026-09-16T10:00:00Z",
        "inputTokens": 100,
        "outputTokens": 20,
        "cachedReadTokens": 10,
        "reasoningTokens": 5,
        "totalTokens": 120,
        "modelCalls": 1,
        "costUsdTicks": 5_000_000_000,
        "primaryModelId": "grok-4.6-build",
    }
    file_path = _make_usage_file(sessions, "ws1", "sess-1", [turn1])

    assert grok_usage.ingest() == 1
    assert len(history.read_text(encoding="utf-8").strip().splitlines()) == 1

    # Add turn 2 to the same file
    turn2 = {
        "turnNumber": 2,
        "endedAt": "2026-09-16T11:00:00Z",
        "inputTokens": 200,
        "outputTokens": 40,
        "cachedReadTokens": 20,
        "reasoningTokens": 10,
        "totalTokens": 240,
        "modelCalls": 1,
        "costUsdTicks": 10_000_000_000,
        "primaryModelId": "grok-4.6-build",
    }
    data = {
        "sessionId": "sess-1",
        "updatedAt": "2026-09-16T11:05:00Z",
        "session": {"totalTokens": 360},
        "turns": [turn1, turn2],
    }
    file_path.write_text(json.dumps(data), encoding="utf-8")
    now_ts = file_path.stat().st_mtime + 2.0
    os.utime(file_path, (now_ts, now_ts))

    assert grok_usage.ingest() == 1
    lines = history.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    event2 = json.loads(lines[1])
    assert event2["kind"] == "grok_call"
    assert event2["turn"] == 2
    assert event2["session_id"] == "sess-1"
    assert event2["tokens"] == 240


def test_malformed_json_skipped_without_raising_and_retried_next_scan(
    _isolate_env: tuple[Path, Path, Path],
) -> None:
    sessions, _ledger, _history = _isolate_env
    sess_dir = sessions / "ws1" / "sess-err"
    sess_dir.mkdir(parents=True, exist_ok=True)
    usage_file = sess_dir / "usage.json"
    usage_file.write_text("NOT_VALID_JSON{", encoding="utf-8")

    # Ingest does not raise and returns 0
    assert grok_usage.ingest() == 0

    # Fix the file
    turn = {
        "turnNumber": 1,
        "endedAt": "2026-09-16T12:00:00Z",
        "inputTokens": 50,
        "outputTokens": 10,
        "cachedReadTokens": 0,
        "reasoningTokens": 0,
        "totalTokens": 60,
        "modelCalls": 1,
        "costUsdTicks": 1_000_000_000,
        "primaryModelId": "grok-4.6-build",
    }
    _make_usage_file(sessions, "ws1", "sess-err", [turn])

    # Next scan ingests it successfully
    assert grok_usage.ingest() == 1


def test_history_line_shape_and_cost_usd_conversion(
    _isolate_env: tuple[Path, Path, Path],
) -> None:
    sessions, _ledger, history = _isolate_env
    turn = {
        "turnNumber": 3,
        "endedAt": "2026-09-16T14:30:00Z",
        "inputTokens": 353517,
        "outputTokens": 5112,
        "cachedReadTokens": 301184,
        "cacheCreationTokens": 0,
        "reasoningTokens": 1905,
        "totalTokens": 358629,
        "modelCalls": 9,
        "costUsdTicks": 972162000,  # 0.0972162 USD
        "primaryModelId": "grok-4.6-build",
    }
    _make_usage_file(sessions, "ws-alpha", "01a0ac46", [turn])

    assert grok_usage.ingest() == 1

    lines = history.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])

    expected_keys = {
        "at",
        "kind",
        "model",
        "input_tokens",
        "output_tokens",
        "cached_read_tokens",
        "reasoning_tokens",
        "tokens",
        "model_calls",
        "cost_usd",
        "session_id",
        "turn",
        "source",
    }
    assert set(record.keys()) == expected_keys
    assert record["at"] == "2026-09-16T14:30:00Z"
    assert record["kind"] == "grok_call"
    assert record["model"] == "grok-4.6-build"
    assert record["input_tokens"] == 353517
    assert record["output_tokens"] == 5112
    assert record["cached_read_tokens"] == 301184
    assert record["reasoning_tokens"] == 1905
    assert record["tokens"] == 358629
    assert record["model_calls"] == 9
    assert record["cost_usd"] == pytest.approx(0.0972162)
    assert record["session_id"] == "01a0ac46"
    assert record["turn"] == 3
    assert record["source"] == "grok_sessions"


def test_day_bucketing_by_ended_at(_isolate_env: tuple[Path, Path, Path]) -> None:
    sessions, _ledger, _history = _isolate_env
    turn_day1 = {
        "turnNumber": 1,
        "endedAt": "2026-09-15T23:55:00Z",
        "inputTokens": 100,
        "outputTokens": 50,
        "totalTokens": 150,
        "modelCalls": 1,
        "costUsdTicks": 0,
        "primaryModelId": "grok-4.6-build",
    }
    turn_day2 = {
        "turnNumber": 2,
        "endedAt": "2026-09-16T00:05:00Z",
        "inputTokens": 200,
        "outputTokens": 100,
        "totalTokens": 300,
        "modelCalls": 2,
        "costUsdTicks": 0,
        "primaryModelId": "grok-4.6-build",
    }
    _make_usage_file(sessions, "ws", "sess-split", [turn_day1, turn_day2])
    assert grok_usage.ingest() == 2

    day1 = grok_usage.read_day("2026-09-15")
    assert day1["tokens"] == 150
    assert day1["turns"] == 1

    day2 = grok_usage.read_day("2026-09-16")
    assert day2["tokens"] == 300
    assert day2["turns"] == 1


def test_usage_summary_aggregates_tokens_calls_turns(
    _isolate_env: tuple[Path, Path, Path],
) -> None:
    sessions, _ledger, _history = _isolate_env
    turn = {
        "turnNumber": 1,
        "endedAt": "2026-09-16T12:00:00Z",
        "inputTokens": 400_000,
        "outputTokens": 37_000,
        "totalTokens": 437_000,
        "modelCalls": 13,
        "costUsdTicks": 1_000_000_000,
        "primaryModelId": "grok-4.6-build",
    }
    _make_usage_file(sessions, "ws", "sess-1", [turn])
    assert grok_usage.ingest() == 1

    summary = grok_usage.usage_summary(now=datetime(2026, 9, 16, 15, 0, tzinfo=UTC))
    assert summary["tokens"] == 437_000
    assert summary["model_calls"] == 13
    assert summary["turns"] == 1
    assert summary["cost_usd"] == 0.1
    assert summary["resets_at"] == "2026-09-17T00:00:00Z"


def test_ingest_never_raises_when_root_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing_dir = tmp_path / "non_existent_grok_sessions"
    monkeypatch.setattr(grok_usage, "SESSIONS_ROOT", missing_dir)
    monkeypatch.setattr(grok_usage, "sessions_root", lambda: missing_dir)

    assert grok_usage.ingest() == 0
    summary = grok_usage.usage_summary()
    assert summary["tokens"] == 0
    assert summary["model_calls"] == 0
