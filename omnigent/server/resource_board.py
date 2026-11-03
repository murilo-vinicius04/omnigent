"""Board where agents claim shared resources (the robot, a camera, a GPU) in turn.

A resource has at most one holder and a first-come queue. A claim is a lease: it ends when its
holder releases it or when it runs out, and then the next in line is promoted. A promoted
waiter has a short window to confirm, so a lane that never comes back cannot hold the others up.

A lease only says whose turn it is. It is never permission to move real hardware; that still
needs the person's explicit go.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_TTL_MIN = 30
MAX_TTL_MIN = 240
#: How long a promoted waiter has to confirm before the resource moves on.
CLAIM_WINDOW_MIN = 10
_NAME_MAX = 48


def normalize_name(name: str) -> str:
    """Lowercase slug of a resource name, so ``"Franka Robot"`` and ``"franka-robot"`` match."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:_NAME_MAX]


@dataclass
class Party:
    """A lane that holds or waits for a resource.

    :param session_id: The lane's session id.
    :param user: User the lane runs as.
    :param lane: The lane's title, for people.
    :param purpose: What it wants the resource for, one line.
    :param since: Epoch seconds when it started holding or waiting.
    :param ttl_min: Minutes it asked to hold the resource for.
    """

    session_id: str
    user: str
    lane: str
    purpose: str
    since: float
    ttl_min: int


@dataclass
class Resource:
    """State of one resource.

    :param holder: Who holds it now, or ``None``.
    :param expires_at: Epoch seconds when the lease ends unless renewed or confirmed.
    :param confirmed: ``False`` while a promoted waiter has not yet confirmed.
    :param queue: Waiters, first in line first.
    """

    holder: Party | None = None
    expires_at: float = 0.0
    confirmed: bool = True
    queue: list[Party] = field(default_factory=list)


@dataclass(frozen=True)
class Event:
    """Something a lane should be told, for the caller to deliver.

    :param kind: ``"promoted"`` (you now hold it), ``"waiting"`` (someone waits for yours) or
        ``"expired"`` (your lease ran out).
    :param session_id: The lane to tell.
    :param resource: The resource name.
    :param text: What to tell it.
    """

    kind: str
    session_id: str
    resource: str
    text: str


class ResourceBoard:
    """Resources kept in one JSON file under the state directory."""

    def __init__(self, path: Path | None = None, clock: Callable[[], float] = time.time) -> None:
        if path is None:
            from omnigent_ui_sdk.terminal._config import state_dir

            path = Path(state_dir()) / "duet" / "resources.json"
        self._path = path
        self._clock = clock
        self._lock = threading.Lock()

    def _load(self) -> dict[str, Resource]:
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, ValueError):
            return {}
        board: dict[str, Resource] = {}
        for name, item in raw.items():
            holder = Party(**item["holder"]) if item.get("holder") else None
            board[name] = Resource(
                holder=holder,
                expires_at=item.get("expires_at", 0.0),
                confirmed=item.get("confirmed", True),
                queue=[Party(**p) for p in item.get("queue", [])],
            )
        return board

    def _save(self, board: dict[str, Resource]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump({n: asdict(r) for n, r in board.items()}, handle)
        os.replace(tmp, self._path)

    def _hand_over(self, name: str, res: Resource, now: float, events: list[Event]) -> None:
        """Give the resource to the first waiter, or leave it free."""
        if not res.queue:
            res.holder, res.confirmed, res.expires_at = None, True, 0.0
            return
        nxt = res.queue.pop(0)
        res.holder, res.confirmed = nxt, False
        res.expires_at = now + CLAIM_WINDOW_MIN * 60
        events.append(
            Event(
                "promoted",
                nxt.session_id,
                name,
                f"It is now your turn for '{name}'. Confirm within {CLAIM_WINDOW_MIN} minutes "
                f"by calling sys_resource_acquire('{name}') again, or it passes to the next "
                "lane. Having the turn is not permission to move real hardware: that still "
                "needs the person's explicit go.",
            )
        )

    def _expire(self, board: dict[str, Resource], now: float, events: list[Event]) -> None:
        for name, res in board.items():
            while res.holder is not None and res.expires_at <= now:
                gone = res.holder
                why = "ran out" if res.confirmed else "was not confirmed in time"
                events.append(
                    Event("expired", gone.session_id, name, f"Your turn for '{name}' {why}.")
                )
                self._hand_over(name, res, now, events)

    def _view(self, name: str, res: Resource, now: float) -> dict[str, Any]:
        def party(p: Party) -> dict[str, Any]:
            return {
                "session_id": p.session_id,
                "user": p.user,
                "lane": p.lane,
                "purpose": p.purpose,
            }

        return {
            "resource": name,
            "state": "free" if res.holder is None else "held",
            "holder": party(res.holder) if res.holder else None,
            "confirmed": res.confirmed,
            "held_minutes": int((now - res.holder.since) / 60) if res.holder else None,
            "minutes_left": max(0, int((res.expires_at - now) / 60)) if res.holder else None,
            "queue": [party(p) for p in res.queue],
        }

    def acquire(
        self,
        name: str,
        *,
        session_id: str,
        user: str,
        lane: str,
        purpose: str,
        ttl_min: int = DEFAULT_TTL_MIN,
    ) -> tuple[dict[str, Any], list[Event]]:
        """Claim a resource, renew or confirm a claim, or join the queue.

        :returns: ``(view, events)``; ``view["result"]`` is ``granted``, ``renewed``,
            ``confirmed``, ``queued`` or ``already_queued``.
        """
        key = normalize_name(name)
        if not key:
            raise ValueError("resource name is empty")
        ttl = max(1, min(int(ttl_min), MAX_TTL_MIN))
        with self._lock:
            now, events = self._clock(), []
            board = self._load()
            self._expire(board, now, events)
            res = board.setdefault(key, Resource())
            me = Party(session_id, user, lane, purpose.strip()[:160], now, ttl)
            if res.holder is None:
                res.holder, res.confirmed, res.expires_at = me, True, now + ttl * 60
                result = "granted"
            elif res.holder.session_id == session_id:
                result = "renewed" if res.confirmed else "confirmed"
                res.holder.ttl_min, res.holder.purpose = ttl, me.purpose or res.holder.purpose
                res.confirmed, res.expires_at = True, now + ttl * 60
            elif any(p.session_id == session_id for p in res.queue):
                result = "already_queued"
                for p in res.queue:
                    if p.session_id == session_id:
                        p.purpose, p.ttl_min = me.purpose or p.purpose, ttl
            else:
                res.queue.append(me)
                result = "queued"
                events.append(
                    Event(
                        "waiting",
                        res.holder.session_id,
                        key,
                        f"Lane '{lane}' ({user}) is waiting for '{key}': "
                        f"{me.purpose or 'no purpose given'}. Release it with "
                        "sys_resource_release as soon as you are done. If you have stopped "
                        "using it and are not ready, use yield_to_next=true to give the "
                        "waiting lane a turn and remain in the queue.",
                    )
                )
            self._save(board)
            view = self._view(key, res, now)
            view["result"] = result
            if result in ("queued", "already_queued"):
                view["position"] = 1 + next(
                    i for i, p in enumerate(res.queue) if p.session_id == session_id
                )
            return view, events

    def release(
        self,
        name: str,
        *,
        session_id: str | None = None,
        force: bool = False,
        yield_to_next: bool = False,
    ) -> tuple[dict[str, Any], list[Event]]:
        """Give a resource up, or leave its queue.

        :param session_id: The lane releasing; it must hold the resource or wait for it.
        :param force: Release whoever holds it (a person overriding); needs no session.
        :returns: ``(view, events)``; ``view["result"]`` is ``released``, ``left_queue`` or
            ``not_holding``.
        """
        key = normalize_name(name)
        with self._lock:
            now, events = self._clock(), []
            board = self._load()
            self._expire(board, now, events)
            res = board.get(key)
            if res is None:
                return {"resource": key, "state": "free", "result": "not_holding"}, events
            if yield_to_next and res.holder is not None and res.holder.session_id == session_id:
                if not res.queue:
                    if events:
                        self._save(board)
                    view = self._view(key, res, now)
                    view["result"] = "no_waiter"
                    return view, events
                previous = res.holder
                previous.since = now
                self._hand_over(key, res, now, events)
                res.queue.append(previous)
                result = "yielded"
            elif yield_to_next:
                result = "not_holding"
            elif res.holder is not None and (force or res.holder.session_id == session_id):
                self._hand_over(key, res, now, events)
                result = "released"
            elif session_id and any(p.session_id == session_id for p in res.queue):
                res.queue = [p for p in res.queue if p.session_id != session_id]
                result = "left_queue"
            else:
                result = "not_holding"
            self._save(board)
            view = self._view(key, res, now)
            view["result"] = result
            return view, events

    def status(self, name: str | None = None) -> tuple[list[dict[str, Any]], list[Event]]:
        """Show one resource, or all of them, after letting expired leases move on."""
        with self._lock:
            now, events = self._clock(), []
            board = self._load()
            self._expire(board, now, events)
            if events:
                self._save(board)
            if name is not None:
                key = normalize_name(name)
                res = board.get(key, Resource())
                return [self._view(key, res, now)], events
            return [self._view(n, r, now) for n, r in sorted(board.items())], events

    def drop_session(self, session_id: str) -> list[Event]:
        """Remove a lane from every resource it holds or waits for, e.g. when it ends."""
        with self._lock:
            now, events = self._clock(), []
            board = self._load()
            for name, res in board.items():
                res.queue = [p for p in res.queue if p.session_id != session_id]
                if res.holder is not None and res.holder.session_id == session_id:
                    self._hand_over(name, res, now, events)
            self._save(board)
            return events
