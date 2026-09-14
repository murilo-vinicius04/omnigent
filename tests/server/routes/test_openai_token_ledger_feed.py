"""Both session-usage write paths feed our own OpenAI daily token ledger.

Relay harnesses (codex, openai-agents) report per-turn deltas; native
harnesses (codex-native) report cumulative totals. Either way, the ledger must
gain exactly the tokens the turn spent, and nothing for non-OpenAI models.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnigent import openai_token_budget as budget
from omnigent.server.routes._sessions.orchestration import (
    _accumulate_session_usage,
    _persist_native_cumulative_usage,
)
from omnigent.stores.conversation_store.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)

_AGENT_ID = "0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def _ledger(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(budget, "ledger_path", lambda: tmp_path / "ledger.json")


def _small_pool_tokens() -> dict[str, int]:
    return next(row for row in budget.pool_usage() if row["id"] == "small")["models"]


def test_relay_turns_add_their_tokens(db_uri: str) -> None:
    store = SqlAlchemyConversationStore(db_uri)
    conv = store.create_conversation(title="relay", agent_id=_AGENT_ID)
    turn = {
        "usage": {
            "model": "gpt-5.6-luna",
            "input_tokens": 27,
            "cache_read_input_tokens": 107_814,
            "output_tokens": 3_298,
            "total_tokens": 111_139,
        }
    }

    _accumulate_session_usage(turn, conv.id, store)
    _accumulate_session_usage(turn, conv.id, store)

    assert _small_pool_tokens() == {"gpt-5.6-luna": 2 * 111_139}


def test_relay_turns_on_other_models_add_nothing(db_uri: str) -> None:
    store = SqlAlchemyConversationStore(db_uri)
    conv = store.create_conversation(title="claude", agent_id=_AGENT_ID)

    _accumulate_session_usage(
        {"usage": {"model": "claude-opus-5", "input_tokens": 500, "output_tokens": 5}},
        conv.id,
        store,
    )

    assert budget.read_day() == {}


def test_native_cumulative_reports_add_only_their_growth(db_uri: str) -> None:
    store = SqlAlchemyConversationStore(db_uri)
    conv = store.create_conversation(title="native", agent_id=_AGENT_ID)

    _persist_native_cumulative_usage(
        conv.id,
        {
            "cumulative_input_tokens": 50_000,
            "cumulative_cache_read_input_tokens": 40_000,
            "cumulative_output_tokens": 1_000,
            "model": "gpt-5.6-terra",
        },
        store,
    )
    _persist_native_cumulative_usage(
        conv.id,
        {
            "cumulative_input_tokens": 80_000,
            "cumulative_cache_read_input_tokens": 65_000,
            "cumulative_output_tokens": 1_500,
            "model": "gpt-5.6-terra",
        },
        store,
    )
    # A replayed lower report must not subtract (or add) anything.
    _persist_native_cumulative_usage(
        conv.id,
        {"cumulative_input_tokens": 10, "cumulative_output_tokens": 1, "model": "gpt-5.6-terra"},
        store,
    )

    # Ledger = the final cumulative total (80k input incl. cached + 1.5k output).
    assert _small_pool_tokens() == {"gpt-5.6-terra": 81_500}
