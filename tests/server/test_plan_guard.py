"""Stopping a working chat before its plan window runs out."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from omnigent.server import auto_compact, hub_handoff, plan_guard, quota_handoff

NOW = datetime(2026, 10, 16, 15, 0, tzinfo=UTC)
CODEX = {
    "omnigent.wrapper": "codex-native-ui",
    "omnigent.last_context_tokens": "300000",
    "omnigent.last_context_window": "475000",
}


@pytest.fixture(autouse=True)
def _clean() -> None:
    auto_compact._CLAIMED.clear()
    auto_compact._ASKED.clear()
    auto_compact._REASON.clear()
    auto_compact._QUOTA_HANDLED.clear()
    auto_compact._LAST_COMPACTED.clear()


def _meter(tmp_path, five: float, week: float = 20, name: str = "plan-limits-codex.json") -> None:
    (tmp_path / name).write_text(
        json.dumps(
            {
                "windows": [
                    {
                        "kind": "session",
                        "percent": five,
                        "resets_at": (NOW + timedelta(hours=1)).isoformat(),
                    },
                    {
                        "kind": "weekly",
                        "percent": week,
                        "resets_at": (NOW + timedelta(days=5)).isoformat(),
                    },
                ],
                "as_of": (NOW - timedelta(seconds=30)).isoformat(),
            }
        )
    )


class _Server:
    def __init__(self, tmp_path, labels=None, working=("conv_a",)) -> None:
        self.tmp_path = tmp_path
        self.labels = {"conv_a": dict(labels or CODEX)}
        self.working = list(working)
        self.refreshed = 0
        self.acted: list[tuple[str, float]] = []

    def deps(self) -> plan_guard.Deps:
        async def labels(session_id):
            return self.labels.get(session_id)

        async def refresh():
            self.refreshed += 1

        async def act(session_id, pressure):
            self.acted.append((session_id, pressure.pct))

        return plan_guard.Deps(
            working=lambda: list(self.working),
            labels=labels,
            refresh=refresh,
            act=act,
            data_dir=self.tmp_path,
            hub_dir=self.tmp_path,
        )


async def _tick(server: _Server) -> list[str]:
    started = await plan_guard.tick(server.deps(), now=NOW)
    await plan_guard.drain()
    return started


async def test_nothing_working_means_no_meter_read_at_all(tmp_path) -> None:
    _meter(tmp_path, 97)
    server = _Server(tmp_path, working=())
    assert await _tick(server) == [] and server.refreshed == 0


async def test_a_working_chat_below_the_ceiling_is_left_running(tmp_path) -> None:
    _meter(tmp_path, 94)
    server = _Server(tmp_path)
    assert await _tick(server) == [] and server.acted == [] and server.refreshed == 1


async def test_a_working_chat_past_the_ceiling_is_stopped_once_per_window(tmp_path) -> None:
    _meter(tmp_path, 96)
    server = _Server(tmp_path)
    assert await _tick(server) == ["conv_a"]
    assert server.acted == [("conv_a", 96)]
    assert await _tick(server) == []  # the same window is not handled twice
    assert len(server.acted) == 1


async def test_a_full_window_is_not_worth_interrupting_for(tmp_path) -> None:
    _meter(tmp_path, 100)
    assert await _tick(_Server(tmp_path)) == []


async def test_a_chat_already_written_up_at_a_turn_end_this_window_is_not_stopped(
    tmp_path,
) -> None:
    _meter(tmp_path, 96)
    server = _Server(tmp_path)
    key = quota_handoff.pressure_for(
        CODEX, data_dir=tmp_path, hub_dir=tmp_path, now=NOW, interrupting=True
    ).key
    auto_compact._QUOTA_HANDLED["conv_a"] = key
    assert await _tick(server) == []


async def test_a_chat_another_flow_holds_is_left_to_it(tmp_path) -> None:
    _meter(tmp_path, 96)
    server = _Server(tmp_path)
    auto_compact.claim("conv_a")
    assert await _tick(server) == []


async def test_a_small_context_is_not_worth_stopping(tmp_path) -> None:
    _meter(tmp_path, 96)
    labels = {**CODEX, "omnigent.last_context_tokens": "9000"}
    assert await _tick(_Server(tmp_path, labels)) == []


async def test_antigravity_cannot_be_interrupted_so_it_is_not_tried(tmp_path) -> None:
    _meter(tmp_path, 97, name="plan-limits-antigravity.json")
    labels = {**CODEX, "omnigent.wrapper": "antigravity-native-ui"}
    assert quota_handoff.provider_of(labels) == "gemini"
    assert await _tick(_Server(tmp_path, labels)) == []


async def test_the_weekly_window_has_its_own_higher_ceiling(tmp_path) -> None:
    _meter(tmp_path, 10, week=97)
    assert await _tick(_Server(tmp_path)) == []
    _meter(tmp_path, 10, week=98)
    assert await _tick(_Server(tmp_path)) == ["conv_a"]


async def test_ceilings_and_the_switch_are_set_in_the_budget_file(tmp_path) -> None:
    _meter(tmp_path, 90)
    (tmp_path / "budget.json").write_text(json.dumps({"handoff": {"interrupt_five_hour_pct": 88}}))
    assert await _tick(_Server(tmp_path)) == ["conv_a"]
    auto_compact._QUOTA_HANDLED.clear()
    (tmp_path / "budget.json").write_text(json.dumps({"handoff": {"interrupt": False}}))
    assert await _tick(_Server(tmp_path)) == []


async def test_one_failing_handoff_does_not_stop_the_guard(tmp_path) -> None:
    _meter(tmp_path, 96)
    server = _Server(tmp_path, working=("conv_a", "conv_b"))
    server.labels["conv_b"] = dict(CODEX)
    deps = server.deps()

    async def act(session_id, pressure):
        if session_id == "conv_a":
            raise RuntimeError("runner gone")
        server.acted.append((session_id, pressure.pct))

    deps = plan_guard.Deps(**{**deps.__dict__, "act": act})
    assert await plan_guard.tick(deps, now=NOW) == ["conv_a", "conv_b"]
    await plan_guard.drain()
    assert server.acted == [("conv_b", 96)]


def test_the_guard_can_be_switched_off(monkeypatch) -> None:
    monkeypatch.setenv(plan_guard.ENABLED_ENV, "0")
    assert plan_guard.enabled() is False
    monkeypatch.delenv(plan_guard.ENABLED_ENV)
    assert plan_guard.enabled() is True


# -- the handoff the guard starts ---------------------------------------------


async def test_the_handoff_interrupts_then_asks_then_compacts(monkeypatch) -> None:
    from omnigent.server.routes._sessions import orchestration

    labels = {**CODEX, "omnigent.wrapper": "codex-native-ui"}
    state = {"status": "running", "compacted": False, "full": None}
    events: list[str] = []
    saved: dict[str, str] = {}
    store = SimpleNamespace(
        get_conversation=lambda _sid: SimpleNamespace(
            labels=dict(labels), runner_id="r1", live_status=state["status"]
        ),
        set_labels=lambda _sid, new: saved.update(new),
        list_items=lambda *a, **k: SimpleNamespace(
            data=[SimpleNamespace(created_at=9e12)] if state["compacted"] else []
        ),
    )

    async def interrupt(_sid, _router):
        events.append("interrupt")
        state["status"] = "idle"
        return True

    async def client(*_a):
        return object()

    async def dispatch(_sid, _conv, body, *_a, **_k):
        events.append("ask")
        assert "interrupted your task" in body.data["content"][0]["text"]
        state["status"] = "running"

        import asyncio

        async def finish():
            await asyncio.sleep(0.03)
            state["status"] = "idle"

        state["task"] = asyncio.ensure_future(finish())

    async def forward(_sid, _router, body, **_k):
        events.append(body["type"])
        state["compacted"] = True
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(orchestration, "_interrupt_session", interrupt)
    monkeypatch.setattr(orchestration, "_get_runner_client", client)
    monkeypatch.setattr(orchestration, "_ensure_runner_relay", lambda *a, **k: None)
    monkeypatch.setattr(orchestration, "_dispatch_session_event_to_runner", dispatch)
    monkeypatch.setattr(orchestration, "_forward_session_change_to_runner", forward)
    monkeypatch.setattr(orchestration, "_plan_full", lambda _labels: state["full"])
    monkeypatch.setattr(hub_handoff, "START_GRACE_S", 0.05)
    monkeypatch.setattr(hub_handoff, "POLL_S", 0.01)
    monkeypatch.setattr(orchestration, "_GUARD_IDLE_WAIT_S", 0.2)

    pressure = auto_compact.Pressure("5h", 96.0, 95.0, "codex:5h:x", "at 16:13")
    await orchestration._interrupt_write_up_and_compact("conv_a", pressure, store, None)
    assert events == ["interrupt", "ask", "compact"]
    assert saved[auto_compact.STATE_LABEL] == auto_compact.STATE_COMPACTED
    assert "conv_a" not in auto_compact._CLAIMED


async def test_an_interrupt_the_runner_did_not_take_leaves_the_chat_alone(monkeypatch) -> None:
    from omnigent.server.routes._sessions import orchestration

    store = SimpleNamespace(
        get_conversation=lambda _sid: SimpleNamespace(labels=dict(CODEX), runner_id="r1"),
        set_labels=lambda *_a: None,
    )
    asked: list[str] = []

    async def interrupt(_sid, _router):
        return False

    async def dispatch(*_a, **_k):
        asked.append("ask")

    monkeypatch.setattr(orchestration, "_interrupt_session", interrupt)
    monkeypatch.setattr(orchestration, "_dispatch_session_event_to_runner", dispatch)
    auto_compact._QUOTA_HANDLED["conv_a"] = "codex:5h:x"
    pressure = auto_compact.Pressure("5h", 96.0, 95.0, "codex:5h:x", "at 16:13")
    await orchestration._interrupt_write_up_and_compact("conv_a", pressure, store, None)
    assert asked == []
    # Not stopped, so the crossing is tried again at the next tick.
    assert "conv_a" not in auto_compact._QUOTA_HANDLED
    assert "conv_a" not in auto_compact._CLAIMED
