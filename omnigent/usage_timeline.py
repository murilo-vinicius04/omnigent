"""Read side of the usage-history log: what each provider spent, and when.

:mod:`omnigent.usage_history` appends one JSON object per model call and per
plan-limit reading; nothing ever read it back. This module does, and turns it
into the two series the web Usage page draws:

- **tokens per provider per UTC day** — from the ``*_call`` events, which carry
  a model id and a token count. The provider is the model's vendor family
  (``claude`` / ``gemini`` / ``openai`` / ``grok``), so Claude tokens recorded
  by a relay turn and Grok tokens ingested from ``~/.grok/sessions`` land in the
  same view without either side knowing about the other.
- **plan-window utilization over time** — from the ``plan_limits`` events, which
  the composer tray writes every few minutes. The tray shows the current
  percentage; this is the curve behind it.

Aggregation is best-effort and total: a malformed line, an unparseable
timestamp, or an unknown event kind is skipped, never raised. The log is debug
data that may be rotated away at any moment, so callers must treat a thin or
empty result as normal rather than as an error.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omnigent import usage_history

logger = logging.getLogger(__name__)

#: Provider family id -> label shown in the UI. ``other`` catches models whose
#: vendor we cannot name, so their tokens are still counted somewhere.
PROVIDER_LABELS: dict[str, str] = {
    "claude": "Claude",
    "gemini": "Gemini",
    "openai": "OpenAI",
    "grok": "Grok",
    "other": "Other",
}

#: Render order: the four vendors the tray tracks, then the catch-all.
PROVIDER_ORDER: tuple[str, ...] = ("claude", "gemini", "openai", "grok", "other")

#: Substring -> provider family, tried in order against the lowercased model id.
#: Model ids arrive in whatever shape the harness reports ("claude-opus-5",
#: "Gemini 3.8 Flash (High)", "gpt-5.6-luna"), so matching is by substring
#: rather than by an exact table we would have to keep current.
_MODEL_FAMILIES: tuple[tuple[str, str], ...] = (
    ("claude", "claude"),
    ("anthropic", "claude"),
    ("gemini", "gemini"),
    ("antigravity", "gemini"),
    ("grok", "grok"),
    ("xai", "grok"),
    ("gpt", "openai"),
    ("codex", "openai"),
    ("openai", "openai"),
    ("o3-", "openai"),
    ("o4-", "openai"),
)

#: Plan-limits provider id -> token provider family. The tray calls Gemini's
#: row "antigravity" (the client it reads quota from); tokens are counted
#: against the vendor.
PLAN_PROVIDER_FAMILY: dict[str, str] = {
    "claude": "claude",
    "antigravity": "gemini",
    "gemini": "gemini",
    "openai": "openai",
    "grok": "grok",
}

#: Short labels for the window kinds the tray records; unknown kinds pass
#: through as themselves (e.g. agy's ``gemini-5h`` bucket ids).
_WINDOW_LABELS: dict[str, str] = {"session": "5h", "weekly": "week", "daily": "day"}

#: Token-count field aliases across writers: the relay path records
#: ``cache_read_input_tokens``, the Grok ingester ``cached_read_tokens``.
_CACHED_KEYS = ("cached_tokens", "cache_read_input_tokens", "cached_read_tokens")

#: Cap on plan-limit points returned per window. A reading every five minutes
#: is ~8.6k points a month per window — far more than a chart can draw.
DEFAULT_MAX_POINTS = 240

#: Built results are cached this long: the tray polls once a minute per tab and
#: the log only grows at the end, so re-parsing it per request is waste.
_CACHE_TTL_SECONDS = 30.0

#: At most this many (since, until, max_points) results are remembered.
_CACHE_MAX_ENTRIES = 8

_cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}


def provider_for_model(model: str | None) -> str:
    """Return the provider family for a raw harness model id.

    :param model: Model id as the harness reported it, e.g. ``"claude-opus-5"``
        or ``"Gemini 3.8 Flash (High)"``. ``None`` or empty reads as ``other``.
    :returns: A key of :data:`PROVIDER_LABELS`.
    """
    if not model:
        return "other"
    lowered = str(model).lower()
    for needle, family in _MODEL_FAMILIES:
        if needle in lowered:
            return family
    return "other"


def _parse_at(raw: Any) -> datetime | None:
    """Parse an event's ``at`` stamp into an aware UTC datetime, or ``None``.

    Writers disagree on precision — Omnigent stamps whole seconds with a ``Z``,
    the Grok ingester forwards xAI's nanosecond offsets — so sub-second digits
    past microseconds are trimmed before parsing.
    """
    if not isinstance(raw, str) or not raw:
        return None
    cleaned = re.sub(r"(\.\d{6})\d+", r"\1", raw.strip()).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _int_field(event: dict[str, Any], *names: str) -> int:
    """Sum the first present, numeric value among *names* (0 when none)."""
    for name in names:
        value = event.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError, OverflowError):
            continue
    return 0


def _float_field(event: dict[str, Any], name: str) -> float:
    """Read a non-negative float field, or ``0.0``."""
    value = event.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 else 0.0


def history_paths(path: Path | None = None) -> list[Path]:
    """Return the log files to read, oldest rotation first.

    :param path: Override for the current log file; the rotated ``.1``
        neighbour is derived from it.
    """
    current = path or usage_history.history_path()
    return [current.with_suffix(".jsonl.1"), current]


def read_events(path: Path | None = None) -> Iterator[dict[str, Any]]:
    """Yield every parseable event from the log and its rotated neighbour.

    Unreadable files and malformed lines are skipped: this log is written
    best-effort and a truncated last line (the writer was interrupted) must not
    cost the caller the rest of the history.
    """
    for file_path in history_paths(path):
        try:
            with open(file_path, encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        event = json.loads(stripped)
                    except ValueError:
                        continue
                    if isinstance(event, dict):
                        yield event
        except OSError as exc:
            logger.debug("usage history read failed for %s: %s", file_path, exc)
            continue


def _event_provider(event: dict[str, Any], kind: str) -> str:
    """Resolve the provider family a token event belongs to.

    An explicit ``provider`` field wins, should a writer ever record one;
    otherwise the model id names the vendor, and failing that the
    ``<name>_call`` kind does (``openai_call`` -> ``openai``).
    """
    explicit = event.get("provider")
    if isinstance(explicit, str) and explicit in PROVIDER_LABELS:
        return explicit
    model = event.get("model")
    family = provider_for_model(model if isinstance(model, str) else None)
    if family != "other":
        return family
    prefix = kind.removesuffix("_call")
    return prefix if prefix in PROVIDER_LABELS else "other"


def _blank_bucket() -> dict[str, Any]:
    return {
        "tokens": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "cost_usd": 0.0,
        "calls": 0,
    }


def _add(bucket: dict[str, Any], counts: dict[str, Any]) -> None:
    """Accumulate one call's counts into *bucket*."""
    for key in ("tokens", "input_tokens", "output_tokens", "cached_tokens", "calls"):
        bucket[key] += counts[key]
    bucket["cost_usd"] = round(bucket["cost_usd"] + counts["cost_usd"], 8)


def _downsample(points: list[dict[str, Any]], max_points: int) -> list[dict[str, Any]]:
    """Thin *points* to at most *max_points*, always keeping the newest one.

    The last reading is what the tray is showing right now, so a chart that
    dropped it would disagree with the pill beside it.
    """
    if max_points <= 0 or len(points) <= max_points:
        return points
    stride = len(points) / max_points
    kept = [points[int(index * stride)] for index in range(max_points)]
    if kept[-1] is not points[-1]:
        kept[-1] = points[-1]
    return kept


def _token_counts(event: dict[str, Any]) -> dict[str, Any]:
    """Project one ``*_call`` event into the counters we aggregate."""
    input_tokens = _int_field(event, "input_tokens")
    output_tokens = _int_field(event, "output_tokens")
    cached = _int_field(event, *_CACHED_KEYS)
    total = _int_field(event, "tokens", "total_tokens")
    if not total:
        total = input_tokens + output_tokens + cached
    return {
        "tokens": total,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached,
        "cost_usd": _float_field(event, "cost_usd"),
        "calls": 1,
    }


def build_token_usage(
    *,
    since: str | None = None,
    until: str | None = None,
    path: Path | None = None,
    max_points: int = DEFAULT_MAX_POINTS,
    include_limits: bool = True,
) -> dict[str, Any]:
    """Aggregate the usage log into per-provider token and plan-limit series.

    :param since: Inclusive lower bound as ``"YYYY-MM-DD"`` UTC, or ``None``
        for "as far back as the log goes".
    :param until: Inclusive upper bound as ``"YYYY-MM-DD"`` UTC, or ``None``.
    :param path: Override for the log file (tests).
    :param max_points: Cap on plan-limit points per window.
    :param include_limits: Collect the plan-window curves. The tray only wants
        today's token totals and would otherwise pay for thousands of readings
        it throws away.
    :returns: ``{"since", "until", "providers", "limits", "totals"}``. Providers
        with no recorded tokens in the window are omitted, so a fresh host gets
        an empty list rather than a row of zeros.
    """
    providers: dict[str, dict[str, Any]] = {}
    limits: dict[str, dict[str, list[dict[str, Any]]]] = {}

    for event in read_events(path):
        kind = event.get("kind")
        if not isinstance(kind, str):
            continue
        at = _parse_at(event.get("at"))
        if at is None:
            continue
        day = at.date().isoformat()
        if since and day < since:
            continue
        if until and day > until:
            continue

        if kind == "plan_limits":
            if not include_limits:
                continue
            provider_id = event.get("provider")
            if not isinstance(provider_id, str):
                continue
            windows = event.get("windows")
            if not isinstance(windows, dict):
                continue
            by_window = limits.setdefault(provider_id, {})
            for window_kind, percent in windows.items():
                if isinstance(percent, bool) or not isinstance(percent, (int, float)):
                    continue
                by_window.setdefault(str(window_kind), []).append(
                    {
                        "at": at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                        "percent": max(0, min(100, round(float(percent)))),
                    }
                )
            continue

        if not kind.endswith("_call"):
            continue

        counts = _token_counts(event)
        if not counts["tokens"] and not counts["cost_usd"]:
            continue

        family = _event_provider(event, kind)
        provider = providers.setdefault(
            family,
            {
                "id": family,
                "label": PROVIDER_LABELS.get(family, family.title()),
                **_blank_bucket(),
                "days": {},
                "models": {},
            },
        )
        _add(provider, counts)
        _add(provider["days"].setdefault(day, {"day": day, **_blank_bucket()}), counts)
        model = event.get("model")
        model_id = str(model) if isinstance(model, str) and model else "unknown"
        _add(
            provider["models"].setdefault(model_id, {"model": model_id, **_blank_bucket()}), counts
        )

    provider_rows = []
    for family in sorted(providers, key=_provider_sort_key):
        provider = providers[family]
        provider["days"] = sorted(provider["days"].values(), key=lambda row: row["day"])
        provider["models"] = sorted(
            provider["models"].values(), key=lambda row: row["tokens"], reverse=True
        )
        provider_rows.append(provider)

    limit_rows = []
    for provider_id in sorted(limits, key=_provider_sort_key):
        windows = [
            {
                "kind": window_kind,
                "label": _WINDOW_LABELS.get(window_kind, window_kind),
                "points": _downsample(points, max_points),
            }
            for window_kind, points in sorted(limits[provider_id].items())
            if points
        ]
        if not windows:
            continue
        family = PLAN_PROVIDER_FAMILY.get(provider_id, provider_id)
        limit_rows.append(
            {
                "provider": provider_id,
                "label": PROVIDER_LABELS.get(family, provider_id.title()),
                "windows": windows,
            }
        )

    totals = _blank_bucket()
    for provider in provider_rows:
        _add(totals, provider)

    return {
        "since": since,
        "until": until,
        "providers": provider_rows,
        "limits": limit_rows,
        "totals": totals,
    }


def _provider_sort_key(provider_id: str) -> tuple[int, str]:
    """Sort known providers into :data:`PROVIDER_ORDER`, unknown ones last."""
    family = PLAN_PROVIDER_FAMILY.get(provider_id, provider_id)
    if family in PROVIDER_ORDER:
        return (PROVIDER_ORDER.index(family), provider_id)
    return (len(PROVIDER_ORDER), provider_id)


def cached_token_usage(
    *,
    since: str | None = None,
    until: str | None = None,
    path: Path | None = None,
    max_points: int = DEFAULT_MAX_POINTS,
    include_limits: bool = True,
) -> dict[str, Any]:
    """:func:`build_token_usage` behind a short TTL cache.

    Every browser tab polls the tray once a minute and the Usage page refetches
    on focus; re-parsing a multi-megabyte log for each of those is pure waste,
    and the numbers move slower than the TTL.
    """
    key = (str(path) if path else None, since, until, max_points, include_limits)
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < _CACHE_TTL_SECONDS:
        return hit[1]
    result = build_token_usage(
        since=since,
        until=until,
        path=path,
        max_points=max_points,
        include_limits=include_limits,
    )
    if len(_cache) >= _CACHE_MAX_ENTRIES:
        _cache.clear()
    _cache[key] = (now, result)
    return result


def tokens_today(
    *,
    now: datetime | None = None,
    path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Return today's (UTC) recorded totals per provider family.

    Used by the composer tray to answer "how many tokens has this vendor burned
    today" beside the plan percentage.

    :returns: ``{family: {"tokens": int, "cost_usd": float, "calls": int}}``,
        holding only families with something recorded today.
    """
    day = (now or datetime.now(UTC)).astimezone(UTC).date().isoformat()
    report = cached_token_usage(since=day, until=day, path=path, include_limits=False)
    return {
        provider["id"]: {
            "tokens": provider["tokens"],
            "cost_usd": provider["cost_usd"],
            "calls": provider["calls"],
        }
        for provider in report["providers"]
    }


def format_tokens(count: int) -> str:
    """Render a token count compactly, e.g. ``184k`` / ``2.5M``."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.2f}".rstrip("0").rstrip(".") + "M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}".rstrip("0").rstrip(".") + "k"
    return str(count)
