"""Token-usage capture for the muse-code harness.

Muse Code persists per-turn model usage ONLY in its local session store:
``~/.local/share/muse/sessions/YYYY/MM/DD/<muse-session-id>/session.jsonl``.
Each line is an envelope that is either a record directly —

    {"id": ..., "stream": {"kind": "session", "id": "<muse-session-id>"},
     "sequence": N, "recorded_at": <microseconds>, ...,
     "payload": {"event": {"kind": ..., "model": "muse-spark-1.3",
        "usage": {"input_tokens": ..., "output_tokens": ...,
                  "reasoning_tokens": ..., "cached_tokens": ...,
                  "cache_read_tokens": ..., "cache_write_tokens": ...}}, ...}}

— or a retained frame whose ``children`` carry the same records as
stringified ``record_json``. The normalizer (:func:`parse_usage_records`)
unpacks both shapes and reads ONLY ``payload.event.usage``: the sibling
``payload.event.record.quantity`` repeats the same counts and must be
ignored, or every turn is billed twice.

The runner-owned poller (:func:`forward_muse_usage_to_session`) tails one
session file by byte offset, folds newly-appended events into cumulative
SESSION totals (deduped by record ``id``), and POSTs them as an
``external_session_usage`` event — the SAME server contract
claude-native, codex-native and cursor-native use, so the web UI's
Session-cost badge and per-model token breakdown light up with no
frontend changes.

Unlike the cursor hook recorder there is no hook to install: Muse writes
its own log, so this module only READS. ``DEFAULT_SESSIONS_ROOT`` honours
``$MUSE_SESSIONS_ROOT`` for tests and relocated stores.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from httpx import Auth as HttpxAuth
else:
    HttpxAuth = object

_logger = logging.getLogger(__name__)

#: Default Muse local session store root (date-partitioned below it).
DEFAULT_SESSIONS_ROOT = Path(
    os.environ.get(
        "MUSE_SESSIONS_ROOT",
        str(Path.home() / ".local" / "share" / "muse" / "sessions"),
    )
)

#: Durable poller state (byte offset + cumulative totals + seen event ids).
_USAGE_STATE_FILE = "muse_usage_forwarder.json"

#: Token fields lifted out of ``payload.event.usage``.
_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cached_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)

_DEFAULT_POLL_INTERVAL_S = 5.0
_POST_TIMEOUT_S = 30.0

# Supervisor backoff (mirrors cursor_native_usage.supervise_cursor_usage_forwarder).
_SUPERVISOR_INITIAL_BACKOFF_S = 1.0
_SUPERVISOR_MAX_BACKOFF_S = 30.0
_SUPERVISOR_HEALTHY_UPTIME_S = 60.0


# --------------------------------------------------------------------------- #
# Pure reader (no I/O beyond its arguments — keep fast; sampler polls it).
# --------------------------------------------------------------------------- #


def _coerce_int(value: object) -> int:
    """Coerce a token field to a non-negative int (0 on anything odd)."""
    if isinstance(value, bool):
        return 0
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        return 0
    try:
        out = int(value)
    except (TypeError, ValueError):
        return 0
    return out if out >= 0 else 0


def _record_candidates(envelope: object) -> list[dict[str, object]]:
    """Unpack one session.jsonl line into its candidate record dicts.

    Returns the envelope itself plus every ``children[].record_json``
    string parsed as JSON (skipped when unparseable). Non-dict lines
    yield nothing.
    """
    if not isinstance(envelope, dict):
        return []
    out = [envelope]
    children = envelope.get("children")
    if isinstance(children, list):
        for child in children:
            if not isinstance(child, dict):
                continue
            raw = child.get("record_json")
            if not isinstance(raw, str) or not raw.strip():
                continue
            try:
                inner = json.loads(raw)
            except ValueError:
                continue
            if isinstance(inner, dict):
                out.append(inner)
    return out


def normalize_usage_record(record: object) -> dict[str, object] | None:
    """Reduce one record dict to its billable usage, or ``None`` to skip.

    Reads ONLY ``payload.event.usage`` — the sibling
    ``payload.event.record.quantity`` repeats the same counts. Requires a
    string record ``id`` (dedup key) and at least one positive token count.
    """
    if not isinstance(record, dict):
        return None
    event_id = record.get("id")
    if not isinstance(event_id, str) or not event_id:
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    event = payload.get("event")
    if not isinstance(event, dict):
        return None
    usage = event.get("usage")
    if not isinstance(usage, dict):
        return None
    tokens = {name: _coerce_int(usage.get(name)) for name in _TOKEN_FIELDS}
    if not tokens["input_tokens"] and not tokens["output_tokens"]:
        return None
    line: dict[str, object] = {"event_id": event_id}
    model = event.get("model")
    if isinstance(model, str) and model:
        line["model"] = model
    line.update(tokens)
    stream = record.get("stream")
    if isinstance(stream, dict) and isinstance(stream.get("id"), str):
        line["muse_session_id"] = stream["id"]
    sequence = record.get("sequence")
    if isinstance(sequence, int):
        line["sequence"] = sequence
    return line


def parse_usage_records(text: str) -> list[dict[str, object]]:
    """Parse session.jsonl text into normalized per-event usage lines.

    Skips blank lines, malformed JSON, and records with no billable usage.
    """
    out: list[dict[str, object]] = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            envelope = json.loads(raw)
        except ValueError:
            continue
        for record in _record_candidates(envelope):
            line = normalize_usage_record(record)
            if line is not None:
                out.append(line)
    return out


@dataclass
class MuseUsageTotals:
    """Cumulative token totals for one Muse session."""

    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    cache_read_tokens: int = 0
    model: str | None = None
    events: int = 0

    def add_line(self, line: dict[str, object]) -> None:
        """Fold one normalized usage line into the totals."""
        self.input_tokens += _coerce_int(line.get("input_tokens"))
        self.output_tokens += _coerce_int(line.get("output_tokens"))
        self.reasoning_tokens += _coerce_int(line.get("reasoning_tokens"))
        self.cached_tokens += _coerce_int(line.get("cached_tokens"))
        self.cache_read_tokens += _coerce_int(line.get("cache_read_tokens"))
        model = line.get("model")
        if isinstance(model, str) and model:
            self.model = model  # latest event's model wins
        self.events += 1


def summarize_text(text: str) -> MuseUsageTotals:
    """Total the billable usage in session.jsonl text (dedup by event id)."""
    totals = MuseUsageTotals()
    seen: set[str] = set()
    for line in parse_usage_records(text):
        event_id = line.get("event_id")
        if not isinstance(event_id, str) or event_id in seen:
            continue
        seen.add(event_id)
        totals.add_line(line)
    return totals


def summarize_file(path: Path) -> MuseUsageTotals:
    """Total the billable usage in one session.jsonl file (missing → zeros)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return MuseUsageTotals()
    return summarize_text(text)


def collect_muse_usage(
    sessions_root: Path = DEFAULT_SESSIONS_ROOT,
) -> dict[str, MuseUsageTotals]:
    """Walk the Muse session store; map muse-session-id → cumulative totals.

    Sessions the store cannot attribute (no ``stream.id`` on any event) are
    grouped under the file's parent directory name when it looks like a
    session id, else skipped.
    """
    totals: dict[str, MuseUsageTotals] = {}
    try:
        files = sorted(sessions_root.rglob("session.jsonl"))
    except OSError:
        return totals
    for path in files:
        file_totals = MuseUsageTotals()
        seen: set[str] = set()
        fallback_id = path.parent.name
        per_session: dict[str, MuseUsageTotals] = {}
        for line in parse_usage_records(_read_text_quietly(path)):
            event_id = line.get("event_id")
            if not isinstance(event_id, str) or event_id in seen:
                continue
            seen.add(event_id)
            sid = line.get("muse_session_id")
            key = sid if isinstance(sid, str) and sid else fallback_id
            per_session.setdefault(key, MuseUsageTotals()).add_line(line)
            file_totals.add_line(line)
        for key, sub in per_session.items():
            existing = totals.get(key)
            if existing is None:
                totals[key] = sub
            else:  # same session id across rotated files: merge
                existing.input_tokens += sub.input_tokens
                existing.output_tokens += sub.output_tokens
                existing.reasoning_tokens += sub.reasoning_tokens
                existing.cached_tokens += sub.cached_tokens
                existing.cache_read_tokens += sub.cache_read_tokens
                existing.events += sub.events
                if sub.model:
                    existing.model = sub.model
    return totals


def _read_text_quietly(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def session_file_for(
    muse_session_id: str, sessions_root: Path = DEFAULT_SESSIONS_ROOT
) -> Path | None:
    """Locate the session.jsonl holding a Muse session id, or ``None``."""
    try:
        files = sorted(sessions_root.rglob("session.jsonl"))
    except OSError:
        return None
    for path in files:
        if path.parent.name == muse_session_id:
            return path
    # Fall back to a content scan: the id may live only in stream ids.
    for path in files:
        try:
            head = path.read_text(encoding="utf-8")[:65536]
        except OSError:
            continue
        if muse_session_id in head:
            return path
    return None


# --------------------------------------------------------------------------- #
# Sampler-side growth reporter (server-owned, no extra process).
# --------------------------------------------------------------------------- #

#: Persisted per-session totals already reported to the usage history, so a
#: rescan reports only growth. Lives in the install state dir next to the
#: plan-limit caches.
_REPORT_STATE_FILE = "muse_usage_reported.json"

#: Minimum seconds between history sweeps; ``collect_plan_limits`` runs far
#: more often than this (every tab poll + the meter keeper).
_REPORT_INTERVAL_S = 240.0

_last_report_s = 0.0


def report_muse_token_growth(
    sessions_root: Path = DEFAULT_SESSIONS_ROOT,
    *,
    state_dir: Path | None = None,
    now_s: float | None = None,
) -> int:
    """Append ``model_call`` history lines for Muse token growth.

    Diffs :func:`collect_muse_usage` against the last reported per-session
    totals and appends one ``model_call`` line per session with positive
    growth (``source="muse-tail"``). Token-only lines are kept: callers that
    need cost join them against the priced relay/native paths. Throttled to
    one sweep per :data:`_REPORT_INTERVAL_S`. Never raises.

    :returns: Number of history lines appended.
    """
    from omnigent import usage_history  # lazy: server-only dependency

    global _last_report_s
    now = now_s if now_s is not None else time.monotonic()
    if now - _last_report_s < _REPORT_INTERVAL_S:
        return 0
    _last_report_s = now
    try:
        from omnigent.install_ledger import state_dir as _state_dir

        directory = state_dir or _state_dir()
        state_path = directory / _REPORT_STATE_FILE
        try:
            reported = json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(reported, dict):
                reported = {}
        except (OSError, ValueError):
            reported = {}
        lines = 0
        current = collect_muse_usage(sessions_root)
        for muse_session_id, totals in current.items():
            old = reported.get(muse_session_id)
            old = old if isinstance(old, dict) else {}
            growth_in = totals.input_tokens - _coerce_int(old.get("input_tokens"))
            growth_out = totals.output_tokens - _coerce_int(old.get("output_tokens"))
            growth_cache = totals.cached_tokens - _coerce_int(old.get("cached_tokens"))
            growth = {
                "input_tokens": max(0, growth_in),
                "output_tokens": max(0, growth_out),
                "cache_read_input_tokens": max(0, growth_cache),
            }
            if growth["input_tokens"] or growth["output_tokens"]:
                usage_history.append_model_call(
                    totals.model or "muse-spark-1.3",
                    input_tokens=growth["input_tokens"],
                    output_tokens=growth["output_tokens"],
                    cache_read_input_tokens=growth["cache_read_input_tokens"],
                    session_id=muse_session_id,
                    source="muse-tail",
                )
                lines += 1
            reported[muse_session_id] = {
                "input_tokens": totals.input_tokens,
                "output_tokens": totals.output_tokens,
                "cached_tokens": totals.cached_tokens,
                "events": totals.events,
            }
        try:
            directory.mkdir(parents=True, exist_ok=True)
            tmp = state_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(reported, sort_keys=True), encoding="utf-8")
            os.replace(tmp, state_path)
        except OSError:
            pass
        return lines
    except Exception:  # noqa: BLE001 — sampling must never break plan limits
        _logger.debug("muse token growth report failed", exc_info=True)
        return 0


# --------------------------------------------------------------------------- #
# Poller-side accumulator + forwarder (runner-owned).
# --------------------------------------------------------------------------- #


@dataclass
class _UsageAccumulator:
    """Cumulative session totals plus the event ids already counted.

    Muse reports PER-EVENT usage snapshots, not deltas — but each event id
    appears once in the append-only log, so summing per-event
    ``input_tokens`` (cache reads included) is the correct cumulative input.
    Dedup by record ``id`` so a re-read after a restart never double-counts.
    """

    totals: MuseUsageTotals = field(default_factory=MuseUsageTotals)
    offset: int = 0
    seen: set[str] = field(default_factory=set)

    def add_line(self, line: dict[str, object]) -> bool:
        """Fold one usage line in if unseen. Returns ``True`` when it counted."""
        event_id = line.get("event_id")
        if not isinstance(event_id, str) or event_id in self.seen:
            return False
        self.seen.add(event_id)
        self.totals.add_line(line)
        return True


def _read_usage_state(state_dir: Path, muse_session_id: str) -> _UsageAccumulator:
    """Load the persisted accumulator, or a cold zero default."""
    try:
        data = json.loads((state_dir / _state_name(muse_session_id)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _UsageAccumulator()
    if not isinstance(data, dict):
        return _UsageAccumulator()
    seen = data.get("seen")
    totals = MuseUsageTotals(
        input_tokens=_coerce_int(data.get("input_tokens")),
        output_tokens=_coerce_int(data.get("output_tokens")),
        reasoning_tokens=_coerce_int(data.get("reasoning_tokens")),
        cached_tokens=_coerce_int(data.get("cached_tokens")),
        cache_read_tokens=_coerce_int(data.get("cache_read_tokens")),
        model=data.get("model") if isinstance(data.get("model"), str) else None,
        events=_coerce_int(data.get("events")),
    )
    acc = _UsageAccumulator(totals=totals, offset=_coerce_int(data.get("offset")))
    acc.seen = {s for s in seen if isinstance(s, str)} if isinstance(seen, list) else set()
    return acc


def _state_name(muse_session_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in muse_session_id)
    return f"{_USAGE_STATE_FILE}.{safe or 'unknown'}"


def _write_usage_state(state_dir: Path, muse_session_id: str, acc: _UsageAccumulator) -> None:
    """Atomically persist the accumulator (tmp write + rename)."""
    state_dir.mkdir(parents=True, exist_ok=True)
    tmp = state_dir / (_state_name(muse_session_id) + ".tmp")
    tmp.write_text(
        json.dumps(
            {
                "input_tokens": acc.totals.input_tokens,
                "output_tokens": acc.totals.output_tokens,
                "reasoning_tokens": acc.totals.reasoning_tokens,
                "cached_tokens": acc.totals.cached_tokens,
                "cache_read_tokens": acc.totals.cache_read_tokens,
                "model": acc.totals.model,
                "events": acc.totals.events,
                "offset": acc.offset,
                "seen": sorted(acc.seen),
            }
        ),
        encoding="utf-8",
    )
    os.replace(tmp, state_dir / _state_name(muse_session_id))


def _read_new_text(path: Path, offset: int) -> tuple[str, int]:
    """Read bytes appended to *path* since *offset*; handles truncation."""
    try:
        size = path.stat().st_size
    except OSError:
        return "", offset
    if size < offset:  # rotated/truncated: start over (dedup by id still holds)
        offset = 0
    try:
        with open(path, "rb") as handle:
            handle.seek(offset)
            chunk = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return "", offset
    return chunk, offset + len(chunk.encode("utf-8", errors="replace"))


def _usage_post_body(acc: _UsageAccumulator) -> dict[str, object]:
    """Build the ``external_session_usage`` ``data`` payload from totals.

    Same contract as cursor-native: ``cumulative_input_tokens`` is INCLUSIVE
    of cache reads; the server splits ``cumulative_cache_read_input_tokens``
    back out and prices it at the cache-read rate. ``model`` lets the server
    price the tokens via the catalog.
    """
    data: dict[str, object] = {
        "cumulative_input_tokens": acc.totals.input_tokens,
        "cumulative_output_tokens": acc.totals.output_tokens,
        "cumulative_cache_read_input_tokens": acc.totals.cache_read_tokens,
    }
    if acc.totals.model:
        data["model"] = acc.totals.model
    return data


async def forward_muse_usage_to_session(
    *,
    base_url: str,
    headers: dict[str, str],
    session_id: str,
    muse_session_id: str,
    state_dir: Path,
    sessions_root: Path = DEFAULT_SESSIONS_ROOT,
    poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
    auth: HttpxAuth | None = None,
) -> None:
    """Tail a Muse session.jsonl and POST cumulative usage to the AP session.

    Each poll resolves the session file (cached after the first hit), reads
    bytes appended since the last offset, folds unseen events into the
    cumulative accumulator (deduped by record ``id``), and — when the totals
    advanced — POSTs an ``external_session_usage`` event. State (offset +
    totals + seen ids) is persisted to *state_dir* only after a successful
    POST, so a failed flush is retried and a supervisor restart resumes
    without re-counting. Never returns normally; cancel the task to stop it.
    """
    import httpx

    from omnigent.cli_auth import open_server_client

    acc = _read_usage_state(state_dir, muse_session_id)
    resolved: Path | None = None
    timeout = httpx.Timeout(_POST_TIMEOUT_S)
    async with open_server_client(base_url, headers=headers, auth=auth, timeout=timeout) as client:
        while True:
            try:
                if resolved is None or not resolved.exists():
                    resolved = await asyncio.to_thread(
                        session_file_for, muse_session_id, sessions_root
                    )
                if resolved is not None:
                    chunk, acc.offset = await asyncio.to_thread(
                        _read_new_text, resolved, acc.offset
                    )
                    changed = False
                    for line in parse_usage_records(chunk):
                        if acc.add_line(line):
                            changed = True
                    if changed:
                        resp = await client.post(
                            f"/v1/sessions/{session_id}/events",
                            json={
                                "type": "external_session_usage",
                                "data": _usage_post_body(acc),
                            },
                        )
                        resp.raise_for_status()
                        # Persist only after a successful POST so a failed
                        # flush is retried (unseen events stay unseen).
                        await asyncio.to_thread(
                            _write_usage_state, state_dir, muse_session_id, acc
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception(
                    "muse usage forwarder poll failed; session=%s muse_session=%s",
                    session_id,
                    muse_session_id,
                )
            await asyncio.sleep(poll_interval_s)


async def supervise_muse_usage_forwarder(
    *,
    base_url: str,
    headers: dict[str, str],
    session_id: str,
    muse_session_id: str,
    state_dir: Path,
    sessions_root: Path = DEFAULT_SESSIONS_ROOT,
    poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
    auth: HttpxAuth | None = None,
) -> None:
    """Run :func:`forward_muse_usage_to_session` under a restart supervisor.

    Mirrors ``supervise_cursor_usage_forwarder``: the poll loop swallows
    per-poll errors, but a crash in client setup would otherwise stop usage
    updates for the session. Restart with bounded exponential backoff;
    :class:`asyncio.CancelledError` propagates for clean teardown. Persisted
    offset + accumulator mean restarts resume exactly.
    """
    backoff_s = _SUPERVISOR_INITIAL_BACKOFF_S
    while True:
        run_started_at = time.monotonic()
        crash_exc: Exception | None = None
        try:
            await forward_muse_usage_to_session(
                base_url=base_url,
                headers=headers,
                session_id=session_id,
                muse_session_id=muse_session_id,
                state_dir=state_dir,
                sessions_root=sessions_root,
                poll_interval_s=poll_interval_s,
                auth=auth,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — supervisor restarts on any Exception
            crash_exc = exc
        if time.monotonic() - run_started_at >= _SUPERVISOR_HEALTHY_UPTIME_S:
            backoff_s = _SUPERVISOR_INITIAL_BACKOFF_S
        if crash_exc is not None:
            _logger.error(
                "muse usage forwarder crashed; restarting in %.1fs; session=%s",
                backoff_s,
                session_id,
                exc_info=crash_exc,
            )
        await asyncio.sleep(backoff_s)
        backoff_s = min(backoff_s * 2.0, _SUPERVISOR_MAX_BACKOFF_S)


def clear_muse_usage_state(state_dir: Path, muse_session_id: str) -> None:
    """Remove persisted forwarder state so a re-created lane starts clean."""
    with contextlib.suppress(OSError):
        (state_dir / _state_name(muse_session_id)).unlink()


def _main(argv: list[str] | None = None) -> int:
    """CLI: ``summarize`` prints totals JSON for a session file or id."""
    parser = argparse.ArgumentParser(prog="omnigent.muse_native_usage")
    sub = parser.add_subparsers(dest="command", required=True)
    summ = sub.add_parser("summarize", help="Print cumulative usage JSON.")
    summ.add_argument("--session-file", type=Path, default=None)
    summ.add_argument("--muse-session-id", default=None)
    summ.add_argument("--sessions-root", type=Path, default=DEFAULT_SESSIONS_ROOT)
    args = parser.parse_args(argv)
    if args.command == "summarize":
        path = args.session_file
        if path is None and args.muse_session_id:
            path = session_file_for(args.muse_session_id, args.sessions_root)
        if path is None:
            parser.error("give --session-file or a resolvable --muse-session-id")
            return 2
        totals = summarize_file(path)
        print(
            json.dumps(
                {
                    "input_tokens": totals.input_tokens,
                    "output_tokens": totals.output_tokens,
                    "reasoning_tokens": totals.reasoning_tokens,
                    "cached_tokens": totals.cached_tokens,
                    "cache_read_tokens": totals.cache_read_tokens,
                    "model": totals.model,
                    "events": totals.events,
                },
                sort_keys=True,
            )
        )
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
