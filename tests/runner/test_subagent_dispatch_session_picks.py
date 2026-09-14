"""A session's per-head picks must reach the CHILD SESSION at create time.

Drives the real dispatch entry point (``execute_tool``) against an
``httpx.MockTransport`` server, the way ``test_subagent_dispatch_process_leak``
does, and reads the ``POST /v1/sessions`` body the dispatch actually sent.

That body is the whole point: the child is created with the picked harness
pinned on it, so the pre-dispatch CLI probe, the server's own validation, the
child's later turns and its reconnects all agree about what it runs. Resolving
the pick only at turn time left every one of those reading the harness the
bundle declared.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from omnigent.runner import app as runner_app
from omnigent.runner.tool_dispatch import execute_tool

PARENT_ID = "conv_parent_picks"
CHILD_ID = "conv_child_picks"


def _spec() -> SimpleNamespace:
    """A one-head bundle whose head declares ``claude-sdk`` and no model.

    :returns: A structural stand-in for the parent's ``AgentSpec``.
    """
    return SimpleNamespace(
        sub_agents=[
            SimpleNamespace(
                name="worker",
                executor=SimpleNamespace(config={"harness": "claude-sdk"}, model=None),
            )
        ]
    )


@pytest.fixture(autouse=True)
def _cli_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report every harness CLI as installed.

    The dispatch probes PATH before creating the child, and these tests are
    about which harness it probes, not whether this machine has it. The probe
    itself is asserted on directly in
    :func:`test_missing_cli_is_judged_on_the_picked_harness`.

    :param monkeypatch: Pytest monkeypatch fixture.
    :returns: None.
    """
    from omnigent.onboarding import harness_install

    monkeypatch.setattr(harness_install, "missing_harness_cli", lambda _harness: None)


async def _dispatch(created: list[dict[str, Any]]) -> str:
    """Run one ``sys_session_send`` against a mock server, capturing the create.

    :param created: List the child-create body is appended to.
    :returns: The dispatch's return string.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        if method == "GET" and path == f"/v1/sessions/{PARENT_ID}":
            return httpx.Response(
                200,
                json={
                    "id": PARENT_ID,
                    "agent_id": "agent_parent",
                    "root_conversation_id": PARENT_ID,
                    "parent_session_id": None,
                },
            )
        if method == "GET" and path == f"/v1/sessions/{PARENT_ID}/child_sessions":
            return httpx.Response(200, json={"data": []})
        if method == "POST" and path == "/v1/sessions":
            created.append(json.loads(request.content or b"{}"))
            return httpx.Response(
                201,
                json={"id": CHILD_ID, "parent_session_id": PARENT_ID, "labels": {}},
            )
        if method == "POST" and path.startswith(f"/v1/sessions/{CHILD_ID}"):
            return httpx.Response(202, json={"ok": True})
        if method == "PATCH" and path.startswith("/v1/sessions/"):
            return httpx.Response(200, json={"ok": True})
        if method == "DELETE" and path.startswith("/v1/sessions/"):
            return httpx.Response(200, json={"deleted": True})
        return httpx.Response(404, json={"error": f"unmocked {method} {path}"})

    inbox: asyncio.Queue[Any] = asyncio.Queue()
    runner_app._session_inboxes_ref[PARENT_ID] = inbox
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://server"
    ) as server_client:
        try:
            return await execute_tool(
                tool_name="sys_session_send",
                arguments=json.dumps(
                    {"agent": "worker", "title": "task-1", "args": "do the thing"}
                ),
                server_client=server_client,
                conversation_id=PARENT_ID,
                agent_spec=_spec(),
                session_inbox=inbox,
            )
        finally:
            runner_app._session_inboxes_ref.pop(PARENT_ID, None)
            runner_app.unregister_child_session(CHILD_ID)
            runner_app.unregister_subagent_work(CHILD_ID)


@pytest.mark.asyncio
async def test_dispatch_pins_the_sessions_harness_pick_on_the_child() -> None:
    """The picked harness is written onto the child at create.

    Without this the create body carries no ``harness_override``, the child
    row tracks the bundle's declared head, and the person who changed the
    dropdown watches the old harness answer.
    """
    runner_app.note_session_sub_agent_overrides(
        PARENT_ID,
        harnesses={"worker": "pi"},
        models={"worker": "some-model"},
        efforts={"worker": "high"},
    )
    created: list[dict[str, Any]] = []
    try:
        output = await _dispatch(created)
    finally:
        runner_app.forget_session_sub_agent_overrides(PARENT_ID)

    assert not output.startswith("Error"), output
    assert created, "the dispatch never created a child session"
    assert created[0].get("harness_override") == "pi", (
        f"the child was created on {created[0].get('harness_override')!r}, not the "
        f"session's pick — the dispatch read the bundle's declared head instead"
    )
    assert created[0].get("model_override") == "some-model"
    assert created[0].get("reasoning_effort") == "high"


@pytest.mark.asyncio
async def test_dispatch_without_a_pick_tracks_the_bundle() -> None:
    """No pick means the create body is the one it always sent.

    The declared team stays the default, and a bundle that never opted into
    this feature sends nothing new.
    """
    created: list[dict[str, Any]] = []
    output = await _dispatch(created)

    assert not output.startswith("Error"), output
    assert created
    assert "harness_override" not in created[0]


@pytest.mark.asyncio
async def test_missing_cli_is_judged_on_the_picked_harness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-dispatch PATH probe asks about the harness that will run.

    A pick retargets the head onto a harness this machine may not have. The
    probe must name THAT one — probing the bundle's declared harness would
    pass, and the failure would surface much later as a generic "turn failed"
    inside the child.
    """
    from omnigent.onboarding import harness_install

    probed: list[str] = []

    def _probe(harness: str) -> object | None:
        probed.append(harness)
        return SimpleNamespace(binary="pi", package="pi-coding-agent", install_hint=None)

    monkeypatch.setattr(harness_install, "missing_harness_cli", _probe)
    runner_app.note_session_sub_agent_overrides(PARENT_ID, harnesses={"worker": "pi"}, models=None)
    created: list[dict[str, Any]] = []
    try:
        output = await _dispatch(created)
    finally:
        runner_app.forget_session_sub_agent_overrides(PARENT_ID)

    assert probed == ["pi"], f"probed {probed!r}, not the picked harness"
    assert "pi" in output and output.startswith("Error")
    assert not created, "a child was created for a harness that cannot start here"


@pytest.mark.asyncio
async def test_an_effort_the_picked_harness_rejects_is_dropped_not_fatal() -> None:
    """A stale effort loses the dispatch nothing.

    "minimal" belongs to the OpenAI ladder and has no Anthropic alias, so the
    head on claude-sdk cannot take it. The orchestrator's turn must not die
    over a value a human chose at session create and is no longer present to
    fix — unlike the dispatch argument and the spec's own value, whose callers
    are.
    """
    runner_app.note_session_sub_agent_overrides(
        PARENT_ID, harnesses=None, models=None, efforts={"worker": "minimal"}
    )
    created: list[dict[str, Any]] = []
    try:
        output = await _dispatch(created)
    finally:
        runner_app.forget_session_sub_agent_overrides(PARENT_ID)

    assert not output.startswith("Error"), output
    assert created
    assert "reasoning_effort" not in created[0]


@pytest.mark.asyncio
async def test_the_effort_is_judged_against_the_harness_the_head_ends_up_on() -> None:
    """The PICKED harness decides, not the one the spec declares.

    "minimal" is refused by the head's declared claude-sdk and accepted by the
    pi it was moved to — which is exactly why this cannot be validated at
    session create, where the harness may still be about to change.
    """
    runner_app.note_session_sub_agent_overrides(
        PARENT_ID, harnesses={"worker": "pi"}, models=None, efforts={"worker": "minimal"}
    )
    created: list[dict[str, Any]] = []
    try:
        output = await _dispatch(created)
    finally:
        runner_app.forget_session_sub_agent_overrides(PARENT_ID)

    assert not output.startswith("Error"), output
    assert created[0].get("reasoning_effort") == "minimal"


@pytest.mark.asyncio
async def test_a_head_whose_harness_needs_a_missing_package_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dispatch refuses before it creates the child.

    THE BUG THIS PINS. ``antigravity`` is an in-process harness needing the
    ``google-antigravity`` package, which no PATH probe can see — so it sailed
    past the CLI check, the child was created, and the harness died inside it
    with an ImportError the orchestrator only ever saw as "turn failed". It
    could then re-dispatch into the same wall.

    Note this test does NOT stub the package probe. The autouse fixture above
    silences the CLI probe, and stubbing this one too is exactly how the
    original hole stayed invisible: every dispatch test asserted against a
    world where the harness was always installable.
    """
    from omnigent.onboarding import harness_install

    monkeypatch.setattr(
        harness_install,
        "missing_harness_package",
        lambda harness: (
            "uv pip install 'omnigent[antigravity]'" if harness == "antigravity" else None
        ),
    )
    runner_app.note_session_sub_agent_overrides(
        PARENT_ID, harnesses={"worker": "antigravity"}, models=None
    )
    created: list[dict[str, Any]] = []
    try:
        output = await _dispatch(created)
    finally:
        runner_app.forget_session_sub_agent_overrides(PARENT_ID)

    assert output.startswith("Error"), output
    assert "omnigent[antigravity]" in output, (
        "the refusal must name the install command, or the operator is told "
        f"only that something is missing: {output!r}"
    )
    assert not created, "no child session may be created for a harness that cannot boot"


@pytest.mark.asyncio
async def test_the_package_probe_is_asked_about_the_real_harness() -> None:
    """The probe runs unstubbed against the harness the head will use.

    Guards the seam the other tests mock away: whatever
    ``missing_harness_package`` says about ``antigravity`` here is what a real
    dispatch would act on, so a change that makes the probe blind to an
    uninstalled SDK fails here rather than in production.
    """
    from omnigent.onboarding.harness_install import missing_harness_package

    # agy-backed: its requirement is a binary, which the CLI probe owns.
    assert missing_harness_package("antigravity-native") is None
    # A pure-SDK harness with nothing to install is not a package harness.
    assert missing_harness_package("claude-sdk") is None

    verdict = missing_harness_package("antigravity")
    try:
        import google_antigravity  # noqa: F401
    except ImportError:
        assert verdict is not None and "antigravity" in verdict, (
            "the SDK is absent here, so the dispatch must be told what to install"
        )
    else:
        assert verdict is None, "the SDK is installed, so nothing should be demanded"
