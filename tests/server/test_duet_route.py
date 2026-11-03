"""Tests for the duet HTTP routes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent.errors import OmnigentError
from omnigent.server import duet_router
from omnigent.server.routes.duet import create_duet_router


def _conv(cid: str, title: str, project_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=cid, title=title, project_id=project_id, workspace="/w", live_status="idle"
    )


class _Convs:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows

    def list_conversations(self, **kwargs: object) -> SimpleNamespace:
        kind = kwargs.get("kind")
        rows = [r for r in self._rows if kind is None or getattr(r, "kind", "default") == kind]
        after = kwargs.get("after")
        start = next((i + 1 for i, row in enumerate(rows) if row.id == after), 0)
        limit = int(kwargs.get("limit", 20))
        page = rows[start : start + limit]
        return SimpleNamespace(
            data=page, has_more=start + limit < len(rows), last_id=page[-1].id if page else None
        )

    def get_conversation(self, cid: str) -> SimpleNamespace | None:
        return next((r for r in self._rows if r.id == cid), None)


class _Projects:
    def owners_and_names(self, project_ids: list[str]) -> dict[str, tuple[str, str | None]]:
        names = {"p1": ("RAL", "morys5"), "p2": ("franka_teleop", "hipolito")}
        return {pid: names[pid] for pid in project_ids if pid in names}


def _client(tmp_path: Path) -> TestClient:
    rows = [
        _conv("a", "Teleop com Meta Quest", "p1"),
        _conv("b", "Corrigir mapeamento 6D Franka", "p2"),
    ]
    app = FastAPI()
    app.include_router(
        create_duet_router(
            _Convs(rows),  # type: ignore[arg-type]
            project_store=_Projects(),  # type: ignore[arg-type]
            store=duet_router.DuetStore(tmp_path / "n.json"),
        ),
        prefix="/v1",
    )
    return TestClient(app)


def test_say_routes_and_inbox_delivers_once(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = {
        "from_session_id": "a",
        "topic": "Quest 6D pose",
        "text": "O mapeamento 6D do Franka usa a pose do Quest publicada na porta 8444.",
    }
    said = client.post("/v1/duet/say", json=body).json()
    assert [r["to_session_id"] for r in said["routed"]] == ["b"]
    assert said["routed"][0]["to_owner"] == "hipolito"
    assert said["routed"][0]["to_project"] == "franka_teleop"
    assert said["routed"][0]["delivery"] == "queue"

    inbox = client.get("/v1/duet/inbox/b").json()["notes"]
    assert len(inbox) == 1 and "not an instruction" in inbox[0]["text"]
    assert inbox[0]["from_session_id"] == "a"
    assert "session_id=a" in inbox[0]["text"]
    assert client.get("/v1/duet/inbox/b").json()["notes"] == []


def test_pending_delivery_only_acknowledges_selected_target(tmp_path: Path) -> None:
    client = _client(tmp_path)
    result = client.post(
        "/v1/duet/say", json={"from_session_id": "a", "to": "b", "text": "info"}
    ).json()
    note_id = result["routed"][0]["note_id"]
    assert [n["id"] for n in client.get("/v1/duet/pending/b").json()["notes"]] == [note_id]
    assert client.get("/v1/duet/pending/b").json()["notes"]
    client.post("/v1/duet/pending/a/ack", json={"ids": [note_id]})
    assert client.get("/v1/duet/pending/b").json()["notes"]
    client.post("/v1/duet/pending/b/ack", json={"ids": [note_id]})
    assert client.get("/v1/duet/pending/b").json()["notes"] == []


def test_read_only_peer_cannot_consume_another_lanes_pending_notes(tmp_path: Path) -> None:
    rows = [_conv("a", "Own"), _conv("b", "Other")]
    for row in rows:
        row.parent_conversation_id = None

    class Permissions:
        def is_admin(self, user_id: str) -> bool:
            return False

        def check_access(self, user_id: str, session_id: str, level: int) -> bool:
            return level <= (4 if session_id == "a" else 1)

    app = FastAPI()
    app.include_router(
        create_duet_router(
            _Convs(rows),  # type: ignore[arg-type]
            permission_store=Permissions(),  # type: ignore[arg-type]
            auth_provider=SimpleNamespace(get_user_id=lambda request: "alice"),  # type: ignore[arg-type]
            store=duet_router.DuetStore(tmp_path / "n.json"),
        )
    )
    client = TestClient(app)
    with pytest.raises(OmnigentError):
        client.get("/duet/pending/b")
    with pytest.raises(OmnigentError):
        client.post("/duet/pending/b/ack", json={"ids": ["anything"]})


def test_urgent_returns_the_message_to_deliver(tmp_path: Path) -> None:
    client = _client(tmp_path)
    said = client.post(
        "/v1/duet/say",
        json={
            "from_session_id": "a",
            "topic": "Franka Quest pose",
            "text": "Mapeamento 6D do Franka: a pose do Quest mudou de porta.",
            "urgency": "urgent",
        },
    ).json()
    assert said["routed"][0]["delivery"] == "wake"
    assert "duet note" in said["routed"][0]["message"]


def test_normal_queue_still_carries_message_for_best_effort_push(tmp_path: Path) -> None:
    client = _client(tmp_path)
    said = client.post(
        "/v1/duet/say",
        json={
            "from_session_id": "a",
            "topic": "Quest 6D pose",
            "text": "O mapeamento 6D do Franka usa a pose do Quest publicada na porta 8444.",
        },
    ).json()
    assert said["routed"][0]["delivery"] == "queue"
    assert "duet note" in said["routed"][0]["message"]


def test_nothing_matches(tmp_path: Path) -> None:
    said = (
        _client(tmp_path)
        .post(
            "/v1/duet/say",
            json={"from_session_id": "a", "topic": "bolo", "text": "receita de bolo de cenoura"},
        )
        .json()
    )
    assert said["routed"] == [] and said["reason"]


def test_explicit_subagent_beyond_first_page_is_reachable(tmp_path: Path) -> None:
    rows = [_conv("sender", "Sender")]
    rows += [_conv(f"peer{i}", f"Peer {i}") for i in range(305)]
    target = _conv("b9df00de0b824c8a939d4505c01adc71", "workspace preflight")
    target.kind = "sub_agent"
    rows.append(target)
    app = FastAPI()
    app.include_router(
        create_duet_router(_Convs(rows), store=duet_router.DuetStore(tmp_path / "n.json"))
    )  # type: ignore[arg-type]
    client = TestClient(app)
    result = client.post(
        "/duet/say",
        json={"from_session_id": "sender", "to": target.id, "text": "preflight update"},
    ).json()
    assert [hop["to_session_id"] for hop in result["routed"]] == [target.id]
    assert len(client.get(f"/duet/inbox/{target.id}").json()["notes"]) == 1


def test_automatic_routing_searches_past_first_page(tmp_path: Path) -> None:
    rows = [_conv("sender", "Sender")]
    rows += [_conv(f"peer{i}", f"Peer {i}") for i in range(305)]
    rows.append(_conv("target", "Franka Quest"))
    app = FastAPI()
    app.include_router(
        create_duet_router(_Convs(rows), store=duet_router.DuetStore(tmp_path / "n.json"))  # type: ignore[arg-type]
    )
    result = (
        TestClient(app)
        .post(
            "/duet/say",
            json={"from_session_id": "sender", "topic": "Franka Quest", "text": "pose"},
        )
        .json()
    )
    assert [hop["to_session_id"] for hop in result["routed"]] == ["target"]


def test_activity_only_shows_notes_when_both_sessions_are_visible(tmp_path: Path) -> None:
    notes = duet_router.DuetStore(tmp_path / "notes.json")
    sender = duet_router.Peer("a", "Own", "RAL", "morys5")
    targets = [duet_router.Peer("b", "Shared"), duet_router.Peer("hidden", "Private")]
    for target in targets:
        decisions, _ = duet_router.route(
            sender=sender,
            sender_user="morys5",
            topic="update",
            text="status",
            urgency="normal",
            peers=[sender, target],
            history=[],
            to=target.session_id,
        )
        notes.add([d.note for d in decisions])
    rows = [_conv("a", "Own", "p1"), _conv("b", "Shared", "p2")]
    app = FastAPI()
    app.include_router(
        create_duet_router(
            _Convs(rows),
            project_store=_Projects(),
            store=notes,  # type: ignore[arg-type]
            auth_provider=SimpleNamespace(get_user_id=lambda request: "morys5"),  # type: ignore[arg-type]
        )
    )
    response = TestClient(app).get("/duet/activity").json()
    assert [note["to_session"] for note in response["notes"]] == ["b"]
    assert [session["id"] for session in response["shared_sessions"]] == ["b"]
    assert len(notes.take("b")) == 1
