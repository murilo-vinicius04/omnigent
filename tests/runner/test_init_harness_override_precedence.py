"""A session's harness pick must outrank the harness its bundle declares.

The fourth seam of the per-head picker, and the one that stayed empty longest.
``_initialize_session`` resolved the spec, then read the harness straight off
it -- ``spec.executor.config["harness"] or spec.executor.type`` -- with no
reference to the envelope the same function had just parsed. So the pick
reached the child's ``POST /v1/sessions`` body (pinned there by the dispatch)
and reached the spawn env as a *model*, but the harness the runner actually
booted was whatever the bundle's author wrote.

That combination is worse than either half failing alone. Debby's ``gpt`` head
declares ``codex``; a session that picked ``antigravity-native`` for it booted
``codex`` anyway and died on a CLI this machine does not have -- while the
picked model, resolved against the picked harness, was applied on the way
down. The user sees a head configured one way and a failure naming a harness
they never chose.

``model_override`` was read from the envelope snapshot here all along (see
``tests/runner/test_enforce_sandbox_gate.py::
test_create_session_seeds_model_override_from_envelope``), which is why these
tests assert the harness against that same snapshot: the two picks describe
one session and must not be able to disagree about it.
"""

from __future__ import annotations

from typing import Any

import pytest

from omnigent.runner import create_runner_app
from omnigent.spec.types import AgentSpec, ExecutorSpec
from tests.runner.conftest import _FakeProcessManager, _runner_client, _ScriptedHarnessClient
from tests.runner.helpers import NullServerClient

AGENT_ID = "ag_debby"
SESSION_ID = "conv_pick"
HEAD_NAME = "gpt"


def _bundle() -> AgentSpec:
    """Debby's shape: a ``claude-sdk`` brain over a ``codex`` head.

    The two declared harnesses differ so an assertion cannot pass by
    accident -- whichever one is spawned names the path that produced it.

    :returns: The parent spec, with its one head attached.
    """
    return AgentSpec(
        spec_version=1,
        name="debby",
        executor=ExecutorSpec(type="omnigent", config={"harness": "claude-sdk"}),
        sub_agents=[
            AgentSpec(
                spec_version=1,
                name=HEAD_NAME,
                executor=ExecutorSpec(type="omnigent", config={"harness": "codex"}),
            )
        ],
    )


async def _resolver(agent_id: str, session_id: str | None = None) -> AgentSpec:
    """Resolve any id to the bundle, as a sub-agent session's server does.

    :param agent_id: Ignored; a head's session is bound to its parent's id.
    :param session_id: Ignored.
    :returns: The parent spec tree.
    """
    del agent_id, session_id
    return _bundle()


def _init_body(
    *, harness_override: str | None, sub_agent_name: str | None = None
) -> dict[str, Any]:
    """Build the protocol-v2 ``session_init`` body the modern server sends.

    Mirrors :func:`build_runner_session_init_payload` rather than calling it,
    so the envelope under test is the one the runner parses off the wire.

    :param harness_override: The session's pick, or ``None`` for no pick.
    :param sub_agent_name: The head this session runs, if any.
    :returns: A ``POST /v1/sessions`` body.
    """
    from omnigent.runner.session_init_protocol import (
        SESSION_INIT_PAYLOAD_KEY,
        SESSION_INIT_PROTOCOL_VERSION,
        RunnerSessionInitEnvelope,
        RunnerSessionInitSnapshot,
    )

    envelope = RunnerSessionInitEnvelope(
        protocol_version=SESSION_INIT_PROTOCOL_VERSION,
        server_version="0.0.0.dev0",
        session_id=SESSION_ID,
        agent_id=AGENT_ID,
        sub_agent_name=sub_agent_name,
        snapshot=RunnerSessionInitSnapshot(
            created_at=0,
            updated_at=0,
            harness_override=harness_override,
        ),
    )
    return {
        "session_id": SESSION_ID,
        "agent_id": AGENT_ID,
        "sub_agent_name": sub_agent_name,
        SESSION_INIT_PAYLOAD_KEY: envelope.model_dump(mode="json"),
    }


async def _spawned_harness(body: dict[str, Any]) -> str:
    """Create a session from *body* and return the harness it booted.

    :param body: The ``POST /v1/sessions`` body to send.
    :returns: The harness name passed to ``get_client`` at create time.
    """
    pm = _FakeProcessManager(_ScriptedHarnessClient([]))
    app = create_runner_app(
        process_manager=pm,  # type: ignore[arg-type]
        spec_resolver=_resolver,
        server_client=NullServerClient(),  # type: ignore[arg-type]
    )
    async with _runner_client(app) as client:
        resp = await client.post("/v1/sessions", json=body)
    assert resp.status_code == 201, f"expected 201, got {resp.status_code}: {resp.text}"
    assert pm.get_client_calls, "create_session never asked for a harness"
    _conv_id, harness, _env = pm.get_client_calls[-1]
    return harness


@pytest.mark.asyncio
async def test_the_session_pick_outranks_the_bundles_harness() -> None:
    """A picked harness boots, not the one the bundle's author declared.

    The bundle's team was fixed at authoring time; a human picking a harness
    in the config dialog is picking it for THIS session.
    """
    harness = await _spawned_harness(_init_body(harness_override="codex"))
    assert harness == "codex", (
        f"the session picked 'codex' and the runner booted {harness!r} -- the "
        "bundle's declared harness outranked the human's pick"
    )


@pytest.mark.asyncio
async def test_a_heads_pick_outranks_the_heads_declared_harness() -> None:
    """The production failure, pinned: the pick must survive the sub-spec swap.

    A head's session resolves the parent tree and swaps to the head's own
    sub-spec. The override is applied *after* that swap, so the pick outranks
    the head's declared ``codex`` rather than the parent's ``claude-sdk``.
    Reading the harness off the freshly swapped spec is exactly what dropped
    the pick in the field.
    """
    harness = await _spawned_harness(
        _init_body(harness_override="antigravity-native", sub_agent_name=HEAD_NAME)
    )
    assert harness == "antigravity-native", (
        f"the head's session picked 'antigravity-native' and the runner booted "
        f"{harness!r}. 'codex' is the field bug: the head dies on a CLI this "
        "machine has not got, having never been asked to run it"
    )


@pytest.mark.asyncio
async def test_no_pick_keeps_the_bundles_harness() -> None:
    """An unpicked session still runs what the bundle declares.

    The override is a human's choice, and its absence is not one. A session
    that picked nothing must be indistinguishable from one created before the
    field existed.
    """
    harness = await _spawned_harness(_init_body(harness_override=None))
    assert harness == "claude-sdk", (
        f"a session with no pick booted {harness!r} instead of the bundle's declared 'claude-sdk'"
    )


@pytest.mark.asyncio
async def test_auto_defers_to_the_bundle_rather_than_being_spawned() -> None:
    """``"auto"`` is Smart Routing's "undecided", not a harness to boot.

    It reaches the snapshot as a harness_override like any other value, and
    ``canonicalize_harness('auto')`` answers ``'auto'`` rather than ``None``,
    so nothing downstream rejects it -- the runner would ask the process
    manager to spawn a harness by that name. It has to be recognised here, and
    the first message's router replaces it (see ``routing_class_from_snapshot``).
    """
    harness = await _spawned_harness(_init_body(harness_override="auto"))
    assert harness == "claude-sdk", (
        f"'auto' was treated as a harness name and booted {harness!r}; it is "
        "the not-yet-routed sentinel and must defer to the bundle"
    )
