"""Lanes: several agents in one project, each its own chat on its own topic.

A lane is a chat in a project. The hub opens one when a request starts work
that should not wait for, or steer, a lane that is busy. Each new lane runs on
the provider, model and effort picked for its first request (``model_router``
for the tier and effort, ``budget`` for the provider), and is told which
repositories the busy lanes are writing in, so two agents never edit the same
checkout.
"""

from __future__ import annotations

import pathlib
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

#: The label that marks a chat the hub opened as a lane; its value is the topic.
LANE_LABEL: Final[str] = "omnigent.hub.lane"

#: Each provider's native terminal wrapper, as the session's ``omnigent.wrapper`` label.
WRAPPERS: Final[dict[str, str]] = {
    "claude": "claude-code-native-ui",
    "codex": "codex-native-ui",
    "gemini": "antigravity-native-ui",
}

#: The built-in agent that runs each provider's wrapper, by agent name.
AGENT_NAMES: Final[dict[str, str]] = {
    "claude": "claude-native-ui",
    "codex": "codex-native-ui",
    "gemini": "antigravity-native-ui",
}

#: The harness whose host catalog lists each provider's models.
HARNESSES: Final[dict[str, str]] = {
    "claude": "claude-native",
    "codex": "codex-native",
    "gemini": "antigravity-native",
}

#: Each provider's harness as a chat's run names it.
PROVIDER_LABELS: Final[dict[str, str]] = {
    "claude": "Claude Code",
    "codex": "Codex",
    "gemini": "Antigravity",
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
}

#: Longest topic kept as a lane's name.
MAX_TOPIC_CHARS: Final[int] = 60

#: Repositories named per busy lane in a new lane's brief.
MAX_REPOS: Final[int] = 4


@dataclass(frozen=True)
class LaneRun:
    """What a new lane runs on.

    :param provider: ``"claude"``, ``"codex"`` or ``"gemini"``.
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


def _version(model_id: str) -> tuple[int, ...]:
    """The version numbers in a model id: ``gpt-6.1-sol`` -> ``(6, 1)``."""
    found = re.search(r"\d+(?:[.-]\d+)*", model_id)
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
    ``("claude", "")``; nothing named -> ``("", "")``.
    """
    tokens = re.findall(r"[a-z]+", words.lower())
    for token in tokens:
        if token in _MODEL_WORDS:
            return _MODEL_WORDS[token]
    for token in tokens:
        if token in _PROVIDER_WORDS:
            return _PROVIDER_WORDS[token], ""
    return "", ""


def topic_name(topic: str, fallback: str) -> str:
    """A lane's name: the topic the router gave, else the start of its request."""
    clean = " ".join((topic or "").split()) or " ".join(fallback.split())
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
