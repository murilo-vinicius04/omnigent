"""What each session runs on, as the hub shows and changes it.

Read from the session's own snapshot (the one its chat page reads), so the
hub and that session's model picker always agree. The rules for which
sessions offer a model or effort picker mirror the chat's
(``web/src/pages/ChatPage.tsx``: ``modelPickerKindForConv`` and
``effortLevelsForConv``).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

#: The chat's own name for each terminal wrapper.
PROVIDERS: Final[dict[str, str]] = {
    "claude-code-native-ui": "Claude Code",
    "codex-native-ui": "Codex",
    "cursor-native-ui": "Cursor",
    "kiro-native-ui": "Kiro",
    "opencode-native-ui": "OpenCode",
    "pi-native-ui": "Pi",
    "antigravity-native-ui": "Antigravity",
    "hermes-native-ui": "Hermes",
    "qwen-native-ui": "Qwen",
    "goose-native-ui": "Goose",
    "kimi-native-ui": "Kimi",
}

#: Wrappers whose chat offers a model picker.
_PICKER_WRAPPERS: Final[dict[str, str]] = {
    "claude-code-native-ui": "claude",
    "codex-native-ui": "codex",
    "cursor-native-ui": "cursor",
    "kiro-native-ui": "kiro",
    "opencode-native-ui": "opencode",
    "pi-native-ui": "pi",
}

#: Efforts by picker kind; Codex's come per model from its catalog.
_EFFORTS: Final[dict[str, tuple[str, ...]]] = {
    "claude": ("low", "medium", "high", "xhigh", "max"),
    "pi": ("none", "minimal", "low", "medium", "high", "xhigh", "max"),
}

#: The host harness whose catalog stands in until the session reports its own.
HOST_CATALOG_HARNESS: Final[dict[str, str]] = {"claude": "claude-native", "codex": "codex-native"}

#: Spoken names for efforts, folded to the catalog's spelling.
_EFFORT_WORDS: Final[dict[str, str]] = {
    "extrahigh": "xhigh",
    "veryhigh": "xhigh",
    "maximum": "max",
    "maximo": "max",
    "máximo": "max",
    "alto": "high",
    "alta": "high",
    "medio": "medium",
    "médio": "medium",
    "media": "medium",
    "média": "medium",
    "baixo": "low",
    "baixa": "low",
}

#: Words that mean "go back to the harness's own default".
DEFAULT_WORDS: Final[frozenset[str]] = frozenset({"default", "padrao", "padrão", "reset"})


@dataclass(frozen=True)
class ModelChoice:
    """One model a session can switch to.

    :param id: What a pick sends, e.g. ``"opus"`` or ``"gpt-6.1-sol"``.
    :param label: What the picker shows, e.g. ``"Opus 5.5"``.
    :param model: The wire model behind it, when the catalog names one.
    :param efforts: Efforts this model takes (Codex lists them per model).
    :param is_default: Whether it is the harness's default.
    """

    id: str
    label: str
    model: str = ""
    efforts: tuple[str, ...] = ()
    is_default: bool = False


@dataclass(frozen=True)
class SessionRun:
    """What a session runs on and what it could run on instead.

    :param provider: Its harness as the chat names it, e.g. ``"Codex"``.
    :param model: The model it runs now, e.g. ``"claude-opus-5-5"``; empty
        when it runs its harness's default and has not said which.
    :param requested: A model picked for it that it has not reported running
        yet, e.g. right after a switch; empty when there is none.
    :param effort: Its reasoning effort; empty for the default.
    :param kind: The chat's picker kind, e.g. ``"claude"``; empty when its
        model cannot be changed from the chat (or from here).
    :param models: What it can switch to.
    :param efforts: Efforts it takes on its current model.
    """

    provider: str = ""
    model: str = ""
    requested: str = ""
    effort: str = ""
    kind: str = ""
    models: tuple[ModelChoice, ...] = ()
    efforts: tuple[str, ...] = ()

    def label(self, model_id: str) -> str:
        """A model's picker label, else the id itself."""
        choice = find_choice(self.models, model_id)
        return choice.label if choice is not None else model_id

    def selected(self) -> str:
        """The choice its picker shows: the pending pick, else what it runs."""
        choice = find_choice(self.models, self.requested or self.model)
        return choice.id if choice is not None else ""

    def summary(self) -> str:
        """One line, e.g. ``"Claude Code · Opus 5.5 · effort xhigh"``."""
        parts = [self.provider or "unknown agent"]
        parts.append(self.label(self.model) if self.model else "default model")
        if self.requested:
            parts.append(f"switching to {self.label(self.requested)}")
        if self.effort:
            parts.append(f"effort {self.effort}")
        return " · ".join(parts)

    def as_dict(self) -> dict[str, Any]:
        """The shape the hub page and the voice read."""
        return {
            "provider": self.provider,
            "model": self.model,
            "model_label": self.label(self.model) if self.model else "",
            "requested": self.requested,
            "requested_label": self.label(self.requested) if self.requested else "",
            "effort": self.effort,
            "selected": self.selected(),
            "models": [
                {"id": m.id, "label": m.label, "efforts": list(m.efforts)} for m in self.models
            ],
            "efforts": list(self.efforts),
        }


def normalize_default(wanted: str) -> bool:
    """Whether *wanted* means the harness's own default."""
    return wanted.strip().lower() in DEFAULT_WORDS


def _fold(text: str) -> str:
    return re.sub(r"[\s._\-/()]+", "", text.strip().lower())


def find_choice(models: Sequence[ModelChoice], wanted: str) -> ModelChoice | None:
    """The choice whose id, wire model or label is exactly *wanted*."""
    key = _fold(wanted)
    if not key:
        return None
    for choice in models:
        if key in {_fold(choice.id), _fold(choice.model), _fold(choice.label)} - {""}:
            return choice
    return None


def match_model(models: Sequence[ModelChoice], wanted: str) -> list[ModelChoice]:
    """The choices *wanted* can mean: an exact one, else every partial match.

    ``"opus"`` finds ``Opus 5.5``; ``"gpt"`` finds every GPT model, which the
    caller asks about instead of guessing.
    """
    exact = find_choice(models, wanted)
    if exact is not None:
        return [exact]
    key = _fold(wanted)
    if not key:
        return []
    return [
        m for m in models if any(key in _fold(name) for name in (m.id, m.model, m.label) if name)
    ]


def match_effort(efforts: Sequence[str], wanted: str) -> str | None:
    """The effort *wanted* names, in the catalog's spelling, if it offers it."""
    key = _fold(wanted)
    key = _EFFORT_WORDS.get(key, key)
    return next((e for e in efforts if _fold(e) == key), None)


def _choices(options: Sequence[Any]) -> tuple[ModelChoice, ...]:
    out: list[ModelChoice] = []
    for raw in options:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        efforts = tuple(
            str(e.get("reasoningEffort"))
            for e in raw.get("supportedReasoningEfforts") or []
            if isinstance(e, dict) and e.get("reasoningEffort")
        )
        out.append(
            ModelChoice(
                id=str(raw["id"]),
                label=str(raw.get("displayName") or raw["id"]),
                model=str(raw.get("model") or ""),
                efforts=tuple(dict.fromkeys(efforts)),
                is_default=bool(raw.get("isDefault")),
            )
        )
    return tuple(out)


def picker_kind(snapshot: dict[str, Any]) -> str:
    """The chat's model picker kind for a session snapshot; empty for none."""
    labels = snapshot.get("labels") or {}
    wrapper = labels.get("omnigent.wrapper")
    if wrapper is None and snapshot.get("harness") == "codex-native":
        return "codex"
    return _PICKER_WRAPPERS.get(str(wrapper or ""), "")


def provider_name(snapshot: dict[str, Any]) -> str:
    """The harness as the chat names it."""
    labels = snapshot.get("labels") or {}
    wrapper = str(labels.get("omnigent.wrapper") or "")
    if wrapper in PROVIDERS:
        return PROVIDERS[wrapper]
    if picker_kind(snapshot) == "codex":
        return "Codex"
    return str(snapshot.get("agent_name") or snapshot.get("harness") or "")


def run_from_snapshot(
    snapshot: dict[str, Any], fallback_options: Sequence[Any] = ()
) -> SessionRun:
    """What a session runs on, from its ``GET /v1/sessions/{id}`` body.

    :param fallback_options: The host's catalog for its harness, used while
        the session has not reported its own (its runner is not up yet).
    """
    kind = picker_kind(snapshot)
    override = str(snapshot.get("model_override") or "")
    reported = str(snapshot.get("llm_model") or "")
    requested = ""
    if kind in ("claude", "codex"):
        model = reported or override
    elif kind in ("cursor", "kiro"):
        model = override
    else:
        model = override or reported
    models = _choices(snapshot.get("model_options") or fallback_options) if kind else ()
    if kind == "codex":
        current = find_choice(models, model)
        efforts = current.efforts if current is not None else ()
    else:
        efforts = _EFFORTS.get(kind, ())
    if kind in ("claude", "codex") and reported and override:
        # Asked for and reported can spell one model two ways ("opus" and
        # "claude-opus-5-5"); only a different model is still on its way.
        asked = find_choice(models, override)
        running = find_choice(models, reported)
        same = asked is not None and asked == running
        if not same and override != reported:
            requested = override
    return SessionRun(
        provider=provider_name(snapshot),
        model=model,
        requested=requested,
        effort=str(snapshot.get("reasoning_effort") or ""),
        kind=kind,
        models=models,
        efforts=efforts,
    )
