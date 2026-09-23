"""A cheap supervisor that workers can ask typed questions mid-task.

The orchestrator (Claude, in nexus) knows the human's request, the plan and
the brief; a worker model knows less and is bad at judging its own work. The
supervisor is a System One decision model -- TypeSafe's Jev, or a local
Jev-compatible server such as reflex -- handed what the orchestrator knows
plus what the worker has done so far. A worker asks it a narrow question,
yes/no or "pick one of these", and gets a calibrated answer in well under a
second, for a fraction of a cent, without waking the orchestrator.

It never writes prose, so it cannot mislead with confident text; it can only
be unsure. An answer below the confidence bar comes back as "not sure, ask
your orchestrator", which is the escalation.

Every question is appended to a JSONL ledger with its answer, latency and
cost, so where the supervisor helps and where it fails can be read off later.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Final, Literal

import httpx

_logger = logging.getLogger(__name__)

Kind = Literal["yes_no", "choice", "score"]

#: OpenRouter's Decisions API speaks TypeSafe's wire format.
DEFAULT_URL: Final[str] = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_MODEL: Final[str] = "typesafe/jev-1.13"
DEFAULT_KEY_FILE: Final[pathlib.Path] = pathlib.Path.home() / ".openrouter-key"

#: Below this, the worker is told to ask its orchestrator instead.
DEFAULT_MIN_CONFIDENCE: Final[float] = 0.75

#: Jev answers in ~0.4 s; a hung call must not hold the worker.
REQUEST_TIMEOUT_S: Final[float] = 20.0

#: Jev reads 32k tokens. Characters per part, most important first; the
#: worker's progress gets what is left, newest steps kept.
_STATE_CHARS: Final[int] = 90_000
_REQUEST_CHARS: Final[int] = 6_000
_BRIEF_CHARS: Final[int] = 24_000
_NOTES_CHARS: Final[int] = 12_000
_EVIDENCE_CHARS: Final[int] = 20_000

MAX_QUESTION_CHARS: Final[int] = 1_000
MAX_OPTIONS: Final[int] = 12

LEDGER_PATH: Final[pathlib.Path] = pathlib.Path.home() / ".omnigent" / "supervisor" / "asks.jsonl"


class SupervisorError(ValueError):
    """A question the supervisor cannot take, with a message for the worker."""


@dataclass(frozen=True)
class Question:
    """What a worker asks.

    :param kind: ``"yes_no"``; ``"choice"`` (pick one of *options*); or
        ``"score"`` (*options* are ordered levels, lowest first).
    :param question: The question, e.g. ``"Is the failing test in utils.py?"``.
    :param options: Choices or levels; empty for yes/no.
    :param evidence: Text the worker attaches for this question, e.g. a
        test output or a diff hunk. Not the whole history: the supervisor
        already has that.
    """

    kind: Kind
    question: str
    options: tuple[str, ...] = ()
    evidence: str = ""


@dataclass(frozen=True)
class Knowledge:
    """What the supervisor is told before a question.

    :param request: What the human asked the orchestrator.
    :param brief: The orchestrator's order that started the worker's turn.
    :param notes: Anything else the orchestrator wrote down for the supervisor.
    :param progress: What the worker has done so far, oldest first.
    """

    request: str = ""
    brief: str = ""
    notes: str = ""
    progress: str = ""


@dataclass(frozen=True)
class Verdict:
    """The supervisor's answer.

    :param answer: ``"yes"``/``"no"``, the chosen option, or the level;
        ``""`` when no backend answered.
    :param probabilities: Probability per answer.
    :param confidence: Probability of *answer*, 0 when unanswered.
    :param sure: Whether *confidence* clears the bar.
    :param backend: Which backend answered, or ``"none"``.
    :param latency_ms: Wall time of the call that answered.
    :param cost_usd: What that call cost, as the backend reported it.
    :param error: Why no backend answered, else ``""``.
    """

    answer: str
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    sure: bool = False
    backend: str = "none"
    latency_ms: int = 0
    cost_usd: float = 0.0
    error: str = ""


@dataclass(frozen=True)
class Backend:
    """A Jev-format ``/systemone``-style endpoint.

    :param name: Label for the ledger, e.g. ``"jev"`` or ``"local"``.
    :param url: POST endpoint taking ``{model, state, questions}``.
    :param model: Model id sent in the body; local servers ignore it.
    :param key: Bearer token, or ``""`` for an unauthenticated local server.
    """

    name: str
    url: str
    model: str
    key: str = ""


#: Appended to every brief while the supervisor is on: some harnesses (Hermes)
#: see no prompt but the brief, so the rule rides on it.
WORKER_RULE: Final[str] = (
    "[Your supervisor: when you are unsure -- which file, which approach, whether "
    "an output means it worked, whether you are done, whether to start editing -- "
    "you MUST ask sys_ask_supervisor before you guess or stop. It has read this "
    "brief, what your orchestrator knows, and your steps so far. Ask it before "
    "listing an open question.]"
)

#: Touch this file to turn the supervisor on without restarting anything:
#: runners are spawned fresh and read it when they register their tools.
ENABLED_FLAG: Final[pathlib.Path] = pathlib.Path.home() / ".omnigent" / "supervisor" / "enabled"


def enabled() -> bool:
    """Whether workers get ``sys_ask_supervisor``.

    ``OMNIGENT_SUPERVISOR=on|off`` wins; otherwise the :data:`ENABLED_FLAG`
    file decides. Either way a backend must be configured, or there is
    nobody to ask.

    :returns: True when the tool should be offered.
    """
    raw = os.environ.get("OMNIGENT_SUPERVISOR", "").strip().lower()
    wanted = raw in ("1", "on", "true", "yes") if raw else ENABLED_FLAG.exists()
    return wanted and bool(configured_backends())


def min_confidence() -> float:
    """Return the confidence bar, from ``OMNIGENT_SUPERVISOR_MIN_CONFIDENCE``."""
    raw = os.environ.get("OMNIGENT_SUPERVISOR_MIN_CONFIDENCE", "").strip()
    try:
        value = float(raw) if raw else DEFAULT_MIN_CONFIDENCE
    except ValueError:
        return DEFAULT_MIN_CONFIDENCE
    return value if 0.5 <= value <= 1.0 else DEFAULT_MIN_CONFIDENCE


def configured_backends() -> list[Backend]:
    """Return the backends to try, in order.

    The primary is Jev through OpenRouter (``OMNIGENT_SUPERVISOR_URL`` /
    ``_MODEL`` / ``_KEY_FILE`` override it), used only when its key file
    exists. ``OMNIGENT_SUPERVISOR_FALLBACK_URL`` adds a local Jev-compatible
    server (reflex) behind it: TypeSafe's firewall refuses some inputs
    outright, and the network can be down.

    :returns: Zero or more backends; empty means the supervisor is off.
    """
    backends: list[Backend] = []
    key_file = pathlib.Path(
        os.environ.get("OMNIGENT_SUPERVISOR_KEY_FILE", "").strip() or DEFAULT_KEY_FILE
    ).expanduser()
    url = os.environ.get("OMNIGENT_SUPERVISOR_URL", "").strip() or DEFAULT_URL
    try:
        key = key_file.read_text(encoding="utf-8").strip()
    except OSError:
        key = ""
    if key:
        model = os.environ.get("OMNIGENT_SUPERVISOR_MODEL", "").strip() or DEFAULT_MODEL
        backends.append(Backend(name="jev", url=url, model=model, key=key))
    fallback = os.environ.get("OMNIGENT_SUPERVISOR_FALLBACK_URL", "").strip()
    if fallback:
        backends.append(Backend(name="local", url=fallback, model="local"))
    return backends


def validate(question: Question) -> None:
    """Refuse a question the supervisor cannot answer well.

    :param question: The worker's question.
    :raises SupervisorError: With a message telling the worker how to fix it.
    """
    text = question.question.strip()
    if not text:
        raise SupervisorError("Ask a question: `question` is empty.")
    if len(text) > MAX_QUESTION_CHARS:
        raise SupervisorError(
            f"Keep the question under {MAX_QUESTION_CHARS} characters and put long "
            "material in `evidence`. One narrow question works best."
        )
    options = [o.strip() for o in question.options]
    if question.kind == "yes_no":
        if options:
            raise SupervisorError("A yes_no question takes no options.")
        return
    if question.kind not in ("choice", "score"):
        raise SupervisorError("`kind` must be yes_no, choice or score.")
    if len(options) < 2 or len(options) > MAX_OPTIONS or any(not o for o in options):
        raise SupervisorError(
            f"A {question.kind} question needs 2-{MAX_OPTIONS} non-empty options."
        )
    if len(set(options)) != len(options):
        raise SupervisorError("Options must be distinct.")


def _clip_head(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "\n[...cut]"


def render_state(knowledge: Knowledge, evidence: str = "") -> str:
    """Lay out everything the supervisor reads, most important first.

    :param knowledge: What the orchestrator knows and what the worker did.
    :param evidence: What the worker attached to this question.
    :returns: The state text, at most ``_STATE_CHARS`` characters.
    """
    parts: list[tuple[str, str]] = [
        ("What the human asked the orchestrator", _clip_head(knowledge.request, _REQUEST_CHARS)),
        ("The orchestrator's order to this worker", _clip_head(knowledge.brief, _BRIEF_CHARS)),
        ("The orchestrator's notes for the supervisor", _clip_head(knowledge.notes, _NOTES_CHARS)),
        ("What the worker attached to this question", _clip_head(evidence, _EVIDENCE_CHARS)),
    ]
    head = "\n\n".join(f"## {title}\n{body}" for title, body in parts if body)
    title = "\n\n## What the worker has done so far (oldest first)\n"
    cut = "[...earlier steps cut]\n"
    room = _STATE_CHARS - len(head) - len(title) - len(cut)
    progress = knowledge.progress.strip()
    if progress and room > 500:
        if len(progress) > room:
            progress = cut + progress[-room:]
        head += title + progress
    return head


def jev_question(question: Question) -> dict[str, Any]:
    """Translate a worker's question into TypeSafe's question format.

    :param question: A validated question.
    :returns: One entry for the request's ``questions`` map.
    """
    if question.kind == "yes_no":
        return {"type": "noul", "instructions": question.question.strip()}
    options = [o.strip() for o in question.options]
    if question.kind == "choice":
        return {
            "type": "choice",
            "instructions": question.question.strip(),
            "criteria": dict.fromkeys(options, ""),
        }
    return {"type": "score", "instructions": question.question.strip(), "criteria": options}


def read_answer(question: Question, answer: dict[str, Any]) -> tuple[str, dict[str, float]]:
    """Turn a backend's answer into (answer, probability per answer).

    :param question: The question that was asked.
    :param answer: The ``answers`` entry the backend returned.
    :returns: The answer and its probability map.
    :raises ValueError: If the entry does not match the question.
    """
    if question.kind == "yes_no":
        p_yes = float(answer["noul"])
        probs = {"yes": round(p_yes, 4), "no": round(1.0 - p_yes, 4)}
        return ("yes" if p_yes >= 0.5 else "no"), probs
    options = [o.strip() for o in question.options]
    raw = answer.get("probabilities") or {}
    probs: dict[str, float] = {}
    if question.kind == "choice":
        for option in options:
            if option in raw:
                probs[option] = round(float(raw[option]), 4)
        chosen = str(answer.get("choice") or "")
        if not probs and chosen in options:
            probs[chosen] = round(float(answer.get("confidence") or 0.0), 4)
    else:
        # Score probabilities come keyed by level index ("0", "1", ...).
        for index, level in enumerate(options):
            if str(index) in raw:
                probs[level] = round(float(raw[str(index)]), 4)
        if not probs and "score" in answer:
            index = min(len(options) - 1, max(0, round(float(answer["score"]))))
            probs[options[index]] = round(float(answer.get("confidence") or 0.0), 4)
    if not probs:
        raise ValueError(f"no probabilities for a {question.kind} answer: {answer!r}")
    return max(probs, key=lambda k: probs[k]), probs


def _client() -> httpx.AsyncClient:
    """Return the HTTP client for backend calls; a seam for tests."""
    return httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S)


async def _call(
    backend: Backend, state: str, question: Question
) -> tuple[dict[str, Any], dict[str, Any]]:
    """POST one question to one backend.

    :returns: ``(answer entry, usage)``.
    :raises RuntimeError: On a refusal, HTTP error or malformed reply.
    """
    answers, usage = await post_questions(backend, state, {"q": jev_question(question)})
    return answers["q"], usage


async def post_questions(
    backend: Backend, state: str, questions: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """POST one state and its questions to one backend.

    :param backend: Where to send them.
    :param state: The text the model reads.
    :param questions: TypeSafe-format questions by id, e.g. ``{"q": {"type": "noul", ...}}``.
    :returns: ``(answers by question id, usage)``; every id is answered.
    :raises RuntimeError: On a refusal, HTTP error or malformed reply.
    """
    body = {"model": backend.model, "state": state, "questions": questions}
    headers = {"Authorization": f"Bearer {backend.key}"} if backend.key else {}
    async with _client() as client:
        resp = await client.post(backend.url, json=body, headers=headers)
    if resp.status_code == 403 and "<html" in resp.text[:2000].lower():
        # TypeSafe's firewall answers some inputs with an HTML block page.
        raise RuntimeError("refused by the provider's firewall")
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict) or any(
        not isinstance(answers.get(k), dict) for k in questions
    ):
        raise RuntimeError(f"malformed reply: {str(data)[:200]}")
    return answers, data.get("usage") or {}


async def ask(
    question: Question,
    knowledge: Knowledge,
    *,
    backends: list[Backend] | None = None,
    session_id: str = "",
    parent_id: str = "",
) -> Verdict:
    """Ask the supervisor one question; never raises for backend trouble.

    :param question: A validated question.
    :param knowledge: What the supervisor reads first.
    :param backends: Override for tests; defaults to :func:`configured_backends`.
    :param session_id: The asking worker's session, for the ledger.
    :param parent_id: Its orchestrator's session, for the ledger.
    :returns: The verdict; ``sure`` is False when unsure or unanswered.
    """
    chosen = configured_backends() if backends is None else backends
    state = render_state(knowledge, question.evidence)
    bar = min_confidence()
    errors: list[str] = []
    verdict: Verdict | None = None
    for backend in chosen:
        started = time.perf_counter()
        try:
            entry, usage = await _call(backend, state, question)
            answer, probs = read_answer(question, entry)
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            errors.append(f"{backend.name}: {exc}")
            _logger.info("supervisor backend %s failed: %s", backend.name, exc)
            continue
        confidence = probs[answer]
        verdict = Verdict(
            answer=answer,
            probabilities=probs,
            confidence=confidence,
            sure=confidence >= bar,
            backend=backend.name,
            latency_ms=round((time.perf_counter() - started) * 1000),
            cost_usd=float(usage.get("cost") or 0.0),
        )
        break
    if verdict is None:
        verdict = Verdict(
            answer="",
            error="; ".join(errors) or "no supervisor backend is configured",
        )
    _record(question, verdict, state_chars=len(state), session_id=session_id, parent_id=parent_id)
    return verdict


def format_for_worker(verdict: Verdict) -> str:
    """Word the verdict as the tool result the worker reads.

    :param verdict: The supervisor's verdict.
    :returns: A short plain-text answer, with the escalation when unsure.
    """
    if not verdict.answer:
        return (
            "The supervisor is unavailable right now. Decide from your brief; if you "
            "truly cannot, end your turn with the question for your orchestrator."
        )
    spread = ", ".join(
        f"{name} {p:.2f}"
        for name, p in sorted(verdict.probabilities.items(), key=lambda kv: -kv[1])
    )
    if verdict.sure:
        return f"Supervisor: {verdict.answer} (confident: {spread})."
    return (
        f"The supervisor is not sure ({spread}). Do not guess: if you can check it "
        "yourself (read the file, run the test), do that and ask again with the "
        "result as evidence; otherwise end your turn with this question for your "
        "orchestrator."
    )


def _record(
    question: Question, verdict: Verdict, *, state_chars: int, session_id: str, parent_id: str
) -> None:
    """Append one line to the supervisor ledger; never raises."""
    append_ledger(
        {
            "at": time.time(),
            "session": session_id,
            "parent": parent_id,
            "kind": question.kind,
            "question": question.question,
            "options": list(question.options),
            "evidence_chars": len(question.evidence),
            "state_chars": state_chars,
            **asdict(verdict),
        }
    )


def append_ledger(line: dict[str, Any]) -> None:
    """Append one JSON line to the supervisor ledger; never raises.

    :param line: The record, e.g. ``{"at": 1790000000.0, "kind": "yes_no", ...}``.
    """
    path = pathlib.Path(os.environ.get("OMNIGENT_SUPERVISOR_LEDGER", "").strip() or LEDGER_PATH)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        _logger.warning("could not write the supervisor ledger at %s", path, exc_info=True)
