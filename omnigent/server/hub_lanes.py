"""Lanes: several agents in one project, each its own chat on its own topic.

A lane is a chat in a project. The hub opens one when a request starts work
that should not wait for, or steer, a lane that is busy. Each new lane runs on
the provider, model and effort picked for its first request (``model_router``
for the tier and effort, ``budget`` for the provider), and is told which
repositories the busy lanes are writing in, so two agents never edit the same
checkout.
"""

from __future__ import annotations

import json
import pathlib
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final
from urllib.parse import unquote

#: The label that marks a chat the hub opened as a lane; its value is the topic.
LANE_LABEL: Final[str] = "omnigent.hub.lane"

#: Content block type the hub stamps on the sends it makes itself. Those are
#: already watched explicitly, so they must not trigger a second watch.
_HUB_SEND_MARKER: Final[str] = "force_claude"

#: Event types that start lane work (and so deserve a hub report when they end).
_WORK_EVENT_TYPES: Final[frozenset[str]] = frozenset({"message", "slash_command"})


def direct_message_lane_topic(
    *,
    body_type: str,
    content: object,
    labels: Mapping[str, str] | None,
) -> str | None:
    """Lane topic when an ingress event should pull the lane's hub into watching.

    Lanes only report back to the hub project when the hub itself sent the
    request. A message typed directly in the lane's own chat starts the same
    work but no watch, so its reply never reaches the project. This is the
    decision half of closing that gap: it returns the lane's topic when the
    event is user-direct work on a hub lane, else ``None``.

    :param body_type: The ingress event type, e.g. ``"message"``.
    :param content: The message content blocks (``body.data["content"]``), or
        ``None`` for events without content.
    :param labels: The session's labels, carrying :data:`LANE_LABEL` on lanes.
    :returns: The stripped lane topic, or ``None`` to leave watching alone.
    """
    if body_type not in _WORK_EVENT_TYPES:
        return None
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == _HUB_SEND_MARKER:
                return None
    topic = (labels or {}).get(LANE_LABEL, "")
    topic = topic.strip() if isinstance(topic, str) else ""
    return topic or None


#: Each provider's native terminal wrapper, as the session's ``omnigent.wrapper`` label.
WRAPPERS: Final[dict[str, str]] = {
    "claude": "claude-code-native-ui",
    "codex": "codex-native-ui",
    "gemini": "antigravity-native-ui",
    "muse": "muse-code",
}

#: The built-in agent that runs each provider's wrapper, by agent name.
AGENT_NAMES: Final[dict[str, str]] = {
    "claude": "claude-native-ui",
    "codex": "codex-native-ui",
    "gemini": "antigravity-native-ui",
    "muse": "muse-code",
}

#: The harness whose host catalog lists each provider's models.
HARNESSES: Final[dict[str, str]] = {
    "claude": "claude-native",
    "codex": "codex-native",
    "gemini": "antigravity-native",
    "muse": "muse-code",
}

#: Each provider's harness as a chat's run names it.
PROVIDER_LABELS: Final[dict[str, str]] = {
    "claude": "Claude Code",
    "codex": "Codex",
    "gemini": "Antigravity",
    "muse": "Muse Code",
}

#: Each provider by the name a chat's run gives its harness.
PROVIDER_OF_HARNESS: Final[dict[str, str]] = {v: k for k, v in PROVIDER_LABELS.items()}

#: Words that name a model family, and the provider and tier word it means.
_MODEL_WORDS: Final[dict[str, tuple[str, str]]] = {
    "opus": ("claude", "Opus"),
    "sonnet": ("claude", "Sonnet"),
    "haiku": ("claude", "Haiku"),
    "sol": ("codex", "Sol"),
    "luna": ("codex", "Luna"),
    "astra": ("codex", "Astra"),
    "flash": ("gemini", "Flash"),
    "argon": ("gemini", "Argon"),
    "spark": ("muse", "Spark"),
    "wonder": ("muse", "Wonder"),
}

#: Words that name only a provider.
_PROVIDER_WORDS: Final[dict[str, str]] = {
    "claude": "claude",
    "anthropic": "claude",
    "codex": "codex",
    "openai": "codex",
    "gpt": "codex",
    "chatgpt": "codex",
    "gemini": "gemini",
    "google": "gemini",
    "agy": "gemini",
    "antigravity": "gemini",
    "muse": "muse",
}

#: Longest topic kept as a lane's name.
MAX_TOPIC_CHARS: Final[int] = 60

#: Repositories named per busy lane in a new lane's brief.
MAX_REPOS: Final[int] = 4


@dataclass(frozen=True)
class LaneRun:
    """What a new lane runs on.

    :param provider: ``"claude"``, ``"codex"``, ``"gemini"`` or ``"muse"``.
    :param model: The id its CLI launches with, e.g. ``"gpt-6.1-sol"``.
    :param effort: The reasoning effort the create carries; empty for Gemini,
        whose effort is part of the model id.
    :param tier: The tier it was picked for, when the router picked it.
    :param reason: Why this one, for the hub chat and the log.
    """

    provider: str
    model: str
    effort: str = ""
    tier: str = ""
    reason: str = ""

    def summary(self) -> str:
        """e.g. ``"Codex · gpt-6.1-sol · high"``."""
        parts = [PROVIDER_LABELS.get(self.provider, self.provider), self.model or "default model"]
        if self.effort:
            parts.append(self.effort)
        return " · ".join(parts)


def _normalize_model_id(value: str) -> str:
    """The bare model id inside a provider-qualified envelope, else the value.

    Muse Code's adapter identifies catalog models as
    ``muse-model:<urlencoded [providerId, profileId, id]>``; version
    comparisons must unwrap that envelope first, or every id versions the
    same (the envelope's own percent-encoding digits).
    """
    prefix = "muse-model:"
    if not value.startswith(prefix):
        return value
    try:
        envelope = json.loads(unquote(value[len(prefix):]))
    except ValueError:
        return value
    if isinstance(envelope, list) and len(envelope) >= 3 and isinstance(envelope[2], str):
        return envelope[2]
    return value


def _version(model_id: str) -> tuple[int, ...]:
    """The version numbers in a model id: ``gpt-6.1-sol`` -> ``(6, 1)``."""
    found = re.search(r"\d+(?:[.-]\d+)*", _normalize_model_id(model_id))
    return tuple(int(n) for n in re.split(r"[.-]", found.group(0))) if found else ()


def launch_model(
    provider: str, word: str, effort: str, catalog: Sequence[str]
) -> tuple[str, str] | None:
    """The catalog's newest model a tier word names, and the effort the create carries.

    agy puts the effort in the model id (``gemini-3.8-flash-high``); the
    other CLIs take it separately.

    :returns: ``(model id, effort)``, or ``None`` when the catalog has none.
    """
    want = word.lower()
    if provider == "gemini":
        family = [m for m in catalog if m.lower().startswith("gemini") and want in m.lower()]
        exact = [m for m in family if m.lower().endswith(f"-{effort}")]
        pool = exact or family
        return (max(pool, key=_version), "") if pool else None
    # "opus[1m]" is the same model with a longer window: the plain id is the default.
    pool = [m for m in catalog if want in m.lower() and "[" not in m]
    return (max(pool, key=_version), effort) if pool else None


def named_run(words: str) -> tuple[str, str]:
    """The provider, and the model family if any, that their words name.

    ``"use Luna"`` -> ``("codex", "Luna")``; ``"on Claude"`` ->
    ``("claude", "")``; nothing named -> ``("", "")``. A close typo of a
    family word still names it (``"spar"`` -> ``("muse", "Spark")``); version
    numbers ride separately via :func:`named_version`.
    """
    tokens = re.findall(r"[a-z]+", (words or "").lower())
    for token in tokens:
        if token in _MODEL_WORDS:
            return _MODEL_WORDS[token]
    for token in tokens:
        if len(token) < 3:
            continue
        for word, named in _MODEL_WORDS.items():
            if word.startswith(token) or token.startswith(word):
                return named
    for token in tokens:
        if token in _PROVIDER_WORDS:
            return _PROVIDER_WORDS[token], ""
    return "", ""


def named_version(words: str) -> str:
    """The model version their words name: ``"muse spark 1.3"`` -> ``"1.3"``.

    Empty when they named no version; the caller decides what an unpinned
    family resolves to.
    """
    found = re.search(r"\d+(?:[.\-]\d+)*", words or "")
    return found.group(0) if found else ""


#: The Muse model a named lane runs unless a pin or the catalog says
#: otherwise: Spark 1.3, never the adapter's older default.
MUSE_DEFAULT_MODEL: Final[str] = "muse-spark-1.3"

#: The effort every named Muse lane carries.
MUSE_DEFAULT_EFFORT: Final[str] = "xhigh"


def muse_model(word: str, version: str, catalog: Sequence[str]) -> str | None:
    """The Muse model id a named lane runs, or ``None`` to stay pending.

    A pinned version (``"1.3"``) wins over newest-of-the-family; it resolves
    against the catalog's bare ids, so qualified envelopes match too. An
    unreachable (empty) catalog trusts a pin, or the 1.3 default, since the
    agent validates a manual id itself. A pin the live catalog does not
    carry, or a family it does not offer, returns ``None``: the lane must
    say so instead of silently running another model.
    """
    family = word or "Spark"
    if version:
        pin = f"muse-{family.lower()}-{version}"
        if not catalog or any(
            _normalize_model_id(m).lower() == pin for m in catalog
        ):
            return pin
        return None
    launch = launch_model("muse", family, MUSE_DEFAULT_EFFORT, catalog)
    if launch is not None:
        return launch[0]
    return MUSE_DEFAULT_MODEL if not catalog else None


def topic_name(topic: str, fallback: str) -> str:
    """A lane's name: the topic the router gave, else the start of its request."""
    clean = " ".join((topic or "").split()) or " ".join(fallback.split("\n\n")[0].split())
    return clean if len(clean) <= MAX_TOPIC_CHARS else clean[: MAX_TOPIC_CHARS - 1] + "…"


def branch_slug(topic: str) -> str:
    """A branch-safe form of a topic: ``"Joystick plan!"`` -> ``"joystick-plan"``."""
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")
    return slug[:40].strip("-") or "lane"


def repo_root(path: str) -> str | None:
    """The git checkout a path sits in (a worktree counts), or ``None``."""
    try:
        here = pathlib.Path(path).expanduser()
        here = here if here.is_dir() else here.parent
        for folder in (here, *here.parents):
            if (folder / ".git").exists():
                return str(folder)
    except OSError:
        return None
    return None


def repos_in_use(paths: Sequence[str]) -> list[str]:
    """The checkouts these paths sit in, the most used first."""
    counted = Counter(root for p in paths if (root := repo_root(p)) is not None)
    return [root for root, _ in counted.most_common(MAX_REPOS)]


@dataclass(frozen=True)
class LaneSpec:
    """A chat the hub opens as a lane.

    :param topic: What it works on; its title and lane label.
    :param run: What it runs on.
    :param like: The chat whose machine and folder it starts in.
    :param branch: A branch for its own git worktree, when its folder is a
        checkout another lane is writing in; empty starts it in the folder.
    """

    topic: str
    run: LaneRun
    like: str
    branch: str = ""
    host_id: str = ""
    workspace: str = ""


@dataclass(frozen=True)
class BusyLane:
    """A lane at work while a new one opens: its name, task and checkouts."""

    name: str
    task: str
    repos: tuple[str, ...] = ()


def brief(project: str, topic: str, busy: Sequence[BusyLane]) -> str:
    """What a new lane is told besides the request: who else is working, and where.

    Never the busy lane's transcript: its task and the checkouts it writes in.
    """
    lines = [f"[The hub opened this chat as a new lane of the project {project}, for: {topic}.]"]
    if not busy:
        return lines[0]
    lines.append(f"Other lanes of {project} are working right now. Leave their files alone:")
    for lane in busy:
        where = f"; writing in {', '.join(lane.repos)}" if lane.repos else ""
        task = f' on "{lane.task}"' if lane.task else ""
        lines.append(f"- {lane.name}{task}{where}")
    if any(lane.repos for lane in busy):
        lines.append(
            "If this task needs changes in one of those repositories, first make your own "
            f"git worktree for it on a new branch (hub/{branch_slug(topic)}) and work there."
        )
    return "\n".join(lines)
