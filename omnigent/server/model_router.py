"""The hub's tier judge: how much model a request needs, asked of a decision model.

A Jev-style decision model (reflex, served on this machine) reads what the
hub sends a project, which project it is and how the project's previous
request went, and answers typed questions with probabilities: the least
capable tier that will do the request right the first time, the reasoning
effort each tier would need for it (asked per tier, since a cheaper model may
need more thought, not less), whether an economical model would need a redo,
how hard it is, and whether the request is fixing the previous result. It
never writes prose. The tier and effort are the decision model's; which
provider runs them is left to rules over each provider's remaining quota.

For now nothing acts on the answer: every decision is logged next to the
model that actually ran the request (shadow mode), so the judgement can be
checked against real outcomes before it picks anyone's model.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Final

import httpx

from omnigent.supervisor import Backend, post_questions

_logger = logging.getLogger(__name__)

#: reflex's ``reflex-serve`` default port, TypeSafe's wire format.
DEFAULT_URL: Final[str] = "http://127.0.0.1:8008/v1/systemone"

#: The flag file under the hub's folder that turns shadow decisions on; it
#: holds the endpoint's URL, or nothing for :data:`DEFAULT_URL`.
FLAG_NAME: Final[str] = "router-url"

#: Each user's decisions, one JSON object per line, beside their hub log.
LOG_NAME: Final[str] = "router.jsonl"

TIERS: Final[tuple[str, ...]] = ("economical", "middle", "top")

#: What each tier means to the model, by name so the request can be weighed
#: against the work each tier is good for.
_TIER_CRITERIA: Final[dict[str, str]] = {
    "economical": (
        "Haiku 5.5, GPT-6 Luna or Gemini Flash is enough: a status question, a lookup, "
        "a small change whose exact steps are given, a routine command"
    ),
    "middle": (
        "Sonnet 5.5: most everyday coding, debugging, writing or analysis with a clear goal"
    ),
    "top": (
        "Opus 5.5 or GPT-6.1 Sol: work a middle model would likely get wrong, such as "
        "open-ended design or research, a subtle bug whose cause is unknown, a change across "
        "many parts of a system, or a paper"
    ),
}

#: Reasoning effort, in the names Claude and Codex share; each provider's own
#: list is matched when the model is picked.
EFFORTS: Final[tuple[str, ...]] = ("low", "medium", "high", "xhigh")

#: Effort leans high: another level is taken only when the decision model is
#: at least this sure of it.
DEFAULT_EFFORT: Final[str] = "high"
EFFORT_MOVE_BAR: Final[float] = 0.6

#: Who each tier's effort question is about.
_TIER_MODELS: Final[dict[str, str]] = {
    "economical": "an economical model (Haiku 5.5, GPT-6 Luna or Gemini Flash)",
    "middle": "a middle model (Sonnet 5.5)",
    "top": "a top model (Opus 5.5 or GPT-6.1 Sol)",
}

_EFFORT_CRITERIA: Final[dict[str, str]] = {
    "low": "little thinking: an answer, a lookup, a mechanical change",
    "medium": "a few steps of reasoning with a clear plan",
    "high": "careful work: debugging, trade-offs, several files, checking its own result",
    "xhigh": "deep work: research, a subtle bug, long autonomous work, a plan others rely on",
}

_DIFFICULTY: Final[list[str]] = [
    "trivial: a lookup or a one-line change",
    "easy: a small, fully specified change",
    "moderate: several steps or files with a clear goal",
    "hard: ambiguous, cross-cutting, or needs debugging",
    "very hard: research, design, or a subtle bug in unfamiliar code",
]

_CATEGORIES: Final[dict[str, str]] = {
    "code": "write or change code",
    "debug": "find and fix a bug or a failure",
    "research": "find out, compare or measure something",
    "writing": "write or revise a document, paper or plan",
    "question": "answer a question or report status, no change made",
    "operations": "run, restart, deploy or configure something",
}

#: A hung decision must not hold anything; the shadow log just says so.
REQUEST_TIMEOUT_S: Final[float] = 20.0

#: Characters of each part of the state the model reads.
_REQUEST_CHARS: Final[int] = 6_000
_PREVIOUS_CHARS: Final[int] = 3_000
_LOGGED_REQUEST_CHARS: Final[int] = 2_000


@dataclass(frozen=True)
class RouterTask:
    """One request the hub sent, as the decision model reads it.

    :param request: What was sent to the project.
    :param project: The project's name.
    :param model: The model the project's chat runs, e.g. ``"claude-opus-5-5"``.
    :param previous: The project's previous request from the hub, if any.
    :param previous_result: What the hub reported back for it.
    """

    request: str
    project: str
    session_id: str = ""
    provider: str = ""
    model: str = ""
    effort: str = ""
    previous: str = ""
    previous_result: str = ""


@dataclass(frozen=True)
class TierDecision:
    """What the decision model judged, or why it could not."""

    tier: str = ""
    tier_probs: dict[str, float] = field(default_factory=dict)
    #: The effort for the chosen tier, and what was judged for every tier.
    effort: str = ""
    effort_probs: dict[str, float] = field(default_factory=dict)
    efforts_by_tier: dict[str, str] = field(default_factory=dict)
    #: Probability that an economical model would get it wrong or need a redo.
    p_cheap_redo: float | None = None
    #: 0 (trivial) to 4 (very hard), probability-weighted.
    difficulty: float | None = None
    category: str = ""
    #: Probability the request is fixing or redoing the previous result;
    #: ``None`` when the project had no previous request.
    p_fixes_previous: float | None = None
    latency_ms: int = 0
    error: str = ""


def configured(hub_dir: pathlib.Path) -> Backend | None:
    """The decision model's endpoint while shadow decisions are on, else ``None``.

    ``OMNIGENT_HUB_ROUTER_URL`` wins (``off`` turns it off); otherwise the
    flag file ``<hub dir>/router-url`` turns it on, holding the URL or nothing.
    """
    raw = os.environ.get("OMNIGENT_HUB_ROUTER_URL", "").strip()
    if raw.lower() in ("off", "0", "false", "no"):
        return None
    if not raw:
        try:
            raw = (hub_dir / FLAG_NAME).read_text(encoding="utf-8").strip() or DEFAULT_URL
        except OSError:
            return None
    return Backend(name="reflex", url=raw, model="reflex")


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def render_state(task: RouterTask) -> str:
    """The text the decision model reads: the request first, then its context."""
    parts = [
        f"## Request sent to the project\n{_clip(task.request, _REQUEST_CHARS)}",
        f"## Project\n{task.project}",
    ]
    if task.previous:
        parts.append(f"## The project's previous request\n{_clip(task.previous, _PREVIOUS_CHARS)}")
        if task.previous_result:
            parts.append(
                f"## What the project reported back for it\n"
                f"{_clip(task.previous_result, _PREVIOUS_CHARS)}"
            )
    return "\n\n".join(parts)


def questions(task: RouterTask) -> dict[str, dict[str, Any]]:
    """The typed questions, in TypeSafe's format; one more when there is a previous request."""
    asked: dict[str, dict[str, Any]] = {
        "tier": {
            "type": "choice",
            "instructions": (
                "What is the least capable model tier that will still do the request sent to "
                "the project right the first time, with no second round?"
            ),
            "criteria": dict(_TIER_CRITERIA),
        },
        **{
            f"effort_{tier}": {
                "type": "choice",
                "instructions": (
                    f"If {who} does the request sent to the project, how much reasoning "
                    "effort does it need to get it right the first time?"
                ),
                "criteria": dict(_EFFORT_CRITERIA),
            }
            for tier, who in _TIER_MODELS.items()
        },
        "cheap_redo": {
            "type": "noul",
            "instructions": (
                "Would an economical model (Haiku 5.5, GPT-6 Luna or Gemini Flash) likely get "
                "this request wrong or leave it needing a second round?"
            ),
        },
        "difficulty": {
            "type": "score",
            "instructions": "How hard is the request sent to the project?",
            "criteria": list(_DIFFICULTY),
        },
        "category": {
            "type": "choice",
            "instructions": "What kind of work does the request ask for?",
            "criteria": dict(_CATEGORIES),
        },
    }
    if task.previous:
        asked["fixes_previous"] = {
            "type": "noul",
            "instructions": (
                "Does the new request say the project's previous result was wrong, incomplete "
                "or broken, or ask it to fix or redo that work?"
            ),
        }
    return asked


def _probs(entry: dict[str, Any]) -> dict[str, float]:
    raw = entry.get("probabilities") or {}
    return {str(k): round(float(v), 4) for k, v in raw.items()}


def pick_effort(probs: dict[str, float]) -> str:
    """High, unless the model is at least :data:`EFFORT_MOVE_BAR` sure of another level."""
    best = max(probs, key=lambda e: probs[e], default=DEFAULT_EFFORT)
    return best if probs.get(best, 0.0) >= EFFORT_MOVE_BAR else DEFAULT_EFFORT


def read_decision(answers: dict[str, Any]) -> TierDecision:
    """Turn the backend's answers into a decision.

    :raises ValueError: If an answer is missing its numbers.
    """
    tier_probs = {t: p for t, p in _probs(answers["tier"]).items() if t in TIERS}
    if not tier_probs:
        raise ValueError(f"no tier probabilities: {answers['tier']!r}")
    by_tier: dict[str, dict[str, float]] = {}
    for tier in TIERS:
        probs = {e: p for e, p in _probs(answers[f"effort_{tier}"]).items() if e in EFFORTS}
        if not probs:
            raise ValueError(f"no effort probabilities for {tier}: {answers[f'effort_{tier}']!r}")
        by_tier[tier] = probs
    tier = max(tier_probs, key=lambda t: tier_probs[t])
    category = _probs(answers["category"])
    fixes = answers.get("fixes_previous")
    return TierDecision(
        tier=tier,
        tier_probs=tier_probs,
        effort=pick_effort(by_tier[tier]),
        effort_probs=by_tier[tier],
        efforts_by_tier={t: pick_effort(p) for t, p in by_tier.items()},
        p_cheap_redo=round(float(answers["cheap_redo"]["noul"]), 4),
        difficulty=round(float(answers["difficulty"]["score"]), 3),
        category=max(category, key=lambda c: category[c]) if category else "",
        p_fixes_previous=round(float(fixes["noul"]), 4) if isinstance(fixes, dict) else None,
    )


async def judge(task: RouterTask, backend: Backend) -> TierDecision:
    """Ask the decision model about one request; never raises for backend trouble."""
    started = time.perf_counter()
    try:
        answers, _usage = await post_questions(backend, render_state(task), questions(task))
        decision = read_decision(answers)
    except (httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        _logger.info("hub tier judge failed: %s", exc)
        decision = TierDecision(error=f"{type(exc).__name__}: {exc}"[:300])
    elapsed = round((time.perf_counter() - started) * 1000)
    return TierDecision(**{**asdict(decision), "latency_ms": elapsed})


def log_decision(path: pathlib.Path, task: RouterTask, decision: TierDecision) -> None:
    """Append one decision, with the model that actually ran the request."""
    line = {
        "at": round(time.time(), 3),
        "session_id": task.session_id,
        "project": task.project,
        "ran_on": {"provider": task.provider, "model": task.model, "effort": task.effort},
        "request": _clip(task.request, _LOGGED_REQUEST_CHARS),
        "had_previous": bool(task.previous),
        **asdict(decision),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        _logger.warning("could not write the hub's router log %s", path, exc_info=True)
