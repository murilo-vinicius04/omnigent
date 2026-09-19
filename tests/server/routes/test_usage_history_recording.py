"""Both session-usage write paths leave a timestamped line in the usage log.

The database keeps running totals, which cannot answer "when did this get
spent, and on whose model". The Usage page's per-provider history is built from
these lines, so a turn that updates the counters must also record one — and a
native harness re-posting the same cumulative totals must not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnigent import usage_history
from omnigent.server.routes._sessions.orchestration import (
    _accumulate_session_usage,
    _persist_native_cumulative_usage,
)
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore

# Agent ids are stored as 16-byte uuids, so tests use a valid 32-char hex id.
_AGENT_ID = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the usage log to a throwaway file."""
    path = tmp_path / "usage-history.jsonl"
    monkeypatch.setattr(usage_history, "history_path", lambda: path)
    return path


def _lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_relay_turn_records_its_own_token_split(db_uri: str, log: Path) -> None:
    store = SqlAlchemyConversationStore(db_uri)
    conv = store.create_conversation(title="relay", agent_id=_AGENT_ID)

    _accumulate_session_usage(
        {
            "usage": {
                "model": "claude-opus-5",
                "input_tokens": 120,
                "output_tokens": 80,
                "total_tokens": 200,
                "cache_read_input_tokens": 4_000,
                "cost_usd": 0.42,
            }
        },
        conv.id,
        store,
    )

    (line,) = _lines(log)
    assert line["kind"] == "model_call"
    assert line["model"] == "claude-opus-5"
    assert line["input_tokens"] == 120
    assert line["output_tokens"] == 80
    assert line["cache_read_input_tokens"] == 4_000
    assert line["tokens"] == 4_200
    assert line["cost_usd"] == 0.42
    assert line["session_id"] == conv.id
    assert line["source"] == "relay"


def test_relay_turn_without_usage_records_nothing(db_uri: str, log: Path) -> None:
    store = SqlAlchemyConversationStore(db_uri)
    conv = store.create_conversation(title="empty", agent_id=_AGENT_ID)

    _accumulate_session_usage({"usage": {"model": "claude-opus-5"}}, conv.id, store)

    assert _lines(log) == []


def test_native_reports_record_growth_not_the_running_total(db_uri: str, log: Path) -> None:
    store = SqlAlchemyConversationStore(db_uri)
    conv = store.create_conversation(title="native", agent_id=_AGENT_ID)

    # Native harnesses re-post cumulative session totals; the history wants the
    # per-turn delta, or the day's chart would count the same tokens twice.
    _persist_native_cumulative_usage(
        conv.id,
        {
            "cumulative_input_tokens": 1_000,
            "cumulative_output_tokens": 100,
            "model": "gpt-5.6-luna",
        },
        store,
    )
    _persist_native_cumulative_usage(
        conv.id,
        {
            "cumulative_input_tokens": 1_500,
            "cumulative_output_tokens": 160,
            "model": "gpt-5.6-luna",
        },
        store,
    )

    first, second = _lines(log)
    assert first["tokens"] == 1_100
    assert second["input_tokens"] == 500
    assert second["output_tokens"] == 60
    assert second["tokens"] == 560
    assert second["source"] == "native"


def test_a_native_poll_that_grew_nothing_records_nothing(db_uri: str, log: Path) -> None:
    store = SqlAlchemyConversationStore(db_uri)
    conv = store.create_conversation(title="idle", agent_id=_AGENT_ID)

    _persist_native_cumulative_usage(
        conv.id, {"cumulative_cost_usd": 2.0, "model": "claude-opus-5"}, store
    )
    # The forwarder polls the statusLine several times a turn; an unchanged
    # total must not add a line (and a lowered one is clamped to no growth).
    _persist_native_cumulative_usage(
        conv.id, {"cumulative_cost_usd": 2.0, "model": "claude-opus-5"}, store
    )
    _persist_native_cumulative_usage(
        conv.id, {"cumulative_cost_usd": 1.0, "model": "claude-opus-5"}, store
    )

    (line,) = _lines(log)
    assert line["cost_usd"] == 2.0
    assert line["tokens"] == 0
