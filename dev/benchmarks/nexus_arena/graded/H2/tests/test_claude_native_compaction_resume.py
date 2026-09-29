"""H2: a ``/compact`` whose completion hook arrives before Claude writes it.

Omnigent keeps its own copy of every Claude compaction (a ``compaction``
event whose ``compacted_messages`` is the context Claude continues from), and
rebuilds Claude's transcript from those copies. Claude fires its
``SessionStart source=compact`` hook, then writes the compacted transcript
about a second later. Whatever the forwarder records for that compaction must
end up being the compacted context, not the history Claude just dropped.

These tests drive the real forwarder loop against a real Claude-format
transcript read by the real ``claude_agent_sdk.get_session_messages``; only
the Omnigent server is faked. They assert on what Omnigent last stored for the
compaction, never on how or when the forwarder gets there.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from omnigent.claude_native_bridge import record_hook_event
from omnigent.claude_native_forwarder import forward_claude_transcript_to_session

pytestmark = pytest.mark.asyncio

SID = "5b0f3c1e-8d2a-4c47-9e61-2f7d0a9c4b11"
CONV = "conv_h2"
PREAMBLE = (
    "This session is being continued from a previous conversation that ran out "
    "of context. The summary below covers the earlier portion of the conversation."
)
NEW_SUMMARY = f"{PREAMBLE}\n\nNEW-SUMMARY-7f3a: the user is tidying a notes app."
OLD_SUMMARY = f"{PREAMBLE}\n\nOLD-SUMMARY-19c2: the user started the notes app."
OLD_TURNS = 60
#: How long Claude takes, after the completion hook, to write the compaction.
CLAUDE_WRITE_DELAY_S = 1.0
#: How long any correct forwarder may take to store the compacted context.
STORE_DEADLINE_S = 12.0


@pytest.fixture(autouse=True)
def _bridge_under_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Treat each test's temp dir as the Claude bridge root."""
    monkeypatch.setattr("omnigent.claude_native_bridge._TRUSTED_PARENT", tmp_path)
    monkeypatch.setattr("omnigent.claude_native_bridge._BRIDGE_ROOT", tmp_path)


@pytest.fixture
def claude_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A private Claude config dir; returns the session's transcript path."""
    config = tmp_path / "claude-config"
    project = config / "projects" / "-work-notes"
    project.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    return project / f"{SID}.jsonl"


# --- a fake Omnigent server -------------------------------------------------


class _Omnigent:
    """Records every request; answers the few reads the forwarder makes."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                del format, args

            def _record(self, status: int, payload: dict[str, Any]) -> None:
                length = int(self.headers.get("Content-Length", "0") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else None
                except ValueError:
                    body = None
                with outer._lock:
                    outer.requests.append(
                        {"method": self.command, "path": self.path, "body": body}
                    )
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                if "/items" in self.path:
                    self._record(200, {"data": [{"id": "item_last"}], "has_more": False})
                else:
                    self._record(200, {"id": CONV, "labels": {}})

            def do_POST(self) -> None:
                self._record(200, {})

            def do_PATCH(self) -> None:
                self._record(200, {})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base_url = f"http://{host}:{port}"

    def events(self, event_type: str) -> list[dict[str, Any]]:
        with self._lock:
            found = list(self.requests)
        return [
            r["body"]
            for r in found
            if r["method"] == "POST"
            and isinstance(r["body"], dict)
            and r["body"].get("type") == event_type
        ]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5.0)


# --- Claude-format transcript records ------------------------------------------


def _stamp(when: float) -> str:
    """Claude's transcript timestamp format, e.g. ``2026-09-21T18:26:05.123Z``."""
    moment = datetime.fromtimestamp(when, timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def _uuid(tag: int, n: int) -> str:
    return f"{tag:08x}-0000-4000-8000-{n:012d}"


def _user(uid: str, parent: str | None, content: str, when: float, **extra: Any) -> dict:
    return {
        "parentUuid": parent,
        "isSidechain": False,
        "userType": "external",
        "cwd": "/work/notes",
        "sessionId": SID,
        "version": "2.1.260",
        "type": "user",
        "message": {"role": "user", "content": content},
        "uuid": uid,
        "timestamp": _stamp(when),
        **extra,
    }


def _assistant(uid: str, parent: str, text: str, when: float, n: int) -> dict:
    return {
        "parentUuid": parent,
        "isSidechain": False,
        "userType": "external",
        "cwd": "/work/notes",
        "sessionId": SID,
        "version": "2.1.260",
        "message": {
            "id": f"msg_old_{n:04d}",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 3, "output_tokens": 12},
        },
        "requestId": f"req_old_{n:04d}",
        "type": "assistant",
        "uuid": uid,
        "timestamp": _stamp(when),
    }


def _boundary(uid: str, logical_parent: str, when: float, pre: int, post: int) -> dict:
    return {
        "parentUuid": None,
        "logicalParentUuid": logical_parent,
        "isSidechain": False,
        "type": "system",
        "subtype": "compact_boundary",
        "content": "Conversation compacted",
        "isMeta": False,
        "timestamp": _stamp(when),
        "uuid": uid,
        "level": "info",
        "compactMetadata": {"trigger": "manual", "preTokens": pre, "postTokens": post},
        "sessionId": SID,
    }


def _summary(uid: str, parent: str, text: str, when: float) -> dict:
    return _user(uid, parent, text, when, isVisibleInTranscriptOnly=True, isCompactSummary=True)


def _write(path: Path, records: list[dict], mode: str = "w") -> None:
    with path.open(mode, encoding="utf-8") as handle:
        handle.write("".join(json.dumps(r) + "\n" for r in records))


def _old_history(start: float, *, after_earlier_compaction: bool) -> list[dict]:
    """The session before ``/compact``: many turns, optionally after a past compaction."""
    records: list[dict] = []
    parent: str | None = None
    if after_earlier_compaction:
        # A compaction from the day before: its boundary and summary head the chain.
        records.append(_boundary(_uuid(0xB0, 0), _uuid(0xA0, 0), start - 86_400, 598_000, 9_100))
        records.append(_summary(_uuid(0xB0, 1), _uuid(0xB0, 0), OLD_SUMMARY, start - 86_400))
        parent = _uuid(0xB0, 1)
    for n in range(OLD_TURNS):
        when = start + n
        if n % 2 == 0:
            text = f"old-turn-{n}: please look at file {n}"
            record = _user(_uuid(0xC0, n), parent, text, when)
        else:
            text = f"old-turn-{n}: done with it"
            record = _assistant(_uuid(0xC0, n), parent or "", text, when, n)
        records.append(record)
        parent = record["uuid"]
    records.append(_user(_uuid(0xC0, OLD_TURNS), parent, "/compact", start + OLD_TURNS))
    return records


def _claude_writes_the_compaction(transcript: Path, last_uuid: str) -> None:
    """What Claude appends once its own compaction is done."""
    now = time.time()
    _write(
        transcript,
        [
            _boundary(_uuid(0xD0, 0), last_uuid, now, 611_000, 7_400),
            _summary(_uuid(0xD0, 1), _uuid(0xD0, 0), NEW_SUMMARY, now),
        ],
        mode="a",
    )


# --- what counts as the compacted context ----------------------------------------


def _texts(message: Any) -> list[str]:
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [
            str(block.get("text"))
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        ]
    return []


def _is_the_compacted_context(event: dict[str, Any]) -> bool:
    """The stored compaction resumes from the new summary and nothing older."""
    messages = (event.get("data") or {}).get("compacted_messages")
    if not isinstance(messages, list) or not messages:
        return False
    if "NEW-SUMMARY-7f3a" not in " ".join(_texts(messages[0])):
        return False
    everything = " ".join(t for m in messages for t in _texts(m))
    return "old-turn-" not in everything and "OLD-SUMMARY-19c2" not in everything


def _describe(events: list[dict[str, Any]]) -> str:
    out = []
    for event in events:
        messages = (event.get("data") or {}).get("compacted_messages")
        if not isinstance(messages, list):
            out.append("compaction without compacted_messages")
            continue
        head = " ".join(_texts(messages[0]))[:60] if messages else ""
        out.append(f"{len(messages)} messages, first={head!r}")
    return "; ".join(out) or "no compaction stored"


# --- the scenario ------------------------------------------------------------------


async def _compact_with_hook_first(
    tmp_path: Path, transcript: Path, *, after_earlier_compaction: bool
) -> list[dict[str, Any]]:
    """Run the forwarder through one ``/compact`` whose hook beats the transcript.

    :returns: Every compaction event the forwarder stored, in order.
    """
    history = _old_history(time.time() - 3_600, after_earlier_compaction=after_earlier_compaction)
    _write(transcript, history)
    bridge_dir = tmp_path / "bridge"
    base = {"session_id": SID, "transcript_path": str(transcript), "cwd": "/work/notes"}
    record_hook_event(bridge_dir, {"hook_event_name": "SessionStart", "source": "startup", **base})

    server = _Omnigent()
    task = asyncio.create_task(
        forward_claude_transcript_to_session(
            base_url=server.base_url,
            headers={},
            session_id=CONV,
            bridge_dir=bridge_dir,
            agent_name="claude-native-ui",
            start_at_end=False,
            poll_interval_s=0.02,
        )
    )
    try:
        loop = asyncio.get_running_loop()
        # The forwarder mirrors the session up to its last turn...
        caught_up_by = loop.time() + 10.0
        while loop.time() < caught_up_by and not any(
            "old-turn-59" in json.dumps(e) for e in server.events("external_conversation_item")
        ):
            await asyncio.sleep(0.02)
        assert any(
            "old-turn-59" in json.dumps(e) for e in server.events("external_conversation_item")
        ), "the forwarder never mirrored the session's history"
        # ...then the user runs /compact. Claude announces the start, compacts,
        # and announces the end; the transcript still holds the old history
        # for about another second after that.
        pre_compact = {"hook_event_name": "PreCompact", "trigger": "manual", **base}
        record_hook_event(bridge_dir, pre_compact)
        await asyncio.sleep(0.3)
        compacted = {"hook_event_name": "SessionStart", "source": "compact", **base}
        record_hook_event(bridge_dir, compacted)
        # Let the forwarder reach the completion hook (it reports the
        # compaction's status to the UI when it does), then Claude writes.
        seen_by = loop.time() + 3.0
        while loop.time() < seen_by and not any(
            (e.get("data") or {}).get("status") == "completed"
            for e in server.events("external_compaction_status")
        ):
            await asyncio.sleep(0.02)
        await asyncio.sleep(CLAUDE_WRITE_DELAY_S)
        _claude_writes_the_compaction(transcript, history[-1]["uuid"])

        deadline = loop.time() + STORE_DEADLINE_S
        while loop.time() < deadline:
            if any(_is_the_compacted_context(e) for e in server.events("compaction")):
                break
            await asyncio.sleep(0.05)
        # Anything stored after the good copy would win on the next rebuild.
        await asyncio.sleep(1.5)
        return server.events("compaction")
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        server.close()


async def test_a_compaction_announced_before_claude_writes_it_stores_the_compacted_context(
    tmp_path: Path, claude_home: Path
) -> None:
    """``/compact`` took the session from ~611k to ~7.4k tokens; the stored
    copy must be what Claude continues from, not the 60 turns it dropped."""
    stored = await _compact_with_hook_first(tmp_path, claude_home, after_earlier_compaction=False)

    assert stored, "no compaction was stored for the /compact"
    assert _is_the_compacted_context(stored[-1]), (
        "the last stored compaction is not the compacted context: " + _describe(stored)
    )


async def test_a_second_compaction_is_not_mistaken_for_the_first(
    tmp_path: Path, claude_home: Path
) -> None:
    """The session was already compacted once (yesterday), so its history
    already starts with a summary. The new ``/compact`` must still store the
    new summary's context, not the old summary plus everything after it."""
    stored = await _compact_with_hook_first(tmp_path, claude_home, after_earlier_compaction=True)

    assert stored, "no compaction was stored for the /compact"
    assert _is_the_compacted_context(stored[-1]), (
        "the last stored compaction is not the compacted context: " + _describe(stored)
    )
