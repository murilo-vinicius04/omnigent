"""``/v1/plan-limits``: a Grok row, a Pro readout on Claude's, and today's tokens.

Every upstream is answered by a mock transport; the credential and cache files
all live in a throwaway directory, never the developer's home.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from omnigent import openai_token_budget
from omnigent.server.routes import plan_limits
from omnigent.server.routes._sessions.orchestration import _accumulate_session_usage
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore

_AGENT_ID = "0123456789abcdef0123456789abcdef"
_XAI_HOST = "cli-chat-proxy.grok.com"

_BILLING = {
    "config": {
        "creditUsagePercent": 42.0,
        "currentPeriod": {
            "type": "USAGE_PERIOD_TYPE_WEEKLY",
            "start": "2026-09-13T20:11:53.090402+00:00",
            "end": "2026-09-20T20:11:53.090402+00:00",
        },
        "productUsage": [
            {"product": "GrokChat", "usagePercent": 35.0},
            {"product": "GrokBuild"},
        ],
    }
}

_CLAUDE_USAGE = {
    "five_hour": {"utilization": 20.0, "resets_at": "2026-09-19T15:00:00+00:00"},
    "seven_day": {"utilization": 3.0, "resets_at": "2026-09-24T03:00:00+00:00"},
}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every file the route reads or writes goes to tmp_path."""
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(plan_limits, "_cache", None)
    monkeypatch.setattr(plan_limits, "ANTIGRAVITY_CACHE_PATH", tmp_path / "agy-cache.json")
    monkeypatch.setattr(plan_limits, "CLAUDE_CACHE_PATH", tmp_path / "claude-cache.json")
    monkeypatch.setattr(plan_limits, "CLAUDE_CREDENTIALS_PATH", tmp_path / "claude-creds.json")
    monkeypatch.setattr(plan_limits, "GROK_AUTH_PATH", tmp_path / "grok-auth.json", raising=False)
    monkeypatch.setattr(
        plan_limits.antigravity_native_rpc, "retrieve_user_quota_summary", lambda: None
    )
    _isolate_grok_sessions(tmp_path, monkeypatch)


def _isolate_grok_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolation only: nothing may read the developer's real ~/.grok sessions.

    Covers ``Path.home()`` read at call time and any module constant built from
    it at import (``SESSIONS_ROOT``, ``GROK_SESSIONS_ROOT``, ...).
    """
    real = Path.home() / ".grok" / "sessions"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    try:
        from omnigent import grok_usage
    except ImportError:
        return
    empty = tmp_path / "home" / ".grok" / "sessions"
    for name, value in list(vars(grok_usage).items()):
        if isinstance(value, Path) and value == real:
            monkeypatch.setattr(grok_usage, name, empty)


def _mock_transport(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """Route every httpx request through *handler*."""
    real_client = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(plan_limits.httpx, "AsyncClient", factory)


def _collect() -> dict[str, Any]:
    return asyncio.run(plan_limits.collect_plan_limits())


def _by_id(payload: dict[str, Any], provider_id: str) -> dict[str, Any]:
    return next(p for p in payload["providers"] if p["id"] == provider_id)


def _grok_key(tmp_path: Path, key: str, expires_at: str) -> None:
    (tmp_path / "grok-auth.json").write_text(
        json.dumps({"acct-1": {"key": key, "expires_at": expires_at}}), encoding="utf-8"
    )


def _claude_signed_in(tmp_path: Path, **plan: str) -> None:
    (tmp_path / "claude-creds.json").write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "claude-test-token",
                    "expiresAt": 4_102_444_800_000,
                    "scopes": ["user:inference", "user:profile"],
                    **plan,
                }
            }
        ),
        encoding="utf-8",
    )


def _upstreams(calls: list[httpx.Request]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == _XAI_HOST:
            return httpx.Response(200, json=_BILLING)
        if request.url.host == "api.anthropic.com":
            return httpx.Response(200, json=_CLAUDE_USAGE)
        return httpx.Response(404)

    return handler


def test_grok_row_shows_the_weeks_plan_usage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _grok_key(tmp_path, "grok-test-key", "2099-01-01T00:00:00.000000000Z")
    calls: list[httpx.Request] = []
    _mock_transport(monkeypatch, _upstreams(calls))

    grok = _by_id(_collect(), "grok")

    assert grok["state"] == "ok"
    (weekly,) = [w for w in grok["windows"] if w["kind"] == "weekly"]
    assert weekly["percent"] == 42
    # [fairness] every existing window carries resets_at, and the period's end is
    # the only reset time in xAI's answer; compared as instants, not strings.
    assert datetime.fromisoformat(weekly["resets_at"].replace("Z", "+00:00")) == (
        datetime.fromisoformat("2026-09-20T20:11:53.090402+00:00")
    )
    (xai,) = [r for r in calls if r.url.host == _XAI_HOST]
    assert xai.headers["Authorization"] == "Bearer grok-test-key"
    assert xai.headers["X-XAI-Token-Auth"] == "xai-grok-cli"


def test_grok_without_a_usable_key_never_calls_xai(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[httpx.Request] = []
    _mock_transport(monkeypatch, _upstreams(calls))

    assert _by_id(_collect(), "grok")["state"] == "signed-out"

    _grok_key(tmp_path, "old-key", "2020-01-01T00:00:00.000000000Z")
    monkeypatch.setattr(plan_limits, "_cache", None)
    # [fairness] an expired key may read as signed out or replay a stale
    # reading; it must not read as a live one, and must not be sent.
    assert _by_id(_collect(), "grok")["state"] != "ok"
    assert not [r for r in calls if r.url.host == _XAI_HOST]


def test_claude_row_carries_the_pro_readout_when_the_plan_is_known(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _claude_signed_in(tmp_path, subscriptionType="max", rateLimitTier="default_claude_max_5x")
    _mock_transport(monkeypatch, _upstreams([]))

    claude = _by_id(_collect(), "claude")

    assert claude["state"] == "ok"
    readout = claude["pro_equivalent"]
    assert readout["multiplier"] == 5
    used = {w["kind"]: w["used_pct"] for w in readout["windows"]}
    assert used["session"] == pytest.approx(100)
    assert used["weekly"] == pytest.approx(15)

    # A plan with no known relation to Pro gets no readout rather than a guess.
    _claude_signed_in(tmp_path, subscriptionType="team", rateLimitTier="default_claude_team")
    monkeypatch.setattr(plan_limits, "_cache", None)
    claude = _by_id(_collect(), "claude")
    assert claude["state"] in ("ok", "stale")
    assert not claude.get("pro_equivalent")


def test_each_row_shows_the_tokens_omnigent_counted_today(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, db_uri: str
) -> None:
    store = SqlAlchemyConversationStore(db_uri)
    conv = store.create_conversation(title="today", agent_id=_AGENT_ID)
    for usage in (
        {"model": "claude-opus-5", "input_tokens": 1_000, "output_tokens": 200},
        {"model": "gemini-3.7-flash", "input_tokens": 500, "output_tokens": 20},
    ):
        _accumulate_session_usage({"usage": {**usage, "cost_usd": 0.01}}, conv.id, store)
    # An OpenAI call counted by the budget proxy today.
    openai_token_budget.record(
        "gpt-5.6-luna", {"input_tokens": 250, "output_tokens": 50}, source="proxy"
    )
    _claude_signed_in(tmp_path, subscriptionType="max", rateLimitTier="default_claude_max_5x")
    monkeypatch.setattr(
        plan_limits.antigravity_native_rpc,
        "retrieve_user_quota_summary",
        lambda: {
            "groups": [
                {
                    "displayName": "Gemini Models",
                    "buckets": [
                        {"bucketId": "gemini-5h", "window": "5h", "remainingFraction": 0.6}
                    ],
                }
            ]
        },
    )
    _mock_transport(monkeypatch, _upstreams([]))

    payload = _collect()

    assert _by_id(payload, "claude")["tokens_today"] == 1_200
    assert _by_id(payload, "antigravity")["tokens_today"] == 520
    assert _by_id(payload, "openai")["tokens_today"] == 300
