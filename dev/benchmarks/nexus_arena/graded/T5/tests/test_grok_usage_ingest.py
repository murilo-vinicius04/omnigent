"""Grok's own session usage files become Grok lines in the usage history.

Each turn must be counted exactly once however often the files are scanned,
a session file that grows must add only its new turns, and a bad file must not
stop the scan. The report is read at its default location under the data dir.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from omnigent import grok_usage, usage_timeline

# The spec leaves the default day window open; these fixtures are dated, so every
# report names its window (a no-bounds call would depend on the day the grader runs).
WINDOW = {"since": "2026-09-01", "until": "2026-09-30"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


def _turn(number: int, ended_at: str, *, input_tokens: int, output_tokens: int) -> dict[str, Any]:
    """One turn as Grok writes it: input already includes the cached part."""
    cached = input_tokens // 2
    return {
        "turnNumber": number,
        "endedAt": ended_at,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cachedReadTokens": cached,
        "cacheCreationTokens": 0,
        "reasoningTokens": output_tokens // 3,
        "totalTokens": input_tokens + output_tokens,
        "modelCalls": 2,
        "costUsdTicks": 5_000_000_000,
        "primaryModelId": "grok-4.6-build",
    }


def _session_file(root: Path, session_id: str, turns: list[dict[str, Any]]) -> Path:
    path = root / "%2Fhome%2Fme%2Fproject" / session_id / "usage.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "sessionId": session_id,
                "updatedAt": turns[-1]["endedAt"] if turns else "2026-09-16T10:00:00Z",
                "session": {
                    "totalTokens": sum(t["totalTokens"] for t in turns),
                    "inputTokens": sum(t["inputTokens"] for t in turns),
                    "outputTokens": sum(t["outputTokens"] for t in turns),
                },
                "turns": turns,
            }
        ),
        encoding="utf-8",
    )
    return path


def _grok() -> dict[str, Any]:
    rows = {row["id"]: row for row in usage_timeline.build_token_usage(**WINDOW)["providers"]}
    return rows.get("grok", {"tokens": 0, "days": [], "models": []})


def test_each_turn_is_counted_once(tmp_path: Path) -> None:
    root, seen = tmp_path / "sessions", tmp_path / "grok-seen.json"
    _session_file(
        root,
        "sess-1",
        [
            _turn(1, "2026-09-16T10:00:00Z", input_tokens=1_000, output_tokens=200),
            _turn(2, "2026-09-16T10:05:00Z", input_tokens=250, output_tokens=50),
        ],
    )

    assert grok_usage.ingest(root=root, path=seen) == 2
    assert grok_usage.ingest(root=root, path=seen) == 0

    grok = _grok()
    # totalTokens is input (cache included) plus output: 1_200 + 300.
    assert grok["tokens"] == 1_500
    assert {m["model"]: m["tokens"] for m in grok["models"]} == {"grok-4.6-build": 1_500}


def test_a_growing_session_file_adds_only_its_new_turns(tmp_path: Path) -> None:
    root, seen = tmp_path / "sessions", tmp_path / "grok-seen.json"
    first = _turn(1, "2026-09-16T10:00:00Z", input_tokens=1_000, output_tokens=200)
    path = _session_file(root, "sess-1", [first])
    assert grok_usage.ingest(root=root, path=seen) == 1

    second = _turn(2, "2026-09-16T11:00:00Z", input_tokens=4_000, output_tokens=100)
    _session_file(root, "sess-1", [first, second])
    # [fairness] Grok rewrites the file minutes later; make the new mtime visible
    # even on a filesystem with coarse timestamps.
    stamp = path.stat().st_mtime + 60
    os.utime(path, (stamp, stamp))

    assert grok_usage.ingest(root=root, path=seen) == 1
    assert _grok()["tokens"] == 1_200 + 4_100


def test_bad_files_are_skipped_and_a_missing_root_is_not_an_error(tmp_path: Path) -> None:
    root, seen = tmp_path / "sessions", tmp_path / "grok-seen.json"
    _session_file(
        root, "good", [_turn(1, "2026-09-16T10:00:00Z", input_tokens=90, output_tokens=10)]
    )
    torn = root / "%2Fhome%2Fme%2Fproject" / "torn" / "usage.json"
    torn.parent.mkdir(parents=True)
    torn.write_text('{"sessionId": "torn", "turns": [{"turnN', encoding="utf-8")
    odd = root / "%2Fhome%2Fme%2Fproject" / "odd" / "usage.json"
    odd.parent.mkdir(parents=True)
    odd.write_text(json.dumps({"sessionId": "odd", "turns": "none"}), encoding="utf-8")

    assert grok_usage.ingest(root=root, path=seen) == 1
    assert _grok()["tokens"] == 100

    assert grok_usage.ingest(root=tmp_path / "no-such-dir", path=seen) == 0


def test_turns_are_dated_by_when_they_ended(tmp_path: Path) -> None:
    root, seen = tmp_path / "sessions", tmp_path / "grok-seen.json"
    _session_file(
        root,
        "sess-1",
        [
            _turn(1, "2026-09-15T23:59:10.123456789+00:00", input_tokens=700, output_tokens=0),
            _turn(2, "2026-09-16T00:00:20.5+00:00", input_tokens=30, output_tokens=0),
        ],
    )

    assert grok_usage.ingest(root=root, path=seen) == 2

    assert [(d["day"], d["tokens"]) for d in _grok()["days"] if d["tokens"]] == [
        ("2026-09-15", 700),
        ("2026-09-16", 30),
    ]
