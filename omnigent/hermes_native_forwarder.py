"""TUI→web forwarder for the hermes-native harness.

The ``omnigent hermes`` wrapper launches the real ``hermes`` TUI in a runner-owned
tmux pane, and :mod:`omnigent.hermes_native_bridge` injects web-UI messages into
it. That covers the web→TUI direction, but the *embedded terminal* is then the
only surface that reflects the agent's work — the Omnigent conversation view (chat
bubbles, title) stays empty because nothing mirrors the TUI's transcript back into
the session.

This module is that missing mirror — the Hermes analog of
:mod:`omnigent.goose_native_forwarder`. Hermes stores all sessions in a single
SQLite database at ``$HERMES_HOME/state.db`` (default ``~/.hermes/state.db``,
verified against the hermes-agent ``hermes_state.py`` schema): a ``sessions`` row
per session (``id`` TEXT, ``source``, ``cwd``, ``started_at`` REAL-seconds) and a
``messages`` row per turn (``id`` autoincrement, ``session_id`` FK, ``role``,
``content`` TEXT, ``active``).

Unlike goose-native, Hermes auto-generates its ``sessions.id`` and gives no
``--name`` to pin it, so discovery follows cursor-native instead: bind the newest
session whose ``cwd`` matches this terminal's workspace and whose ``started_at`` is
at/after the recorded launch time, with a claim guard so two hermes-native sessions
launched in the same cwd never mirror the same row into two conversations. We then
poll ``messages`` past a high-water ``id`` and POST new user/assistant rows as
``external_conversation_item`` events (which also seeds the session title).

To make the web render this harness's in-flight tool calls **live** (a spinner +
ticking elapsed timer, matching claude-/codex-native), the forwarder assigns one
``response_id`` per turn — ``hermes_turn_{opening-msg-id}`` shared across every row
of the turn — POSTs a ``running`` ``external_session_status`` edge carrying that id
at turn start, and stamps the turn's mirrored ``function_call`` items with the same
id (see :func:`_annotate_turn_actions`). The server keys the live card off a
``running`` edge whose ``response_id`` matches the items' ``response_id`` (#1874).

The forwarder deliberately does NOT take ``idle`` ownership: the runner's
PTY-activity watcher (see :mod:`omnigent.runner.app`) still emits the id-less
``running``/``idle`` ``session.status`` edges for hermes-native (as for
goose-/cursor-native), and the server pops the active response id on *any* ``idle``.
A silent tool (e.g. ``sleep``) leaves the pane quiet, so that watcher's ~1s idle
would settle a live card mid-turn — the forwarder therefore re-asserts the in-flight
turn's ``running`` each poll. The trade-off: an aborted turn whose terminal row is
never written is indistinguishable from a silent tool in the store, so its card
stays live until a terminal row lands (an interrupt's empty-prose assistant row
closes the turn) or the next user turn re-opens with a fresh id; the watcher's idle
settles the card only once nothing re-arms the id (turn closed, or this forwarder
died). That watcher drives only the web spinner, though — it never wakes a parent
orchestrator. So this forwarder additionally derives turn completion from the
message log (an ``assistant`` row with no ``tool_calls`` is the agentic loop's
terminal step) and POSTs an ``external_session_status: idle`` event once per
completed turn — the SAME server contract claude-/codex-/opencode-/cursor-native
use to mark a sub-agent turn terminal and wake its parent's inbox. The post is
deduped against a persisted posted-count (:mod:`omnigent.hermes_native_status`) so
a supervisor restart never re-wakes the parent for a turn it already reported.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path

import httpx

from omnigent import hermes_native_status
from omnigent.inner.native_attachments import ATTACHMENT_MARKER_STRIP_PATTERN

_logger = logging.getLogger(__name__)

#: Seconds between store polls. Hermes flushes a ``messages`` row per agentic step
#: (each assistant-text / tool-call cycle) as a turn progresses, so a snappier
#: sub-second cadence makes the mirrored chat track the terminal step-by-step.
#: 0.4s balances liveness vs. load.
_DEFAULT_POLL_INTERVAL_S = 0.4
_POST_TIMEOUT_S = 30.0

# Supervisor backoff (mirrors goose_native_forwarder.supervise_goose_forwarder).
_SUPERVISOR_INITIAL_BACKOFF_S = 1.0
_SUPERVISOR_MAX_BACKOFF_S = 30.0
_SUPERVISOR_HEALTHY_UPTIME_S = 60.0

#: Discovery tolerance (seconds): a session whose ``started_at`` is within this
#: many seconds *before* the recorded launch time still counts as this session's
#: row. Covers the small skew between the runner stamping ``launch_epoch_s`` and
#: Hermes writing the ``sessions`` row once the TUI initializes.
_DISCOVERY_SKEW_S = 10.0

_STATE_FILE = "hermes_forwarder.json"

#: Event type for a one-shot reasoning (thinking) mirror, matching the
#: codex-/opencode-native forwarders' transient reasoning contract.
_EXTERNAL_OUTPUT_REASONING_DELTA = "external_output_reasoning_delta"

# A sibling session's persisted claim (naming the same ``hermes_session_id``)
# counts as a LIVE owner only if its heartbeat was refreshed within this window;
# an older claim is treated as a dead session and may be taken over. Generous
# relative to the ~0.4s poll so a brief supervisor backoff never drops a claim.
_CLAIM_FRESH_MS = 30_000

# Sqlite read errors are swallowed in the helpers below (a live DB is briefly
# unreadable mid-checkpoint, so returning empty and retrying is correct). But a
# *persistent* error (schema drift, wrong path) would otherwise leave the chat
# view silently empty forever — so surface each distinct error string once.
_warned_sqlite_errors: set[str] = set()


def _warn_sqlite_once(context: str, exc: sqlite3.Error) -> None:
    """Log a distinct sqlite error at warning level once (dedup by message)."""
    key = f"{context}:{exc}"
    if key in _warned_sqlite_errors:
        return
    _warned_sqlite_errors.add(key)
    _logger.warning("hermes forwarder sqlite error during %s: %s", context, exc)


# The executor injects ``[Attached: <path>]`` (or the could-not-load marker
# from native_attachments) for web-UI attachments before pasting into the TUI;
# strip them from the mirrored bubble (internal bridge details).
_ATTACHMENT_MARKER_RE = re.compile(ATTACHMENT_MARKER_STRIP_PATTERN)

# Hermes injects skill content as a user message prefixed with this marker.
# The full skill prompt is not useful in the web UI — replace it with a
# short summary so the chat view stays clean.
_SKILL_INVOKE_RE = re.compile(
    r'^\[IMPORTANT: The user has invoked the "(?P<name>[^"]+)" skill',
)

#: Maximum characters for a tool output mirrored into the web UI chat view.
#: Longer outputs are truncated so skill loads and other verbose results don't
#: flood the conversation bubbles. The full output remains visible in the


def _read_model_from_hermes_config(bridge_dir: Path) -> str | None:
    """Best-effort read of the model name from the per-session HERMES_HOME config.

    Falls back to the user's ``~/.hermes/config.yaml`` if no per-session config
    exists. Returns ``None`` when the model cannot be determined.
    """
    candidates = [
        bridge_dir / "hermes_home" / "config.yaml",
        Path.home() / ".hermes" / "config.yaml",
    ]
    for config_path in candidates:
        if not config_path.is_file():
            continue
        try:
            import yaml

            data = yaml.safe_load(config_path.read_text()) or {}
            model = data.get("model")
            if isinstance(model, str) and model:
                return model
        except Exception:  # noqa: BLE001
            continue
    return None


class _HermesUsageTracker:
    """Post ``external_session_usage`` events for a hermes-native session.

    Hermes' SQLite ``state.db`` does not expose per-message token counts, so
    this tracker posts only the model name to the server — enough for the
    server to associate the model for display and (eventually) pricing.

    Follows the :class:`omnigent.codex_native_forwarder._SessionUsageCoalescer`
    pattern: deduplicates (only posts when the model changes) and is flushed
    from the poll loop.

    TODO: Token-level cost tracking (input_tokens, output_tokens, total_tokens)
    requires Hermes to expose usage data in its state.db or session transcript.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        session_id: str,
        bridge_dir: Path,
    ) -> None:
        self._client = client
        self._session_id = session_id
        self._bridge_dir = bridge_dir
        self._model: str | None = None
        self._posted_model: str | None = None

    async def flush(self) -> None:
        """Post the model name if it changed since the last flush."""
        if self._model is None:
            self._model = await asyncio.to_thread(_read_model_from_hermes_config, self._bridge_dir)
        if not self._model or self._model == self._posted_model:
            return
        try:
            resp = await self._client.post(
                f"/v1/sessions/{self._session_id}/events",
                json={
                    "type": "external_session_usage",
                    "data": {"model": self._model},
                },
            )
            if resp.status_code < 400:
                self._posted_model = self._model
            else:
                _logger.warning("hermes usage tracker POST failed: status=%s", resp.status_code)
        except httpx.HTTPError:
            _logger.debug("hermes usage tracker POST failed", exc_info=True)


def _hermes_home() -> Path:
    """Return Hermes' home dir for this process (``$HERMES_HOME`` or ``~/.hermes``)."""
    raw = os.environ.get("HERMES_HOME", "").strip()
    return Path(raw) if raw else Path.home() / ".hermes"


def default_state_db() -> Path:
    """Return Hermes' SQLite session store path for this process.

    Resolves to ``$HERMES_HOME/state.db`` (default ``~/.hermes/state.db``) the same
    way Hermes' own ``get_hermes_home()`` does, so the forwarder reads the exact
    DB the native TUI writes. Overridable via ``HERMES_STATE_DB`` (tests,
    non-standard installs).
    """
    override = os.environ.get("HERMES_STATE_DB", "").strip()
    if override:
        return Path(override)
    return _hermes_home() / "state.db"


@dataclass
class _ForwardState:
    """Durable forwarder cursor, persisted to ``bridge_dir/hermes_forwarder.json``.

    :param hermes_session_id: The resolved Hermes ``sessions.id`` being tailed, or
        ``None`` before one is discovered.
    :param last_id: Highest **fully** processed ``messages.id``: every item the row
        expanded to was forwarded, or the row was skipped. ``messages.id`` is
        autoincrement, so the high-water mark is sufficient dedup with O(1) state.
    :param partial_row_id: The ``messages.id`` of a row whose mirroring failed
        partway, or ``0`` when none is pending. One row expands to several items
        (reasoning, prose, a call per tool call), so ``last_id`` cannot advance until
        the last of them lands, or the row is skipped next poll and its undelivered
        items are lost. Named explicitly (rather than implied as "the row after
        ``last_id``") because compaction can soft-delete the row before the retry,
        in which case the offset must not be applied to some other row.
    :param partial_row_items: How many of *partial_row_id*'s items already posted.
        The row is re-read whole and this many leading items are dropped rather than
        posted twice.
    :param launch_epoch_s: This session's launch time (Unix seconds), used to
        scope discovery and to break ties when two sessions discover the same row:
        the earlier-launched (established) session keeps it. ``0.0`` for cold.
    :param heartbeat_ms: Wall-clock ms of the last persist. A sibling reads this
        to tell a live owner from a dead session's leftover claim. Stamped by
        :func:`_write_state`.
    :param active_turn_id: The per-turn ``response_id`` of the turn currently in
        flight (``hermes_turn_{opening-msg-id}``), or ``None`` between turns.
        Persisted so a turn that spans polls — or a forwarder restart mid-turn —
        keeps its id and does not re-emit a ``running`` edge (see
        :func:`_annotate_turn_actions`).
    """

    hermes_session_id: str | None = None
    last_id: int = 0
    partial_row_id: int = 0
    partial_row_items: int = 0
    launch_epoch_s: float = 0.0
    heartbeat_ms: int = 0
    active_turn_id: str | None = None
    abandoned_posted: str | None = None


def _read_state(bridge_dir: Path) -> _ForwardState:
    """Load the persisted forward cursor, or a cold default."""
    try:
        raw = (bridge_dir / _STATE_FILE).read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return _ForwardState()
    sid = data.get("hermes_session_id")
    last_id = data.get("last_id")
    partial_id = data.get("partial_row_id")
    partial_items = data.get("partial_row_items")
    # Both or neither: a partial row id without a positive item count (or vice
    # versa) is meaningless, and honoring half of it would drop or duplicate items.
    if not (
        isinstance(partial_id, int)
        and partial_id > 0
        and isinstance(partial_items, int)
        and partial_items > 0
    ):
        partial_id, partial_items = 0, 0
    launch_epoch_s = data.get("launch_epoch_s")
    heartbeat_ms = data.get("heartbeat_ms")
    active_turn_id = data.get("active_turn_id")
    abandoned_posted = data.get("abandoned_posted")
    return _ForwardState(
        hermes_session_id=sid if isinstance(sid, str) else None,
        last_id=last_id if isinstance(last_id, int) else 0,
        partial_row_id=partial_id,
        partial_row_items=partial_items,
        launch_epoch_s=float(launch_epoch_s) if isinstance(launch_epoch_s, (int, float)) else 0.0,
        heartbeat_ms=heartbeat_ms if isinstance(heartbeat_ms, int) else 0,
        active_turn_id=active_turn_id
        if isinstance(active_turn_id, str) and active_turn_id
        else None,
        abandoned_posted=abandoned_posted
        if isinstance(abandoned_posted, str) and abandoned_posted
        else None,
    )


def _write_state(bridge_dir: Path, state: _ForwardState) -> bool:
    """Atomically persist the forward cursor (tmp write + rename).

    :returns: ``True`` on success. A failure is logged and returns ``False`` — the
        in-memory cursor still guards against within-process re-posting.
    """
    try:
        bridge_dir.mkdir(parents=True, exist_ok=True)
        tmp = bridge_dir / (_STATE_FILE + ".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "hermes_session_id": state.hermes_session_id,
                    "last_id": state.last_id,
                    "partial_row_id": state.partial_row_id,
                    "partial_row_items": state.partial_row_items,
                    "launch_epoch_s": state.launch_epoch_s,
                    "active_turn_id": state.active_turn_id,
                    "abandoned_posted": state.abandoned_posted,
                    # Stamp the heartbeat at persist time so every poll refreshes
                    # the session claim; a peer treats a claim older than
                    # ``_CLAIM_FRESH_MS`` as a dead session it may take over.
                    "heartbeat_ms": int(time.time() * 1000),
                }
            ),
            encoding="utf-8",
        )
        os.replace(tmp, bridge_dir / _STATE_FILE)
        return True
    except OSError:
        _logger.warning(
            "hermes forwarder could not persist state to %s", bridge_dir, exc_info=True
        )
        return False


def clear_hermes_bridge_state(bridge_dir: Path) -> None:
    """Remove the persisted forward cursor so a re-created terminal starts clean."""
    with contextlib.suppress(OSError):
        (bridge_dir / _STATE_FILE).unlink()


def _session_claimed_by_other(
    bridge_dir: Path, hermes_session_id: str, my_launch_s: float
) -> bool:
    """Whether another LIVE session is already mirroring *hermes_session_id*.

    Two hermes-native sessions launched in the same cwd can momentarily discover
    the same newest ``sessions`` row before each binds its own — without this
    guard both would mirror it into two conversations. A sibling bridge dir under
    the same root claims the row when its persisted state names the same
    ``hermes_session_id`` with a heartbeat fresher than ``_CLAIM_FRESH_MS``. Ties
    resolve toward the EARLIER-launched session (then the lexicographically smaller
    bridge-dir name) for a deterministic, symmetric verdict.

    :param bridge_dir: This session's bridge dir (its parent is the shared root).
    :param hermes_session_id: The Hermes session id this session would mirror.
    :param my_launch_s: This session's ``launch_epoch_s``.
    :returns: ``True`` if a different live session owns the row.
    """
    root = bridge_dir.parent
    if not root.is_dir():
        return False
    now_ms = int(time.time() * 1000)
    me = bridge_dir.name
    for sibling in root.iterdir():
        if sibling.name == me or not sibling.is_dir():
            continue
        other = _read_state(sibling)
        if other.hermes_session_id != hermes_session_id:
            continue
        if now_ms - other.heartbeat_ms > _CLAIM_FRESH_MS:
            continue  # stale claim — the owning session is gone; ignore it
        if other.launch_epoch_s < my_launch_s:
            return True
        if other.launch_epoch_s == my_launch_s and sibling.name < me:
            return True
    return False


def _connect_ro(db_path: Path) -> sqlite3.Connection | None:
    """Open *db_path* read-only in a way that reads the live WAL, or ``None``.

    ``mode=ro`` (not ``immutable=1``) so a live session's ``-wal`` sidecar is read
    via the ``-shm``; a plain connection is the fallback for the rare window where
    ``-shm`` is momentarily absent. Only SELECTs are issued.
    """
    for uri, use_uri in ((f"file:{db_path}?mode=ro", True), (str(db_path), False)):
        try:
            if use_uri:
                return sqlite3.connect(uri, timeout=5.0, uri=True)
            return sqlite3.connect(uri, timeout=5.0)
        except sqlite3.Error:
            continue
    return None


def _table_columns(con: sqlite3.Connection, table: str) -> frozenset[str]:
    """Return the set of column names on *table*, or empty on any error.

    Hermes' ``state.db`` schema drifts across versions (e.g. schema_version 11
    dropped ``sessions.cwd`` and ``messages.active`` / ``messages.compacted``
    that older builds carried). The forwarder introspects the live schema and
    adapts its SELECTs instead of assuming a fixed column set — otherwise a
    single ``no such column`` error aborts discovery / mirroring and the chat
    goes silent even though Hermes itself is working (visible in the terminal).
    """
    try:
        return frozenset(str(r[1]) for r in con.execute(f"PRAGMA table_info({table})"))
    except sqlite3.Error:
        return frozenset()


def _discover_session_id(
    db_path: Path,
    workspace: str,
    launch_epoch_s: float,
    *,
    excluded: frozenset[str] = frozenset(),
) -> str | None:
    """Return this terminal's Hermes ``sessions.id``, or ``None`` if not yet present.

    Hermes can't be told its session id in advance, so we bind the newest session
    created at/after this terminal's launch (minus a small skew). A row whose
    ``cwd`` matches the terminal's workspace wins outright (the reliable case); if
    none match cwd we fall back to the newest qualifying row only when EXACTLY ONE
    qualifies — never guessing among multiple, so a concurrent session in another
    workspace can't be mirrored by mistake. Rows in *excluded* (already claimed by
    a live sibling) are skipped.

    :param db_path: The Hermes ``state.db`` to read.
    :param workspace: The terminal's working directory (realpath-normalized).
    :param launch_epoch_s: Wall-clock seconds when this terminal launched.
    :param excluded: Hermes session ids already claimed by a live sibling.
    :returns: The matching ``sessions.id``, or ``None``.
    """
    con = _connect_ro(db_path)
    if con is None:
        return None
    floor_s = launch_epoch_s - _DISCOVERY_SKEW_S
    # Newer Hermes (schema_version >= 11) no longer records ``sessions.cwd``.
    # Select it only when present; otherwise treat every row as cwd-less and
    # rely on the started_at-since-launch fallback below (the newest lone
    # session started after this terminal launched is this terminal's session).
    has_cwd = "cwd" in _table_columns(con, "sessions")
    try:
        if has_cwd:
            rows = con.execute(
                "SELECT id, cwd FROM sessions WHERE started_at >= ? ORDER BY started_at DESC",
                (floor_s,),
            ).fetchall()
        else:
            rows = [
                (sid, None)
                for (sid,) in con.execute(
                    "SELECT id FROM sessions WHERE started_at >= ? ORDER BY started_at DESC",
                    (floor_s,),
                ).fetchall()
            ]
    except sqlite3.Error as exc:
        _warn_sqlite_once("session discovery", exc)
        return None
    finally:
        con.close()
    candidates = [
        (sid, cwd) for sid, cwd in rows if isinstance(sid, str) and sid and sid not in excluded
    ]
    # Reliable case: a row whose cwd matches the workspace. Newest (rows are
    # already started_at DESC) wins.
    for sid, cwd in candidates:
        if isinstance(cwd, str) and cwd and _same_path(cwd, workspace):
            return sid
    # Fallback ONLY when Hermes recorded no cwd at all for any candidate (older
    # builds / unusual backends): bind a lone candidate. We never bind a row whose
    # cwd is a *different* real dir — unlike cursor's md5-hashed dirs, Hermes
    # stores the plain path, so a cwd mismatch is a genuine "not my session".
    if all(not (isinstance(cwd, str) and cwd) for _sid, cwd in candidates):
        if len(candidates) == 1:
            return candidates[0][0]
    return None


def _discover_child_session(db_path: Path, parent_session_id: str) -> str | None:
    """Return the newest Hermes session whose parent is *parent_session_id*.

    Hermes auto-compresses by ending the current session and forking a CHILD
    (``sessions.parent_session_id`` points at the old id; present in hermes
    v0.17.0 state.db). The forwarder pins one id for life, so after compaction it
    keeps polling the dead parent and the chat goes silent. Re-discover the
    newest child so the mirror re-pins. Mirrors :func:`_discover_session_id`'s
    read-only connect + swallow-and-warn handling; returns ``None`` on any error.
    """
    con = _connect_ro(db_path)
    if con is None:
        return None
    try:
        row = con.execute(
            "SELECT id FROM sessions WHERE parent_session_id = ? ORDER BY started_at DESC LIMIT 1",
            (parent_session_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        _warn_sqlite_once("child discovery", exc)
        return None
    finally:
        con.close()
    if row and isinstance(row[0], str) and row[0]:
        return row[0]
    return None


def _same_path(a: str, b: str) -> bool:
    """Return whether two filesystem paths resolve to the same realpath."""
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except OSError:
        return a == b


@dataclass
class _MirrorItem:
    """One conversation item ready to POST, plus the message id that produced it."""

    msg_id: int
    item_type: str
    item_data: dict[str, object]
    response_id: str
    #: The source ``messages`` row role ("user"/"assistant"/"tool"). Carried so a
    #: row that yields no renderable item (a sentinel, ``item_type == ""``) still
    #: exposes its role to turn detection — an empty-prose ``assistant`` terminal
    #: row must still close the turn (see :func:`_mirror_item_role`).
    role: str | None = None


def _message_to_items(
    msg_id: int,
    role: object,
    content: object,
    tool_calls: object,
    tool_call_id: object,
    tool_name: object,  # noqa: ARG001 — reserved for future use (e.g. logging)
    reasoning_content: object,
    reasoning: object,
    agent_name: str,
) -> list[_MirrorItem]:
    """Convert one ``messages`` row to mirror items.

    An assistant row with reasoning emits a one-shot
    ``external_output_reasoning_delta`` item first, then a ``function_call``
    item per call, followed by a ``message`` item if it also has prose content.
    A tool row emits a ``function_call_output`` item. Returns an empty list to
    skip.
    """
    if not isinstance(role, str):
        return []
    text = ""
    if isinstance(content, str):
        text = _ATTACHMENT_MARKER_RE.sub("", content).strip()
    response_id = f"hermes:{msg_id}"

    if role == "user":
        if not text:
            return []
        # Hermes injects skill content as a user message — replace with
        # a short summary so the chat view stays readable.
        skill_match = _SKILL_INVOKE_RE.match(text)
        if skill_match:
            text = f"/{skill_match.group('name')}"
        return [
            _MirrorItem(
                msg_id=msg_id,
                item_type="message",
                item_data={"role": "user", "content": [{"type": "input_text", "text": text}]},
                response_id=response_id,
            )
        ]

    if role == "assistant":
        items: list[_MirrorItem] = []
        # Hermes persists completed reasoning rows rather than deltas, so mirror
        # the first available reasoning field as one event before the response.
        thinking = ""
        for raw in (reasoning_content, reasoning):
            if isinstance(raw, str):
                stripped = _ATTACHMENT_MARKER_RE.sub("", raw).strip()
                if stripped:
                    thinking = stripped
                    break
        if thinking:
            items.append(
                _MirrorItem(
                    msg_id=msg_id,
                    item_type=_EXTERNAL_OUTPUT_REASONING_DELTA,
                    item_data={"delta": thinking, "started": True},
                    response_id=response_id,
                )
            )
        # Emit the prose FIRST, then the tool calls. An assistant row's text is
        # the model's preamble ("I'll run X…") that precedes the calls it makes
        # in the same step, so the natural order is message → function_call(s).
        # It also matters for live rendering: the web only shows the running
        # spinner on the TRAILING tool phase, so a message emitted AFTER the
        # calls would leave the in-flight tool non-trailing (no spinner) until
        # its output lands.
        if text:
            items.append(
                _MirrorItem(
                    msg_id=msg_id,
                    item_type="message",
                    item_data={
                        "role": "assistant",
                        "agent": agent_name,
                        "content": [{"type": "output_text", "text": text}],
                    },
                    response_id=response_id,
                )
            )
        # Parse tool_calls JSON — assistant rows may include tool call requests.
        if isinstance(tool_calls, str) and tool_calls:
            try:
                calls = json.loads(tool_calls)
            except (json.JSONDecodeError, ValueError):
                calls = []
            if isinstance(calls, list):
                for call in calls:
                    if not isinstance(call, dict):
                        continue
                    call_id = call.get("call_id") or call.get("id") or ""
                    func = call.get("function", {})
                    name = func.get("name", "") if isinstance(func, dict) else ""
                    arguments = func.get("arguments", "{}") if isinstance(func, dict) else "{}"
                    if call_id and name:
                        items.append(
                            _MirrorItem(
                                msg_id=msg_id,
                                item_type="function_call",
                                item_data={
                                    "agent": agent_name,
                                    "name": name,
                                    "arguments": arguments,
                                    "call_id": call_id,
                                },
                                response_id=response_id,
                            )
                        )
        return items

    if role == "tool":
        # Tool result row — emit function_call_output.
        if isinstance(tool_call_id, str) and tool_call_id:
            output = text or ""
            return [
                _MirrorItem(
                    msg_id=msg_id,
                    item_type="function_call_output",
                    item_data={"call_id": tool_call_id, "output": output},
                    response_id=response_id,
                )
            ]
        return []

    return []


def _read_new_items(
    db_path: Path, hermes_session_id: str, last_id: int, agent_name: str
) -> list[_MirrorItem]:
    """Read ``messages`` rows with ``id > last_id`` for this session as items.

    A skipped row (tool/system/empty/inactive) still advances the cursor via a
    sentinel item so it is never reconsidered.
    """
    con = _connect_ro(db_path)
    if con is None:
        return []
    # ``messages.active`` (compaction soft-delete flag) was dropped in newer
    # Hermes schemas; only filter on it when present.
    cols = _table_columns(con, "messages")
    active_filter = " AND active = 1" if "active" in cols else ""
    # ``reasoning_content`` / ``reasoning`` and the bookkeeping markers were added
    # in newer schemas; SELECT them only when present so a v11 (or other older) DB
    # does not raise ``no such column``.
    optional = [c for c in ("reasoning_content", "reasoning", *_BOOKKEEPING_COLUMNS) if c in cols]
    optional_sql = "".join(f", {c}" for c in optional)
    try:
        rows = con.execute(
            "SELECT id, role, content, tool_calls, tool_call_id, tool_name "
            f"{optional_sql} "
            "FROM messages "
            f"WHERE session_id = ? AND id > ?{active_filter} ORDER BY id",
            (hermes_session_id, last_id),
        ).fetchall()
    except sqlite3.Error as exc:
        _warn_sqlite_once("message read", exc)
        return []
    finally:
        con.close()
    items: list[_MirrorItem] = []
    for row in rows:
        msg_id, role, content, tool_calls_json, tool_call_id, tool_name_val = row[:6]
        extra = dict(zip(optional, row[6:], strict=True))
        # A compaction summary or hidden empty step is not the model's answer:
        # never mirror it as a reply, and never let it close the turn.
        bookkeeping = (
            role == "assistant"
            and not _assistant_row_has_tool_calls(tool_calls_json)
            and _is_bookkeeping_row(
                content, extra.get("_compressed_summary"), extra.get("display_kind")
            )
        )
        converted = (
            []
            if bookkeeping
            else _message_to_items(
                msg_id,
                role,
                content,
                tool_calls_json,
                tool_call_id,
                tool_name_val,
                extra.get("reasoning_content"),
                extra.get("reasoning"),
                agent_name,
            )
        )
        if converted:
            items.extend(converted)
        else:
            # A skipped row (empty/tool/system) still advances the cursor via a
            # sentinel; carry its role so turn detection can still see, e.g., an
            # empty-prose ``assistant`` terminal row and close the turn.
            items.append(
                _MirrorItem(
                    msg_id=msg_id,
                    item_type="",
                    item_data={},
                    response_id="",
                    role=None if bookkeeping or not isinstance(role, str) else role,
                )
            )
    return items


def _drop_delivered_prefix(
    items: list[_MirrorItem], row_id: int, delivered: int
) -> list[_MirrorItem]:
    """Drop the *delivered* leading items of row *row_id* from a re-read batch.

    A row whose mirroring failed partway is re-read whole so its undelivered items
    still land; its already-posted prefix is removed here so they are not mirrored
    twice. Items of other rows pass through untouched, so if *row_id* is gone from
    the batch (compaction soft-deleted it before the retry) this is a no-op rather
    than trimming some other row.
    """
    kept: list[_MirrorItem] = []
    seen = 0
    for it in items:
        if it.msg_id != row_id:
            kept.append(it)
            continue
        # Count position within the row rather than compare items: two items of one
        # row can be equal (identical repeated tool calls), so identity is the index.
        # A row shorter than the recorded offset (schema/parse change) drops entirely:
        # re-posting a delivered item duplicates it, which no later poll can undo.
        if seen >= delivered:
            kept.append(it)
        seen += 1
    return kept


@dataclass
class _TurnAction:
    """One ordered step when mirroring a poll batch.

    ``kind`` is ``"running"`` (POST a ``running`` status edge) or ``"item"`` (POST
    a mirrored conversation item). ``turn_id_after`` is the turn id still active
    once this step is applied — persisted after each step so a turn that spans
    polls (or a forwarder restart mid-turn) keeps its id. ``last_of_row`` marks the
    final item of a ``msg_id`` group, the only point at which the row is fully
    mirrored and the cursor may advance past it.
    """

    kind: str
    msg_id: int
    turn_id_after: str | None
    response_id: str | None = None
    item: _MirrorItem | None = None
    last_of_row: bool = False


def _mirror_item_role(item: _MirrorItem) -> str | None:
    """Return the source-row role of a mirror item for turn detection.

    A ``message`` item reads it from ``item_data``; a sentinel (``item_type ==
    ""``, produced for a row that yields no renderable item) reads the row role
    carried on the item — so an empty-prose ``assistant`` terminal row is still
    seen as an assistant row and closes the turn. Other item types (function
    calls / outputs) return ``None``; ``has_function_call`` covers those.
    """
    if item.item_type == "message":
        role = item.item_data.get("role")
        return role if isinstance(role, str) else None
    if item.item_type == "":
        return item.role
    return None


def _annotate_turn_actions(
    items: list[_MirrorItem], active_turn_id: str | None
) -> tuple[list[_TurnAction], str | None]:
    """Assign a per-turn ``response_id`` to a poll batch and interleave ``running``
    edges at turn starts; return the ordered actions and the turn id still active
    after the batch.

    A Hermes turn is ``user -> (assistant+tool_calls -> tool)* ->
    assistant-without-tool_calls``. Rows arrive append-only in ``id`` order and each
    ``messages`` row is a single role, so items are grouped by ``msg_id``:

    - a ``user`` group **opens** a turn → mint ``hermes_turn_{msg_id}`` and emit a
      ``running`` edge before its items;
    - assistant activity while no turn is active also mints one (missed-start
      recovery — e.g. a forwarder that starts mid-turn), so its cards still go live;
    - every mirrored item is re-stamped with the active turn id so the web renders
      the turn's tool-call cards live against the ``running`` edge;
    - an ``assistant`` group with **no** ``function_call`` item is the terminal step
      → clear the id after it.

    The ``idle`` edge is intentionally NOT emitted here: the completed-turn idle
    post settles the card when the turn closes. A turn that never writes a terminal
    row (some aborts) keeps its id active — the poll loop's ``running`` re-assert
    holds the card live until the next turn replaces the id (see module docstring).
    """
    actions: list[_TurnAction] = []
    for msg_id, group_iter in groupby(items, key=lambda it: it.msg_id):
        group = list(group_iter)
        has_function_call = any(it.item_type == "function_call" for it in group)
        roles = {_mirror_item_role(it) for it in group}
        opens = "user" in roles
        is_assistant = "assistant" in roles
        terminal = is_assistant and not has_function_call

        if opens or (active_turn_id is None and (has_function_call or is_assistant)):
            active_turn_id = f"hermes_turn_{msg_id}"
            actions.append(
                _TurnAction("running", msg_id, active_turn_id, response_id=active_turn_id)
            )

        for ix, it in enumerate(group):
            if active_turn_id is not None:
                it.response_id = active_turn_id
            actions.append(
                _TurnAction(
                    "item",
                    msg_id,
                    active_turn_id,
                    item=it,
                    last_of_row=ix == len(group) - 1,
                )
            )

        if terminal:
            active_turn_id = None
            if actions:
                actions[-1].turn_id_after = None

    return actions, active_turn_id


def _assistant_row_has_tool_calls(tool_calls: object) -> bool:
    """Whether an assistant ``messages`` row carries a non-empty ``tool_calls`` list.

    Hermes writes one ``messages`` row per agentic step (complete, append-only —
    rows are never updated in place, which is why message mirroring keys off
    ``id > last_id``). An assistant row with one or more tool calls means the loop
    continues (a tool result + further assistant step follow); a row with no tool
    calls is the loop's terminal step — the model returning its final answer.
    Mirrors the ``tool_calls`` parsing in :func:`_message_to_items`.
    """
    if not isinstance(tool_calls, str) or not tool_calls.strip():
        return False
    try:
        calls = json.loads(tool_calls)
    except (json.JSONDecodeError, ValueError):
        return False
    return isinstance(calls, list) and len(calls) > 0


#: Content prefixes of the summary Hermes writes when it compacts its context.
_COMPACTION_SUMMARY_PREFIXES = ("[CONTEXT COMPACTION", "[CONTEXT SUMMARY")

#: Optional ``messages`` columns that mark a row as Hermes bookkeeping.
_BOOKKEEPING_COLUMNS = ("_compressed_summary", "display_kind")


def _is_bookkeeping_row(
    content: object, compressed_summary: object = None, display_kind: object = None
) -> bool:
    """Whether an assistant row is Hermes bookkeeping rather than the model's answer.

    A compaction writes its summary as an assistant row without tool calls, and an
    interrupted step leaves a hidden empty one. Both look like a final answer, so
    counting them told a parent orchestrator the worker had finished mid-task.
    The content prefix covers schemas that lack the marker columns.
    """
    if compressed_summary not in (None, 0, "0", ""):
        return True
    if display_kind == "hidden":
        return True
    return isinstance(content, str) and content.lstrip().startswith(_COMPACTION_SUMMARY_PREFIXES)


def _count_completed_turns(
    db_path: Path, hermes_session_id: str, max_id: int | None = None
) -> int:
    """Count completed turns for *hermes_session_id* (0 on unreadable/empty).

    A completed turn is an ``assistant`` row with no ``tool_calls`` — the agentic
    loop's terminal step (see :func:`_assistant_row_has_tool_calls`) — that is not
    Hermes bookkeeping (see :func:`_is_bookkeeping_row`). Rows are
    counted regardless of the ``active`` flag: Hermes soft-deletes on compaction
    (sets ``active = 0``) rather than deleting rows, so ignoring it keeps the
    count monotonic and append-only — the dedup baseline can then only grow, never
    drop below the posted-count and falsely re-arm an idle post for an old turn.

    With *max_id*, only rows at or below that id are counted. The idle check
    passes the mirror's high-water mark here so a terminal row that lands while
    a batch is still being POSTed cannot be counted — and ring the parent-waking
    idle edge — before the row itself has been mirrored.
    """
    con = _connect_ro(db_path)
    if con is None:
        return 0
    marker_cols = [c for c in _BOOKKEEPING_COLUMNS if c in _table_columns(con, "messages")]
    select = ", ".join(["tool_calls", "content", *marker_cols])
    query = f"SELECT {select} FROM messages WHERE session_id = ? AND role = 'assistant'"
    params: tuple[object, ...] = (hermes_session_id,)
    if max_id is not None:
        query += " AND id <= ?"
        params = (hermes_session_id, max_id)
    try:
        rows = con.execute(query + " ORDER BY id", params).fetchall()
    except sqlite3.Error as exc:
        _warn_sqlite_once("turn-end count", exc)
        return 0
    finally:
        con.close()
    completed = 0
    for row in rows:
        markers = dict(zip(marker_cols, row[2:], strict=True))
        if _assistant_row_has_tool_calls(row[0]):
            continue
        if _is_bookkeeping_row(
            row[1], markers.get("_compressed_summary"), markers.get("display_kind")
        ):
            continue
        completed += 1
    return completed


def abandoned_turn_reason(log_text: str) -> str | None:
    """Report why the newest turn in *log_text* ended without an answer.

    Scans the agent log text for the turn-end markers Hermes writes and returns
    the verdict of the NEWEST one: ``None`` for a healthy
    ``Turn ended: reason=text_response``, the bare word for any other
    ``Turn ended: reason=<word>``, ``"api_error: <msg>"`` for
    ``API call failed after <n> retries. <msg>`` (msg trimmed at the first
    `` | ``), and ``"hermes_exited"`` for ``CLI cleanup calling memory
    shutdown``. The shutdown marker is logged on every exit — including turns
    that failed for a concrete reason — so it only wins when no more specific
    marker is present.
    """
    reason = None
    newest = -1
    hermes_exited = False
    for match in re.finditer(r"Turn ended: reason=(\w+)", log_text):
        if match.start() <= newest:
            continue
        newest = match.start()
        reason = None if match.group(1) == "text_response" else match.group(1)
    for match in re.finditer(
        r"API call failed after \d+ retries\. ([^\n|]*)", log_text
    ):
        if match.start() <= newest:
            continue
        newest = match.start()
        reason = f"api_error: {match.group(1).strip()}"
    if "CLI cleanup calling memory shutdown" in log_text:
        hermes_exited = True
    if reason is None and newest == -1 and hermes_exited:
        return "hermes_exited"
    return reason


def read_agent_log_tail(bridge_dir: Path, max_bytes: int = 65536) -> str:
    """Return the last *max_bytes* of the agent log under *bridge_dir*.

    Reads ``bridge_dir / "hermes_home" / "logs" / "agent.log"`` tail-first and
    returns ``""`` for a missing or unreadable log. Never raises.
    """
    log_path = bridge_dir / "hermes_home" / "logs" / "agent.log"
    try:
        with log_path.open("rb") as handle:
            try:
                size = handle.seek(0, 2)
                start = max(0, size - max_bytes)
                handle.seek(start)
                return handle.read(max_bytes).decode(errors="replace")
            except OSError:
                return ""
    except OSError:
        return ""


async def _post_external_session_status(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    status: str,
    response_id: str | None = None,
) -> None:
    """POST one ``external_session_status`` event to the Sessions API.

    For a sub-agent conversation the server maps an ``idle`` edge to a terminal
    completion that wakes the parent orchestrator's inbox — the SAME contract
    claude-/codex-/opencode-/cursor-native use. The runner's PTY-activity watcher
    emits only a web-spinner ``session.status`` edge for hermes-native and never
    wakes a parent, which is why this explicit post is required.

    When *response_id* is given (the turn's ``hermes_turn_{id}``), the edge carries
    it: a ``running`` edge marks that response id active so the web renders the
    turn's tool-call cards live, and a clean-close ``idle`` names the card to settle
    (an id-less idle is a no-op on the web while a response is still streaming). An
    ``idle`` with no id still resolves via the server popping the active id and the
    snapshot refetch — the abort / turn-spanned-a-prior-batch path.

    :raises httpx.HTTPError: If the Omnigent request fails or is rejected.
    """
    data: dict[str, object] = {"status": status}
    if response_id is not None:
        data["response_id"] = response_id
    resp = await client.post(
        f"/v1/sessions/{session_id}/events",
        json={"type": "external_session_status", "data": data},
    )
    resp.raise_for_status()


async def _post_conversation_item(
    client: httpx.AsyncClient, *, session_id: str, item: _MirrorItem
) -> None:
    """POST one mirrored item as the appropriate session event.

    Reasoning items post a transient ``external_output_reasoning_delta`` (the
    web finalizes the block when the assistant message lands); all others post
    an ``external_conversation_item``.
    """
    if item.item_type == _EXTERNAL_OUTPUT_REASONING_DELTA:
        resp = await client.post(
            f"/v1/sessions/{session_id}/events",
            json={
                "type": _EXTERNAL_OUTPUT_REASONING_DELTA,
                "data": item.item_data,
            },
        )
        resp.raise_for_status()
        return
    resp = await client.post(
        f"/v1/sessions/{session_id}/events",
        json={
            "type": "external_conversation_item",
            "data": {
                "item_type": item.item_type,
                "item_data": item.item_data,
                "response_id": item.response_id,
            },
        },
    )
    resp.raise_for_status()


def _has_new_compaction(db_path: Path, hermes_session_id: str) -> bool:
    """Check if hermes has compacted messages for this session."""
    con = _connect_ro(db_path)
    if con is None:
        return False
    # ``messages.compacted`` was dropped in newer Hermes schemas; without it we
    # cannot detect a compaction boundary here (safe: no boundary item posted).
    if "compacted" not in _table_columns(con, "messages"):
        con.close()
        return False
    try:
        row = con.execute(
            "SELECT 1 FROM messages WHERE session_id = ? AND compacted = 1 LIMIT 1",
            (hermes_session_id,),
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False
    finally:
        con.close()


async def _persist_hermes_compaction_item(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    db_path: Path,
    hermes_session_id: str,
) -> None:
    """Persist a compaction boundary item with post-compaction messages."""
    resp = await client.get(
        f"/v1/sessions/{session_id}/items",
        params={"limit": 1, "order": "desc"},
    )
    resp.raise_for_status()
    items = resp.json().get("data", [])
    last_item_id = items[0]["id"] if items else f"compact_boundary_{session_id}"

    compacted_messages = None
    con = _connect_ro(db_path)
    if con is not None:
        _active = " AND active = 1" if "active" in _table_columns(con, "messages") else ""
        try:
            rows = con.execute(
                f"SELECT role, content FROM messages WHERE session_id = ?{_active} ORDER BY id",
                (hermes_session_id,),
            ).fetchall()
            msgs = []
            for role, content in rows:
                if role in ("user", "assistant") and content:
                    block_type = "input_text" if role == "user" else "output_text"
                    msgs.append(
                        {
                            "type": "message",
                            "role": role,
                            "content": [{"type": block_type, "text": content}],
                        }
                    )
            if msgs:
                compacted_messages = msgs
        except sqlite3.Error as exc:
            _warn_sqlite_once("compaction read", exc)
        finally:
            con.close()

    data: dict[str, object] = {
        "summary": "[Hermes compaction — context was compacted via /compress]",
        "last_item_id": last_item_id,
        "model": "unknown",
        "token_count": 0,
    }
    if compacted_messages:
        data["compacted_messages"] = compacted_messages

    resp = await client.post(
        f"/v1/sessions/{session_id}/events",
        json={"type": "compaction", "data": data},
    )
    resp.raise_for_status()


async def forward_hermes_store_to_session(
    *,
    base_url: str,
    headers: dict[str, str],
    session_id: str,
    bridge_dir: Path,
    agent_name: str,
    workspace: str,
    launch_epoch_s: float,
    db_path: Path | None = None,
    poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
    auth: httpx.Auth | None = None,
) -> None:
    """Tail Hermes' session store and mirror new messages into the AP session.

    Discovers this session's Hermes ``sessions.id`` (newest row whose ``cwd``
    matches *workspace* and ``started_at`` is at/after ``launch_epoch_s``), then
    polls its ``messages`` rows, posting each new user/assistant row as an
    ``external_conversation_item``. The high-water ``id`` is persisted to
    ``bridge_dir`` so a supervisor restart resumes without re-posting.

    :param base_url: Omnigent server base URL.
    :param headers: Static HTTP headers (auth normally via ``auth``).
    :param session_id: Omnigent session/conversation id.
    :param bridge_dir: The hermes-native bridge dir (holds the persisted cursor).
    :param agent_name: Agent label stamped on mirrored assistant items.
    :param workspace: The session's working directory (Hermes' ``sessions.cwd``).
    :param launch_epoch_s: Wall-clock seconds when this terminal launched.
    :param db_path: Hermes state DB; defaults to :func:`default_state_db`.
    :param poll_interval_s: Seconds between store polls.
    :param auth: Optional refresh-capable httpx Auth for remote deployments.
    :returns: Never normally returns; cancel the task to stop it.
    """
    db = db_path or default_state_db()
    persisted = _read_state(bridge_dir)
    hermes_session_id: str | None = persisted.hermes_session_id
    last_id = persisted.last_id if hermes_session_id is not None else 0
    # A row whose mirroring failed partway, and how many of its items already
    # posted; both ``0`` when ``last_id`` is a clean fully-mirrored high-water mark.
    # Only meaningful alongside ``last_id``, so all three reset together.
    partial_row_id = persisted.partial_row_id if hermes_session_id is not None else 0
    partial_row_items = persisted.partial_row_items if hermes_session_id is not None else 0
    # The turn currently in flight (its shared ``response_id``), threaded through
    # every ``_write_state`` so it survives polls / a restart. Reset whenever the
    # tailed hermes session changes (discovery, claim-yield, compaction re-pin).
    active_turn_id: str | None = (
        persisted.active_turn_id if hermes_session_id is not None else None
    )
    # The abandonment reason whose parent-wake idle was already posted (or None).
    # Threading it through every ``_write_state`` keeps the once-only guarantee
    # across polls and forwarder restarts.
    abandoned_posted: str | None = (
        persisted.abandoned_posted if hermes_session_id is not None else None
    )
    # Track whether we have already PATCHed the external_session_id to the
    # Omnigent server so we do it at most once per forwarder lifetime.
    _external_id_synced = False
    timeout = httpx.Timeout(_POST_TIMEOUT_S)
    from omnigent.cli_auth import open_server_client

    async with open_server_client(base_url, headers=headers, auth=auth, timeout=timeout) as client:
        usage_tracker = _HermesUsageTracker(client, session_id, bridge_dir)
        compaction_persisted = False
        while True:
            try:
                if hermes_session_id is None:
                    resolved = await asyncio.to_thread(
                        _discover_session_id, db, workspace, launch_epoch_s
                    )
                    if resolved is not None and not await asyncio.to_thread(
                        _session_claimed_by_other, bridge_dir, resolved, launch_epoch_s
                    ):
                        hermes_session_id = resolved
                        resuming = persisted.hermes_session_id == resolved
                        last_id = persisted.last_id if resuming else 0
                        partial_row_id = persisted.partial_row_id if resuming else 0
                        partial_row_items = persisted.partial_row_items if resuming else 0
                        # Discovery only (re)binds on a cold start or a
                        # claim-yield / compaction re-pin reacquire — never the
                        # mid-turn restart-resume case, which keeps its session
                        # pinned and skips this block. So always start turn
                        # tracking fresh here; restoring the one-shot ``persisted``
                        # snapshot could resurrect a stale turn id on reacquire.
                        active_turn_id = None
                        _write_state(
                            bridge_dir,
                            _ForwardState(
                                hermes_session_id=resolved,
                                last_id=last_id,
                                partial_row_id=partial_row_id,
                                partial_row_items=partial_row_items,
                                launch_epoch_s=launch_epoch_s,
                                active_turn_id=active_turn_id,
                                abandoned_posted=abandoned_posted,
                            ),
                        )
                # PATCH the external_session_id once so the server
                # knows which Hermes session backs this conversation
                # (needed for fork/resume).
                if hermes_session_id is not None and not _external_id_synced:
                    try:
                        resp = await client.patch(
                            f"/v1/sessions/{session_id}",
                            json={"external_session_id": hermes_session_id},
                        )
                        resp.raise_for_status()
                        _external_id_synced = True
                    except httpx.HTTPError:
                        _logger.debug(
                            "hermes forwarder failed to PATCH external_session_id; "
                            "will retry next poll; session=%s",
                            session_id,
                            exc_info=True,
                        )
                if hermes_session_id is not None:
                    # Yield to an earlier-launched live session rather than mirror
                    # the same row into a second conversation; re-discover next poll.
                    if await asyncio.to_thread(
                        _session_claimed_by_other, bridge_dir, hermes_session_id, launch_epoch_s
                    ):
                        _logger.warning(
                            "hermes session %s already mirrored by another session; "
                            "pausing mirror for session=%s",
                            hermes_session_id,
                            session_id,
                        )
                        hermes_session_id = None
                        active_turn_id = None
                    else:
                        # ``last_id`` is the last row whose items ALL posted, so a row
                        # that failed partway is re-read here. Drop the items of it
                        # that already posted: the item POST carries no idempotency
                        # key, so a replay would duplicate them in the conversation.
                        items = await asyncio.to_thread(
                            _read_new_items, db, hermes_session_id, last_id, agent_name
                        )
                        # Dropping every item leaves the cursor parked until a newer
                        # row lands, which is correct: there is nothing left to
                        # deliver for it, and the next row restarts the count.
                        if partial_row_id:
                            items = _drop_delivered_prefix(
                                items, partial_row_id, partial_row_items
                            )
                        # Assign a per-turn response_id and interleave ``running``
                        # edges at turn starts; items are re-stamped in place so
                        # the turn's tool-call cards render live on the web.
                        turn_actions, active_turn_id = _annotate_turn_actions(
                            items, active_turn_id
                        )
                        # The response_id of the last turn that closed in this
                        # batch (its terminal step clears ``turn_id_after``). Fed
                        # to the completed-turn ``idle`` post below so the web
                        # settles that exact card deterministically — an id-less
                        # idle is a no-op while a response is still streaming.
                        closed_turn_id: str | None = None
                        # Whether this batch already posted a ``running`` edge (turn
                        # open), so the in-flight re-assert below doesn't duplicate it.
                        running_posted_this_batch = False
                        for action in turn_actions:
                            if action.kind == "running":
                                if action.response_id is not None:
                                    running_posted_this_batch = True
                                    # Best-effort: the running edge only makes the
                                    # turn's cards render live. If it fails, mirroring
                                    # (and the idle/PTY-watcher resolution) must still
                                    # proceed — never abort the turn for a live-card post.
                                    try:
                                        await _post_external_session_status(
                                            client,
                                            session_id=session_id,
                                            status="running",
                                            response_id=action.response_id,
                                        )
                                    except Exception:  # noqa: BLE001 — live-card edge is best-effort
                                        _logger.debug(
                                            "hermes forwarder running-edge post failed; "
                                            "cards may not go live; session=%s",
                                            session_id,
                                            exc_info=True,
                                        )
                                # A running edge mirrors no message row, so it must
                                # NOT advance the ``last_id`` cursor. The opening
                                # group's item action (same msg_id, next iteration)
                                # advances it only AFTER its row is POSTed — so a
                                # crash in the window re-reads the opening row on
                                # restart instead of skipping it.
                                continue
                            if action.item is not None and action.item.item_type:
                                await _post_conversation_item(
                                    client, session_id=session_id, item=action.item
                                )
                            if (
                                action.turn_id_after is None
                                and action.item is not None
                                and action.item.response_id
                            ):
                                closed_turn_id = action.item.response_id
                            # A row expands to several items, so the cursor may only
                            # advance once the LAST one lands. Until then record how
                            # far into the row we got: a POST that fails on a later
                            # item then resumes inside the row on the next poll,
                            # instead of ``last_id`` moving past it and the rest of
                            # its items being skipped forever.
                            if action.last_of_row:
                                last_id = action.msg_id
                                partial_row_id = 0
                                partial_row_items = 0
                            else:
                                # Count from 1 on a row we were not already inside.
                                # A partial row can disappear before its retry
                                # (compaction soft-deletes it, and the re-pin that
                                # resets these is skipped when the session has no
                                # child), so carrying its count into the next row
                                # would over-drop that row's items as delivered.
                                if partial_row_id != action.msg_id:
                                    partial_row_items = 0
                                partial_row_id = action.msg_id
                                partial_row_items += 1
                            _write_state(
                                bridge_dir,
                                _ForwardState(
                                    hermes_session_id=hermes_session_id,
                                    last_id=last_id,
                                    partial_row_id=partial_row_id,
                                    partial_row_items=partial_row_items,
                                    launch_epoch_s=launch_epoch_s,
                                    active_turn_id=action.turn_id_after,
                                    abandoned_posted=abandoned_posted,
                                ),
                            )
                        if not compaction_persisted and await asyncio.to_thread(
                            _has_new_compaction, db, hermes_session_id
                        ):
                            try:
                                await _persist_hermes_compaction_item(
                                    client,
                                    session_id=session_id,
                                    db_path=db,
                                    hermes_session_id=hermes_session_id,
                                )
                                compaction_persisted = True
                            except Exception:  # noqa: BLE001
                                _logger.warning(
                                    "Failed to persist hermes compaction item for %s",
                                    session_id,
                                    exc_info=True,
                                )
                            # Compaction ends this Hermes session and forks a
                            # child (parent_session_id chain). Re-pin to the
                            # newest child so the mirror follows the live session
                            # instead of polling the dead parent forever. Done at
                            # most once per compaction (compaction_persisted resets
                            # to False for the child, which carries no compacted
                            # rows yet); if no child exists we stay on the parent.
                            try:
                                child = await asyncio.to_thread(
                                    _discover_child_session, db, hermes_session_id
                                )
                                if child is not None and not await asyncio.to_thread(
                                    _session_claimed_by_other, bridge_dir, child, launch_epoch_s
                                ):
                                    hermes_session_id = child
                                    last_id = 0
                                    partial_row_id = 0
                                    partial_row_items = 0
                                    active_turn_id = None
                                    compaction_persisted = False
                                    _external_id_synced = False
                                    # The idle dedup baseline is per-terminal but
                                    # the completed-turn count is per
                                    # hermes_session_id; the child restarts its
                                    # count near 0, so rebase the baseline to the
                                    # child's current count. Without this the guard
                                    # `completed_turns > posted_count` stays False
                                    # until the child exceeds the parent's total,
                                    # suppressing idle posts for the child's first
                                    # turns — a worker that compacts then finishes
                                    # would never wake its parent.
                                    await asyncio.to_thread(
                                        hermes_native_status.write_posted_count,
                                        bridge_dir,
                                        await asyncio.to_thread(_count_completed_turns, db, child),
                                    )
                                    _write_state(
                                        bridge_dir,
                                        _ForwardState(
                                            hermes_session_id=child,
                                            last_id=0,
                                            partial_row_id=0,
                                            partial_row_items=0,
                                            launch_epoch_s=launch_epoch_s,
                                            active_turn_id=None,
                                            abandoned_posted=None,
                                        ),
                                    )
                                    continue
                            except Exception:  # noqa: BLE001
                                _logger.warning(
                                    "hermes forwarder failed to re-pin to child session "
                                    "after compaction; staying on %s; session=%s",
                                    hermes_session_id,
                                    session_id,
                                    exc_info=True,
                                )
                        # Post model/usage data after mirroring messages.
                        await usage_tracker.flush()
                        # Re-assert ``running`` for a turn still in flight that did
                        # not open this batch. Hermes leaves the tmux pane quiet
                        # during a silent tool (e.g. ``sleep``), so the runner's
                        # PTY-activity watcher fires an id-less ``idle`` after ~1s
                        # and the server pops the turn's active_response_id — which
                        # would let a snapshot refetch settle the live card early.
                        # Re-posting the turn's ``running`` each poll (0.4s) re-arms
                        # that id well inside the 1s window, so the card stays live
                        # until the real terminal step. Best-effort: a live-card edge
                        # never blocks mirroring.
                        if active_turn_id is not None and not running_posted_this_batch:
                            try:
                                await _post_external_session_status(
                                    client,
                                    session_id=session_id,
                                    status="running",
                                    response_id=active_turn_id,
                                )
                            except Exception:  # noqa: BLE001 — live-card edge is best-effort
                                _logger.debug(
                                    "hermes forwarder running re-assert failed; "
                                    "card may settle early; session=%s",
                                    session_id,
                                    exc_info=True,
                                )
                        # Refresh the claim heartbeat every poll (even with no new
                        # items) so an idle owner keeps its claim.
                        _write_state(
                            bridge_dir,
                            _ForwardState(
                                hermes_session_id=hermes_session_id,
                                last_id=last_id,
                                partial_row_id=partial_row_id,
                                partial_row_items=partial_row_items,
                                launch_epoch_s=launch_epoch_s,
                                active_turn_id=active_turn_id,
                                abandoned_posted=abandoned_posted,
                            ),
                        )
                        # Turn each newly-completed turn into an
                        # ``external_session_status: idle`` edge — the signal that
                        # wakes a parent orchestrator (the PTY watcher's spinner
                        # status never does). A completed turn is an assistant row
                        # with no tool_calls (the agentic loop's terminal step);
                        # posted only AFTER its messages are mirrored above so the
                        # parent sees the content before the completion — the
                        # count is bounded by the mirrored high-water mark, so a
                        # terminal row landing while this poll's batch was being
                        # POSTed waits for the next poll to mirror it. Deduped
                        # against a persisted posted-count so a supervisor restart
                        # never re-wakes the parent for a turn it already reported.
                        # Best-effort: a failed post raises into the outer handler
                        # and leaves the count unadvanced, so the next poll retries.
                        completed_turns = await asyncio.to_thread(
                            _count_completed_turns, db, hermes_session_id, last_id
                        )
                        if completed_turns > await asyncio.to_thread(
                            hermes_native_status.read_posted_count, bridge_dir
                        ):
                            # Carry the closed turn's response_id when this batch
                            # observed the terminal step, so the web settles that
                            # card deterministically (matching codex-native). A
                            # retry where the terminal row landed in a prior batch
                            # has no id here and posts id-less — the PTY watcher's
                            # idle (which pops the active id server-side) plus the
                            # snapshot refetch still resolve it, as on abort.
                            await _post_external_session_status(
                                client,
                                session_id=session_id,
                                status="idle",
                                response_id=closed_turn_id,
                            )
                            await asyncio.to_thread(
                                hermes_native_status.write_posted_count,
                                bridge_dir,
                                completed_turns,
                            )
                            # A healthy completed turn resolves any earlier
                            # abandonment, so the next one can still wake the
                            # parent instead of being deduped against a stale
                            # reason.
                            if abandoned_posted is not None:
                                abandoned_posted = None
                                _write_state(
                                    bridge_dir,
                                    _ForwardState(
                                        hermes_session_id=hermes_session_id,
                                        last_id=last_id,
                                        partial_row_id=partial_row_id,
                                        partial_row_items=partial_row_items,
                                        launch_epoch_s=launch_epoch_s,
                                        active_turn_id=active_turn_id,
                                        abandoned_posted=None,
                                    ),
                                )
                        else:
                            # No NEW completed turn. If the log shows the in-flight
                            # turn was abandoned (an error marker rather than a
                            # terminal step), wake the parent exactly once for it:
                            # a stuck turn never lands a terminal row, so the guard
                            # above would otherwise stay silent forever and the
                            # orchestrator would hang. The reason is persisted so
                            # later polls (or a forwarder restart) do not repeat
                            # the wake; a different reason later still posts.
                            reason = abandoned_turn_reason(
                                read_agent_log_tail(bridge_dir)
                            )
                            if reason is not None and reason != abandoned_posted:
                                await _post_external_session_status(
                                    client,
                                    session_id=session_id,
                                    status="idle",
                                    response_id=closed_turn_id,
                                )
                                abandoned_posted = reason
                                _write_state(
                                    bridge_dir,
                                    _ForwardState(
                                        hermes_session_id=hermes_session_id,
                                        last_id=last_id,
                                        partial_row_id=partial_row_id,
                                        partial_row_items=partial_row_items,
                                        launch_epoch_s=launch_epoch_s,
                                        active_turn_id=active_turn_id,
                                        abandoned_posted=abandoned_posted,
                                    ),
                                )
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception(
                    "hermes forwarder poll failed; session=%s hermes_session=%s",
                    session_id,
                    hermes_session_id,
                )
            await asyncio.sleep(poll_interval_s)


def _supervisor_monotonic() -> float:
    """Indirection so tests can stub the supervisor's clock."""
    return time.monotonic()


async def _supervisor_sleep(seconds: float) -> None:
    """Indirection so tests can stub the supervisor's backoff sleep."""
    await asyncio.sleep(seconds)


async def supervise_hermes_forwarder(
    *,
    base_url: str,
    headers: dict[str, str],
    session_id: str,
    bridge_dir: Path,
    agent_name: str,
    workspace: str,
    launch_epoch_s: float,
    db_path: Path | None = None,
    poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
    auth: httpx.Auth | None = None,
) -> None:
    """Run :func:`forward_hermes_store_to_session` under a restart supervisor.

    Mirrors :func:`omnigent.goose_native_forwarder.supervise_goose_forwarder`:
    bounded exponential backoff, :class:`asyncio.CancelledError` propagates for
    clean teardown, and the persisted ``id`` cursor means restarts resume exactly
    where they left off.

    :returns: Never normally returns; cancel the task to stop it.
    """
    backoff_s = _SUPERVISOR_INITIAL_BACKOFF_S
    while True:
        run_started_at = _supervisor_monotonic()
        crash_exc: Exception | None = None
        try:
            await forward_hermes_store_to_session(
                base_url=base_url,
                headers=headers,
                session_id=session_id,
                bridge_dir=bridge_dir,
                agent_name=agent_name,
                workspace=workspace,
                launch_epoch_s=launch_epoch_s,
                db_path=db_path,
                poll_interval_s=poll_interval_s,
                auth=auth,
            )
            _logger.warning(
                "hermes forwarder returned unexpectedly; restarting; session=%s bridge_dir=%s",
                session_id,
                bridge_dir,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — supervisor restarts on any Exception
            crash_exc = exc
        if _supervisor_monotonic() - run_started_at >= _SUPERVISOR_HEALTHY_UPTIME_S:
            backoff_s = _SUPERVISOR_INITIAL_BACKOFF_S
        if crash_exc is not None:
            _logger.error(
                "hermes forwarder crashed; restarting in %.1fs; session=%s bridge_dir=%s",
                backoff_s,
                session_id,
                bridge_dir,
                exc_info=crash_exc,
            )
        await _supervisor_sleep(backoff_s)
        backoff_s = min(backoff_s * 2.0, _SUPERVISOR_MAX_BACKOFF_S)
