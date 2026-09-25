"""``GET /v1/usage/tokens``: the per-provider token history, over HTTP.

The route is what the web Usage page draws from; it must answer the
``build_token_usage`` report for the host's usage log and honour the day window.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent.server.routes.usage import create_usage_router
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore


def _openai_call(at: str, model: str, tokens: int) -> dict:
    return {
        "at": at,
        "kind": "openai_call",
        "model": model,
        "pool": "small",
        "tokens": tokens,
        "input_tokens": tokens,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "output_tokens": 0,
        "source": "proxy",
    }


_EVENTS = [
    _openai_call("2026-09-14T10:00:00Z", "gpt-5.6-luna", 1_000),
    {
        "at": "2026-09-14T10:05:00Z",
        "kind": "plan_limits",
        "provider": "claude",
        "state": "ok",
        "windows": {"session": 33, "weekly": 4},
        "tier": None,
    },
    _openai_call("2026-09-15T10:00:00Z", "gpt-5.6-terra", 250),
]


@pytest.fixture
def client(tmp_path: Path, db_uri: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A client whose usage log is a fixture file in a throwaway data dir."""
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path))
    (tmp_path / "usage-history.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in _EVENTS), encoding="utf-8"
    )
    app = FastAPI()
    app.include_router(create_usage_router(SqlAlchemyConversationStore(db_uri)), prefix="/v1")
    return TestClient(app)


def test_the_route_answers_the_token_report(client: TestClient) -> None:
    response = client.get("/v1/usage/tokens")

    assert response.status_code == 200
    body = response.json()
    (openai,) = body["providers"]
    assert openai["id"] == "openai"
    assert openai["tokens"] == 1_250
    assert [(d["day"], d["tokens"]) for d in openai["days"]] == [
        ("2026-09-14", 1_000),
        ("2026-09-15", 250),
    ]
    assert body["totals"]["tokens"] == 1_250
    (claude,) = body["limits"]
    assert claude["provider"] == "claude"
    assert {w["kind"]: [p["percent"] for p in w["points"]] for w in claude["windows"]} == {
        "session": [33],
        "weekly": [4],
    }


def test_the_route_honours_the_day_window(client: TestClient) -> None:
    body = client.get("/v1/usage/tokens", params={"since": "2026-09-15", "until": "2026-09-15"})
    assert body.status_code == 200
    (openai,) = body.json()["providers"]
    assert openai["tokens"] == 250
    assert [d["day"] for d in openai["days"]] == ["2026-09-15"]

    earlier = client.get("/v1/usage/tokens", params={"until": "2026-09-14"}).json()
    assert earlier["totals"]["tokens"] == 1_000
