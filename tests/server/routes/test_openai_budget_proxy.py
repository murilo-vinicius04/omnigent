"""The OpenAI budget proxy refuses calls that could leave the free pools, and meters the rest.

A request that crosses a pool's daily limit is billed in full, so every
refusal here must happen before anything reaches OpenAI.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent import openai_token_budget as budget
from omnigent.server.routes import openai_budget_proxy as proxy

Responder = Callable[[httpx.Request], httpx.Response]


class _Upstream:
    """A fake api.openai.com that records what reached it."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.respond: Responder = lambda _req: httpx.Response(500)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)

    def body(self, index: int = -1) -> dict[str, object]:
        return json.loads(self.requests[index].content)


@pytest.fixture
def upstream() -> _Upstream:
    return _Upstream()


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, upstream: _Upstream
) -> Iterator[TestClient]:
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path))
    (tmp_path / "openai-key").write_text("sk-real-key\n")
    proxy._inflight.clear()
    app = FastAPI()
    app.include_router(
        proxy.create_openai_budget_proxy_router(
            upstream_base_url="https://upstream.test/v1",
            transport=httpx.MockTransport(upstream),
        ),
        prefix="/v1",
    )
    with TestClient(app) as test_client:
        token = proxy.ensure_proxy_token()
        test_client.headers["authorization"] = f"Bearer {token}"
        yield test_client


def _sse(*events: dict[str, object]) -> bytes:
    return b"".join(f"data: {json.dumps(e)}\n\n".encode() for e in events)


def _small_pool() -> dict[str, object]:
    return next(row for row in budget.pool_usage() if row["id"] == "small")


def test_token_file_is_private_and_bad_tokens_are_refused(
    client: TestClient, upstream: _Upstream, tmp_path: Path
) -> None:
    assert (tmp_path / "openai-budget-token").stat().st_mode & 0o777 == 0o600
    for header in ({"authorization": "Bearer wrong"}, {"authorization": ""}):
        reply = client.post("/v1/openai-budget/v1/responses", json={"model": "x"}, headers=header)
        assert reply.status_code == 401
    assert upstream.requests == []


def test_streamed_call_is_relayed_pinned_capped_and_metered(
    client: TestClient, upstream: _Upstream
) -> None:
    stream = _sse(
        {"type": "response.created", "response": {"id": "r1"}},
        {"type": "response.output_text.delta", "delta": "hi"},
        {
            "type": "response.completed",
            "response": {
                "id": "r1",
                "usage": {
                    "input_tokens": 1_200,
                    "input_tokens_details": {"cached_tokens": 1_000},
                    "output_tokens": 300,
                },
            },
        },
    )
    upstream.respond = lambda _req: httpx.Response(
        200, content=stream, headers={"content-type": "text/event-stream"}
    )

    reply = client.post(
        "/v1/openai-budget/v1/responses", json={"model": "gpt-5-mini", "stream": True}
    )

    assert reply.status_code == 200
    assert reply.content == stream
    sent = upstream.requests[0]
    assert sent.headers["authorization"] == "Bearer sk-real-key"
    assert upstream.body() == {
        "model": "gpt-5-mini-2025-08-07",
        "stream": True,
        "max_output_tokens": proxy.DEFAULT_OUTPUT_RESERVE,
    }
    assert _small_pool()["models"] == {"gpt-5-mini": 1_500}
    assert proxy._inflight == {"small": 0}


def test_model_outside_the_free_pools_never_reaches_openai(
    client: TestClient, upstream: _Upstream
) -> None:
    for model in ("o3-mini", "gpt-5-mini-2025-10-01", "gpt-5.3-codex"):
        reply = client.post("/v1/openai-budget/v1/responses", json={"model": model})
        assert reply.status_code == 429
        assert reply.json()["error"]["code"] == "insufficient_quota"
    assert upstream.requests == []


def test_call_that_could_cross_the_pool_is_refused_whole(
    client: TestClient, upstream: _Upstream
) -> None:
    budget.record("gpt-5.6-luna", {"input_tokens": 2_490_000})

    # 10k left: the forced 32k output cap alone could cross the limit.
    refused = client.post("/v1/openai-budget/v1/responses", json={"model": "gpt-5.6-terra"})
    assert refused.status_code == 429
    assert "10k of 2.5M tokens left" in refused.json()["error"]["message"]
    assert upstream.requests == []

    # The same call with its own small output cap fits, so it goes through.
    upstream.respond = lambda _req: httpx.Response(
        200, json={"usage": {"input_tokens": 20, "output_tokens": 30}}
    )
    admitted = client.post(
        "/v1/openai-budget/v1/responses",
        json={"model": "gpt-5.6-terra", "max_output_tokens": 1_000},
    )
    assert admitted.status_code == 200
    assert upstream.body()["max_output_tokens"] == 1_000
    assert _small_pool()["tokens"] == 2_490_050


def test_calls_in_flight_hold_their_worst_case(client: TestClient, upstream: _Upstream) -> None:
    proxy._inflight["small"] = 2_495_000
    reply = client.post(
        "/v1/openai-budget/v1/responses",
        json={"model": "gpt-5.6-luna", "max_output_tokens": 10_000},
    )
    assert reply.status_code == 429
    assert upstream.requests == []


def test_streamed_chat_asks_for_usage_and_is_metered(
    client: TestClient, upstream: _Upstream
) -> None:
    stream = (
        _sse(
            {"choices": [{"delta": {"content": "ok"}}]},
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 50,
                    "completion_tokens": 7,
                    "prompt_tokens_details": {"cached_tokens": 0},
                },
            },
        )
        + b"data: [DONE]\n\n"
    )
    upstream.respond = lambda _req: httpx.Response(
        200, content=stream, headers={"content-type": "text/event-stream"}
    )

    reply = client.post(
        "/v1/openai-budget/v1/chat/completions",
        json={"model": "gpt-4.1-nano", "stream": True, "messages": []},
    )

    assert reply.status_code == 200
    sent = upstream.body()
    assert sent["stream_options"] == {"include_usage": True}
    assert sent["max_completion_tokens"] == proxy.DEFAULT_OUTPUT_RESERVE
    assert _small_pool()["models"] == {"gpt-4.1-nano": 57}


def test_stream_without_usage_is_still_charged_an_estimate(
    client: TestClient, upstream: _Upstream
) -> None:
    upstream.respond = lambda _req: httpx.Response(
        200,
        content=_sse({"type": "response.created"}),
        headers={"content-type": "text/event-stream"},
    )
    client.post("/v1/openai-budget/v1/responses", json={"model": "gpt-5.6-luna"})
    bucket = budget.read_day()["gpt-5.6-luna"]
    assert bucket["tokens"] > 0
    assert set(bucket["by_source"]) == {"proxy-estimate"}


def test_upstream_errors_record_nothing(client: TestClient, upstream: _Upstream) -> None:
    upstream.respond = lambda _req: httpx.Response(400, json={"error": {"message": "bad"}})
    reply = client.post("/v1/openai-budget/v1/responses", json={"model": "gpt-5.6-luna"})
    assert reply.status_code == 400
    assert budget.read_day() == {}
    assert proxy._inflight == {"small": 0}


def test_unmetered_and_malformed_posts_are_refused(
    client: TestClient, upstream: _Upstream
) -> None:
    embeddings = client.post("/v1/openai-budget/v1/embeddings", json={"model": "gpt-5.6-luna"})
    assert embeddings.status_code == 403
    no_model = client.post("/v1/openai-budget/v1/responses", json={"input": "hi"})
    assert no_model.status_code == 400
    assert upstream.requests == []


def test_get_is_passed_through_unmetered(client: TestClient, upstream: _Upstream) -> None:
    upstream.respond = lambda _req: httpx.Response(200, json={"data": [{"id": "gpt-5.6-luna"}]})
    reply = client.get("/v1/openai-budget/v1/models")
    assert reply.json() == {"data": [{"id": "gpt-5.6-luna"}]}
    assert upstream.requests[0].headers["authorization"] == "Bearer sk-real-key"
    assert budget.read_day() == {}
