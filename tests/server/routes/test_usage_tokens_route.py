"""Tests for ``GET /v1/usage/tokens`` — the per-provider token history.

The route is a thin, host-scoped projection of the usage-history log. What is
worth pinning is the contract the web Usage page depends on: the response shape
survives Pydantic, the day window is honoured, and a nonsense bound degrades to
"everything" instead of a 422 that would blank the charts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent import usage_timeline
from omnigent.server.routes.usage import create_usage_router
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore

_EVENTS = [
    {
        "at": "2026-09-14T10:00:00Z",
        "kind": "model_call",
        "model": "claude-opus-5",
        "tokens": 1_000,
        "input_tokens": 400,
        "output_tokens": 600,
        "cost_usd": 1.5,
    },
    {
        "at": "2026-09-15T10:00:00Z",
        "kind": "grok_call",
        "model": "grok-4.6-build",
        "tokens": 2_000,
        "cached_read_tokens": 1_900,
        "output_tokens": 100,
    },
    {
        "at": "2026-09-15T10:05:00Z",
        "kind": "plan_limits",
        "provider": "claude",
        "windows": {"session": 33},
    },
]


@pytest.fixture
def client(tmp_path: Path, db_uri: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A test client whose usage log is a fixture file, never the real home."""
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path))
    usage_timeline._cache.clear()
    log = tmp_path / "usage-history.jsonl"
    log.write_text("".join(json.dumps(event) + "\n" for event in _EVENTS), encoding="utf-8")

    app = FastAPI()
    app.include_router(create_usage_router(SqlAlchemyConversationStore(db_uri)), prefix="/v1")
    return TestClient(app)


def test_token_report_groups_the_log_by_vendor(client: TestClient) -> None:
    body = client.get("/v1/usage/tokens").json()

    assert body["object"] == "token_usage_report"
    assert [provider["id"] for provider in body["providers"]] == ["claude", "grok"]
    claude = body["providers"][0]
    assert claude["label"] == "Claude"
    assert claude["tokens"] == 1_000
    assert claude["cost_usd"] == 1.5
    assert claude["days"] == [
        {
            "day": "2026-09-14",
            "tokens": 1_000,
            "input_tokens": 400,
            "output_tokens": 600,
            "cached_tokens": 0,
            "cost_usd": 1.5,
            "calls": 1,
        }
    ]
    assert claude["models"][0]["model"] == "claude-opus-5"
    assert body["totals"]["tokens"] == 3_000
    assert body["limits"][0]["windows"][0]["points"] == [
        {"at": "2026-09-15T10:05:00Z", "percent": 33}
    ]


def test_day_window_narrows_the_report(client: TestClient) -> None:
    body = client.get("/v1/usage/tokens", params={"since": "2026-09-15"}).json()

    assert [provider["id"] for provider in body["providers"]] == ["grok"]
    assert body["since"] == "2026-09-15"
    assert body["until"] is None


def test_a_malformed_bound_is_ignored_rather_than_rejected(client: TestClient) -> None:
    # A decorative chart must not turn a bad query string into a 422 and an
    # empty page; the bound is simply dropped.
    response = client.get("/v1/usage/tokens", params={"since": "last tuesday"})

    assert response.status_code == 200
    assert response.json()["totals"]["tokens"] == 3_000


def test_plan_curve_point_cap_is_bounded(client: TestClient) -> None:
    assert client.get("/v1/usage/tokens", params={"max_points": 5_000}).status_code == 422
    assert client.get("/v1/usage/tokens", params={"max_points": 1}).status_code == 200


def test_an_empty_log_is_an_empty_report(
    tmp_path: Path,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path / "fresh"))
    usage_timeline._cache.clear()
    app = FastAPI()
    app.include_router(create_usage_router(SqlAlchemyConversationStore(db_uri)), prefix="/v1")

    body = TestClient(app).get("/v1/usage/tokens").json()
    assert body["providers"] == []
    assert body["limits"] == []
    assert body["totals"]["tokens"] == 0
