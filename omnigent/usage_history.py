"""Append-only log of model usage, for looking back at what was spent where.

The composer tray shows what is left *now*, and the OpenAI ledger holds
*today's* totals per model. Neither says what happened at 14:20, which model
answered, or how fast a pool drained — the questions that come up when a run
costs more than expected.

So every recorded call, and every plan-limit reading the tray asks for, is
appended here as one JSON object per line:

```json
{"at": "2026-09-14T17:41:02Z", "kind": "openai_call", "model": "gpt-5.6-luna",
 "pool": "small", "tokens": 31601, "input_tokens": 27, "cached_tokens": 107814,
 "output_tokens": 3298, "source": "proxy"}
{"at": "2026-09-14T17:42:00Z", "kind": "plan_limits", "provider": "claude",
 "state": "ok", "windows": {"session": 42, "weekly": 17}}
{"at": "2026-09-14T17:43:10Z", "kind": "model_call", "model": "claude-opus-5",
 "tokens": 9600, "input_tokens": 1200, "output_tokens": 400,
 "cache_read_input_tokens": 8000, "cost_usd": 0.03, "source": "relay"}
```

Best-effort and disposable: writes never raise, and the file is rotated once it
passes :data:`_MAX_BYTES` so it cannot fill the disk. The database, not this
log, is the authoritative record of what a session spent —
:mod:`omnigent.usage_timeline` reads these lines back only to draw the
per-provider history, and must tolerate a window that was rotated away.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omnigent.install_ledger import state_dir

logger = logging.getLogger(__name__)

#: Rotate at this size; one previous file is kept as ``<name>.1``.
_MAX_BYTES = 8 * 1024 * 1024

#: Plan-limit readings are polled once a minute per browser tab; keep at most
#: one line per provider per this many seconds.
_SNAPSHOT_INTERVAL_S = 300.0

_last_snapshot: dict[str, float] = {}


def history_path() -> Path:
    """Return the log file, e.g. ``~/.omnigent/usage-history.jsonl``."""
    return state_dir() / "usage-history.jsonl"


def _rotate(path: Path) -> None:
    """Keep one previous file so the log cannot grow without bound."""
    try:
        if path.stat().st_size < _MAX_BYTES:
            return
        os.replace(path, path.with_suffix(".jsonl.1"))
    except OSError:
        return


def append(kind: str, **fields: Any) -> None:
    """Append one event. Never raises: this log must not break a call.

    :param kind: Event kind, e.g. ``"openai_call"`` or ``"plan_limits"``.
    :param fields: JSON-serializable detail, e.g. ``model="gpt-5.6-luna"``.
    """
    path = history_path()
    event = {
        "at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "kind": kind,
        **fields,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate(path)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, default=str) + "\n")
    except OSError as exc:
        logger.debug("usage history write failed: %s", exc)


def append_model_call(
    model: str | None,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    cost_usd: float | None = None,
    session_id: str | None = None,
    source: str = "session",
) -> None:
    """Append one ``model_call`` line for a turn's token delta.

    The per-session counters in the database say what a session has spent in
    total; this says *when* it was spent and on which model, which is what the
    Usage page's per-provider timeline reads back. The vendor is derived from
    the model id at read time (:func:`omnigent.usage_timeline.provider_for_model`),
    so nothing here has to know the provider families.

    A call with no tokens and no cost is dropped rather than logged: cost-only
    native polls arrive several times a turn and would otherwise flood the log.

    :param model: Raw harness model id, e.g. ``"claude-opus-5"``.
    :param input_tokens: Non-cached input tokens for this turn.
    :param output_tokens: Output tokens for this turn.
    :param cache_read_input_tokens: Cached input tokens read this turn.
    :param cache_creation_input_tokens: Cache-write tokens for this turn.
    :param cost_usd: Priced cost of the turn, when the turn was priced.
    :param session_id: Originating session, for tracing a spike back.
    :param source: Which write path recorded it (``relay`` / ``native``).
    """
    total = (
        max(0, input_tokens)
        + max(0, output_tokens)
        + max(0, cache_read_input_tokens)
        + max(0, cache_creation_input_tokens)
    )
    if not total and not cost_usd:
        return
    append(
        "model_call",
        model=model or "unknown",
        input_tokens=max(0, input_tokens),
        output_tokens=max(0, output_tokens),
        cache_read_input_tokens=max(0, cache_read_input_tokens),
        cache_creation_input_tokens=max(0, cache_creation_input_tokens),
        tokens=total,
        cost_usd=round(float(cost_usd), 8) if cost_usd else 0.0,
        session_id=session_id,
        source=source,
    )


def append_plan_limits(providers: list[dict[str, Any]], *, now: float) -> None:
    """Record one line per provider from a plan-limits reading, at most every
    :data:`_SNAPSHOT_INTERVAL_S` seconds per provider.

    :param providers: The route's provider rows.
    :param now: Monotonic-ish seconds used for the per-provider throttle.
    """
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        name = str(provider.get("id") or "?")
        if now - _last_snapshot.get(name, 0.0) < _SNAPSHOT_INTERVAL_S:
            continue
        _last_snapshot[name] = now
        windows = {
            str(w.get("kind")): w.get("percent")
            for w in provider.get("windows") or []
            if isinstance(w, dict)
        }
        append(
            "plan_limits",
            provider=name,
            state=provider.get("state"),
            windows=windows,
            tier=provider.get("tier"),
        )
