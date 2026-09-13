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


def test_the_prompt_names_the_share_and_asks_for_a_file() -> None:
    text = auto_compact.DOCUMENTATION_PROMPT.format(pct=62)
    assert "62%" in text
    assert "compacted" in text
    # The point of compacting early: the notes outlive the summary.
    assert "docs" in text or "notes" in text
