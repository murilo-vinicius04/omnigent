"""Deciding when to write the context down and compact.

The decision is kept away from delivery so it can be driven here turn by
turn, the way a session actually crosses the threshold.
"""

from __future__ import annotations

import pytest

from omnigent.server import auto_compact


@pytest.fixture(autouse=True)
def _clean_state() -> None:
    auto_compact._ASKED.clear()
    auto_compact._LAST_COMPACTED.clear()
    auto_compact._REASON.clear()
    auto_compact._CLAIMED.clear()
    auto_compact._QUOTA_HANDLED.clear()


def _labels(tokens: int, window: int, **extra: str) -> dict[str, str]:
    return {
        "omnigent.last_context_tokens": str(tokens),
        "omnigent.last_context_window": str(window),
        **extra,
    }


def test_a_session_below_the_threshold_is_left_alone() -> None:
    assert auto_compact.next_step("conv_a", _labels(400_000, 1_000_000)) is None


def test_crossing_the_threshold_asks_for_the_write_up_first() -> None:
    # Two turns on purpose: compacting in the same breath would drop the
    # detail the notes are made of.
    assert auto_compact.next_step("conv_a", _labels(620_000, 1_000_000)) == "write-notes"
    assert auto_compact.next_step("conv_a", _labels(640_000, 1_000_000)) == "compact"


def test_a_compacted_session_starts_over() -> None:
    auto_compact.next_step("conv_a", _labels(700_000, 1_000_000))  # asked
    auto_compact.next_step("conv_a", _labels(700_000, 1_000_000))  # compacted
    # Compaction freed the window, so nothing more to do.
    assert auto_compact.next_step("conv_a", _labels(200_000, 1_000_000)) is None


def test_sessions_are_tracked_apart() -> None:
    assert auto_compact.next_step("conv_a", _labels(700_000, 1_000_000)) == "write-notes"
    assert auto_compact.next_step("conv_b", _labels(700_000, 1_000_000)) == "write-notes"
    assert auto_compact.next_step("conv_b", _labels(700_000, 1_000_000)) == "compact"
    assert auto_compact.next_step("conv_a", _labels(700_000, 1_000_000)) == "compact"


def test_a_session_that_never_reported_its_context_is_left_alone() -> None:
    assert auto_compact.next_step("conv_a", {}) is None
    assert auto_compact.next_step("conv_a", _labels(0, 0)) is None
    assert auto_compact.next_step("conv_a", {"omnigent.last_context_tokens": "nonsense"}) is None


def test_the_threshold_can_be_set_per_session() -> None:
    early = _labels(300_000, 1_000_000, **{auto_compact.THRESHOLD_LABEL: "25"})
    assert auto_compact.next_step("conv_a", early) == "write-notes"
    late = _labels(700_000, 1_000_000, **{auto_compact.THRESHOLD_LABEL: "90"})
    assert auto_compact.next_step("conv_b", late) is None


def test_an_absurd_threshold_is_clamped_rather_than_obeyed() -> None:
    assert auto_compact.threshold_pct({auto_compact.THRESHOLD_LABEL: "0"}) == (
        auto_compact.MIN_THRESHOLD_PCT
    )
    assert auto_compact.threshold_pct({auto_compact.THRESHOLD_LABEL: "400"}) == (
        auto_compact.MAX_THRESHOLD_PCT
    )
    assert auto_compact.threshold_pct({auto_compact.THRESHOLD_LABEL: "soon"}) == (
        auto_compact.DEFAULT_THRESHOLD_PCT
    )
    assert auto_compact.threshold_pct(None) == auto_compact.DEFAULT_THRESHOLD_PCT


def test_the_kill_switch_stops_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(auto_compact.ENABLED_ENV, "0")
    assert auto_compact.next_step("conv_a", _labels(900_000, 1_000_000)) is None
    monkeypatch.setenv(auto_compact.ENABLED_ENV, "1")
    assert auto_compact.next_step("conv_a", _labels(900_000, 1_000_000)) == "write-notes"


def test_forgetting_a_session_cancels_the_pending_compaction() -> None:
    # Delivery failed, so the turn that would have carried the notes never ran.
    assert auto_compact.next_step("conv_a", _labels(700_000, 1_000_000)) == "write-notes"
    auto_compact.forget("conv_a")
    assert auto_compact.next_step("conv_a", _labels(700_000, 1_000_000)) == "write-notes"


def test_a_compaction_that_freed_nothing_does_not_ask_again_immediately() -> None:
    labels = _labels(900_000, 1_000_000)
    assert auto_compact.next_step("conv_a", labels, now=0.0) == "write-notes"
    assert auto_compact.next_step("conv_a", labels, now=10.0) == "compact"
    # The window is still full: without the cooldown this asks again forever.
    assert auto_compact.next_step("conv_a", labels, now=20.0) is None
    assert (
        auto_compact.next_step("conv_a", labels, now=auto_compact.RETRY_AFTER_S + 30.0)
        == "write-notes"
    )


def test_the_state_a_turn_end_should_publish() -> None:
    # The reader watched a compaction happen with nothing on screen saying so,
    # and then read a stale percentage and thought it had failed. Each step
    # names what the UI should say.
    assert auto_compact.state_for("write-notes") == auto_compact.STATE_WRITING_NOTES
    assert auto_compact.state_for("compact") == auto_compact.STATE_COMPACTED
    # Nothing to do: whatever the badge said, the turn that just ended has a
    # fresh measurement, so the badge is cleared.
    assert auto_compact.state_for(None) == ""


def test_the_prompt_names_the_share_and_asks_for_a_file() -> None:
    text = auto_compact.DOCUMENTATION_PROMPT.format(pct=62)
    assert "62%" in text
    assert "compacted" in text
    # The point of compacting early: the notes outlive the summary.
    assert "docs" in text or "notes" in text


async def test_compaction_waits_for_the_turns_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both steps start a new turn. Started before the summary lands, the page
    files the reply as history and never narrates it (seen twice, 2026-09-13)."""
    import asyncio

    from omnigent.server.routes._sessions import orchestration

    ran: list[str] = []

    async def _fake_run(session_id: str, *_args: object) -> None:
        ran.append(session_id)

    monkeypatch.setattr(orchestration, "_run_auto_compaction", _fake_run)
    monkeypatch.setattr(orchestration, "_AUTO_COMPACT_AFTER_SUMMARY_GRACE_S", 0.0)

    summary: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    orchestration._spawn_auto_compaction("conv_a", object(), None, after=summary)  # type: ignore[arg-type]
    await asyncio.sleep(0.05)
    assert ran == [], "compaction started before the summary landed"

    summary.set_result(None)
    for _ in range(20):
        await asyncio.sleep(0.01)
        if ran:
            break
    assert ran == ["conv_a"]


async def test_a_summary_that_never_lands_does_not_block_compaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from omnigent.server.routes._sessions import orchestration

    ran: list[str] = []

    async def _fake_run(session_id: str, *_args: object) -> None:
        ran.append(session_id)

    monkeypatch.setattr(orchestration, "_run_auto_compaction", _fake_run)
    monkeypatch.setattr(orchestration, "_AUTO_COMPACT_SUMMARY_WAIT_S", 0.05)
    monkeypatch.setattr(orchestration, "_AUTO_COMPACT_AFTER_SUMMARY_GRACE_S", 0.0)

    never: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    orchestration._spawn_auto_compaction("conv_b", object(), None, after=never)  # type: ignore[arg-type]
    for _ in range(50):
        await asyncio.sleep(0.01)
        if ran:
            break
    assert ran == ["conv_b"]
    never.cancel()


# -- plan window pressure -----------------------------------------------------


def _pressure(pct: float, key: str = "codex:5h:2026-10-16T19:13:05+00:00"):
    return auto_compact.Pressure(window="5h", pct=pct, limit=80.0, key=key, resets="at 16:13")


def test_a_window_past_its_ceiling_asks_for_the_write_up_even_with_a_small_context() -> None:
    # 120k of 1M is nowhere near the context threshold; the window is what matters.
    labels = _labels(120_000, 1_000_000)
    assert auto_compact.next_step("conv_a", labels, pressure=_pressure(86)) == "write-notes"
    assert auto_compact.reason("conv_a") == "quota"
    assert auto_compact.next_step("conv_a", labels, pressure=_pressure(88)) == "compact"


def test_a_window_below_its_ceiling_or_already_blocked_does_nothing() -> None:
    labels = _labels(120_000, 1_000_000)
    assert auto_compact.next_step("conv_a", labels, pressure=_pressure(79)) is None
    # At 100% the session cannot answer a write-up request anyway.
    assert auto_compact.next_step("conv_a", labels, pressure=_pressure(100)) is None


def test_a_context_too_small_to_matter_is_not_compacted_for_the_window() -> None:
    assert (
        auto_compact.next_step("conv_a", _labels(10_000, 1_000_000), pressure=_pressure(90))
        is None
    )


def test_one_window_asks_once_but_the_next_window_asks_again() -> None:
    labels = _labels(120_000, 1_000_000)
    assert auto_compact.next_step("conv_a", labels, pressure=_pressure(86)) == "write-notes"
    assert auto_compact.next_step("conv_a", labels, pressure=_pressure(87)) == "compact"
    # Still big and still past the ceiling in the same window: not asked again.
    assert auto_compact.next_step("conv_a", labels, now=10_000.0, pressure=_pressure(90)) is None
    later = _pressure(86, key="codex:5h:2026-10-17T00:13:05+00:00")
    assert auto_compact.next_step("conv_a", labels, now=20_000.0, pressure=later) == "write-notes"


def test_a_crossing_that_could_not_be_asked_is_asked_again_next_turn() -> None:
    labels = _labels(120_000, 1_000_000)
    assert auto_compact.next_step("conv_a", labels, pressure=_pressure(86)) == "write-notes"
    auto_compact.forget("conv_a")  # the runner was unreachable
    assert auto_compact.next_step("conv_a", labels, pressure=_pressure(86)) == "write-notes"


def test_the_prompt_says_which_window_and_when_it_resets() -> None:
    auto_compact.next_step("conv_a", _labels(120_000, 1_000_000), pressure=_pressure(86))
    text = auto_compact.prompt_for("conv_a", 12.0, _pressure(86))
    assert "5h plan window" in text and "86%" in text and "at 16:13" in text
    # A context-driven request keeps the old wording.
    auto_compact.forget("conv_b")
    assert "12% of its context window" in auto_compact.prompt_for("conv_b", 12.0)


def test_a_claimed_session_is_left_to_the_flow_that_claimed_it() -> None:
    assert auto_compact.claim("conv_a") is True
    assert auto_compact.claim("conv_a") is False
    assert auto_compact.next_step("conv_a", _labels(900_000, 1_000_000)) is None
    assert (
        auto_compact.next_step("conv_a", _labels(120_000, 1_000_000), pressure=_pressure(90))
        is None
    )
    auto_compact.release("conv_a", compacted=True)
    # Just compacted: the share check holds off, so it is not asked at once.
    assert auto_compact.next_step("conv_a", _labels(900_000, 1_000_000)) is None


async def test_a_turn_end_past_the_window_ceiling_asks_then_compacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from omnigent.server.routes._sessions import orchestration

    labels = _labels(120_000, 1_000_000)
    store = SimpleNamespace(
        get_conversation=lambda _sid: SimpleNamespace(labels=dict(labels), runner_id="r1"),
        set_labels=lambda _sid, new: labels.update(new),
    )
    sent: list[str] = []
    forwarded: list[dict] = []

    async def _client(*_args: object) -> object:
        return object()

    async def _dispatch(_sid, _conv, body, *_args, **_kwargs) -> None:
        sent.append(body.data["content"][0]["text"])

    async def _forward(_sid, _router, body, **_kwargs):
        forwarded.append(body)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(orchestration, "_get_runner_client", _client)
    monkeypatch.setattr(orchestration, "_ensure_runner_relay", lambda *a, **k: None)
    monkeypatch.setattr(orchestration, "_dispatch_session_event_to_runner", _dispatch)
    monkeypatch.setattr(orchestration, "_forward_session_change_to_runner", _forward)
    monkeypatch.setattr(orchestration, "_plan_pressure", lambda _labels: _pressure(86))

    await orchestration._run_auto_compaction("conv_q", store, None)  # type: ignore[arg-type]
    assert len(sent) == 1 and "5h plan window" in sent[0] and "86%" in sent[0]
    assert forwarded == []  # the write-up comes first
    assert labels[auto_compact.STATE_LABEL] == auto_compact.STATE_WRITING_NOTES

    await orchestration._run_auto_compaction("conv_q", store, None)  # type: ignore[arg-type]
    assert [f["type"] for f in forwarded] == ["compact"]
    assert labels[auto_compact.STATE_LABEL] == auto_compact.STATE_COMPACTED


async def test_the_compaction_step_is_skipped_when_the_write_up_filled_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from omnigent.server.routes._sessions import orchestration

    labels = _labels(120_000, 1_000_000)
    store = SimpleNamespace(
        get_conversation=lambda _sid: SimpleNamespace(labels=dict(labels), runner_id="r1"),
        set_labels=lambda _sid, new: labels.update(new),
    )
    forwarded: list[dict] = []

    async def _client(*_args: object) -> object:
        return object()

    async def _dispatch(*_args: object, **_kwargs: object) -> None:
        return None

    async def _forward(_sid, _router, body, **_kwargs):
        forwarded.append(body)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(orchestration, "_get_runner_client", _client)
    monkeypatch.setattr(orchestration, "_ensure_runner_relay", lambda *a, **k: None)
    monkeypatch.setattr(orchestration, "_dispatch_session_event_to_runner", _dispatch)
    monkeypatch.setattr(orchestration, "_forward_session_change_to_runner", _forward)
    monkeypatch.setattr(orchestration, "_plan_pressure", lambda _labels: _pressure(86))

    await orchestration._run_auto_compaction("conv_f", store, None)  # type: ignore[arg-type]
    monkeypatch.setattr(orchestration, "_plan_full", lambda _labels: "5h window full until 16:13")
    await orchestration._run_auto_compaction("conv_f", store, None)  # type: ignore[arg-type]
    assert forwarded == []  # nothing typed into a pane whose plan turns requests away
    assert labels[auto_compact.STATE_LABEL] == ""
