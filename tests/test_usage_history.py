"""The usage-history log: one line per call, one per plan reading, never fatal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnigent import openai_token_budget as budget
from omnigent import usage_history


@pytest.fixture(autouse=True)
def _state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path))
    usage_history._last_snapshot.clear()
    return tmp_path


def _lines() -> list[dict]:
    path = usage_history.history_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_every_recorded_call_is_logged_with_its_model_and_pool() -> None:
    budget.record(
        "gpt-5.6-luna",
        {"input_tokens": 27, "cache_read_input_tokens": 1_000, "output_tokens": 300},
        source="proxy",
    )
    budget.record("claude-opus-5", {"input_tokens": 500})  # not OpenAI: not recorded

    (line,) = _lines()
    assert line["kind"] == "openai_call"
    assert line["model"] == "gpt-5.6-luna"
    assert line["pool"] == "small"
    assert line["tokens"] == 1_327
    assert line["source"] == "proxy"
    assert line["at"].endswith("Z")


def test_plan_readings_are_logged_once_per_provider_per_window() -> None:
    providers = [
        {"id": "claude", "state": "ok", "windows": [{"kind": "session", "percent": 42}]},
        {"id": "openai", "state": "ok", "windows": [{"kind": "daily-small", "percent": 7}]},
        "not a provider row",
    ]
    usage_history.append_plan_limits(providers, now=1_000.0)
    usage_history.append_plan_limits(providers, now=1_060.0)  # too soon: ignored

    lines = _lines()
    assert [line["provider"] for line in lines] == ["claude", "openai"]
    assert lines[0]["windows"] == {"session": 42}

    usage_history.append_plan_limits(providers, now=1_000.0 + usage_history._SNAPSHOT_INTERVAL_S)
    assert len(_lines()) == 4


def test_the_log_rotates_and_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(usage_history, "_MAX_BYTES", 200)
    for i in range(20):
        usage_history.append("openai_call", model="gpt-5.6-luna", tokens=i)
    assert usage_history.history_path().with_suffix(".jsonl.1").exists()
    assert len(_lines()) < 20  # the rest rolled into the kept previous file

    monkeypatch.setattr(usage_history, "history_path", lambda: Path("/proc/nope/history.jsonl"))
    usage_history.append("openai_call", model="gpt-5.6-luna", tokens=1)
