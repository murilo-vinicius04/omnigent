"""Tests for the ``/v1/plan-limits`` provider aggregation.

Focus: the response must stay well-formed and per-provider-isolated no matter
how badly one upstream behaves, because it renders in the composer tray on
every turn. A vendor being signed out, rate-limited, or returning garbage must
degrade that one row and nothing else.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from omnigent.server.routes import plan_limits


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Isolate all cross-test state: the in-process cache and the on-disk one.

    The Antigravity provider persists its last good reading to the real state
    dir. Without redirecting it, tests would both read the developer's actual
    quota and overwrite it.
    """
    monkeypatch.setattr(plan_limits, "_cache", None)
    monkeypatch.setattr(
        plan_limits, "ANTIGRAVITY_CACHE_PATH", tmp_path / "plan-limits-antigravity.json"
    )
    _stub_agy(monkeypatch, None)


def _stub_agy(monkeypatch: pytest.MonkeyPatch, summary: Any) -> None:
    """Make the local agy quota RPC return *summary* (``None`` = no agy running)."""
    monkeypatch.setattr(
        plan_limits.antigravity_native_rpc,
        "retrieve_user_quota_summary",
        lambda: summary,
    )


def _agy_summary(*buckets: dict[str, Any]) -> dict[str, Any]:
    """Wrap *buckets* in agy's group envelope."""
    return {"groups": [{"displayName": "Gemini Models", "buckets": list(buckets)}]}


def _collect() -> dict[str, Any]:
    return asyncio.run(plan_limits.collect_plan_limits())


def _by_id(payload: dict[str, Any], provider_id: str) -> dict[str, Any]:
    return next(p for p in payload["providers"] if p["id"] == provider_id)


def _mock_transport(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """Route every httpx request in the module through *handler*."""
    real_client = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(plan_limits.httpx, "AsyncClient", factory)


# --- window normalization ---------------------------------------------------


def test_window_accepts_utilization_and_percent_spellings() -> None:
    assert plan_limits._window("session", "5h", {"utilization": 31.0})["percent"] == 31
    assert plan_limits._window("session", "5h", {"percent": 44})["percent"] == 44


def test_window_clamps_and_rounds() -> None:
    assert plan_limits._window("s", "l", {"utilization": 99.6})["percent"] == 100
    assert plan_limits._window("s", "l", {"utilization": 140})["percent"] == 100
    assert plan_limits._window("s", "l", {"utilization": -5})["percent"] == 0


def test_window_returns_none_for_absent_or_unusable_payloads() -> None:
    # Providers null out windows that don't apply to the account's plan; those
    # must vanish rather than render as a bogus 0%.
    assert plan_limits._window("s", "l", None) is None
    assert plan_limits._window("s", "l", {}) is None
    assert plan_limits._window("s", "l", {"utilization": "n/a"}) is None


# --- claude provider --------------------------------------------------------


def test_claude_reports_windows_from_usage_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plan_limits, "_read_claude_token", lambda: "tok")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer tok"
        assert request.headers["anthropic-beta"] == plan_limits.CLAUDE_OAUTH_BETA
        return httpx.Response(
            200,
            json={
                "five_hour": {"utilization": 31.0, "resets_at": "2026-09-07T00:10:00+00:00"},
                "seven_day": {"utilization": 11.0, "resets_at": "2026-09-13T03:00:00+00:00"},
            },
        )

    _mock_transport(monkeypatch, handler)
    claude = _by_id(_collect(), "claude")
    assert claude["state"] == "ok"
    assert [(w["kind"], w["percent"]) for w in claude["windows"]] == [
        ("session", 31),
        ("weekly", 11),
    ]


def test_claude_signed_out_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plan_limits, "_read_claude_token", lambda: None)
    _mock_transport(monkeypatch, lambda r: httpx.Response(200, json={}))
    claude = _by_id(_collect(), "claude")
    assert claude["state"] == "signed-out"
    assert claude["windows"] == []


def test_claude_expired_token_reads_as_signed_out(monkeypatch: pytest.MonkeyPatch) -> None:
    # Access tokens are short-lived and Claude Code refreshes them on its own
    # schedule; a 401 is routine, not an error worth surfacing.
    monkeypatch.setattr(plan_limits, "_read_claude_token", lambda: "stale")
    _mock_transport(monkeypatch, lambda r: httpx.Response(401, json={}))
    assert _by_id(_collect(), "claude")["state"] == "signed-out"


def test_claude_network_failure_is_contained(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plan_limits, "_read_claude_token", lambda: "tok")

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    _mock_transport(monkeypatch, boom)
    payload = _collect()
    assert _by_id(payload, "claude")["state"] == "error"
    # The other provider still gets its own verdict (no agy running here).
    assert _by_id(payload, "antigravity")["state"] == "unsupported"


# --- antigravity provider ---------------------------------------------------


def test_antigravity_converts_remaining_fraction_to_percent_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """agy reports what is LEFT; the tray shows what is USED.

    Getting this backwards would render a nearly-exhausted plan as a healthy
    one, so the inversion is pinned explicitly.
    """
    monkeypatch.setattr(plan_limits, "_read_claude_token", lambda: None)
    _mock_transport(monkeypatch, lambda r: httpx.Response(200, json={}))
    _stub_agy(
        monkeypatch,
        _agy_summary(
            {
                "bucketId": "gemini-weekly",
                "window": "weekly",
                "remainingFraction": 0.9755219,
                "resetTime": "2026-09-12T13:34:37Z",
            },
            {"bucketId": "gemini-5h", "window": "5h", "remainingFraction": 0.25},
        ),
    )
    row = _by_id(_collect(), "antigravity")
    assert row["state"] == "ok"
    assert row["windows"] == [
        {
            "kind": "gemini-weekly",
            "label": "Gemini week",
            "percent": 2,
            "resets_at": "2026-09-12T13:34:37Z",
        },
        {"kind": "gemini-5h", "label": "Gemini 5h", "percent": 75, "resets_at": None},
    ]


def test_antigravity_window_kinds_are_unique_across_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bucket ids, not window tokens, are the window ``kind``.

    Two model groups each publish a ``5h`` and a ``weekly`` bucket. The UI keys
    its tooltip rows on ``kind``, so collapsing them onto the window token would
    produce duplicate React keys and silently drop rows.
    """
    monkeypatch.setattr(plan_limits, "_read_claude_token", lambda: None)
    _mock_transport(monkeypatch, lambda r: httpx.Response(200, json={}))
    _stub_agy(
        monkeypatch,
        {
            "groups": [
                {
                    "displayName": "Gemini Models",
                    "buckets": [{"bucketId": "gemini-5h", "window": "5h", "remainingFraction": 1}],
                },
                {
                    "displayName": "Claude and GPT models",
                    "buckets": [{"bucketId": "3p-5h", "window": "5h", "remainingFraction": 1}],
                },
            ]
        },
    )
    windows = _by_id(_collect(), "antigravity")["windows"]
    assert [w["kind"] for w in windows] == ["gemini-5h", "3p-5h"]
    assert [w["label"] for w in windows] == ["Gemini 5h", "Claude/GPT 5h"]


def test_antigravity_replays_last_reading_when_no_agy_is_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quota is only readable while an agy lives; the pill must not flicker.

    The first collection (agy up) persists; the second (agy gone) replays it as
    ``stale`` with an ``as_of`` stamp rather than dropping the row.
    """
    monkeypatch.setattr(plan_limits, "_read_claude_token", lambda: None)
    _mock_transport(monkeypatch, lambda r: httpx.Response(200, json={}))
    _stub_agy(
        monkeypatch,
        _agy_summary({"bucketId": "gemini-5h", "window": "5h", "remainingFraction": 0.4}),
    )
    live = _by_id(_collect(), "antigravity")
    assert live["state"] == "ok"

    monkeypatch.setattr(plan_limits, "_cache", None)
    _stub_agy(monkeypatch, None)
    replayed = _by_id(_collect(), "antigravity")
    assert replayed["state"] == "stale"
    assert replayed["windows"] == live["windows"]
    assert replayed["as_of"]


def test_antigravity_without_agy_or_cache_is_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plan_limits, "_read_claude_token", lambda: None)
    _mock_transport(monkeypatch, lambda r: httpx.Response(200, json={}))
    row = _by_id(_collect(), "antigravity")
    assert row["state"] == "unsupported"
    assert row["windows"] == []


def test_antigravity_rpc_failure_is_contained(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raising RPC helper must degrade the row, not 500 the whole tray."""
    monkeypatch.setattr(plan_limits, "_read_claude_token", lambda: "tok")
    _mock_transport(
        monkeypatch,
        lambda r: httpx.Response(200, json={"five_hour": {"utilization": 5}}),
    )

    def boom() -> Any:
        raise RuntimeError("lsof exploded")

    monkeypatch.setattr(plan_limits.antigravity_native_rpc, "retrieve_user_quota_summary", boom)
    payload = _collect()
    assert _by_id(payload, "antigravity")["state"] == "unsupported"
    assert _by_id(payload, "claude")["state"] == "ok"


def test_antigravity_skips_malformed_buckets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plan_limits, "_read_claude_token", lambda: None)
    _mock_transport(monkeypatch, lambda r: httpx.Response(200, json={}))
    _stub_agy(
        monkeypatch,
        _agy_summary(
            {"bucketId": "gemini-5h", "window": "5h"},  # no fraction
            {"window": "weekly", "remainingFraction": 0.5},  # no id
            "not-a-dict",  # type: ignore[arg-type]
            {"bucketId": "gemini-weekly", "window": "weekly", "remainingFraction": 0.5},
        ),
    )
    windows = _by_id(_collect(), "antigravity")["windows"]
    assert [w["kind"] for w in windows] == ["gemini-weekly"]


# --- caching ----------------------------------------------------------------


def test_second_call_is_served_from_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every open tab polls this route; one upstream call per window is enough."""
    monkeypatch.setattr(plan_limits, "_read_claude_token", lambda: "tok")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"five_hour": {"utilization": 5}})

    _mock_transport(monkeypatch, handler)
    first = _collect()
    second = _collect()
    assert calls == 1
    assert first == second


def test_malformed_json_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plan_limits, "_read_claude_token", lambda: "tok")
    _mock_transport(monkeypatch, lambda r: httpx.Response(200, content=b"not json"))
    assert _by_id(_collect(), "claude")["state"] == "error"


def test_claude_token_reader_tolerates_corrupt_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    bad = tmp_path / ".credentials.json"
    bad.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(plan_limits, "CLAUDE_CREDENTIALS_PATH", bad)
    assert plan_limits._read_claude_token() is None

    missing = tmp_path / "absent.json"
    monkeypatch.setattr(plan_limits, "CLAUDE_CREDENTIALS_PATH", missing)
    assert plan_limits._read_claude_token() is None

    wrong_shape = tmp_path / "wrong.json"
    wrong_shape.write_text(json.dumps({"claudeAiOauth": {}}), encoding="utf-8")
    monkeypatch.setattr(plan_limits, "CLAUDE_CREDENTIALS_PATH", wrong_shape)
    assert plan_limits._read_claude_token() is None
