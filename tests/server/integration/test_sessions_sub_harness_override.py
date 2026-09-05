"""Integration tests for the per-sub-agent ``sub_harness_override`` column.

Mirrors ``test_sessions_harness_override.py`` one level down: the brain
override pins the ORCHESTRATOR, this pins each head of a multi-agent
bundle. Same create-time-only lifetime, same fail-loud validation, and the
same runner-body forwarding — which is where the first version of this
feature broke: the value persisted, the snapshot read it back, and every
turn dropped it before the runner could see it.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tests.server.helpers import create_test_agent

pytestmark = pytest.mark.asyncio


class _CaptureClient:
    """Runner-client stub that records the POSTed path + body.

    :param captured: Dict the test inspects after the route fires.
    """

    def __init__(self, captured: dict[str, Any]) -> None:
        self._captured = captured

    async def post(self, path: str, *, json: dict[str, Any], **_: Any) -> Any:
        """Record the path + body and return a fake 202 response."""
        self._captured["path"] = path
        self._captured["body"] = json

        class _Resp:
            status_code = 202
            headers: dict[str, str] = {}
            text = ""

        return _Resp()

    async def get(self, *_: Any, **__: Any) -> Any:
        raise NotImplementedError


def _stub_runner_client(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch ``_get_runner_client`` to return a capturing stub.

    :param monkeypatch: Pytest monkeypatch fixture.
    :returns: A dict the test inspects after the runner POST runs.
    """
    from omnigent.server.routes import sessions as sessions_mod

    captured: dict[str, Any] = {}

    async def _stub(*_: Any, **__: Any) -> _CaptureClient:
        return _CaptureClient(captured)

    monkeypatch.setattr(sessions_mod, "_get_runner_client", _stub)
    return captured


async def _bundle_agent(client: httpx.AsyncClient, name: str = "team-agent") -> dict[str, Any]:
    """Create a two-head bundle whose heads both declare ``claude-sdk``.

    :param client: Test HTTP client.
    :param name: Agent name to register under.
    :returns: The agent JSON, as :func:`create_test_agent` returns it.
    """
    return await create_test_agent(
        client,
        name=name,
        sub_agents=[{"name": "worker"}, {"name": "reviewer"}],
    )


async def test_create_persists_sub_harness_and_snapshot_reports_it(
    client: httpx.AsyncClient,
) -> None:
    """A create-time pick survives to the session snapshot.

    ``harness`` on the response is the BRAIN's, so it must NOT move; the
    heads' pick reads back from its own field. Without that field a client
    has no way to tell a session that chose a harness from one that didn't,
    which is exactly how the read path was found broken.
    """
    agent = await _bundle_agent(client)
    resp = await client.post(
        "/v1/sessions",
        json={
            "agent_id": agent["id"],
            "initial_items": [],
            "sub_harness_override": {"worker": "pi"},
        },
    )
    assert resp.status_code == 201, resp.text
    sid = resp.json()["id"]

    get = await client.get(f"/v1/sessions/{sid}")
    assert get.status_code == 200, get.text
    body = get.json()
    assert json.loads(body["sub_harness_override"]) == {"worker": "pi"}, (
        f"GET snapshot lost the per-sub-agent pick; got {body.get('sub_harness_override')!r}."
    )
    assert body.get("harness") == "claude-sdk", (
        "A head's pick moved the BRAIN's harness — the two overrides are "
        "independent and the response's `harness` is the orchestrator's."
    )


async def test_create_canonicalizes_sub_harness_alias(client: httpx.AsyncClient) -> None:
    """An alias is stored canonically, as the brain override's is.

    A raw alias on the row would miss the runner's harness registry when
    the child session is created with it.
    """
    agent = await _bundle_agent(client)
    resp = await client.post(
        "/v1/sessions",
        json={
            "agent_id": agent["id"],
            "initial_items": [],
            "sub_harness_override": {"worker": "openai-agents-sdk"},
        },
    )
    assert resp.status_code == 201, resp.text
    get = await client.get(f"/v1/sessions/{resp.json()['id']}")
    assert json.loads(get.json()["sub_harness_override"]) == {"worker": "openai-agents"}


async def test_create_rejects_unknown_sub_agent_name(client: httpx.AsyncClient) -> None:
    """A name the bundle never declares fails loud rather than no-opping.

    Silently ignoring it is the failure this feature exists to end: the
    session would run the declared team while the caller believed it had
    retargeted a head.
    """
    agent = await _bundle_agent(client)
    resp = await client.post(
        "/v1/sessions",
        json={
            "agent_id": agent["id"],
            "initial_items": [],
            "sub_harness_override": {"nobody": "pi"},
        },
    )
    assert resp.status_code == 400, resp.text
    assert "nobody" in resp.text


async def test_create_rejects_unknown_sub_harness(client: httpx.AsyncClient) -> None:
    """An unknown harness fails at create, before a session row exists."""
    agent = await _bundle_agent(client)
    resp = await client.post(
        "/v1/sessions",
        json={
            "agent_id": agent["id"],
            "initial_items": [],
            "sub_harness_override": {"worker": "bogus"},
        },
    )
    assert resp.status_code == 400, resp.text
    assert "bogus" in resp.text


async def test_runner_event_forwards_sub_harness_override(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every turn carries the picks down to the runner.

    The regression this pins: the create persisted the value and the
    snapshot reported it, but the message-forward body dropped it, so the
    runner never learned the pick and dispatched the declared team. Unlike
    the brain override — read once when the harness process spawns — these
    are read when the BRAIN DISPATCHES A CHILD, which can be any later
    turn, so every message must carry them.
    """
    captured = _stub_runner_client(monkeypatch)

    agent = await _bundle_agent(client)
    resp = await client.post(
        "/v1/sessions",
        json={
            "agent_id": agent["id"],
            "initial_items": [],
            "sub_harness_override": {"worker": "pi"},
            "sub_model_override": {"worker": "some-model"},
        },
    )
    assert resp.status_code == 201, resp.text
    sid = resp.json()["id"]

    event = await client.post(
        f"/v1/sessions/{sid}/events",
        json={
            "type": "message",
            "data": {
                "role": "user",
                "content": [{"type": "input_text", "text": "first turn"}],
            },
        },
    )
    assert event.status_code == 202, event.text
    assert captured.get("body") is not None, "the runner client was never POSTed to"
    assert json.loads(captured["body"]["sub_harness_override"]) == {"worker": "pi"}, (
        f"the message forward dropped the per-sub-agent harness; body had "
        f"{captured['body'].get('sub_harness_override')!r}"
    )
    assert json.loads(captured["body"]["sub_model_override"]) == {"worker": "some-model"}


async def test_create_without_picks_forwards_nothing(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A session that picked nothing sends the body it sent before this existed.

    The keys are omitted rather than sent as ``null`` so an older runner —
    and the bundle's own declared team — are untouched by the feature.
    """
    captured = _stub_runner_client(monkeypatch)

    agent = await _bundle_agent(client, name="team-agent-plain")
    resp = await client.post(
        "/v1/sessions",
        json={"agent_id": agent["id"], "initial_items": []},
    )
    assert resp.status_code == 201, resp.text
    await client.post(
        f"/v1/sessions/{resp.json()['id']}/events",
        json={
            "type": "message",
            "data": {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        },
    )
    assert "sub_harness_override" not in (captured.get("body") or {})
    assert "sub_model_override" not in (captured.get("body") or {})
