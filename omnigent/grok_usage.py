"""Ingest and summarize local Grok session usage from ~/.grok/sessions.

Mirrors :mod:`omnigent.openai_token_budget`: tracks daily usage in an on-disk
ledger, logs every new turn to the usage history log, and provides a daily
summary for the plan limits pill.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import logging
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from omnigent import usage_history
from omnigent.install_ledger import state_dir

logger = logging.getLogger(__name__)

#: Root directory where grok saves session usage JSON files.
SESSIONS_ROOT = Path(os.path.expanduser("~/.grok/sessions"))

#: Days of history kept in the ledger; older days are pruned on write.
_KEEP_DAYS = 30


def sessions_root() -> Path:
    """Return the base path for Grok sessions."""
    return SESSIONS_ROOT


def ledger_path() -> Path:
    """Return the ledger file path, e.g. ``~/.omnigent/grok-usage-ledger.json``."""
    return state_dir() / "grok-usage-ledger.json"


def utc_day(now: datetime | None = None) -> str:
    """Return the UTC calendar day as ``"YYYY-MM-DD"``."""
    return (now or datetime.now(UTC)).astimezone(UTC).date().isoformat()


def next_reset(now: datetime | None = None) -> datetime:
    """Return the next 00:00 UTC after *now*."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    return datetime(current.year, current.month, current.day, tzinfo=UTC) + timedelta(days=1)


def format_tokens(count: int) -> str:
    """Render a token count compactly, e.g. ``184k`` / ``2.5M``."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.2f}".rstrip("0").rstrip(".") + "M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}".rstrip("0").rstrip(".") + "k"
    return str(count)


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
        if not isinstance(data, dict):
            data = {}
        if not isinstance(data.get("days"), dict):
            data["days"] = {}
        if not isinstance(data.get("files"), dict):
            data["files"] = {}
        if not isinstance(data.get("seen_turns"), dict):
            data["seen_turns"] = {}
        yield data
        cutoff = (datetime.now(UTC) - timedelta(days=_KEEP_DAYS)).date().isoformat()
        data["days"] = {day: v for day, v in data["days"].items() if day >= cutoff}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)


def ingest(
    *,
    root: Path | None = None,
    path: Path | None = None,
    now: datetime | None = None,
) -> int:
    """Scan Grok usage.json files, record unseen turns, and log them to history.

    :param root: Grok sessions directory to scan.
    :param path: Path to the ledger JSON file.
    :param now: Override timestamp for testing.
    :returns: Number of new turns ingested.
    """
    search_root = root or sessions_root()
    if not search_root.exists() or not search_root.is_dir():
        return 0

    try:
        usage_files = sorted(search_root.glob("*/*/usage.json"))
    except OSError as exc:
        logger.warning("failed to list grok session files in %s: %s", search_root, exc)
        return 0

    if not usage_files:
        return 0

    new_turns_count = 0
    with _locked_ledger(path or ledger_path()) as data:
        files_map = data["files"]
        seen_turns = data["seen_turns"]
        days = data["days"]

        for file_path in usage_files:
            try:
                mtime = file_path.stat().st_mtime
            except OSError as exc:
                logger.warning("failed to stat grok usage file %s: %s", file_path, exc)
                continue

            str_path = str(file_path)
            if files_map.get(str_path) == mtime:
                continue

            try:
                content = file_path.read_text(encoding="utf-8")
                usage_data = json.loads(content)
            except Exception as exc:  # noqa: BLE001
                logger.warning("grok usage file %s unreadable or malformed: %s", file_path, exc)
                continue

            if not isinstance(usage_data, dict):
                logger.warning("grok usage file %s is not a json object", file_path)
                continue

            session_id = usage_data.get("sessionId")
            turns = usage_data.get("turns")
            if not session_id or not isinstance(turns, list):
                logger.warning("grok usage file %s missing sessionId or turns", file_path)
                continue

            for turn in turns:
                if not isinstance(turn, dict):
                    continue
                turn_number = turn.get("turnNumber")
                if turn_number is None:
                    continue

                turn_key = f"{session_id}:{turn_number}"
                if turn_key in seen_turns:
                    continue

                ended_at_str = turn.get("endedAt")
                if ended_at_str:
                    try:
                        ended_at_dt = datetime.fromisoformat(ended_at_str.replace("Z", "+00:00"))
                    except Exception:  # noqa: BLE001
                        ended_at_dt = now or datetime.now(UTC)
                else:
                    ended_at_dt = now or datetime.now(UTC)

                day_str = utc_day(ended_at_dt)
                at_str = (
                    ended_at_str
                    if ended_at_str
                    else ended_at_dt.astimezone(UTC)
                    .replace(microsecond=0)
                    .isoformat()
                    .replace("+00:00", "Z")
                )

                primary_model_id = turn.get("primaryModelId") or "grok"
                input_tokens = int(turn.get("inputTokens") or 0)
                output_tokens = int(turn.get("outputTokens") or 0)
                cached_read_tokens = int(turn.get("cachedReadTokens") or 0)
                cache_creation_tokens = int(turn.get("cacheCreationTokens") or 0)
                reasoning_tokens = int(turn.get("reasoningTokens") or 0)
                total_tokens = int(turn.get("totalTokens") or 0)
                model_calls = int(turn.get("modelCalls") or 0)
                cost_usd_ticks = int(turn.get("costUsdTicks") or 0)
                cost_usd = cost_usd_ticks / 1e10

                day_bucket = days.setdefault(
                    day_str,
                    {
                        "tokens": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cached_read_tokens": 0,
                        "cache_creation_tokens": 0,
                        "reasoning_tokens": 0,
                        "model_calls": 0,
                        "turns": 0,
                        "cost_usd": 0.0,
                        "models": {},
                    },
                )
                day_bucket["tokens"] += total_tokens
                day_bucket["input_tokens"] += input_tokens
                day_bucket["output_tokens"] += output_tokens
                day_bucket["cached_read_tokens"] += cached_read_tokens
                day_bucket["cache_creation_tokens"] += cache_creation_tokens
                day_bucket["reasoning_tokens"] += reasoning_tokens
                day_bucket["model_calls"] += model_calls
                day_bucket["turns"] += 1
                day_bucket["cost_usd"] = round(day_bucket["cost_usd"] + cost_usd, 8)

                model_bucket = day_bucket.setdefault("models", {}).setdefault(
                    primary_model_id,
                    {
                        "tokens": 0,
                        "model_calls": 0,
                        "turns": 0,
                        "cost_usd": 0.0,
                    },
                )
                model_bucket["tokens"] += total_tokens
                model_bucket["model_calls"] += model_calls
                model_bucket["turns"] += 1
                model_bucket["cost_usd"] = round(model_bucket["cost_usd"] + cost_usd, 8)

                seen_turns[turn_key] = True
                new_turns_count += 1

                usage_history.append(
                    "grok_call",
                    at=at_str,
                    model=primary_model_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_read_tokens=cached_read_tokens,
                    reasoning_tokens=reasoning_tokens,
                    tokens=total_tokens,
                    model_calls=model_calls,
                    cost_usd=cost_usd,
                    session_id=session_id,
                    turn=turn_number,
                    source="grok_sessions",
                )

            files_map[str_path] = mtime

    return new_turns_count


def read_day(day: str | None = None, *, path: Path | None = None) -> dict[str, Any]:
    """Return one UTC day's recorded stats (empty when nothing recorded)."""
    try:
        data = json.loads((path or ledger_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    days = data.get("days") if isinstance(data, dict) else None
    day_entry = days.get(day or utc_day()) if isinstance(days, dict) else None
    return day_entry if isinstance(day_entry, dict) else {}


def usage_summary(
    now: datetime | None = None,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Summarize today's Grok token usage for the plan limits provider.

    :param now: Reference datetime.
    :param path: Override ledger path.
    :returns: Summary dictionary.
    """
    day_data = read_day(utc_day(now), path=path)
    tokens = int(day_data.get("tokens", 0))
    model_calls = int(day_data.get("model_calls", 0))
    turns = int(day_data.get("turns", 0))
    cost_usd = float(day_data.get("cost_usd", 0.0))
    resets_at = next_reset(now).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    return {
        "tokens": tokens,
        "model_calls": model_calls,
        "turns": turns,
        "cost_usd": cost_usd,
        "resets_at": resets_at,
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="grok_usage", description="Grok token usage ledger")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("ingest", help="scan grok sessions and update ledger")
    sub.add_parser("show", help="print today's grok usage")
    args = parser.parse_args(argv)

    if args.command == "ingest":
        added = ingest()
        print(f"Ingested {added} new turn(s)")
        return 0

    if args.command == "show" or args.command is None:
        summary = usage_summary()
        print(f"UTC day {utc_day()} (resets {next_reset().isoformat()})")
        print(f"Tokens: {summary['tokens']:,} ({format_tokens(summary['tokens'])})")
        print(f"Model calls: {summary['model_calls']}")
        print(f"Turns: {summary['turns']}")
        print(f"Cost USD: ${summary['cost_usd']:.4f}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    import sys

    sys.exit(_main())
