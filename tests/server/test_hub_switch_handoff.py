"""A model switch through the hub writes the chat up and compacts it first."""

from __future__ import annotations

import asyncio
import time

import pytest

from omnigent.server import auto_compact, hub_handoff
from omnigent.server.hub import Hub, HubBackend, HubCandidate, HubSession, HubState, TurnState
from omnigent.server.hub_models import run_from_snapshot
from tests.server.test_hub_models import AMARRAS, CLAUDE_SNAPSHOT, _Router


@pytest.fixture(autouse=True)
def _clean() -> None:
    auto_compact._CLAIMED.clear()
    auto_compact._ASKED.clear()
    auto_compact._LAST_COMPACTED.clear()


class _Lane:
    def __init__(self, tokens: int = 300_000, *, handoff: bool = True) -> None:
        self.session = HubSession(
            id=AMARRAS, title="Amarras", run=run_from_snapshot(CLAUDE_SNAPSHOT)
        )
        self.tokens = tokens
        self.handoff = handoff
        self.events: list[str] = []
        self.status_now = "idle"
        self.compact_lands = True
        self.blocked_by: str | None = None
        self.compacted = False
        self.tasks: list[asyncio.Future[None]] = []

    def backend(self) -> HubBackend:
        async def sessions(ids):
            return [self.session] if self.session.id in ids else []

        async def listing():
            return [HubCandidate(id=self.session.id, title="Amarras")]

        async def nothing(*_args):
            return ""

        async def send(session_id, text, files=()):
            self.events.append("write-up")
            self.status_now = "running"

            async def finish():
                await asyncio.sleep(0.03)
                self.status_now = "idle"

            self.tasks.append(asyncio.ensure_future(finish()))

        async def turn(_session_id, _since):
            return TurnState(status="idle")

        async def configure(session_id, model, effort):
            self.events.append(f"configure:{model}")

        async def status(_session_id):
            return self.status_now

        async def projects():
            from omnigent.server.hub import ProjectRecord

            return [ProjectRecord(id="aaaa" + "0" * 28, name="Amarras", chat_ids=(AMARRAS,))]

        async def tokens(_sid):
            return self.tokens

        async def compact(_sid):
            self.events.append("compact")
            self.compacted = self.compact_lands

        async def landed(_sid, _since):
            return self.compacted

        async def blocked(_sid):
            return self.blocked_by

        return HubBackend(
            sessions=sessions,
            listing=listing,
            activity=nothing,
            send=send,
            check=nothing,
            turn=turn,
            projects=projects,
            create_project=nothing,
            rename_project=nothing,
            delete_project=nothing,
            file_chat=nothing,
            configure=configure,
            status=status,
            compaction=(
                hub_handoff.Compaction(
                    context_tokens=tokens,
                    compact=compact,
                    compacted_since=landed,
                    blocked=blocked,
                )
                if self.handoff
                else None
            ),
        )


def _hub(tmp_path, lane: _Lane) -> Hub:
    async def factory():
        return _Router()

    hub = Hub("u1", router_factory=factory, state=HubState("u1", root=tmp_path), poll_s=0.01)
    hub.bind(lane.backend())
    return hub


async def _settle(hub: Hub) -> None:
    deadline = time.monotonic() + 5
    while hub._tasks and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert not hub._tasks


async def test_a_model_switch_writes_up_and_compacts_before_it_applies(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(hub_handoff, "START_GRACE_S", 0.05)
    lane = _Lane()
    hub = _hub(tmp_path, lane)
    told = await hub.set_model(lane.session, "sonnet", "", via="typed")
    assert "write down where it is and compact before moving to Sonnet 5.5" in told.text
    assert lane.events == []  # nothing happened inline: the hub is not held up
    await _settle(hub)
    assert lane.events == ["write-up", "compact", "configure:sonnet"]
    last = hub.state.entries[-1]
    assert last.text.startswith("Switched Amarras to Sonnet 5.5;")
    assert "Its context was written up and compacted first." in last.text


async def test_a_small_context_switches_at_once_with_nothing_extra(tmp_path) -> None:
    lane = _Lane(tokens=8_000)
    hub = _hub(tmp_path, lane)
    await hub.set_model(lane.session, "sonnet", "", via="typed")
    await _settle(hub)
    assert lane.events == ["configure:sonnet"]
    assert "compacted" not in hub.state.entries[-1].text


async def test_a_compaction_that_never_lands_still_switches_and_says_so(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(hub_handoff, "START_GRACE_S", 0.05)
    monkeypatch.setattr(hub_handoff, "COMPACT_WAIT_S", 0.2)
    lane = _Lane()
    lane.compact_lands = False
    hub = _hub(tmp_path, lane)
    await hub.set_model(lane.session, "sonnet", "", via="typed")
    await _settle(hub)
    assert lane.events[-1] == "configure:sonnet"
    assert "did not confirm in time, so it was switched as it was" in hub.state.entries[-1].text


async def test_effort_alone_and_the_model_it_already_runs_are_not_compacted(tmp_path) -> None:
    lane = _Lane()
    hub = _hub(tmp_path, lane)
    await hub.set_model(lane.session, "", "high", via="typed")
    await hub.set_model(lane.session, "opus", "", via="typed")  # it runs Opus already
    await _settle(hub)
    assert "write-up" not in lane.events and "compact" not in lane.events
    assert lane.events == ["configure:None", "configure:opus"]


async def test_without_the_compaction_capability_a_switch_applies_at_once(tmp_path) -> None:
    lane = _Lane(handoff=False)
    hub = _hub(tmp_path, lane)
    entry = await hub.set_model(lane.session, "sonnet", "", via="typed")
    assert entry.text.startswith("Switched Amarras to Sonnet 5.5;")
    assert lane.events == ["configure:sonnet"]


async def test_a_second_switch_while_one_is_in_flight_is_not_stacked(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(hub_handoff, "START_GRACE_S", 0.05)
    lane = _Lane()
    hub = _hub(tmp_path, lane)
    await hub.set_model(lane.session, "sonnet", "", via="typed")
    second = await hub.set_model(lane.session, "haiku", "", via="typed")
    await _settle(hub)
    # The second one is applied directly instead of starting another write-up.
    assert lane.events.count("write-up") == 1
    assert second.text.startswith("Switched Amarras to Haiku 5.5;")


async def test_the_store_backend_reads_context_and_compactions_from_the_store() -> None:
    from types import SimpleNamespace

    from tests.server.test_hub import AMARRAS as STORE_CHAT
    from tests.server.test_hub import _ConvStore, _store_backend

    store = _ConvStore()
    chat = store.get_conversation(STORE_CHAT)
    chat.labels = {"omnigent.last_context_tokens": "312000"}
    compaction = _store_backend(store).compaction
    assert compaction is not None
    assert await compaction.context_tokens(STORE_CHAT) == 312_000
    assert await compaction.compacted_since(STORE_CHAT, 100.0) is False
    store.items[STORE_CHAT].append(SimpleNamespace(type="compaction", created_at=200.0, data={}))
    assert await compaction.compacted_since(STORE_CHAT, 100.0) is True
    # A compaction from before the request does not count as the answer to it.
    assert await compaction.compacted_since(STORE_CHAT, 300.0) is False


async def test_a_switch_after_the_plan_is_full_goes_straight_through_and_says_why(
    tmp_path,
) -> None:
    lane = _Lane()
    lane.blocked_by = "5h window full until 16:13"
    hub = _hub(tmp_path, lane)
    entry = await hub.set_model(lane.session, "sonnet", "", via="typed")
    await _settle(hub)
    # No write-up asked, no compaction, no waiting: the model is simply switched.
    assert lane.events == ["configure:sonnet"]
    assert entry.text.startswith("Switched Amarras to Sonnet 5.5;")
    assert "plan window is full, so it could not write up or compact first" in entry.text
