"""Tests for keeping linked projects readable by the other person."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from omnigent.db.db_models import (
    OmnigentBase,
    SqlConversationMetadata,
    SqlProject,
    SqlSessionPermission,
)
from omnigent.server import duet_links

NOW = 1_700_000_000


def _engine(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    OmnigentBase.metadata.create_all(engine)
    return engine


def _project(session: Session, owner: str, name: str) -> str:
    pid = uuid.uuid4().hex
    session.add(
        SqlProject(
            workspace_id=0, id=pid, name=name, user_id=owner, created_at=NOW, updated_at=NOW
        )
    )
    return pid


def _lane(session: Session, project_id: str, owner: str) -> str:
    cid = uuid.uuid4().hex
    session.add(SqlConversationMetadata(workspace_id=0, id=cid, project_id=project_id))
    session.add(SqlSessionPermission(workspace_id=0, user_id=owner, conversation_id=cid, level=4))
    return cid


def _level(engine, user: str, cid: str) -> int | None:
    with Session(engine) as s:
        return s.scalar(
            select(SqlSessionPermission.level).where(
                SqlSessionPermission.user_id == user, SqlSessionPermission.conversation_id == cid
            )
        )


def test_new_lanes_of_a_linked_project_become_readable_and_nothing_else_does(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    with Session(engine) as s:
        ral = _project(s, "morys5", "RAL")
        freela = _project(s, "morys5", "freela")
        franka = _project(s, "hipolito", "franka_teleop")
        old = _lane(s, ral, "morys5")
        private = _lane(s, freela, "morys5")
        his = _lane(s, franka, "hipolito")
        s.commit()
    links = [
        duet_links.Link("morys5", "hipolito", ("RAL",)),
        duet_links.Link("hipolito", "morys5", ("franka_teleop",)),
    ]

    assert duet_links.sync(engine, links) == {"hipolito": 1, "morys5": 1}
    assert _level(engine, "hipolito", old) == 1 and _level(engine, "morys5", his) == 1
    assert _level(engine, "hipolito", private) is None

    with Session(engine) as s:
        later = _lane(s, ral, "morys5")
        s.commit()
    assert duet_links.sync(engine, links) == {"hipolito": 1}
    assert _level(engine, "hipolito", later) == 1
    assert duet_links.sync(engine, links) == {}


def test_it_never_raises_or_removes_an_existing_grant(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with Session(engine) as s:
        ral = _project(s, "morys5", "RAL")
        lane = _lane(s, ral, "morys5")
        s.add(
            SqlSessionPermission(workspace_id=0, user_id="hipolito", conversation_id=lane, level=3)
        )
        s.commit()

    duet_links.sync(engine, [duet_links.Link("morys5", "hipolito", ("RAL",))])

    assert _level(engine, "hipolito", lane) == 3


def test_links_file_is_read_and_a_broken_one_means_no_links(tmp_path: Path) -> None:
    path = tmp_path / "links.json"
    path.write_text(
        json.dumps(
            {
                "shares": [
                    {"owner": "a", "reader": "b", "projects": ["X", "Y"]},
                    {"owner": "only-this"},
                ]
            }
        )
    )
    assert duet_links.load_links(path) == [duet_links.Link("a", "b", ("X", "Y"))]

    path.write_text("not json")
    assert duet_links.load_links(path) == []
    assert duet_links.load_links(tmp_path / "missing.json") == []


async def test_the_loop_shares_new_lanes_without_being_asked_and_survives_a_bad_round(
    tmp_path: Path, monkeypatch
) -> None:
    import asyncio

    engine = _engine(tmp_path)
    with Session(engine) as s:
        ral = _project(s, "morys5", "RAL")
        s.commit()
    rounds = {"n": 0}
    link = duet_links.Link("morys5", "hipolito", ("RAL",))

    def flaky_links() -> list[duet_links.Link]:
        rounds["n"] += 1
        if rounds["n"] == 1:
            raise RuntimeError("links file unreadable this round")
        return [link]

    monkeypatch.setattr(duet_links, "load_links", flaky_links)
    task = asyncio.create_task(duet_links.keep_shared(engine, interval_s=0.01))
    await asyncio.sleep(0.1)
    with Session(engine) as s:
        lane = _lane(s, ral, "morys5")
        s.commit()
    for _ in range(100):
        if _level(engine, "hipolito", lane) == 1:
            break
        await asyncio.sleep(0.02)
    task.cancel()

    assert rounds["n"] >= 2 and _level(engine, "hipolito", lane) == 1
