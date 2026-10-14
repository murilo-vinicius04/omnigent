"""Where a message for a busy lane goes: into its work now, after it, or to a new lane.

The hub's router places each message it sends to a WORKING lane, from what the
message says and what the lane is doing: a direction or correction for that
work goes in now ("steer"), more work that needs the lane's result waits for
it ("after"), and a different task opens a new lane ("new"). Until the
router's places agree with theirs, and whenever it is unsure, the hub asks
them instead ("ask"): the message waits, with the three places to pick from
in the hub chat, and goes when the lane finishes if they pick none. They can
also correct any placement there. Each answer is shown to the router as a
lesson from then on, and every placement with its answer is kept as a label
for training the decision model (reflex), which answers the same question in
shadow.
"""

from __future__ import annotations

import json
import logging
import pathlib
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Final

import httpx

from omnigent.supervisor import Backend, post_questions

_logger = logging.getLogger(__name__)

PLACES: Final[tuple[str, ...]] = ("steer", "after", "new")

#: Not a place: the hub asks them where it goes.
ASK: Final[str] = "ask"

#: What the router may answer.
ROUTER_PLACES: Final[tuple[str, ...]] = (*PLACES, ASK)

#: Each place as the hub chat names it.
PLACE_LABELS: Final[dict[str, str]] = {
    "steer": "into its work now",
    "after": "after it finishes",
    "new": "a new lane",
    ASK: "where should this go?",
}

#: The router decides alone once its own place matched their answer at least
#: :data:`TRUST_AGREE` times in their newest :data:`TRUST_WINDOW` answers.
TRUST_WINDOW: Final[int] = 10
TRUST_AGREE: Final[int] = 9

#: Each user's placements and their corrections, one JSON object per line.
LOG_NAME: Final[str] = "placements.jsonl"

#: Corrections the router is shown as lessons, newest first.
MAX_LESSONS: Final[int] = 8

#: A lane opened for a message it should not have been is closed again only
#: this soon after; later it may have done work worth keeping.
CLOSE_FRESH_LANE_S: Final[float] = 15 * 60

_QUOTE_CHARS: Final[int] = 200
_STATE_CHARS: Final[int] = 1_500


@dataclass(frozen=True)
class Placement:
    """One message the router placed beside a working lane.

    :param lane_id: The working lane it was judged against.
    :param task: What that lane was last asked, the work it was doing.
    :param picked: What the hub did: a place, or :data:`ASK`.
    :param guess: The router's own place, when it gave one.
    :param opened: For ``"new"``, the lane opened for it.
    :param by: ``"router"``, or ``"default"`` when the router gave no place.
    """

    id: str
    at: float
    project: str
    project_id: str
    lane: str
    lane_id: str
    task: str
    message: str
    said: str
    picked: str
    topic: str = ""
    opened: str = ""
    via: str = ""
    by: str = "router"
    guess: str = ""


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def _append(path: pathlib.Path, row: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        _logger.warning("could not write the hub's placement log %s", path, exc_info=True)


def log_placement(path: pathlib.Path, placement: Placement) -> None:
    _append(path, {"placement": asdict(placement)})


def log_feedback(path: pathlib.Path, placement_id: str, right: str, *, via: str) -> None:
    """Their word on a placement: the place it should have had."""
    _append(
        path, {"feedback": placement_id, "right": right, "via": via, "at": round(time.time(), 3)}
    )


def log_shadow(path: pathlib.Path, placement_id: str, judged: dict[str, Any]) -> None:
    """The decision model's answer to the same question, for comparison and training."""
    _append(path, {"shadow": placement_id, **judged})


@dataclass
class Record:
    """A placement with what came after it."""

    placement: Placement
    right: str = ""
    shadow: dict[str, Any] = field(default_factory=dict)


def read_log(path: pathlib.Path) -> dict[str, Record]:
    """Every placement by id, with its newest correction and shadow answer."""
    found: dict[str, Record] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return found
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if isinstance(row.get("placement"), dict):
            try:
                placement = Placement(**row["placement"])
            except TypeError:
                continue
            found[placement.id] = Record(placement)
        elif (record := found.get(str(row.get("feedback") or ""))) is not None:
            if row.get("right") in PLACES:
                record.right = str(row["right"])
        elif (record := found.get(str(row.get("shadow") or ""))) is not None:
            record.shadow = {k: v for k, v in row.items() if k != "shadow"}
    return found


def find(path: pathlib.Path, placement_id: str) -> Record | None:
    return read_log(path).get(placement_id)


def latest(path: pathlib.Path, *, within_s: float) -> Record | None:
    """Their newest placement, if it is recent enough to be what they mean."""
    records = list(read_log(path).values())
    if not records:
        return None
    newest = max(records, key=lambda r: r.placement.at)
    return newest if time.time() - newest.placement.at <= within_s else None


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def trusted(path: pathlib.Path) -> bool:
    """Whether the router's own places have matched their answers enough to decide alone."""
    answered = sorted(
        (r for r in read_log(path).values() if r.right and r.placement.guess),
        key=lambda r: r.placement.at,
        reverse=True,
    )[:TRUST_WINDOW]
    agreed = sum(r.right == r.placement.guess for r in answered)
    return len(answered) >= TRUST_WINDOW and agreed >= TRUST_AGREE


def lessons(path: pathlib.Path, limit: int = MAX_LESSONS) -> str:
    """Their answers and corrections, newest first, as the router reads them; empty when none."""
    taught = sorted(
        (r for r in read_log(path).values() if r.right and r.right != r.placement.picked),
        key=lambda r: r.placement.at,
        reverse=True,
    )[:limit]
    if not taught:
        return ""
    lines = [
        "[Messages for busy lanes and where they said each belonged, newest first: when "
        "the hub asked them, and when it placed one wrong. Place messages like these the "
        "same way.]"
    ]
    for record in taught:
        p = record.placement
        if p.picked != ASK:
            how = f'not "{p.picked}"'
        elif p.guess:
            how = f'the hub asked; it had guessed "{p.guess}"'
        else:
            how = "the hub asked"
        lines.append(
            f'- While {p.lane} worked on "{_clip(p.task, _QUOTE_CHARS)}", they said '
            f'"{_clip(p.said or p.message, _QUOTE_CHARS)}": it belonged "{record.right}" '
            f"({how})."
        )
    return "\n".join(lines)


# -- the decision model, in shadow ---------------------------------------------------

_SHADOW_QUESTIONS: Final[dict[str, dict[str, Any]]] = {
    "same_task": {
        "type": "noul",
        "instructions": (
            "Is the new message about the task the busy lane is working on right now (a "
            "direction, correction, more detail, change of priority, stop, or a question about "
            "that task), rather than a different task?"
        ),
    },
    "needs_result": {
        "type": "noul",
        "instructions": (
            "Does the new message ask for work that can only start after the busy lane "
            "finishes, because it uses that lane's result?"
        ),
    },
}


def shadow_state(placement: Placement) -> str:
    parts = [f"## New message for the project {placement.project}\n{placement.message}"]
    if placement.said and placement.said != placement.message:
        parts.append(f"## Their own words\n{placement.said}")
    parts.append(
        f"## The busy lane\n{placement.lane}, working on this task:\n"
        f"{placement.task[:_STATE_CHARS]}"
    )
    return "\n\n".join(parts)


async def shadow_judge(placement: Placement, backend: Backend) -> dict[str, Any]:
    """The decision model's place for it; never raises for backend trouble."""
    started = time.perf_counter()
    try:
        answers, _ = await post_questions(backend, shadow_state(placement), _SHADOW_QUESTIONS)
        same = float(answers["same_task"]["noul"])
        after = float(answers["needs_result"]["noul"])
        pick = "steer" if same >= 0.5 else "after" if after >= 0.5 else "new"
        judged: dict[str, Any] = {
            "pick": pick,
            "p_same_task": round(same, 4),
            "p_needs_result": round(after, 4),
        }
    except (httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        judged = {"pick": "", "error": f"{type(exc).__name__}: {exc}"[:300]}
    judged["latency_ms"] = round((time.perf_counter() - started) * 1000)
    return judged
