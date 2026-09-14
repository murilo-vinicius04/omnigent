"""Read-only route exposing subscription plan limits for the composer tray.

The context ring next to the composer answers "how full is *this* conversation".
It says nothing about the budget that actually stops work: the provider-side
subscription windows shared by every session on the host. This route supplies
that second number so the UI can sit them side by side.

**Providers are best-effort and independent.** Each one reports its own
``state`` and a (possibly empty) list of windows; one provider being
unauthenticated or unsupported never fails the response. The UI renders what
came back and hides the rest, so a host with only one signed-in vendor still
gets a useful tray.

Sources, and why each is what it is:

- ``claude`` — ``GET https://api.anthropic.com/api/oauth/usage`` with the
  OAuth access token Claude Code persists in ``~/.claude/.credentials.json``.
  This is the same endpoint Claude Code's own ``/usage`` command reads, so the
  numbers match what the user sees there. Returns ``five_hour`` and
  ``seven_day`` utilization percentages plus reset timestamps.

- ``antigravity`` — the ``RetrieveUserQuotaSummary`` connect-RPC on a **local**
  agy language server (see
  :func:`omnigent.antigravity_native_rpc.retrieve_user_quota_summary`). This is
  the exact data agy's own ``/usage`` screen renders, so the numbers match what
  the user sees there.

  Asking agy rather than Google is deliberate. agy proxies this upstream to
  ``cloudcode-pa.googleapis.com``, but that upstream call answers ``403`` ("no
  valid license of this product") for a consumer login *even with agy's own
  OAuth token from the keyring* — entitlement is resolved from client identity
  we cannot reproduce. The local RPC sidesteps it: agy already holds both the
  credential and the entitlement.

  The cost of that choice is that quota is only readable **while an agy is
  running**, which on this host means "while a Gemini-backed sub-agent is
  alive". Rather than let the tray flicker in and out as sub-agents come and
  go, the last good reading is persisted (:data:`ANTIGRAVITY_CACHE_PATH`) and
  replayed with ``state="stale"`` plus an ``as_of`` stamp, so the UI can show
  the number while being explicit that it is not live.

Percentages are whole numbers 0-100 and measure the share **consumed** (agy
reports the reciprocal, ``remainingFraction``). ``resets_at`` is ISO-8601 UTC
or ``None``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Request

from omnigent import antigravity_native_rpc, openai_token_budget, usage_history
from omnigent.install_ledger import state_dir
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user

logger = logging.getLogger(__name__)

#: Claude Code's OAuth credential file (subscription logins only; an
#: ``ANTHROPIC_API_KEY`` deployment has no plan window and reports signed-out).
CLAUDE_CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"

#: Same endpoint Claude Code's ``/usage`` reads.
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

#: Required for OAuth-token (as opposed to API-key) requests.
CLAUDE_OAUTH_BETA = "oauth-2025-04-20"

#: Last good Antigravity reading, replayed when no agy is running to query.
ANTIGRAVITY_CACHE_PATH = state_dir() / "plan-limits-antigravity.json"

#: Last good Claude reading, replayed during rate limit cooldowns.
CLAUDE_CACHE_PATH = state_dir() / "plan-limits-claude.json"

_claude_cache: dict[str, Any] | None = None
_claude_cooldown_until: float = 0.0
_claude_had_failure: bool = False
_claude_fetch_counter: int = 0
_last_claude_row: dict[str, Any] | None = None
_claude_lock: asyncio.Lock | None = None


def _get_claude_lock() -> asyncio.Lock:
    global _claude_lock
    if _claude_lock is None:
        _claude_lock = asyncio.Lock()
    return _claude_lock


#: agy bucket-id prefix -> tray label. agy groups models that share a quota
#: pool; the prefix is the stable machine key ("gemini-5h", "3p-weekly"), while
#: the group's own ``displayName`` ("Claude and GPT models") is too long for a
#: tooltip line. Unknown prefixes fall through to the prefix itself, so a new
#: model group still renders (just less prettily) instead of vanishing.
ANTIGRAVITY_GROUP_LABELS = {"gemini": "Gemini", "3p": "Claude/GPT"}

#: agy window token -> (window ``kind`` suffix is the bucket id itself, so this
#: only supplies the short human label rendered in the tray).
ANTIGRAVITY_WINDOW_LABELS = {"5h": "5h", "weekly": "week", "daily": "day"}

#: Plan windows move slowly and every browser tab polls this route, so a short
#: process-wide cache keeps a room full of tabs down to one upstream call per
#: window. Chosen well under the ~5-minute granularity of the underlying meters.
_CACHE_TTL_SECONDS = 60.0

#: Upstream calls sit in the composer's render path; fail fast rather than
#: hanging the tray on a slow network.
_HTTP_TIMEOUT_SECONDS = 6.0

_cache: tuple[float, dict[str, Any], float] | tuple[float, dict[str, Any]] | None = None


def _window(kind: str, label: str, payload: Any) -> dict[str, Any] | None:
    """Normalize one provider window into the UI's flat shape.

    :param kind: Stable machine id for the window (``session`` / ``weekly``).
    :param label: Short human label rendered in the tray.
    :param payload: Provider sub-object carrying ``utilization``/``percent``.
    :returns: The normalized window, or ``None`` when *payload* is absent or
        carries no usable percentage (a provider may null out windows that do
        not apply to the account's plan).
    """
    if not isinstance(payload, dict):
        return None
    raw = payload.get("utilization")
    if raw is None:
        raw = payload.get("percent")
    if raw is None:
        return None
    try:
        percent = round(float(raw))
    except (TypeError, ValueError):
        return None
    return {
        "kind": kind,
        "label": label,
        "percent": max(0, min(100, percent)),
        "resets_at": payload.get("resets_at"),
    }


def _read_claude_token() -> str | None:
    """Return Claude Code's stored OAuth access token, or ``None``.

    Treats every read/parse failure as "signed out": this route is decorative,
    and a malformed credential file must not surface as a 500 in the composer.
    """
    try:
        blob = json.loads(CLAUDE_CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    oauth = blob.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    token = oauth.get("accessToken")
    return token if isinstance(token, str) and token else None


def _parse_retry_after(raw: str | None, default: float = 60.0) -> float:
    """Parse a Retry-After header into a cooldown duration in seconds.

    Supports integer/float seconds or HTTP-date (RFC 7231). Defaults to
    ``default`` (60.0s) when missing, negative, or invalid.
    """
    if not raw:
        return default
    cleaned = raw.strip()
    try:
        val = float(cleaned)
        if val >= 0:
            return val
        return default
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(cleaned)
        if dt is not None:
            return max(0.0, dt.timestamp() - time.time())
    except (ValueError, TypeError, OverflowError):
        pass
    return default


def _read_claude_cache() -> dict[str, Any] | None:
    """Return the persisted last-good Claude reading, or ``None``."""
    try:
        blob = json.loads(CLAUDE_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(blob, dict) or not isinstance(blob.get("windows"), list):
        return None
    return blob


def _write_claude_cache(windows: list[dict[str, Any]]) -> None:
    """Persist a fresh Claude reading for replay during rate-limit cooldowns."""
    payload = {
        "windows": windows,
        "as_of": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    try:
        CLAUDE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CLAUDE_CACHE_PATH.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as exc:
        logger.debug("claude plan-limit cache write failed: %s", exc)


def _cached_reading_age(cached: dict[str, Any] | None) -> float | None:
    """Calculate the age in seconds of a cached reading's as_of timestamp."""
    if not cached:
        return None
    as_of_str = cached.get("as_of")
    if not isinstance(as_of_str, str):
        return None
    try:
        dt = datetime.fromisoformat(as_of_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return max(0.0, time.time() - dt.timestamp())
    except (ValueError, TypeError, OverflowError):
        return None


def _build_claude_stale_or_rate_limited(
    *,
    cooldown_until: float | None = None,
) -> dict[str, Any]:
    """Build a Claude provider row from cache (stale) or error when none exists."""
    global _claude_cache
    if _claude_cache is None:
        _claude_cache = _read_claude_cache()

    row: dict[str, Any] = {"id": "claude", "label": "Claude", "windows": []}
    if cooldown_until is not None:
        row["retry_at"] = round(cooldown_until)
        row["reason"] = "rate_limited"

    if _claude_cache is not None:
        row["windows"] = _claude_cache["windows"]
        row["state"] = "stale"
        row["as_of"] = _claude_cache.get("as_of")
        return row

    row["state"] = "error"
    if cooldown_until is not None:
        row["reason"] = "rate_limited"
    return row


async def _claude_provider(client: httpx.AsyncClient) -> dict[str, Any]:
    """Build the ``claude`` provider row from the plan-usage endpoint."""
    global _claude_cache, _claude_cooldown_until, _claude_had_failure
    global _claude_fetch_counter, _last_claude_row

    token = _read_claude_token()
    if token is None:
        return {"id": "claude", "label": "Claude", "windows": [], "state": "signed-out"}

    now_wall = time.time()
    if now_wall < _claude_cooldown_until:
        return _build_claude_stale_or_rate_limited(cooldown_until=_claude_cooldown_until)

    start_counter = _claude_fetch_counter
    lock = _get_claude_lock()
    async with lock:
        now_wall = time.time()
        if now_wall < _claude_cooldown_until:
            return _build_claude_stale_or_rate_limited(cooldown_until=_claude_cooldown_until)

        # If a concurrent fetch completed while waiting for the lock, share its result
        if _claude_fetch_counter != start_counter and _last_claude_row is not None:
            return _last_claude_row

        try:
            resp = await client.get(
                CLAUDE_USAGE_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "anthropic-beta": CLAUDE_OAUTH_BETA,
                    "anthropic-version": "2023-06-01",
                },
            )
        except httpx.HTTPError as exc:
            logger.debug("claude plan-limit fetch failed: %s", exc)
            _claude_had_failure = True
            row = _build_claude_stale_or_rate_limited()
            _claude_fetch_counter += 1
            _last_claude_row = row
            return row

        if resp.status_code == 401:
            # Access tokens are short-lived; Claude Code refreshes them on its own
            # next run. Report signed-out rather than error so the tray stays quiet.
            row = {"id": "claude", "label": "Claude", "windows": [], "state": "signed-out"}
            _claude_fetch_counter += 1
            _last_claude_row = row
            return row

        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            retry_after = _parse_retry_after(resp.headers.get("retry-after"), default=60.0)
            _claude_cooldown_until = now_wall + retry_after
            _claude_had_failure = True

            cached = _claude_cache or _read_claude_cache()
            age = _cached_reading_age(cached)
            age_str = f"{round(age)}s" if age is not None else "none"
            logger.warning(
                "Claude plan-limit cooldown started: status=%d, retry_after=%.0fs, cached_age=%s",
                resp.status_code,
                retry_after,
                age_str,
            )
            row = _build_claude_stale_or_rate_limited(cooldown_until=_claude_cooldown_until)
            _claude_fetch_counter += 1
            _last_claude_row = row
            return row

        if resp.status_code != 200:
            _claude_had_failure = True
            row = _build_claude_stale_or_rate_limited()
            _claude_fetch_counter += 1
            _last_claude_row = row
            return row

        try:
            data = resp.json()
        except ValueError:
            _claude_had_failure = True
            row = _build_claude_stale_or_rate_limited()
            _claude_fetch_counter += 1
            _last_claude_row = row
            return row

        windows = [
            w
            for w in (
                _window("session", "5h", data.get("five_hour")),
                _window("weekly", "week", data.get("seven_day")),
            )
            if w is not None
        ]

        row: dict[str, Any] = {"id": "claude", "label": "Claude", "windows": windows}
        if not windows:
            row["state"] = "unsupported"
            _claude_fetch_counter += 1
            _last_claude_row = row
            return row

        row["state"] = "ok"
        _claude_cache = {
            "windows": windows,
            "as_of": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }
        _write_claude_cache(windows)

        if _claude_had_failure:
            logger.info("Claude plan-limit fetch recovered: status=200")
            _claude_had_failure = False

        _claude_fetch_counter += 1
        _last_claude_row = row
        return row


def _antigravity_windows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten agy's grouped quota summary into the UI's flat window list.

    agy nests buckets under model groups that share a pool, and reports how much
    is *left*; the tray shows one flat list of how much is *used*. Bucket ids
    (``"gemini-5h"``) carry through as the window ``kind`` because they are
    already stable, unique machine keys — which matters here, since a single
    provider now contributes several windows and the UI keys on ``kind``.

    :param summary: The ``response`` object from ``RetrieveUserQuotaSummary``.
    :returns: Normalized windows; malformed buckets are skipped rather than
        failing the row.
    """
    windows: list[dict[str, Any]] = []
    for group in summary.get("groups") or []:
        if not isinstance(group, dict):
            continue
        for bucket in group.get("buckets") or []:
            if not isinstance(bucket, dict):
                continue
            bucket_id = bucket.get("bucketId")
            remaining = bucket.get("remainingFraction")
            if not isinstance(bucket_id, str) or not isinstance(remaining, (int, float)):
                continue
            prefix = bucket_id.rsplit("-", 1)[0]
            window_token = bucket.get("window")
            group_label = ANTIGRAVITY_GROUP_LABELS.get(prefix, prefix)
            window_label = ANTIGRAVITY_WINDOW_LABELS.get(
                window_token if isinstance(window_token, str) else "", ""
            )
            windows.append(
                {
                    "kind": bucket_id,
                    "label": f"{group_label} {window_label}".strip(),
                    "percent": max(0, min(100, round((1.0 - float(remaining)) * 100))),
                    "resets_at": bucket.get("resetTime"),
                }
            )
    return windows


def _read_antigravity_cache() -> dict[str, Any] | None:
    """Return the persisted last-good Antigravity reading, or ``None``.

    Every read/parse failure is treated as "no cache": this only ever downgrades
    the tray to showing nothing, which is the same outcome as never having run.
    """
    try:
        blob = json.loads(ANTIGRAVITY_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(blob, dict) or not isinstance(blob.get("windows"), list):
        return None
    return blob


def _write_antigravity_cache(windows: list[dict[str, Any]]) -> None:
    """Persist a fresh Antigravity reading for replay while no agy is running.

    Best-effort: a read-only or full state dir must not break the route, so
    write failures are logged at debug and swallowed.
    """
    payload = {
        "windows": windows,
        "as_of": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    try:
        ANTIGRAVITY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        ANTIGRAVITY_CACHE_PATH.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as exc:
        logger.debug("antigravity plan-limit cache write failed: %s", exc)


def _antigravity_provider() -> dict[str, Any]:
    """Build the ``antigravity`` provider row from a local agy's quota RPC.

    Synchronous (the RPC helper does blocking socket discovery); the caller runs
    it off the event loop.
    """
    row: dict[str, Any] = {"id": "antigravity", "label": "Antigravity", "windows": []}
    try:
        summary = antigravity_native_rpc.retrieve_user_quota_summary()
    except Exception as exc:  # noqa: BLE001 - decorative route; never 500 the tray
        logger.debug("antigravity quota RPC failed: %s", exc)
        summary = None

    if summary is not None:
        windows = _antigravity_windows(summary)
        if windows:
            _write_antigravity_cache(windows)
            row["windows"] = windows
            row["state"] = "ok"
            return row

    # No live agy (the common case between sub-agent dispatches). Replay the
    # last good reading, clearly marked, rather than dropping the pill.
    cached = _read_antigravity_cache()
    if cached is not None:
        row["windows"] = cached["windows"]
        row["state"] = "stale"
        row["as_of"] = cached.get("as_of")
        row["reason"] = "no running Antigravity session to query"
        return row

    row["state"] = "unsupported"
    row["reason"] = "no running Antigravity session to query"
    return row


def _openai_provider() -> dict[str, Any]:
    """Build the ``openai`` provider row from our own daily token ledger.

    OpenAI exposes no reading of the complimentary daily token allowance to a
    project key, so this row is not a provider meter like Claude's or
    Antigravity's: it is :mod:`omnigent.openai_token_budget`'s count of every
    token Omnigent sessions spent on OpenAI models today (UTC), against the
    per-pool allowance. One window per free pool; tokens on models in no free
    pool are billed from the first one, so they are called out in the tier line.
    """
    row: dict[str, Any] = {"id": "openai", "label": "OpenAI", "windows": []}
    try:
        pools = openai_token_budget.pool_usage()
    except Exception as exc:  # noqa: BLE001 - decorative route; never 500 the tray
        logger.debug("openai token ledger read failed: %s", exc)
        row["state"] = "error"
        row["reason"] = "token ledger unreadable"
        return row

    resets_at = (
        openai_token_budget.next_reset().replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    tier = "Omnigent's own count"
    for pool in pools:
        tokens = int(pool["tokens"])
        cap = pool["daily_tokens"]
        if cap is None:
            if tokens:
                tier += f" · {openai_token_budget.format_tokens(tokens)} billed (not free)"
            continue
        row["windows"].append(
            {
                "kind": f"daily-{pool['id']}",
                "label": (
                    f"{pool['label']} {openai_token_budget.format_tokens(tokens)}"
                    f"/{openai_token_budget.format_tokens(cap)} today"
                ),
                # Floor, not round: 99.6% must not read as a full 100% "safe"
                # boundary, and any use at all should not read as 0% once
                # past the first percent.
                "percent": max(0, min(100, tokens * 100 // cap)),
                "resets_at": resets_at,
            }
        )
    row["tier"] = tier
    row["state"] = "ok"
    return row


async def collect_plan_limits() -> dict[str, Any]:
    """Fetch every provider's plan windows, honoring the process-wide cache.

    :returns: ``{"providers": [...], "fetched_at": <unix seconds>}``. Never
        raises: a provider that fails contributes a row with a non-``ok``
        ``state`` instead of propagating.
    """
    global _cache
    now = time.monotonic()
    if _cache is not None:
        cached_time, cached_payload = _cache[:2]
        ttl = _cache[2] if len(_cache) > 2 else _CACHE_TTL_SECONDS
        if now - cached_time < ttl:
            return cached_payload

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
        # Antigravity's lookup does blocking port discovery (lsof / procfs), so
        # it goes to a worker thread; running it concurrently with Claude's HTTP
        # call keeps the route's latency at the slower of the two, not the sum.
        claude_row, antigravity_row, openai_row = await asyncio.gather(
            _claude_provider(client),
            asyncio.to_thread(_antigravity_provider),
            asyncio.to_thread(_openai_provider),
        )
        providers = [claude_row, antigravity_row, openai_row]

    payload = {"providers": providers, "fetched_at": time.time()}
    # Debug history: what each provider read, throttled to one line per
    # provider every few minutes (every tab polls this route).
    usage_history.append_plan_limits(providers, now=time.monotonic())

    ttl = _CACHE_TTL_SECONDS
    now_wall = time.time()
    if _claude_cooldown_until > now_wall:
        ttl = min(_CACHE_TTL_SECONDS, max(0.0, _claude_cooldown_until - now_wall))

    _cache = (now, payload, ttl)
    return payload


def create_plan_limits_router(*, auth_provider: AuthProvider | None = None) -> APIRouter:
    """Build the router for ``GET /plan-limits``."""
    router = APIRouter()

    @router.get("/plan-limits")
    async def read_plan_limits(request: Request) -> dict[str, Any]:
        require_user(request, auth_provider)
        return await collect_plan_limits()

    return router
