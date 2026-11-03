"""Duet merge: import a second person's Omnigent server into this one.

Two people who each run their own Omnigent server end up with two
silos: separate users, projects, hosts and conversations, so their
agents duplicate work neither can see. This tool merges the remote
person's SQLite database into the local one, once, so both can work
on a single server afterwards:

- Their ``users`` row is imported with its password hash intact, so
  they log in here with the password they already use — no invite,
  no reset. An existing local row is never overwritten.
- Their ``projects``, ``hosts`` and ``user_daily_cost`` rows keep
  their original owner, so a host imported from the remote stays
  owned by its own user and keeps spending that user's provider
  quota, not the local admin's.
- Their ``conversations`` (plus metadata, items and labels) are
  imported with their grants, so history arrives visible to them.
- :func:`share_project` then grants a second user access to every
  session in a project, which is the collaboration itself: both
  people open the same lanes instead of redoing each other's work.

The merge is idempotent (existing primary keys are skipped) and
touches only tables present in both databases. Run the server
stopped, or accept SQLite WAL concurrency, and keep the backup the
CLI writes next to the target database.

CLI::

    python -m omnigent.server.duet_merge --source remote.db --target chat.db
    python -m omnigent.server.duet_merge --source remote.db --target chat.db \
        --share-project <project-id> --share-with <other-user>
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MergeReport:
    """Counts of rows imported by one merge run."""

    users_added: int = 0
    users_conflict: int = 0
    projects_added: int = 0
    hosts_added: int = 0
    conversations_added: int = 0
    items_added: int = 0
    labels_added: int = 0
    permissions_added: int = 0
    costs_added: int = 0
    skipped_tables: list[str] = field(default_factory=list)


def _tables(con: sqlite3.Connection) -> set[str]:
    return {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in con.execute(f"PRAGMA table_info({table})")]


def _shared_columns(src: sqlite3.Connection, dst: sqlite3.Connection, table: str) -> list[str]:
    dst_cols = set(_columns(dst, table))
    return [col for col in _columns(src, table) if col in dst_cols]


def _copy_missing(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    table: str,
    where: str = "",
    params: tuple = (),
) -> int:
    """INSERT OR IGNORE every source row into the target; return rows added."""
    cols = _shared_columns(src, dst, table)
    col_list = ", ".join(cols)
    placeholders = ", ".join("?" * len(cols))
    rows = src.execute(f"SELECT {col_list} FROM {table} {where}", params).fetchall()
    before = dst.total_changes
    dst.executemany(f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({placeholders})", rows)
    return dst.total_changes - before


def merge_databases(
    source: str | Path,
    target: str | Path,
    *,
    dry_run: bool = False,
    backup: bool = False,
) -> MergeReport:
    """Merge the remote database at ``source`` into ``target``.

    :param source: SQLite file exported/rsynced from the other server.
    :param target: This server's SQLite file (e.g. ``~/.omnigent/chat.db``).
    :param dry_run: Compute the report but roll every write back.
    :param backup: Copy ``target`` to ``<target>.bak-duet-<epoch>`` first.
    :returns: A :class:`MergeReport` with per-table counts.
    """
    source, target = Path(source), Path(target)
    if backup and not dry_run:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(target, target.with_name(f"{target.name}.bak-duet-{stamp}"))

    report = MergeReport()
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        dst.execute("PRAGMA foreign_keys=OFF")
        src_tables, dst_tables = _tables(src), _tables(dst)
        both = src_tables & dst_tables
        for needed in ("users", "projects", "hosts", "conversations"):
            if needed not in both:
                report.skipped_tables.append(needed)

        if "users" in both:
            existing = {
                row[0]: row[1] for row in dst.execute("SELECT id, password_hash FROM users")
            }
            for user_id, pw_hash in src.execute("SELECT id, password_hash FROM users"):
                if user_id in existing and existing[user_id] != pw_hash:
                    report.users_conflict += 1
            report.users_added = _copy_missing(src, dst, "users")

        if "projects" in both:
            report.projects_added = _copy_missing(src, dst, "projects")
        if "hosts" in both:
            report.hosts_added = _copy_missing(src, dst, "hosts")
        if "user_daily_cost" in both:
            report.costs_added = _copy_missing(src, dst, "user_daily_cost")

        if "conversations" in both:
            dst_ids = {row[0] for row in dst.execute("SELECT id FROM conversations")}
            new_ids = [
                row[0]
                for row in src.execute("SELECT id FROM conversations")
                if row[0] not in dst_ids
            ]
            if new_ids:
                marks = ",".join("?" * len(new_ids))
                report.conversations_added = _copy_missing(
                    src,
                    dst,
                    "conversations",
                    f"WHERE id IN ({marks})",
                    tuple(new_ids),
                )
            if new_ids and "omnigent_conversation_metadata" in both:
                marks = ",".join("?" * len(new_ids))
                _copy_missing(
                    src,
                    dst,
                    "omnigent_conversation_metadata",
                    f"WHERE id IN ({marks})",
                    tuple(new_ids),
                )
            if new_ids and "conversation_items" in both:
                marks = ",".join("?" * len(new_ids))
                report.items_added = _copy_missing(
                    src,
                    dst,
                    "conversation_items",
                    f"WHERE conversation_id IN ({marks})",
                    tuple(new_ids),
                )
            if new_ids and "conversation_labels" in both:
                marks = ",".join("?" * len(new_ids))
                report.labels_added = _copy_missing(
                    src,
                    dst,
                    "conversation_labels",
                    f"WHERE conversation_id IN ({marks})",
                    tuple(new_ids),
                )

        if "session_permissions" in both:
            report.permissions_added = _copy_missing(src, dst, "session_permissions")
            # A grant that exists on both sides keeps the higher level.
            dst.executemany(
                "UPDATE session_permissions SET level = MAX(level, ?) "
                "WHERE workspace_id = ? AND user_id = ? AND conversation_id = ?",
                [
                    (level, workspace_id, user_id, conv_id)
                    for workspace_id, user_id, conv_id, level in src.execute(
                        "SELECT workspace_id, user_id, conversation_id, level "
                        "FROM session_permissions"
                    )
                ],
            )

        if dry_run:
            dst.rollback()
        else:
            dst.commit()
        return report
    finally:
        src.close()
        dst.close()


def limit_grants(target: str | Path, *, users: Sequence[str], level: int = 1) -> list[tuple]:
    """Lower the non-owner grants of ``users`` that exceed ``level``.

    Owners (level 4) are never touched. Intended for the two members of a duet, so that
    what they can reach of each other's sessions stays read-only.

    :param users: User ids whose grants to cap, e.g. ``["morys5", "hipolito"]``.
    :param level: The highest level to leave in place; 1 means read-only.
    :returns: The ``(workspace_id, user_id, conversation_id, old_level)`` rows that were
        lowered, so the change can be undone.
    """
    if level not in (1, 2, 3):
        raise ValueError(f"invalid cap {level}")
    marks = ",".join("?" * len(users))
    con = sqlite3.connect(target)
    try:
        rows = con.execute(
            "SELECT workspace_id, user_id, conversation_id, level FROM session_permissions "
            f"WHERE user_id IN ({marks}) AND level > ? AND level < 4",
            (*users, level),
        ).fetchall()
        con.execute(
            "UPDATE session_permissions SET level = ? "
            f"WHERE user_id IN ({marks}) AND level > ? AND level < 4",
            (level, *users, level),
        )
        con.commit()
        return rows
    finally:
        con.close()


def share_project(
    target: str | Path,
    *,
    project_id: str,
    target_user: str,
    level: int = 1,
) -> int:
    """Grant ``target_user`` access to every session in a project.

    Projects have no ACL of their own; sharing is expressed as
    ``session_permissions`` rows (1=read, 2=edit, 3=manage, 4=owner)
    on each conversation whose metadata places it in the project.
    An existing higher level is never downgraded.

    :returns: The number of new grants added.
    :raises ValueError: If ``target_user`` has no local user row.
    """
    if level not in (1, 2, 3, 4):
        raise ValueError(f"invalid permission level {level}")
    con = sqlite3.connect(target)
    try:
        if con.execute("SELECT 1 FROM users WHERE id = ?", (target_user,)).fetchone() is None:
            raise ValueError(f"user {target_user!r} does not exist in the target database")
        # Real project ids are stored as 16 raw bytes (Uuid16) but are
        # shown everywhere as 32-char hex; accept either form.
        candidates: list = [project_id]
        try:
            raw = bytes.fromhex(project_id)
        except ValueError:
            raw = b""
        if len(raw) == 16:
            candidates.append(raw)
        marks = ",".join("?" * len(candidates))
        convs = con.execute(
            "SELECT workspace_id, id FROM omnigent_conversation_metadata "
            f"WHERE project_id IN ({marks})",
            tuple(candidates),
        ).fetchall()
        added = 0
        for workspace_id, conv_id in convs:
            cur = con.execute(
                "INSERT OR IGNORE INTO session_permissions "
                "(workspace_id, user_id, conversation_id, level) VALUES (?, ?, ?, ?)",
                (workspace_id, target_user, conv_id, level),
            )
            added += cur.rowcount
            con.execute(
                "UPDATE session_permissions SET level = MAX(level, ?) "
                "WHERE workspace_id = ? AND user_id = ? AND conversation_id = ?",
                (level, workspace_id, target_user, conv_id),
            )
        con.commit()
        return added
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True, help="Remote server's SQLite database")
    parser.add_argument("--target", required=True, help="This server's SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="Report only; write nothing")
    parser.add_argument("--no-backup", action="store_true", help="Skip the target backup copy")
    parser.add_argument("--share-project", help="Project id to share after merging")
    parser.add_argument("--share-with", help="User to share --share-project with")
    parser.add_argument(
        "--share-level", type=int, default=1, help="Grant level 1-4 (default 1=read-only)"
    )
    args = parser.parse_args(argv)

    report = merge_databases(
        args.source, args.target, dry_run=args.dry_run, backup=not args.no_backup
    )
    print(report)
    if args.share_project:
        if not args.share_with:
            parser.error("--share-project requires --share-with")
        if args.dry_run:
            print("dry-run: project sharing skipped (no writes)")
        else:
            added = share_project(
                args.target,
                project_id=args.share_project,
                target_user=args.share_with,
                level=args.share_level,
            )
            print(
                f"shared project {args.share_project}: {added} new grant(s) for {args.share_with}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
