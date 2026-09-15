"""The Worker control: nexus offers one worker at a time, and dispatch follows the pick."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from omnigent.runner.tool_dispatch import _team_worker_pick
from omnigent.spec import load
from omnigent.team_worker import WORKER_LABEL, WORKER_MODEL_LABEL, resolve_worker, worker_choices

NEXUS = Path(__file__).resolve().parents[1] / "examples" / "nexus"


@pytest.fixture
def nexus_spec():
    return load(NEXUS)


def test_nexus_offers_exactly_its_four_workers(nexus_spec) -> None:
    choices = worker_choices(nexus_spec)
    assert [(c.name, c.label, c.harness) for c in choices] == [
        ("gemini", "Gemini", "antigravity-native"),
        ("claude", "Claude", "claude-native"),
        ("codex", "Codex", "codex"),
        ("hermes", "Hermes (NVIDIA NIM)", "hermes-native"),
    ]
    assert choices[2].models == ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol")


def test_a_bundle_without_worker_choices_is_untouched(nexus_spec) -> None:
    nexus_spec.executor.config.pop("worker_choices")
    assert worker_choices(nexus_spec) == []
    assert resolve_worker(nexus_spec, {WORKER_LABEL: "codex"}) == (None, None)
    # Runner tests stand in a bare namespace for the spec; that must not crash.
    assert worker_choices(object()) == []  # type: ignore[arg-type]


def test_nothing_picked_lets_the_orchestrator_choose() -> None:
    spec = load(NEXUS)
    assert resolve_worker(spec, {}) == (None, None)
    # A label naming no offered worker is ignored rather than blocking everything.
    assert resolve_worker(spec, {WORKER_LABEL: "cursor"}) == (None, None)


def test_the_picked_worker_and_model_win() -> None:
    spec = load(NEXUS)
    labels = {WORKER_LABEL: "codex", WORKER_MODEL_LABEL: "gpt-5.6-terra"}
    assert resolve_worker(spec, labels) == ("codex", "gpt-5.6-terra")
    # An empty model means the worker's own default.
    assert resolve_worker(spec, {WORKER_LABEL: "codex", WORKER_MODEL_LABEL: " "}) == (
        "codex",
        None,
    )


def test_dispatch_reads_the_pick_from_the_live_session() -> None:
    spec = load(NEXUS)
    seen: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"id": "conv_x", "labels": {WORKER_LABEL: "claude"}})

    async def run() -> tuple[str | None, str | None]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond), base_url="http://server"
        ) as client:
            return await _team_worker_pick(
                server_client=client,
                conversation_id="conv_x",
                agent_spec=spec,
            )

    assert asyncio.run(run()) == ("claude", None)
    assert seen == ["/v1/sessions/conv_x"]


def test_dispatch_never_blocks_when_the_session_cannot_be_read() -> None:
    spec = load(NEXUS)

    async def run() -> tuple[str | None, str | None]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(503)),
            base_url="http://server",
        ) as client:
            return await _team_worker_pick(
                server_client=client,
                conversation_id="conv_x",
                agent_spec=spec,
            )

    assert asyncio.run(run()) == (None, None)
