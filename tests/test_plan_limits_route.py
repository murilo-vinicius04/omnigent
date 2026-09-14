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
    monkeypatch.setattr(
        plan_limits, "CLAUDE_CACHE_PATH", tmp_path / "plan-limits-claude.json", raising=False
    )
    monkeypatch.setattr(plan_limits, "_claude_cache", None, raising=False)
    monkeypatch.setattr(plan_limits, "_claude_cooldown_until", 0.0, raising=False)
    monkeypatch.setattr(plan_limits, "_claude_had_failure", False, raising=False)
    monkeypatch.setattr(plan_limits, "_last_claude_fetch_time", None, raising=False)
    monkeypatch.setattr(plan_limits, "_claude_lock", None, raising=False)
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


# --- claude rate limiting, cooldown, and caching ----------------------------


class MockTime:
    def __init__(self, start: float = 1726315200.0) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _mock_clock(monkeypatch: pytest.MonkeyPatch, initial: float = 1726315200.0) -> MockTime:
    clock = MockTime(initial)
    monkeypatch.setattr(plan_limits.time, "time", clock.time)
    monkeypatch.setattr(plan_limits.time, "monotonic", clock.monotonic)
    return clock


def test_claude_rate_limit_cooldown_serves_stale_and_resumes_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _mock_clock(monkeypatch)
    monkeypatch.setattr(plan_limits, "_read_claude_token", lambda: "tok")

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json={
                    "five_hour": {"utilization": 30.0},
                    "seven_day": {"utilization": 10.0},
                },
            )
        if calls == 2:
            return httpx.Response(429, headers={"retry-after": "30"})
        return httpx.Response(
            200,
            json={
                "five_hour": {"utilization": 50.0},
                "seven_day": {"utilization": 20.0},
            },
        )

    _mock_transport(monkeypatch, handler)

    # 1. First fetch succeeds and populates cache
    res1 = _collect()
    claude1 = _by_id(res1, "claude")
    assert claude1["state"] == "ok"
    assert calls == 1

    # 2. Advance time past route TTL (61s) so next request hits upstream and gets 429
    clock.advance(61)
    res2 = _collect()
    claude2 = _by_id(res2, "claude")
    assert claude2["state"] == "stale"
    assert [(w["kind"], w["percent"]) for w in claude2["windows"]] == [
        ("session", 30),
        ("weekly", 10),
    ]
    assert claude2.get("as_of")
    assert calls == 2

    # 3. Within 30s cooldown (advance 15s) -> NO upstream call made
    clock.advance(15)
    monkeypatch.setattr(plan_limits, "_cache", None)
    res3 = _collect()
    claude3 = _by_id(res3, "claude")
    assert claude3["state"] == "stale"
    assert calls == 2

    # 4. First call after 30s cooldown (advance another 16s, total 31s since 429) -> hits upstream
    clock.advance(16)
    res4 = _collect()
    claude4 = _by_id(res4, "claude")
    assert claude4["state"] == "ok"
    assert [(w["kind"], w["percent"]) for w in claude4["windows"]] == [
        ("session", 50),
        ("weekly", 20),
    ]
    assert calls == 3


def test_claude_rate_limit_missing_retry_after_defaults_to_60(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _mock_clock(monkeypatch)
    monkeypatch.setattr(plan_limits, "_read_claude_token", lambda: "tok")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429)  # no retry-after header
        return httpx.Response(
            200,
            json={"five_hour": {"utilization": 10.0}},
        )

    _mock_transport(monkeypatch, handler)

    res1 = _collect()
    assert _by_id(res1, "claude")["state"] == "error"
    assert calls == 1

    # At 50s (within 60s default cooldown), should NOT hit upstream
    clock.advance(50)
    monkeypatch.setattr(plan_limits, "_cache", None)
    res2 = _collect()
    assert _by_id(res2, "claude")["state"] == "error"
    assert calls == 1

    # At 61s (after 60s default cooldown), hits upstream
    clock.advance(11)
    res3 = _collect()
    assert _by_id(res3, "claude")["state"] == "ok"
    assert calls == 2


def test_claude_rate_limit_no_reading_gives_error_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_clock(monkeypatch, initial=1000.0)
    monkeypatch.setattr(plan_limits, "_read_claude_token", lambda: "tok")
    _mock_transport(monkeypatch, lambda r: httpx.Response(429, headers={"retry-after": "45"}))

    res = _collect()
    claude = _by_id(res, "claude")
    assert claude["state"] == "error"
    assert claude["reason"] == "rate_limited"
    assert claude["windows"] == []
    assert claude["retry_at"] == 1045


def test_claude_rate_limit_http_date_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _mock_clock(monkeypatch, initial=1789387200.0)
    monkeypatch.setattr(plan_limits, "_read_claude_token", lambda: "tok")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                headers={"retry-after": "Mon, 14 Sep 2026 12:00:40 GMT"},
            )
        return httpx.Response(200, json={"five_hour": {"utilization": 40.0}})

    _mock_transport(monkeypatch, handler)

    res1 = _collect()
    assert _by_id(res1, "claude")["state"] == "error"
    assert calls == 1

    # At 30s, still cooling down
    clock.advance(30)
    monkeypatch.setattr(plan_limits, "_cache", None)
    assert _by_id(_collect(), "claude")["state"] == "error"
    assert calls == 1

    # At 45s, cooldown has passed
    clock.advance(15)
    res3 = _collect()
    assert calls == 2
    assert _by_id(res3, "claude")["state"] == "ok"


def test_claude_concurrent_requests_make_one_upstream_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plan_limits, "_read_claude_token", lambda: "tok")
    calls = 0

    async def async_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return httpx.Response(200, json={"five_hour": {"utilization": 25.0}})

    real_client = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(async_handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(plan_limits.httpx, "AsyncClient", factory)

    async def run_concurrent() -> list[dict[str, Any]]:
        return await asyncio.gather(
            plan_limits.collect_plan_limits(),
            plan_limits.collect_plan_limits(),
            plan_limits.collect_plan_limits(),
        )

    results = asyncio.run(run_concurrent())
    assert calls == 1
    for res in results:
        claude = _by_id(res, "claude")
        assert claude["state"] == "ok"
        assert claude["windows"][0]["percent"] == 25


def test_claude_cooldown_logs_warning_once_and_info_on_recovery(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    clock = _mock_clock(monkeypatch)
    monkeypatch.setattr(plan_limits, "_read_claude_token", lambda: "tok")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json={"five_hour": {"utilization": 15.0}})
        if calls == 2:
            return httpx.Response(429, headers={"retry-after": "30"})
        return httpx.Response(200, json={"five_hour": {"utilization": 20.0}})

    _mock_transport(monkeypatch, handler)

    with caplog.at_level(logging.INFO):
        # 1. Initial success - no info logged (routine fetch)
        _collect()
        assert not [r for r in caplog.records if r.levelno == logging.WARNING]
        assert not [r for r in caplog.records if r.levelno == logging.INFO]

        # 2. Trigger 429
        clock.advance(61)
        _collect()
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        warning_msg = warnings[0].message
        assert "429" in warning_msg
        assert "30" in warning_msg

        # 3. Call again during cooldown - nothing logged per request
        clock.advance(10)
        monkeypatch.setattr(plan_limits, "_cache", None)
        _collect()
        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1

        # 4. First success after failure - logs one INFO
        clock.advance(25)
        _collect()
        infos = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(infos) == 1

        # 5. Subsequent success - no further INFO
        clock.advance(61)
        _collect()
        assert len([r for r in caplog.records if r.levelno == logging.INFO]) == 1


def test_claude_cache_persists_to_disk_and_reloads_after_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    cache_file = tmp_path / "plan-limits-claude.json"
    monkeypatch.setattr(plan_limits, "CLAUDE_CACHE_PATH", cache_file)
    monkeypatch.setattr(plan_limits, "_read_claude_token", lambda: "tok")
    should_fail = False

    def handler(request: httpx.Request) -> httpx.Response:
        if should_fail:
            return httpx.Response(500)
        return httpx.Response(200, json={"five_hour": {"utilization": 42.0}})

    _mock_transport(monkeypatch, handler)

    # 1. Fetch good reading
    res = _collect()
    assert _by_id(res, "claude")["state"] == "ok"
    assert cache_file.exists()

    # 2. Simulate restart: clear in-memory cache, next upstream call fails
    monkeypatch.setattr(plan_limits, "_cache", None)
    monkeypatch.setattr(plan_limits, "_claude_cache", None)
    should_fail = True

    reloaded = _collect()
    claude = _by_id(reloaded, "claude")
    assert claude["state"] == "stale"
    assert claude["windows"][0]["percent"] == 42
    assert claude.get("as_of")

