"""Our own daily token counter for OpenAI's complimentary-token pools.

OpenAI gives organizations that share API traffic a free daily token
allowance per model group, and bills everything past it. The API exposes no
reading of that allowance to a project key (the usage endpoints answer ``403``
without ``api.usage.read``), so the only way to stay inside it is to count
every token ourselves, the same way the composer tray shows Claude's and
Gemini's plan windows.

**What counts.** Every token the model processed: non-cached input, cached
input, cache writes and output (reasoning tokens are already inside output).
Cached input is cheap to *pay* for, but nothing says it is free of the
allowance, so it is counted in full.

**The day.** The allowance is assumed to reset at 00:00 UTC, and the ledger
buckets by UTC day.

**Where tokens come from.** Omnigent's OpenAI traffic goes through the
budget proxy (:mod:`omnigent.server.routes.openai_budget_proxy`), which records
each call's real usage here and refuses calls that could overrun a pool. Calls
made outside Omnigent (a manual ``curl``, a ``codex exec`` in a terminal) are
not seen; record them with ``python -m omnigent.openai_token_budget add``.

The ledger is a small JSON file under the Omnigent state dir, guarded by an
``flock`` so the server and the CLI can both write to it.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import logging
import os
import re
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from omnigent import usage_history
from omnigent.install_ledger import state_dir

logger = logging.getLogger(__name__)

#: Days of history kept in the ledger; older days are pruned on write.
_KEEP_DAYS = 35

#: Token fields summed into a model's daily count, as they appear in a
#: ``session_usage`` delta. ``input_tokens`` is the NON-cached portion there.
_COUNTED_FIELDS = (
    "input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "output_tokens",
)


@dataclass(frozen=True)
class Pool:
    """One daily allowance shared by a group of models.

    ``models`` maps each model family to the exact snapshot date OpenAI lists
    as free, or ``None`` when the list names the undated id itself.
    """

    id: str
    label: str
    daily_tokens: int
    models: Mapping[str, str | None]


#: OpenAI's complimentary-token groups at usage tiers 1-2 (the user's tier),
#: as listed on OpenAI's data-sharing help page on 2026-09-14. A request that
#: crosses a pool's limit is billed in full, not just the overflow.
POOLS: tuple[Pool, ...] = (
    Pool(
        id="small",
        label="Luna/Terra",
        daily_tokens=2_500_000,
        models={
            "gpt-5.6-terra": None,
            "gpt-5.6-luna": None,
            "gpt-5.4-mini": "2026-03-17",
            "gpt-5.4-nano": "2026-03-17",
            "gpt-5.1-codex-mini": None,
            "gpt-5-mini": "2025-08-07",
            "gpt-5-nano": "2025-08-07",
            "gpt-4.1-mini": "2025-04-14",
            "gpt-4.1-nano": "2025-04-14",
            "gpt-4o-mini": "2024-07-18",
            "o4-mini": "2025-04-16",
            "o1-mini": "2024-09-12",
            "codex-mini-latest": None,
        },
    ),
    Pool(
        id="large",
        label="Sol",
        daily_tokens=250_000,
        models={
            "gpt-5.6-sol": None,
            "gpt-5.5": "2026-04-23",
            "gpt-5.4": "2026-03-05",
            "gpt-5.2": "2025-12-11",
            "gpt-5.1": "2025-11-13",
            "gpt-5.1-codex": None,
            "gpt-5-codex": None,
            "gpt-5": "2025-08-07",
            "gpt-5-chat-latest": None,
            "gpt-4.1": "2025-04-14",
            "gpt-4o": "2024-11-20",
            "o3": "2025-04-16",
            "o1-preview": "2024-09-12",
            "o1": "2024-12-17",
        },
    ),
)

#: Pool id for OpenAI models in no free pool: counted, but every token bills.
UNLISTED = "unlisted"

_DATE_SUFFIX = re.compile(r"-(\d{4}-\d{2}-\d{2})$")
_OPENAI_MODEL = re.compile(r"^(gpt-|o\d|codex-|chatgpt-)")

#: gpt-4o has three free snapshots; any of them is fine, the newest is sent.
_EXTRA_FREE_SNAPSHOTS = {"gpt-4o": frozenset({"2024-05-13", "2024-08-06"})}


def ledger_path() -> Path:
    """Return the ledger file, e.g. ``~/.omnigent/openai-token-ledger.json``."""
    return state_dir() / "openai-token-ledger.json"


def _split_model(model: str) -> tuple[str, str | None]:
    """Split ``"openai/gpt-5-mini-2025-08-07"`` into ``("gpt-5-mini", "2025-08-07")``."""
    name = model.strip().lower().rsplit("/", 1)[-1]
    match = _DATE_SUFFIX.search(name)
    if match is None:
        return name, None
    return name[: match.start()], match.group(1)


def normalize_model(model: str) -> str:
    """Reduce a model id to its family name, e.g. ``"gpt-5.6-luna"``."""
    return _split_model(model)[0]


def free_model_id(model: str) -> str | None:
    """Return the exact id to send so the call draws from a free pool.

    An undated alias is pinned to the listed snapshot, since an alias can move
    to a newer snapshot that is not free. A dated id must be a listed snapshot.

    :param model: Requested id, e.g. ``"gpt-5-mini"``.
    :returns: e.g. ``"gpt-5-mini-2025-08-07"``, or ``None`` when no free
        snapshot matches.
    """
    family, date = _split_model(model)
    for pool in POOLS:
        if family not in pool.models:
            continue
        listed = pool.models[family]
        if listed is None:
            return family if date is None else None
        if date is None or date == listed or date in _EXTRA_FREE_SNAPSHOTS.get(family, ()):
            return f"{family}-{date or listed}"
        return None
    return None


def pool_for(model: str) -> str | None:
    """Return the pool id a model draws from.

    :returns: A :data:`POOLS` id, :data:`UNLISTED` for an OpenAI model (or
        snapshot) in no free pool, or ``None`` for a model that is not
        OpenAI's at all.
    """
    if free_model_id(model) is not None:
        family = normalize_model(model)
        return next(pool.id for pool in POOLS if family in pool.models)
    return UNLISTED if _OPENAI_MODEL.match(normalize_model(model)) else None


def utc_day(now: datetime | None = None) -> str:
    """Return the UTC calendar day as ``"YYYY-MM-DD"``."""
    return (now or datetime.now(UTC)).astimezone(UTC).date().isoformat()


def next_reset(now: datetime | None = None) -> datetime:
    """Return the next 00:00 UTC after *now*."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    return datetime(current.year, current.month, current.day, tzinfo=UTC) + timedelta(days=1)


def token_count(value: object) -> int:
    """Coerce one usage field to a non-negative int (junk reads as 0)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if value != value or value < 0 or value == float("inf"):  # NaN / negative / inf
        return 0
    return int(value)


@contextlib.contextmanager
def _locked_ledger(path: Path) -> Iterator[dict[str, Any]]:
    """Yield the parsed ledger under an exclusive lock, and write it back."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict) or not isinstance(data.get("days"), dict):
            data = {"days": {}}
        yield data
        cutoff = (datetime.now(UTC) - timedelta(days=_KEEP_DAYS)).date().isoformat()
        data["days"] = {day: v for day, v in data["days"].items() if day >= cutoff}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)


def record(
    model: str,
    usage: Mapping[str, object],
    *,
    source: str = "session",
    now: datetime | None = None,
    path: Path | None = None,
) -> int:
    """Add one usage delta for *model* to today's ledger.

    :param model: Model id the tokens were spent on, e.g. ``"gpt-5.6-luna"``.
    :param usage: Token fields in ``session_usage`` shape (``input_tokens`` is
        the non-cached portion), e.g. ``{"input_tokens": 20,
        "cache_read_input_tokens": 1000, "output_tokens": 300}``.
    :param source: Where the tokens came from, kept per model, e.g.
        ``"session"`` or ``"manual"``.
    :returns: Tokens recorded (``0`` for a non-OpenAI model or empty delta).
    """
    if pool_for(model) is None:
        return 0
    counts = {field: token_count(usage.get(field)) for field in _COUNTED_FIELDS}
    total = sum(counts.values())
    if total == 0:
        return 0
    with _locked_ledger(path or ledger_path()) as data:
        day = data["days"].setdefault(utc_day(now), {})
        bucket = day.setdefault(normalize_model(model), {})
        for field, count in counts.items():
            bucket[field] = int(bucket.get(field, 0)) + count
        bucket["tokens"] = int(bucket.get("tokens", 0)) + total
        by_source = bucket.setdefault("by_source", {})
        by_source[source] = int(by_source.get(source, 0)) + total
    usage_history.append(
        "openai_call",
        model=normalize_model(model),
        pool=pool_for(model),
        tokens=total,
        source=source,
        **counts,
    )
    return total


def read_day(day: str | None = None, *, path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Return one UTC day's per-model buckets (empty when nothing recorded)."""
    try:
        data = json.loads((path or ledger_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    days = data.get("days") if isinstance(data, dict) else None
    models = days.get(day or utc_day()) if isinstance(days, dict) else None
    return models if isinstance(models, dict) else {}


def pool_usage(now: datetime | None = None, *, path: Path | None = None) -> list[dict[str, Any]]:
    """Summarize today's tokens per pool, free pools first, unlisted last.

    :returns: Rows like ``{"id": "small", "label": "Luna/Terra", "tokens":
        184000, "daily_tokens": 2500000, "models": {"gpt-5.6-luna": 180000}}``;
        ``daily_tokens`` is ``None`` for the unlisted row.
    """
    models = read_day(utc_day(now), path=path)
    rows: dict[str, dict[str, Any]] = {
        pool.id: {
            "id": pool.id,
            "label": pool.label,
            "tokens": 0,
            "daily_tokens": pool.daily_tokens,
            "models": {},
        }
        for pool in POOLS
    }
    rows[UNLISTED] = {
        "id": UNLISTED,
        "label": "Not free",
        "tokens": 0,
        "daily_tokens": None,
        "models": {},
    }
    for model, bucket in models.items():
        pool_id = pool_for(model)
        if pool_id is None or not isinstance(bucket, dict):
            continue
        tokens = token_count(bucket.get("tokens"))
        rows[pool_id]["tokens"] += tokens
        rows[pool_id]["models"][model] = tokens
    return list(rows.values())


def format_tokens(count: int) -> str:
    """Render a token count compactly, e.g. ``184k`` / ``2.5M``."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.2f}".rstrip("0").rstrip(".") + "M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}".rstrip("0").rstrip(".") + "k"
    return str(count)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m omnigent.openai_token_budget")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", help="print today's usage per pool")
    add = sub.add_parser("add", help="record tokens spent outside Omnigent")
    add.add_argument("model")
    add.add_argument("--input", type=int, default=0, help="non-cached input tokens")
    add.add_argument("--cached", type=int, default=0, help="cached input tokens")
    add.add_argument("--cache-write", type=int, default=0, help="cache write tokens")
    add.add_argument("--output", type=int, default=0, help="output tokens (incl. reasoning)")
    args = parser.parse_args(argv)

    if args.cmd == "add":
        recorded = record(
            args.model,
            {
                "input_tokens": args.input,
                "cache_read_input_tokens": args.cached,
                "cache_creation_input_tokens": args.cache_write,
                "output_tokens": args.output,
            },
            source="manual",
        )
        if recorded == 0:
            print(f"nothing recorded ({args.model} is not an OpenAI model, or no tokens)")
            return 1
        print(f"recorded {recorded} tokens on {normalize_model(args.model)}")

    print(f"UTC day {utc_day()} (resets {next_reset().isoformat()})")
    for row in pool_usage():
        cap = row["daily_tokens"]
        used = format_tokens(row["tokens"])
        share = f" of {format_tokens(cap)} ({row['tokens'] * 100 / cap:.1f}%)" if cap else ""
        print(f"  {row['label']}: {used}{share}")
        for model, tokens in sorted(row["models"].items()):
            print(f"    {model}: {tokens}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
