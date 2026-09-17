"""Unit tests for cost accounting across harness sessions.

The HTTP-level behaviour lives in
``tests/server/integration/test_sessions_endpoints.py``; these cover the
bookkeeping that would need dozens of round trips to reach.
"""

from __future__ import annotations

from typing import Any

import pytest

from omnigent.server.routes._sessions.orchestration import (
    _COST_LAST_REPORTED_KEY,
    _MAX_COST_SCOPES,
    _native_cost_across_sessions,
)


def _apply(current: dict[str, Any], cost: float, session: str) -> float:
    old = float(current.get("total_cost_usd", 0.0) or 0.0)
    total = _native_cost_across_sessions(current, cost=cost, cost_session_id=session, old_cost=old)
    current["total_cost_usd"] = total
    return total


def test_a_resumed_session_keeps_counting_under_its_old_id() -> None:
    """A restarted counter under an unchanged session id still counts.

    ``--resume`` keeps the Claude session uuid but the process starts its
    cost counter over. Keying on the uuid and keeping the largest figure
    would silently drop every run after the biggest one — the whole reason
    this follows growth rather than peaks.
    """
    current: dict[str, Any] = {}
    _apply(current, 4.0, "sess-a")
    _apply(current, 9.0, "sess-a")
    # Resumed: same id, counter back to zero, then climbing again.
    _apply(current, 0.5, "sess-a")
    total = _apply(current, 3.0, "sess-a")
    assert total == pytest.approx(12.0)


def test_the_first_tagged_report_adds_to_the_untagged_history() -> None:
    """Switching a running conversation to tagged reports loses nothing.

    Conversations already carry a lifetime total from before the forwarder
    tagged anything. That figure stands, and the session reporting for the
    first time adds what it has spent — the one-time catch-up that records
    the spend the clamp had been swallowing.
    """
    current: dict[str, Any] = {"total_cost_usd": 143.98}
    total = _apply(current, 53.34, "sess-today")
    assert total == pytest.approx(197.32)
    # Further polls add only the growth, not the whole total again.
    assert _apply(current, 53.50, "sess-today") == pytest.approx(197.48)


def test_two_scopes_do_not_read_as_each_other_restarting() -> None:
    """Interleaved scopes are tracked apart.

    Sharing one baseline would make every alternating report look like a
    restart and re-add the other scope's whole total.
    """
    current: dict[str, Any] = {}
    _apply(current, 10.0, "sess-a")
    _apply(current, 1.0, "sess-b")
    _apply(current, 11.0, "sess-a")
    total = _apply(current, 2.0, "sess-b")
    assert total == pytest.approx(13.0)


def test_scope_map_is_bounded() -> None:
    """The map stops growing; the total is unaffected.

    The scope key comes from the client and a long-lived conversation
    launches many sessions, so an unbounded map is a row that grows forever.
    """
    current: dict[str, Any] = {}
    total = 0.0
    for index in range(_MAX_COST_SCOPES + 10):
        total = _apply(current, 1.0, f"sess-{index}")

    assert total == pytest.approx(float(_MAX_COST_SCOPES + 10))
    assert len(current[_COST_LAST_REPORTED_KEY]) == _MAX_COST_SCOPES
    assert "sess-0" not in current[_COST_LAST_REPORTED_KEY]
    assert "sess-73" in current[_COST_LAST_REPORTED_KEY]


def test_a_corrupt_scope_map_does_not_break_accounting() -> None:
    """Non-numeric entries are ignored rather than raising.

    ``session_usage`` is a free-form JSON column; a bad row must not take
    the usage write path down with it.
    """
    current: dict[str, Any] = {
        "total_cost_usd": 4.0,
        _COST_LAST_REPORTED_KEY: {"sess-a": "nonsense", "sess-b": 3.0},
    }
    # sess-b's baseline survives, so only its growth is added.
    assert _apply(current, 3.5, "sess-b") == pytest.approx(4.5)
