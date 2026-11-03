"""Tests for the board where agents take turns on shared resources."""

from __future__ import annotations

from pathlib import Path

from omnigent.server.resource_board import CLAIM_WINDOW_MIN, MAX_TTL_MIN, ResourceBoard


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, minutes: float) -> None:
        self.now += minutes * 60


def _board(tmp_path: Path) -> tuple[ResourceBoard, _Clock]:
    clock = _Clock()
    return ResourceBoard(tmp_path / "r.json", clock), clock


def _take(board: ResourceBoard, who: str, name: str = "franka-robot", ttl: int = 30):
    return board.acquire(
        name,
        session_id=who,
        user=f"user-{who}",
        lane=f"lane {who}",
        purpose=f"{who} test",
        ttl_min=ttl,
    )


def test_first_lane_gets_it_and_renews_by_asking_again(tmp_path: Path) -> None:
    board, clock = _board(tmp_path)

    first, _ = _take(board, "a")
    clock.advance(20)
    again, _ = _take(board, "a")

    assert first["result"] == "granted" and again["result"] == "renewed"
    assert again["minutes_left"] == 30 and again["holder"]["lane"] == "lane a"


def test_others_queue_in_order_and_the_holder_is_told_someone_waits(tmp_path: Path) -> None:
    board, _ = _board(tmp_path)
    _take(board, "a")

    second, events = _take(board, "b")
    third, _ = _take(board, "c")
    repeat, _ = _take(board, "b")

    assert (second["result"], second["position"]) == ("queued", 1)
    assert third["position"] == 2 and repeat["result"] == "already_queued"
    assert [(e.kind, e.session_id) for e in events] == [("waiting", "a")]
    assert "lane b" in events[0].text and "b test" in events[0].text


def test_release_hands_over_and_the_waiter_must_confirm(tmp_path: Path) -> None:
    board, clock = _board(tmp_path)
    _take(board, "a")
    _take(board, "b")

    view, events = board.release("franka-robot", session_id="a")

    assert view["result"] == "released" and view["holder"]["session_id"] == "b"
    assert view["confirmed"] is False and view["minutes_left"] == CLAIM_WINDOW_MIN
    assert [(e.kind, e.session_id) for e in events] == [("promoted", "b")]
    assert "not permission to move real hardware" in events[0].text

    clock.advance(3)
    confirmed, _ = _take(board, "b", ttl=45)
    assert confirmed["result"] == "confirmed" and confirmed["minutes_left"] == 45


def test_an_unconfirmed_waiter_loses_the_turn_to_the_next_one(tmp_path: Path) -> None:
    board, clock = _board(tmp_path)
    _take(board, "a")
    _take(board, "b")
    _take(board, "c")
    board.release("franka-robot", session_id="a")

    clock.advance(CLAIM_WINDOW_MIN + 1)
    view, events = board.status("franka-robot")

    assert view[0]["holder"]["session_id"] == "c" and view[0]["confirmed"] is False
    assert [(e.kind, e.session_id) for e in events] == [("expired", "b"), ("promoted", "c")]


def test_a_lease_that_runs_out_moves_on_and_a_lone_one_frees_the_resource(tmp_path: Path) -> None:
    board, clock = _board(tmp_path)
    _take(board, "a", ttl=10)
    _take(board, "b")

    clock.advance(11)
    now_b, events = board.status("franka-robot")
    assert now_b[0]["holder"]["session_id"] == "b"
    assert events[0].kind == "expired" and events[0].session_id == "a"

    clock.advance(CLAIM_WINDOW_MIN + 1)
    free, _ = board.status("franka-robot")
    assert free[0]["state"] == "free" and free[0]["queue"] == []


def test_leaving_the_queue_not_holding_and_a_person_forcing_it_free(tmp_path: Path) -> None:
    board, _ = _board(tmp_path)
    _take(board, "a")
    _take(board, "b")

    left, _ = board.release("franka-robot", session_id="b")
    stranger, _ = board.release("franka-robot", session_id="zzz")
    forced, events = board.release("franka-robot", force=True)

    assert left["result"] == "left_queue" and stranger["result"] == "not_holding"
    assert forced["result"] == "released" and forced["state"] == "free" and events == []


def test_names_are_normalised_ttl_is_capped_and_state_survives_a_restart(tmp_path: Path) -> None:
    board, clock = _board(tmp_path)
    granted, _ = board.acquire(
        "Franka  Robot!", session_id="a", user="u", lane="l", purpose="p", ttl_min=99999
    )
    assert granted["resource"] == "franka-robot" and granted["minutes_left"] == MAX_TTL_MIN

    reopened = ResourceBoard(tmp_path / "r.json", clock)
    held, _ = reopened.status("FRANKA-ROBOT")
    assert held[0]["holder"]["session_id"] == "a"
    assert [r["resource"] for r in reopened.status()[0]] == ["franka-robot"]


def test_a_lane_that_ends_is_removed_everywhere(tmp_path: Path) -> None:
    board, _ = _board(tmp_path)
    _take(board, "a")
    _take(board, "b")
    _take(board, "b", name="zed2")
    _take(board, "c", name="zed2")

    events = board.drop_session("b")

    robot = board.status("franka-robot")[0][0]
    zed = board.status("zed2")[0][0]
    assert robot["holder"]["session_id"] == "a" and robot["queue"] == []
    assert zed["holder"]["session_id"] == "c" and [e.session_id for e in events] == ["c"]
