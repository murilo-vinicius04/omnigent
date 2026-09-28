"""Tests for per-user ``/v1/plan-limits`` on a multi-user server.

The server process can only read its own OS user's vendor logins, so each
user's reading must come from their own host — never another user's.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from omnigent.host import identity
from omnigent.host.frames import (
    HostPlanLimitsFrame,
    HostPlanLimitsResultFrame,
    decode_host_frame,
    encode_host_frame,
)
from omnigent.server.routes import plan_limits


@pytest.fixture(autouse=True)
def _clear_user_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plan_limits, "_user_cache", {})


class _FakeRegistry:
    """Host registry whose hosts answer ``host.plan_limits`` with a canned payload."""

    def __init__(self, conns: list[Any], answers: dict[str, dict[str, Any] | None]) -> None:
        self._conns = {conn.host_id: conn for conn in conns}
        self._answers = answers
        self.asked: list[str] = []

    def online_host_ids(self) -> list[str]:
        return list(self._conns)

    def get(self, host_id: str) -> Any:
        return self._conns.get(host_id)

    def send_text(self, conn: Any, text: str) -> None:
        frame = decode_host_frame(text)
        assert isinstance(frame, HostPlanLimitsFrame)
        self.asked.append(conn.host_id)
        answer = self._answers.get(conn.host_id)
        if answer is None:
            return  # an older host ignores the unknown frame
        conn.pending_plan_limits[frame.request_id].set_result(
            {"status": "ok", "payload": answer, "error": None}
        )


def _conn(host_id: str, owner: str, connected_at: float = 0.0) -> Any:
    return SimpleNamespace(
        host_id=host_id, owner=owner, connected_at=connected_at, pending_plan_limits={}
    )


def _request(registry: Any, host_owners: dict[str, str]) -> Any:
    host_store = SimpleNamespace(
        get_host=lambda host_id: (
            SimpleNamespace(user_id=host_owners[host_id]) if host_id in host_owners else None
        )
    )
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(host_registry=registry, host_store=host_store))
    )


def _payload(tag: str) -> dict[str, Any]:
    return {"providers": [{"id": "claude", "tag": tag}], "fetched_at": 1.0}


def _machine_host(monkeypatch: pytest.MonkeyPatch, host_id: str | None) -> None:
    found = None if host_id is None else SimpleNamespace(host_id=host_id)
    monkeypatch.setattr(identity, "load_host_identity_if_present", lambda: found)


def _server_side_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_collect() -> dict[str, Any]:
        return _payload("server-os-user")

    monkeypatch.setattr(plan_limits, "collect_plan_limits", fake_collect)


def test_plan_limits_frames_round_trip() -> None:
    request = decode_host_frame(encode_host_frame(HostPlanLimitsFrame(request_id="r1")))
    assert request == HostPlanLimitsFrame(request_id="r1")

    result = HostPlanLimitsResultFrame(request_id="r1", status="ok", payload=_payload("x"))
    assert decode_host_frame(encode_host_frame(result)) == result


def test_each_user_gets_their_own_hosts_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    _machine_host(monkeypatch, "h-alice")
    _server_side_reading(monkeypatch)
    registry = _FakeRegistry(
        [_conn("h-alice", "alice"), _conn("h-bob", "bob")],
        {"h-alice": _payload("alice"), "h-bob": _payload("bob")},
    )
    request = _request(registry, {"h-alice": "alice", "h-bob": "bob"})

    alice = asyncio.run(plan_limits.plan_limits_for_user(request, "alice"))
    bob = asyncio.run(plan_limits.plan_limits_for_user(request, "bob"))

    assert alice == _payload("alice")
    assert bob == _payload("bob")


def test_newest_host_is_asked_first(monkeypatch: pytest.MonkeyPatch) -> None:
    _machine_host(monkeypatch, None)
    registry = _FakeRegistry(
        [_conn("old", "bob", connected_at=1.0), _conn("new", "bob", connected_at=2.0)],
        {"old": _payload("old"), "new": _payload("new")},
    )

    got = asyncio.run(plan_limits.plan_limits_for_user(_request(registry, {}), "bob"))

    assert got == _payload("new")
    assert registry.asked == ["new"]


def test_user_without_a_host_never_sees_the_server_users_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _machine_host(monkeypatch, "h-alice")
    _server_side_reading(monkeypatch)
    registry = _FakeRegistry([_conn("h-alice", "alice")], {"h-alice": _payload("alice")})

    got = asyncio.run(
        plan_limits.plan_limits_for_user(_request(registry, {"h-alice": "alice"}), "bob")
    )

    assert got["providers"] == []
    assert registry.asked == []


def test_machine_owner_falls_back_to_server_reading_when_host_is_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plan_limits, "_HOST_PLAN_LIMITS_TIMEOUT_S", 0.05)
    _machine_host(monkeypatch, "h-alice")
    _server_side_reading(monkeypatch)
    registry = _FakeRegistry([_conn("h-alice", "alice")], {"h-alice": None})
    request = _request(registry, {"h-alice": "alice"})

    got = asyncio.run(plan_limits.plan_limits_for_user(request, "alice"))

    assert got == _payload("server-os-user")
    assert registry.get("h-alice").pending_plan_limits == {}


def test_silent_host_of_another_user_yields_no_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plan_limits, "_HOST_PLAN_LIMITS_TIMEOUT_S", 0.05)
    _machine_host(monkeypatch, "h-alice")
    _server_side_reading(monkeypatch)
    registry = _FakeRegistry([_conn("h-bob", "bob")], {"h-bob": None})

    got = asyncio.run(
        plan_limits.plan_limits_for_user(_request(registry, {"h-alice": "alice"}), "bob")
    )

    assert got["providers"] == []


def test_host_reading_is_cached_per_user(monkeypatch: pytest.MonkeyPatch) -> None:
    _machine_host(monkeypatch, None)
    registry = _FakeRegistry([_conn("h-bob", "bob")], {"h-bob": _payload("bob")})
    request = _request(registry, {})

    asyncio.run(plan_limits.plan_limits_for_user(request, "bob"))
    asyncio.run(plan_limits.plan_limits_for_user(request, "bob"))

    assert registry.asked == ["h-bob"]
