"""The one worker an orchestrator delegates to, chosen by a person mid-conversation.

A bundle opts in by listing its interchangeable workers under the brain's
``executor.config.worker_choices``; the UI then offers a single Worker control
instead of one row per sub-agent. The choice lives in session labels, so it
can change on a running conversation, and the runner reads it fresh on every
delegation rather than caching it at session start.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from omnigent.spec.types import AgentSpec

#: Declared sub-agent name of the chosen worker, e.g. ``"codex"``.
WORKER_LABEL = "team.worker"
#: Model the chosen worker runs; absent or empty means its own default.
WORKER_MODEL_LABEL = "team.worker_model"
#: Reasoning effort the chosen worker runs; absent or empty means its own default.
WORKER_EFFORT_LABEL = "team.worker_effort"


@dataclass(frozen=True)
class WorkerChoice:
    """One worker a person can pick.

    :param name: Declared sub-agent name, e.g. ``"codex"``.
    :param label: What the control shows, e.g. ``"Codex"``.
    :param harness: The harness the sub-agent declares, e.g. ``"codex"``.
    :param models: Models to offer when the host cannot list the harness's
        own catalog, e.g. ``("gpt-5.6-luna", "gpt-5.6-terra")``.
    :param efforts: Reasoning efforts to offer, e.g. ``("low", "high")``; empty
        hides the effort control for this worker.
    :param default_model: The model the worker's own spec pins, e.g.
        ``"grok-4.6"``; ``None`` when it runs its harness's default.
    :param default_effort: The effort the worker's own spec pins, e.g.
        ``"low"``; ``None`` when it runs its harness's default.
    """

    name: str
    label: str
    harness: str | None
    models: tuple[str, ...]
    efforts: tuple[str, ...] = ()
    default_model: str | None = None
    default_effort: str | None = None


def worker_choices(spec: AgentSpec | None) -> list[WorkerChoice]:
    """Return the workers *spec* lets a person choose between, in declared order.

    Entries naming no declared sub-agent are dropped, so a typo in the bundle
    cannot offer a worker that would fail at dispatch.

    :param spec: The orchestrator's spec, e.g. nexus.
    :returns: The choices, or ``[]`` when the bundle does not opt in.
    """
    config = getattr(getattr(spec, "executor", None), "config", None)
    raw = config.get("worker_choices") if isinstance(config, Mapping) else None
    if isinstance(raw, str):
        # The spec parser stores executor.config values as strings, so a
        # YAML list arrives as its Python repr; literal_eval reads literals only.
        try:
            raw = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return []
    if not isinstance(raw, list):
        return []
    declared = {
        child.name: child for child in getattr(spec, "sub_agents", None) or [] if child.name
    }
    choices: list[WorkerChoice] = []
    for entry in raw:
        item = {"name": entry} if isinstance(entry, str) else entry
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        if not isinstance(name, str) or name not in declared:
            continue
        label = item.get("label")
        models = item.get("models")
        efforts = item.get("efforts")
        executor = declared[name].executor
        choices.append(
            WorkerChoice(
                name=name,
                label=label if isinstance(label, str) and label else name,
                harness=getattr(executor, "harness_kind", None),
                models=tuple(m for m in models if isinstance(m, str) and m)
                if isinstance(models, list)
                else (),
                efforts=tuple(e for e in efforts if isinstance(e, str) and e)
                if isinstance(efforts, list)
                else (),
                default_model=getattr(executor, "model", None) or None,
                default_effort=getattr(executor, "reasoning_effort", None) or None,
            )
        )
    return choices


def resolve_worker(
    spec: AgentSpec | None, labels: Mapping[str, object]
) -> tuple[str | None, str | None, str | None]:
    """Return the person's worker choice, which every delegation goes to.

    The orchestrator cannot see the Worker control, so a dispatch naming
    another worker is sent to the chosen one rather than refused: a refusal
    costs a full orchestrator turn just to repeat the dispatch.

    :param spec: The orchestrator's spec.
    :param labels: The orchestrator session's labels.
    :returns: ``(worker, model, effort)``: the chosen sub-agent, its model and
        its reasoning effort, each of the last two ``None`` for the worker's own
        default. All ``None`` when nothing was chosen.
    """
    choices = worker_choices(spec)
    if not choices:
        return None, None, None
    picked = labels.get(WORKER_LABEL)
    if not isinstance(picked, str) or picked not in {c.name for c in choices}:
        return None, None, None
    return (
        picked,
        _label_value(labels, WORKER_MODEL_LABEL),
        _label_value(labels, WORKER_EFFORT_LABEL),
    )


def _label_value(labels: Mapping[str, object], key: str) -> str | None:
    """Return the stripped label *key*, or ``None`` when absent or blank."""
    value = labels.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None
