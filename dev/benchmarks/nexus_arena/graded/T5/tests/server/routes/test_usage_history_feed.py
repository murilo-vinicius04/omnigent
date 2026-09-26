"""Session turns land in the per-provider token history.

The database keeps each session's running totals; the Usage page also needs
*when* a turn spent its tokens and on whose model. Both write paths (relay
per-turn deltas, native cumulative totals) must reach the report once each, and
a turn that spent nothing must add nothing.

The report is read through its default location on purpose: where a solution
keeps the history is its own choice, as long as it lives under the data dir.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from omnigent import usage_timeline
from omnigent.server.routes._sessions.orchestration import (
    _accumulate_session_usage,
    _persist_native_cumulative_usage,
)
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore

# Agent ids are stored as 16-byte uuids, so tests use a valid 32-char hex id.
_AGENT_ID = "0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def _data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the data dir (and so the usage log) at a throwaway directory."""
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path))
    return tmp_path


def _providers() -> dict[str, dict]:
    return {row["id"]: row for row in usage_timeline.build_token_usage()["providers"]}


def test_relay_turns_show_up_under_their_vendor(db_uri: str) -> None:
    store = SqlAlchemyConversationStore(db_uri)
    conv = store.create_conversation(title="relay", agent_id=_AGENT_ID)
    claude_turn = {
        "usage": {
            "model": "claude-opus-5",
            "input_tokens": 120,
            "output_tokens": 80,
            "cache_read_input_tokens": 4_000,
            "cost_usd": 0.42,
        }
    }
    gemini_turn = {
        "usage": {
            "model": "gemini-3.7-flash",
            "input_tokens": 1_000,
            "output_tokens": 50,
            "cost_usd": 0.01,
        }
    }

    _accumulate_session_usage(claude_turn, conv.id, store)
    _accumulate_session_usage(claude_turn, conv.id, store)
    _accumulate_session_usage(gemini_turn, conv.id, store)

    providers = _providers()
    today = datetime.now(UTC).date().isoformat()
    # Every token the turn moved counts, cache reads included (task: "a turn's
    # tokens are all of them").
    assert providers["claude"]["tokens"] == 2 * 4_200
    assert {m["model"]: m["tokens"] for m in providers["claude"]["models"]} == {
        "claude-opus-5": 8_400
    }
    assert [(d["day"], d["tokens"]) for d in providers["claude"]["days"] if d["tokens"]] == [
        (today, 8_400)
    ]
    assert providers["gemini"]["tokens"] == 1_050
    assert usage_timeline.build_token_usage()["totals"]["tokens"] == 8_400 + 1_050


def test_native_reports_count_their_growth_once(db_uri: str) -> None:
    store = SqlAlchemyConversationStore(db_uri)
    conv = store.create_conversation(title="native", agent_id=_AGENT_ID)

    # Native harnesses re-post the session's cumulative totals, several times a
    # turn; the history must add what grew, never the running total again.
    for report in (
        {"cumulative_input_tokens": 1_000, "cumulative_output_tokens": 100},
        {"cumulative_input_tokens": 1_500, "cumulative_output_tokens": 160},
        {"cumulative_input_tokens": 1_500, "cumulative_output_tokens": 160},
    ):
        _persist_native_cumulative_usage(conv.id, {**report, "model": "claude-opus-5"}, store)

    assert _providers()["claude"]["tokens"] == 1_660


def test_a_turn_that_spent_nothing_adds_nothing(db_uri: str) -> None:
    store = SqlAlchemyConversationStore(db_uri)
    conv = store.create_conversation(title="idle", agent_id=_AGENT_ID)

    _accumulate_session_usage({"usage": {"model": "claude-opus-5"}}, conv.id, store)
    # Cost-only statusLine polls: no token growth at all.
    for cost in (2.0, 2.0, 1.0):
        _persist_native_cumulative_usage(
            conv.id, {"cumulative_cost_usd": cost, "model": "claude-opus-5"}, store
        )

    report = usage_timeline.build_token_usage()
    # [fairness] a zero-token row (e.g. one carrying only cost) is as good as no row.
    assert sum(row["tokens"] for row in report["providers"]) == 0
    assert report["totals"]["tokens"] == 0
