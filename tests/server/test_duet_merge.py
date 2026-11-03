"""Tests for the duet (two-person) database merge tool.

The merge imports a second person's Omnigent server database
(their user, projects, hosts, conversations and grants) into
this server's database, so both people can work in one place
while each host stays owned by its own user (separate quota).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from omnigent.server.duet_merge import limit_grants, merge_databases, share_project

_SCHEMA = """
CREATE TABLE users (workspace_id INTEGER, id TEXT, is_admin INTEGER,
                    password_hash TEXT, created_at INTEGER, last_login_at INTEGER,
                    PRIMARY KEY (workspace_id, id));
CREATE TABLE projects (workspace_id INTEGER, id TEXT, name TEXT, user_id TEXT,
                       created_at INTEGER, updated_at INTEGER, config TEXT,
                       PRIMARY KEY (workspace_id, id));
CREATE TABLE hosts (workspace_id INTEGER, host_id TEXT, user_id TEXT, name TEXT,
                    status INTEGER, created_at INTEGER, updated_at INTEGER,
                    PRIMARY KEY (workspace_id, host_id));
CREATE TABLE session_permissions (workspace_id INTEGER, user_id TEXT,
                                  conversation_id TEXT, level INTEGER,
                                  PRIMARY KEY (workspace_id, user_id, conversation_id));
CREATE TABLE conversations (workspace_id INTEGER, id TEXT, created_at INTEGER,
                            updated_at INTEGER, title TEXT,
                            parent_conversation_id TEXT, root_conversation_id TEXT,
                            next_position INTEGER, archived INTEGER,
                            agent_id TEXT, session_overrides TEXT,
                            PRIMARY KEY (workspace_id, id));
CREATE TABLE omnigent_conversation_metadata (workspace_id INTEGER, id TEXT,
                                             kind INTEGER, project_id TEXT,
                                             PRIMARY KEY (workspace_id, id));
CREATE TABLE conversation_items (workspace_id INTEGER, id TEXT,
                                 conversation_id TEXT, data TEXT,
                                 PRIMARY KEY (workspace_id, id));
CREATE TABLE user_daily_cost (workspace_id INTEGER, user_id TEXT, day_utc TEXT,
                              cost_usd REAL, updated_at INTEGER,
                              ask_approved_usd REAL,
                              PRIMARY KEY (workspace_id, user_id, day_utc));
"""


def _make_db(path: Path, rows: dict[str, list[tuple]]) -> None:
    con = sqlite3.connect(path)
    con.executescript(_SCHEMA)
    for table, values in rows.items():
        placeholders = ",".join("?" * len(values[0]))
        con.executemany(f"INSERT INTO {table} VALUES ({placeholders})", values)
    con.commit()
    con.close()


def _count(path: Path, sql: str) -> int:
    con = sqlite3.connect(path)
    try:
        return int(con.execute(sql).fetchone()[0])
    finally:
        con.close()


def _remote_rows() -> dict[str, list[tuple]]:
    return {
        "users": [(0, "hipolito", 0, "hash-hipo", 100, None)],
        "projects": [(0, "proj-hipo", "RAL", "hipolito", 100, 100, None)],
        "hosts": [(0, "host-hipo", "hipolito", "hipolito-precision", 1, 100, 100)],
        "session_permissions": [(0, "hipolito", "conv-hipo", 4)],
        "conversations": [
            (0, "conv-hipo", 100, 100, "his lane", None, "conv-hipo", 2, 0, None, None)
        ],
        "omnigent_conversation_metadata": [(0, "conv-hipo", 1, "proj-hipo")],
        "conversation_items": [
            (0, "item-1", "conv-hipo", "{}"),
            (0, "item-2", "conv-hipo", "{}"),
        ],
        "user_daily_cost": [(0, "hipolito", "2026-10-30", 3.5, 100, 0.0)],
    }


def test_merge_imports_his_user_projects_and_conversations(tmp_path: Path) -> None:
    src = tmp_path / "remote.db"
    dst = tmp_path / "ours.db"
    _make_db(src, _remote_rows())
    _make_db(dst, {"users": [(0, "morys5", 1, "hash-mine", 90, None)]})

    report = merge_databases(src, dst)

    assert report.users_added == 1
    assert report.projects_added == 1
    assert report.hosts_added == 1
    assert report.conversations_added == 1
    assert report.items_added == 2
    # His login keeps working here: the password hash is preserved.
    assert (
        _count(dst, "SELECT COUNT(*) FROM users WHERE id='hipolito' AND password_hash='hash-hipo'")
        == 1
    )
    # His host stays owned by him, not by us (separate provider quota).
    assert (
        _count(dst, "SELECT COUNT(*) FROM hosts WHERE host_id='host-hipo' AND user_id='hipolito'")
        == 1
    )
    # Idempotent: a second merge adds nothing.
    again = merge_databases(src, dst)
    assert again.users_added == 0
    assert again.conversations_added == 0
    assert again.items_added == 0


def test_merge_dry_run_writes_nothing(tmp_path: Path) -> None:
    src = tmp_path / "remote.db"
    dst = tmp_path / "ours.db"
    _make_db(src, _remote_rows())
    _make_db(dst, {"users": [(0, "morys5", 1, "hash-mine", 90, None)]})

    report = merge_databases(src, dst, dry_run=True)

    assert report.users_added == 1
    assert _count(dst, "SELECT COUNT(*) FROM users WHERE id='hipolito'") == 0


def test_merge_never_overwrites_existing_password(tmp_path: Path) -> None:
    src = tmp_path / "remote.db"
    dst = tmp_path / "ours.db"
    _make_db(src, _remote_rows())
    _make_db(dst, {"users": [(0, "hipolito", 0, "hash-ours", 90, None)]})

    report = merge_databases(src, dst)

    assert report.users_added == 0
    assert report.users_conflict == 1
    assert (
        _count(dst, "SELECT COUNT(*) FROM users WHERE id='hipolito' AND password_hash='hash-ours'")
        == 1
    )


def test_share_project_grants_the_other_user_its_sessions(tmp_path: Path) -> None:
    dst = tmp_path / "ours.db"
    rows = _remote_rows()
    rows["users"] = [*rows["users"], (0, "morys5", 1, "hash-mine", 90, None)]
    _make_db(dst, rows)

    added = share_project(dst, project_id="proj-hipo", target_user="morys5", level=2)

    assert added == 1
    assert (
        _count(
            dst,
            "SELECT COUNT(*) FROM session_permissions WHERE user_id='morys5' "
            "AND conversation_id='conv-hipo' AND level=2",
        )
        == 1
    )
    # Sharing again adds nothing, and the owner's level is not downgraded.
    assert share_project(dst, project_id="proj-hipo", target_user="morys5", level=2) == 0
    assert (
        _count(
            dst, "SELECT COUNT(*) FROM session_permissions WHERE user_id='hipolito' AND level=4"
        )
        == 1
    )


def test_limit_grants_makes_cross_access_read_only_and_reports_what_it_lowered(
    tmp_path: Path,
) -> None:
    dst = tmp_path / "ours.db"
    rows = _remote_rows()
    rows["users"] = [*rows["users"], (0, "morys5", 1, "hash-mine", 90, None)]
    _make_db(dst, rows)
    share_project(dst, project_id="proj-hipo", target_user="morys5", level=3)

    lowered = limit_grants(dst, users=["morys5", "hipolito"], level=1)

    assert [(user, old) for _ws, user, _conv, old in lowered] == [("morys5", 3)]
    assert (
        _count(
            dst,
            "SELECT COUNT(*) FROM session_permissions WHERE user_id='morys5' "
            "AND conversation_id='conv-hipo' AND level=1",
        )
        == 1
    )
    # The owner keeps owning, and a second run finds nothing left to lower.
    assert (
        _count(
            dst, "SELECT COUNT(*) FROM session_permissions WHERE user_id='hipolito' AND level=4"
        )
        == 1
    )
    assert limit_grants(dst, users=["morys5", "hipolito"], level=1) == []


def test_sharing_defaults_to_read_only(tmp_path: Path) -> None:
    dst = tmp_path / "ours.db"
    rows = _remote_rows()
    rows["users"] = [*rows["users"], (0, "morys5", 1, "hash-mine", 90, None)]
    _make_db(dst, rows)

    share_project(dst, project_id="proj-hipo", target_user="morys5")

    assert (
        _count(
            dst,
            "SELECT COUNT(*) FROM session_permissions WHERE user_id='morys5' AND level=1",
        )
        == 1
    )
