"""A project's living brief: what it is, where it stands, what is left.

The companion rewrites it after each report a lane makes, so the page a person
opens to remember a project is never older than its last report. The model
answers with *changes* (a new overview or status, one line of progress, to-dos
added or ticked off by id), and this module applies them to what is stored.
Applying changes rather than taking a whole new brief means a thin or garbled
answer cannot wipe the person's own to-dos, and an item they ticked while the
model was thinking is still ticked.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final

OVERVIEW_CHARS: Final[int] = 600
STATUS_CHARS: Final[int] = 500
PROGRESS_CHARS: Final[int] = 240
TODO_CHARS: Final[int] = 200

#: Lines of progress a brief keeps, newest first.
PROGRESS_KEPT: Final[int] = 6

#: Open items the companion may have on the list at once, and finished ones it
#: keeps in view before the oldest are forgotten. The person's own items are
#: never counted or trimmed.
AUTO_OPEN_MAX: Final[int] = 12
AUTO_DONE_KEPT: Final[int] = 6

#: Who wrote a to-do item.
BY_COMPANION: Final[str] = "companion"
BY_PERSON: Final[str] = "you"

_BRIEF_TASK: Final[str] = """\
You keep the written brief of the project {project}. It is the page the person \
opens to remember what the project is, where it stands and what is left, \
without reading its chats. Part of the project just reported (below). Update \
the brief from that report.

Write in {language}. Write for someone who knows the project only by its name: \
plain sentences about the work and its results. Never write commands, tool \
names, file paths, error codes or log lines unless one is the very thing the \
person needs to act on. Say only what the reports show; do not guess. Do not \
write "finished:", and do not speak of "lanes", "agents" or "the hub": say \
"the project", or name the part of the work.

[The brief now]
Overview: {overview}
Where it stands: {status}
Recent progress, newest first:
{progress}
To-do, as [id] state (who wrote it) text:
{todos}

[The rest of the project]
{others}

[What the person asked this part of the project]
{ask}

[What it reported, as the person was told]
{told}

[Its full last reply]
{reply}

Reply with ONE JSON object and nothing else:
{{"overview": "...", "status": "...", "progress": "...", "todo_add": ["..."], \
"todo_done": ["id"], "todo_drop": ["id"]}}
- overview: two or three sentences: what the project is and what it is for. \
{overview_rule}
- status: one to three sentences: where the project stands right now. What \
works, what is running, what is stuck or waiting on the person. Always write it.
- progress: one sentence on what this report added; "" when it achieved nothing new.
- todo_add: things still to do that the report names or clearly implies, short \
and concrete. Never repeat an item already listed.
- todo_done: ids of listed items the report shows are now done.
- todo_drop: ids of items marked (companion) that no longer matter. Never drop \
an item marked (you).
Use [] for a list with nothing in it. No markdown.\
"""

_OVERVIEW_OPEN: Final[str] = (
    "If the current overview is empty or wrong, write it from what the work shows; "
    "if it is still right, repeat it unchanged."
)
_OVERVIEW_PINNED: Final[str] = 'The person wrote the overview themselves: reply with "" for it.'


@dataclass(frozen=True)
class Update:
    """What the companion changed, as it answered.

    :param overview: A new overview; ``""`` to leave it.
    :param status: Where the project stands now; ``""`` to leave it.
    :param progress: One line on what the report added; ``""`` for none.
    :param add: New to-do items.
    :param done: Ids of items now done.
    :param drop: Ids of the companion's own items no longer relevant.
    """

    overview: str = ""
    status: str = ""
    progress: str = ""
    add: tuple[str, ...] = ()
    done: tuple[str, ...] = ()
    drop: tuple[str, ...] = ()


@dataclass(frozen=True)
class Living:
    """The part of a brief the companion writes.

    :param overview: What the project is and what it is for.
    :param status: Where it stands now.
    :param progress: ``{"at", "text", "lane"}`` lines, newest first.
    :param updated_at: Seconds since the epoch of the last change; ``0`` before any.
    """

    overview: str = ""
    status: str = ""
    progress: tuple[dict[str, Any], ...] = ()
    updated_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        """The shape stored in the project's config and sent to the panel."""
        return {
            "overview": self.overview,
            "status": self.status,
            "progress": [dict(p) for p in self.progress],
            "updated_at": self.updated_at,
        }


def living_from(value: Any) -> Living:
    """Read a stored brief, forgiving about anything missing or the wrong type."""
    if not isinstance(value, dict):
        return Living()
    progress: list[dict[str, Any]] = []
    for line in value.get("progress") or []:
        if isinstance(line, dict) and str(line.get("text") or "").strip():
            progress.append(
                {
                    "at": float(line.get("at") or 0),
                    "text": str(line["text"]).strip()[:PROGRESS_CHARS],
                    "lane": str(line.get("lane") or ""),
                }
            )
    return Living(
        overview=str(value.get("overview") or "").strip()[:OVERVIEW_CHARS],
        status=str(value.get("status") or "").strip()[:STATUS_CHARS],
        progress=tuple(progress[:PROGRESS_KEPT]),
        updated_at=float(value.get("updated_at") or 0),
    )


def _strings(value: Any, limit: int) -> tuple[str, ...]:
    """A list of non-empty strings, each clipped; anything else is ignored."""
    if not isinstance(value, list):
        return ()
    return tuple(
        text[:limit]
        for item in value
        if isinstance(item, str) and (text := " ".join(item.split()))
    )


def _line(value: Any, limit: int) -> str:
    """One string on one line, clipped."""
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def parse_update(reply: str) -> Update | None:
    """Read the companion's JSON answer; ``None`` when it holds no usable object."""
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(reply[start : end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    update = Update(
        overview=_line(data.get("overview"), OVERVIEW_CHARS),
        status=_line(data.get("status"), STATUS_CHARS),
        progress=_line(data.get("progress"), PROGRESS_CHARS),
        add=_strings(data.get("todo_add"), TODO_CHARS),
        done=_strings(data.get("todo_done"), 32),
        drop=_strings(data.get("todo_drop"), 32),
    )
    return update if update != Update() else None


def with_ids(todos: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The list with an id on every item: ids are how the companion names one."""
    taken = {str(t["id"]) for t in todos if t.get("id")}
    out: list[dict[str, Any]] = []
    for item in todos:
        kept = dict(item)
        if not kept.get("id"):
            while (fresh := uuid.uuid4().hex[:4]) in taken:
                pass
            taken.add(fresh)
            kept["id"] = fresh
        out.append(kept)
    return out


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def apply_update(
    living: Living,
    todos: Sequence[dict[str, Any]],
    update: Update,
    *,
    lane: str,
    pinned: bool,
    now: float | None = None,
) -> tuple[Living, list[dict[str, Any]]]:
    """Fold the companion's changes into the stored brief and to-do list.

    :param living: What is stored now.
    :param todos: The stored list; items are ``{"id", "content", "status", "by"}``.
    :param update: The companion's answer.
    :param lane: The part of the project that reported, for the progress line.
    :param pinned: The person wrote the overview; the companion leaves it.
    :param now: Seconds since the epoch; the clock when omitted.
    :returns: The new brief and the new list.
    """
    stamp = time.time() if now is None else now
    progress = list(living.progress)
    if update.progress and not (progress and _norm(progress[0]["text"]) == _norm(update.progress)):
        progress.insert(0, {"at": stamp, "text": update.progress, "lane": lane})
    merged = Living(
        overview=living.overview if pinned or not update.overview else update.overview,
        status=update.status or living.status,
        progress=tuple(progress[:PROGRESS_KEPT]),
        updated_at=stamp,
    )

    items = with_ids(todos)
    done = set(update.done)
    drop = set(update.drop)
    for item in items:
        if str(item["id"]) in done:
            item["status"] = "done"
    items = [i for i in items if not (str(i["id"]) in drop and i.get("by") == BY_COMPANION)]
    known = {_norm(str(i.get("content") or "")) for i in items}
    open_auto = sum(1 for i in items if i.get("by") == BY_COMPANION and i.get("status") != "done")
    for text in update.add:
        if _norm(text) in known or open_auto >= AUTO_OPEN_MAX:
            continue
        items.append({"content": text, "status": "open", "by": BY_COMPANION})
        known.add(_norm(text))
        open_auto += 1
    items = with_ids(items)

    # Only its own finished items are forgotten, oldest first.
    finished = [i for i in items if i.get("by") == BY_COMPANION and i.get("status") == "done"]
    forget = {id(i) for i in finished[: max(0, len(finished) - AUTO_DONE_KEPT)]}
    return merged, [i for i in items if id(i) not in forget]


def _show_todos(todos: Sequence[dict[str, Any]]) -> str:
    if not todos:
        return "  (none yet)"
    lines = []
    for item in todos[:40]:
        who = "companion" if item.get("by") == BY_COMPANION else "you"
        state = "done" if item.get("status") == "done" else "open"
        lines.append(f"  [{item.get('id')}] {state} ({who}) {item.get('content')}")
    return "\n".join(lines)


def _show_progress(living: Living) -> str:
    if not living.progress:
        return "  (nothing yet)"
    return "\n".join(f"  - {p['text']}" for p in living.progress)


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


@dataclass(frozen=True)
class Report:
    """What a part of the project just said, in the words the task is built from.

    :param ask: What the person asked it, without the router's added context.
    :param told: The report as the person was told it.
    :param reply: Its full last reply.
    :param lane: Its name.
    :param others: One line per other part of the project, e.g. ``"sim: working"``.
    """

    ask: str = ""
    told: str = ""
    reply: str = ""
    lane: str = ""
    others: Sequence[str] = field(default_factory=tuple)


def build_task(
    *,
    project: str,
    language: str,
    living: Living,
    todos: Sequence[dict[str, Any]],
    report: Report,
    pinned: bool,
) -> str:
    """The prompt that asks the companion to update a project's brief."""
    return _BRIEF_TASK.format(
        project=project,
        language=language,
        overview=living.overview or "(not written yet)",
        status=living.status or "(not written yet)",
        progress=_show_progress(living),
        todos=_show_todos(todos),
        others="\n".join(f"  - {line}" for line in report.others) or "  (nothing else running)",
        ask=_clip(report.ask, 600) or "(not known)",
        told=_clip(report.told, 1200) or "(nothing)",
        reply=_clip(report.reply, 4000) or "(nothing)",
        overview_rule=_OVERVIEW_PINNED if pinned else _OVERVIEW_OPEN,
    )
