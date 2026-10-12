"""A locally run tool's result must reach the harness, or the turn waits for it forever."""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from omnigent.runner import tool_dispatch
from omnigent.runner.tool_dispatch import dispatch_tool_locally
from omnigent.spec.types import AgentSpec


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_dispatch, "_TOOL_RESULT_RETRY_BASE_S", 0.0)


async def _dispatch(harness_handler) -> str:
    """Run a cheap local tool (a rename the server refuses) against *harness_handler*."""

    def server_handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("server unavailable")

    async with (
        httpx.AsyncClient(
            transport=httpx.MockTransport(server_handler), base_url="http://server"
        ) as server_client,
        httpx.AsyncClient(
            transport=httpx.MockTransport(harness_handler), base_url="http://harness"
        ) as harness_client,
    ):
        return await dispatch_tool_locally(
            tool_name="sys_session_rename",
            call_id="call_1",
            arguments=json.dumps({"title": "Check the stencil modes"}),
            response_id="response_1",
            harness_client=harness_client,
            server_client=server_client,
            conversation_id="conv_current",
            agent_spec=AgentSpec(spec_version=1),
        )


@pytest.mark.asyncio
async def test_a_dropped_connection_is_retried_and_the_result_delivered() -> None:
    tries: list[dict[str, object]] = []

    def harness(request: httpx.Request) -> httpx.Response:
        tries.append(json.loads(request.content))
        if len(tries) == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return httpx.Response(204)

    output = await _dispatch(harness)

    assert len(tries) == 2
    assert tries[-1] == {"type": "tool_result", "call_id": "call_1", "output": output}


@pytest.mark.asyncio
async def test_a_harness_5xx_is_retried() -> None:
    codes = iter([503, 204])
    tries: list[int] = []

    def harness(_request: httpx.Request) -> httpx.Response:
        tries.append(1)
        return httpx.Response(next(codes))

    await _dispatch(harness)

    assert len(tries) == 2


@pytest.mark.asyncio
async def test_a_harness_that_stays_down_gives_up_without_raising(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tries: list[int] = []

    def harness(_request: httpx.Request) -> httpx.Response:
        tries.append(1)
        raise httpx.ConnectError("harness gone")

    with caplog.at_level(logging.WARNING, logger=tool_dispatch._logger.name):
        output = await _dispatch(harness)

    assert "sys_session_rename failed" in json.loads(output)["error"]
    assert len(tries) == tool_dispatch._TOOL_RESULT_POST_ATTEMPTS
    assert "after 4 attempt(s)" in caplog.text


@pytest.mark.asyncio
async def test_a_refused_result_is_not_retried() -> None:
    tries: list[int] = []

    def harness(_request: httpx.Request) -> httpx.Response:
        tries.append(1)
        return httpx.Response(404, json={"detail": "conversation mismatch"})

    await _dispatch(harness)

    assert len(tries) == 1
