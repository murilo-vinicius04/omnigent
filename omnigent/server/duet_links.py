"""Keep the sessions of linked projects readable by the other person as new lanes appear.

Sessions are shared one by one, so a lane created after a share is invisible to the other side.
A link names a person's projects and who may read them; :func:`sync` grants read access to every
session in those projects that the reader does not have yet. It only ever adds read grants: it
never raises or removes one.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from omnigent.db.db_models import (
    SqlConversationMetadata,
    SqlProject,
    SqlSessionPermission,
    current_workspace_id,
)

_logger = logging.getLogger(__name__)

READ = 1


@dataclass(frozen=True)
class Link:
    """One direction of sharing.

    :param owner: The user whose projects are shared.
    :param reader: The user who may read their sessions.
    :param projects: Names of the owner's projects, e.g. ``("RAL",)``.
    """

    owner: str
    reader: str
    projects: tuple[str, ...]


def links_path() -> Path:
    """Where the links are kept, next to the duet notes."""
    from omnigent_ui_sdk.terminal._config import state_dir

    return Path(state_dir()) / "duet" / "links.json"


def load_links(path: Path | None = None) -> list[Link]:
    """Read the links file; a missing or broken file means no links.

    The file holds ``{"shares": [{"owner": "a", "reader": "b", "projects": ["P"]}, ...]}``.
    """
    try:
        raw = json.loads((path or links_path()).read_text())
    except (OSError, ValueError):
        return []
    links: list[Link] = []
    for item in raw.get("shares", []) if isinstance(raw, dict) else []:
        try:
            names = tuple(str(p) for p in item["projects"])
            links.append(Link(str(item["owner"]), str(item["reader"]), names))
        except (KeyError, TypeError):
            _logger.warning("ignoring a malformed duet link: %r", item)
    return links


def sync(engine: Engine, links: list[Link]) -> dict[str, int]:
    """Give each reader read access to the sessions of their linked projects.

    :param engine: The Omnigent database engine.
    :param links: The links to apply.
    :returns: Grants added, per reader.
    """
    added: dict[str, int] = {}
    workspace = current_workspace_id()
    with Session(engine) as session:
        for link in links:
            project_ids = session.scalars(
                select(SqlProject.id).where(
                    SqlProject.workspace_id == workspace,
                    SqlProject.user_id == link.owner,
                    SqlProject.name.in_(link.projects),
                )
            ).all()
            if not project_ids:
                continue
            sessions = session.scalars(
                select(SqlConversationMetadata.id).where(
                    SqlConversationMetadata.workspace_id == workspace,
                    SqlConversationMetadata.project_id.in_(project_ids),
                )
            ).all()
            has = set(
                session.scalars(
                    select(SqlSessionPermission.conversation_id).where(
                        SqlSessionPermission.workspace_id == workspace,
                        SqlSessionPermission.user_id == link.reader,
                        SqlSessionPermission.conversation_id.in_(sessions),
                    )
                )
            )
            for conversation_id in sessions:
                if conversation_id not in has:
                    session.add(
                        SqlSessionPermission(
                            workspace_id=workspace,
                            user_id=link.reader,
                            conversation_id=conversation_id,
                            level=READ,
                        )
                    )
                    added[link.reader] = added.get(link.reader, 0) + 1
        session.commit()
    return added


async def keep_shared(engine: Engine, *, interval_s: float = 60.0) -> None:
    """Re-apply the links every ``interval_s`` seconds so new lanes are shared within a minute.

    A failure is logged and retried on the next round; it never stops the loop.
    """
    while True:
        try:
            added = await asyncio.to_thread(lambda: sync(engine, load_links()))
            if added:
                _logger.info("duet links: shared new sessions %s", added)
        except Exception:  # noqa: BLE001 - a bad round must not end the loop
            _logger.warning("duet links round failed", exc_info=True)
        await asyncio.sleep(interval_s)
