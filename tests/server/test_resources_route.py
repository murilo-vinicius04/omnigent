"""Tests for the resource board routes: who holds what, the queue, and the notices."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent.server import duet_router
from omnigent.server.resource_board import ResourceBoard
from omnigent.server.routes.resources import create_resources_router


class _Convs:
    def get_conversation(self, cid: str) -> SimpleNamespace:
        return SimpleNamespace(id=cid, title=f"lane {cid}")


def _client(tmp_path: Path) -> tuple[TestClient, duet_router.DuetStore]:
    notes = duet_router.DuetStore(tmp_path / "notes.json")
    app = FastAPI()
    app.include_router(
        create_resources_router(
            _Convs(),  # type: ignore[arg-type]
            board=ResourceBoard(tmp_path / "resources.json"),
            notes=notes,
        ),
        prefix="/v1",
    )
    return TestClient(app), notes


def _acquire(http: TestClient, who: str, name: str = "Franka Robot") -> dict:
    resp = http.post(
        f"/v1/resources/{name}/acquire", json={"session_id": who, "purpose": f"{who} test"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_a_second_lane_queues_and_both_sides_are_told(tmp_path: Path) -> None:
    http, notes = _client(tmp_path)

    first = _acquire(http, "a")
    second = _acquire(http, "b")

    assert first["result"] == "granted" and first["resource"] == "franka-robot"
    assert (second["result"], second["position"]) == ("queued", 1)
    waiting = notes.take("a")
    assert len(waiting) == 1 and waiting[0].topic == "franka-robot: waiting"
    assert "lane b" in waiting[0].text

    released = http.post("/v1/resources/franka-robot/release", json={"session_id": "a"}).json()
    assert released["result"] == "released" and released["holder"]["session_id"] == "b"
    turn = notes.take("b")
    assert len(turn) == 1 and "now your turn" in turn[0].text
    assert "not permission to move real hardware" in turn[0].text


def test_the_board_lists_holders_and_waiters(tmp_path: Path) -> None:
    http, _ = _client(tmp_path)
    _acquire(http, "a")
    _acquire(http, "b")
    _acquire(http, "c", name="zed2")

    listed = {r["resource"]: r for r in http.get("/v1/resources").json()["resources"]}
    one = http.get("/v1/resources/franka-robot").json()

    assert set(listed) == {"franka-robot", "zed2"}
    assert [p["session_id"] for p in listed["franka-robot"]["queue"]] == ["b"]
    assert one["holder"]["lane"] == "lane a" and one["state"] == "held"
    assert http.get("/v1/resources/never-used").json()["state"] == "free"


def test_releasing_needs_a_lane_or_an_explicit_override(tmp_path: Path) -> None:
    http, _ = _client(tmp_path)
    _acquire(http, "a")

    assert http.post("/v1/resources/franka-robot/release", json={}).status_code == 422
    other = http.post("/v1/resources/franka-robot/release", json={"session_id": "zzz"}).json()
    assert other["result"] == "not_holding" and other["holder"]["session_id"] == "a"

    forced = http.post("/v1/resources/franka-robot/release", json={"force": True}).json()
    assert forced["result"] == "released" and forced["state"] == "free"


def test_an_empty_name_is_rejected(tmp_path: Path) -> None:
    http, _ = _client(tmp_path)
    resp = http.post("/v1/resources/!!!/acquire", json={"session_id": "a", "purpose": "x"})
    assert resp.status_code == 422


def test_holder_can_yield_while_keeping_place(tmp_path: Path) -> None:
    http, notes = _client(tmp_path)
    _acquire(http, "a")
    _acquire(http, "b")
    _acquire(http, "c")
    result = http.post(
        "/v1/resources/franka-robot/release",
        json={"session_id": "a", "yield_to_next": True},
    ).json()
    assert result["result"] == "yielded"
    assert result["holder"]["session_id"] == "b"
    assert [p["session_id"] for p in result["queue"]] == ["c", "a"]
    assert not result["confirmed"]
    assert any(n.topic == "franka-robot: promoted" for n in notes.take("b"))
    not_holder = http.post(
        "/v1/resources/franka-robot/release",
        json={"session_id": "a", "yield_to_next": True},
    ).json()
    assert not_holder["result"] == "not_holding"
    assert [p["session_id"] for p in not_holder["queue"]] == ["c", "a"]


def test_yield_without_waiter_keeps_holder(tmp_path: Path) -> None:
    http, _ = _client(tmp_path)
    _acquire(http, "a")
    result = http.post(
        "/v1/resources/franka-robot/release",
        json={"session_id": "a", "yield_to_next": True},
    ).json()
    assert result["result"] == "no_waiter"
    assert result["holder"]["session_id"] == "a"
