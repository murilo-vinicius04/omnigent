"""The one worker an orchestrator delegates to, chosen by a person mid-conversation.

A bundle opts in by listing its interchangeable workers under the brain's
``executor.config.worker_choices``; the UI then offers a single Worker control
instead of one row per sub-agent. The choice lives in two session labels, so it
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


@dataclass(frozen=True)
class WorkerChoice:
    """One worker a person can pick.

    :param name: Declared sub-agent name, e.g. ``"codex"``.
    :param label: What the control shows, e.g. ``"Codex"``.
    :param harness: The harness the sub-agent declares, e.g. ``"codex"``.
    :param models: Models to offer when the host cannot list the harness's
        own catalog, e.g. ``("gpt-5.6-luna", "gpt-5.6-terra")``.
    """

    name: str
    label: str
    harness: str | None
    models: tuple[str, ...]


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
        choices.append(
            WorkerChoice(
                name=name,
                label=label if isinstance(label, str) and label else name,
                harness=getattr(declared[name].executor, "harness_kind", None),
                models=tuple(m for m in models if isinstance(m, str) and m)
                if isinstance(models, list)
                else (),
            )
        )
    return choices


def resolve_worker(
    spec: AgentSpec | None, labels: Mapping[str, object], sub_agent_name: str
) -> tuple[str | None, str | None]:
    """Check a delegation against the person's worker choice.

    :param spec: The orchestrator's spec.
    :param labels: The orchestrator session's labels.
    :param sub_agent_name: The sub-agent the orchestrator is dispatching to.
    :returns: ``(error, model)``. ``error`` refuses a dispatch to any other
        sub-agent than the chosen one; ``model`` is the chosen worker's model,
        or ``None`` for its default. Both ``None`` when nothing was chosen.
    """
    choices = worker_choices(spec)
    if not choices:
        return None, None
    picked = labels.get(WORKER_LABEL)
    if not isinstance(picked, str) or picked not in {c.name for c in choices}:
        return None, None
    if sub_agent_name != picked:
        return (
            f"Error: the person chose {picked!r} as the worker in this "
            f"conversation's Worker control, so {sub_agent_name!r} is not "
            f"dispatched. Send this and every following task to {picked!r}. "
            "If a different worker is really needed, ask them to change the control.",
            None,
        )
    model = labels.get(WORKER_MODEL_LABEL)
    return None, model.strip() if isinstance(model, str) and model.strip() else None
