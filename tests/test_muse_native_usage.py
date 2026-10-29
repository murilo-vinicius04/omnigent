"""Unit tests for muse-native token-usage capture.

Covers the pure pieces no live Muse session is needed for: unpacking both
session.jsonl envelope shapes (direct record vs ``children[].record_json``),
reading ONLY ``payload.event.usage`` (the sibling ``record.quantity`` repeats
the same counts), the cumulative accumulator (per-event sum + record-id
dedup), state round-trip, the ``external_session_usage`` POST shape, the
summarize CLI, and the poll loop (POST-on-change, no-repost-when-unchanged,
persist-only-after-success).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from pathlib import Path

import httpx
import pytest

from omnigent import muse_native_usage as usage


def _direct_line(
    event_id: str,
    *,
    muse_session_id: str = "muse-1",
    model: str = "muse-spark-1.3",
    in_tokens: int = 100,
    out_tokens: int = 10,
    cached: int = 60,
    sequence: int = 1,
) -> str:
    return json.dumps(
        {
            "id": "outer-" + event_id,
            "stream": {"kind": "session", "id": muse_session_id},
            "sequence": sequence,
            "recorded_at": 1792673775053723,
            "payload": {
                "event": {
                    "kind": "model_usage",
                    "model": model,
                    "usage": {
                        "input_tokens": in_tokens,
                        "output_tokens": out_tokens,
                        "reasoning_tokens": 2,
                        "cached_tokens": cached,
                        "cache_read_tokens": cached,
                        "cache_write_tokens": 0,
                    },
                    # Same counts repeated: must NOT be billed a second time.
                    "record": {
                        "quantity": {
                            "input_tokens": in_tokens,
                            "output_tokens": out_tokens,
                            "reasoning_tokens": 2,
                            "cached_tokens": cached,
                        }
                    },
                }
            },
        }
    )


def _wrapped_line(event_id: str, **kwargs: object) -> str:
    inner = json.loads(_direct_line(event_id, **kwargs))  # type: ignore[arg-type]
    inner["id"] = event_id
    return json.dumps(
        {
            "retained_frame": "session_log",
            "outer_log_ordinal": 7,
            "children": [{"child_index": 0, "record_json": json.dumps(inner)}],
        }
    )


class TestNormalizeUsageRecord:
    def test_extracts_usage_fields(self) -> None:
        line = usage.normalize_usage_record(json.loads(_direct_line("e1")))
        assert line is not None
        assert line["event_id"] == "outer-e1"
        assert line["model"] == "muse-spark-1.3"
        assert line["input_tokens"] == 100
        assert line["output_tokens"] == 10
        assert line["cache_read_tokens"] == 60
        assert line["muse_session_id"] == "muse-1"

    def test_unpacks_children_record_json(self) -> None:
        lines = usage.parse_usage_records(_wrapped_line("e9"))
        assert len(lines) == 1 and lines[0]["event_id"] == "e9"

    def test_ignores_quantity_sibling(self) -> None:
        totals = usage.summarize_text(_direct_line("e1"))
        assert totals.input_tokens == 100  # not 200
        assert totals.events == 1

    def test_skips_without_id(self) -> None:
        record = json.loads(_direct_line("e1"))
        del record["id"]
        assert usage.normalize_usage_record(record) is None

    def test_skips_without_usage(self) -> None:
        assert usage.normalize_usage_record({"id": "x"}) is None

    def test_skips_zero_tokens(self) -> None:
        record = json.loads(_direct_line("e1", in_tokens=0, out_tokens=0))
        assert usage.normalize_usage_record(record) is None

    def test_non_dict_is_skipped(self) -> None:
        assert usage.normalize_usage_record("nope") is None

    def test_coerces_and_floors_negative(self) -> None:
        line = usage.normalize_usage_record(
            {
                "id": "e",
                "payload": {
                    "event": {"model": "m", "usage": {"input_tokens": "7", "output_tokens": -3}}
                },
            }
        )
        assert line is not None
        assert line["input_tokens"] == 7
        assert line["output_tokens"] == 0


class TestParseUsageRecords:
    def test_both_shapes_and_junk(self) -> None:
        text = (
            _direct_line("e1")
            + "\n\n"
            + "not json\n"
            + _wrapped_line("e2")
            + "\n"
            + json.dumps({"id": "idle-snapshot"})  # no usage: skipped
            + "\n"
        )
        lines = usage.parse_usage_records(text)
        assert [line["event_id"] for line in lines] == ["outer-e1", "e2"]

    def test_empty_is_empty(self) -> None:
        assert usage.parse_usage_records("") == []


class TestSummarize:
    def test_dedups_by_event_id(self) -> None:
        text = _direct_line("e1") + "\n" + _direct_line("e1") + "\n"
        totals = usage.summarize_text(text)
        assert totals.input_tokens == 100
        assert totals.events == 1

    def test_sums_across_events(self) -> None:
        totals = usage.summarize_text(_direct_line("e1") + "\n" + _direct_line("e2"))
        assert totals.input_tokens == 200
        assert totals.output_tokens == 20
        assert totals.cached_tokens == 120
        assert totals.events == 2
        assert totals.model == "muse-spark-1.3"

    def test_missing_file_is_zeros(self, tmp_path: Path) -> None:
        totals = usage.summarize_file(tmp_path / "nope.jsonl")
        assert (totals.input_tokens, totals.events) == (0, 0)


class TestCollect:
    def _write_session(self, root: Path, day: str, muse_session_id: str, *event_ids: str) -> None:
        session_dir = root / day / muse_session_id
        session_dir.mkdir(parents=True)
        (session_dir / "session.jsonl").write_text(
            "\n".join(_direct_line(e, muse_session_id=muse_session_id) for e in event_ids) + "\n"
        )

    def test_groups_by_stream_id(self, tmp_path: Path) -> None:
        self._write_session(tmp_path, "2026/10/22", "muse-a", "e1", "e2")
        self._write_session(tmp_path, "2026/10/22", "muse-b", "e3")
        totals = usage.collect_muse_usage(tmp_path)
        assert set(totals) == {"muse-a", "muse-b"}
        assert totals["muse-a"].input_tokens == 200
        assert totals["muse-b"].events == 1

    def test_missing_root_is_empty(self, tmp_path: Path) -> None:
        assert usage.collect_muse_usage(tmp_path / "absent") == {}


class TestSessionFileFor:
    def test_finds_by_directory_name(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "2026" / "muse-9"
        session_dir.mkdir(parents=True)
        target = session_dir / "session.jsonl"
        target.write_text("{}\n")
        assert usage.session_file_for("muse-9", tmp_path) == target

    def test_unknown_id_is_none(self, tmp_path: Path) -> None:
        assert usage.session_file_for("nope", tmp_path) is None


class TestUsagePostBody:
    def test_shape_with_model(self) -> None:
        acc = usage._UsageAccumulator()
        acc.add_line(
            {
                "event_id": "e1",
                "model": "muse-spark-1.3",
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_tokens": 60,
            }
        )
        assert usage._usage_post_body(acc) == {
            "cumulative_input_tokens": 100,
            "cumulative_output_tokens": 20,
            "cumulative_cache_read_input_tokens": 60,
            "model": "muse-spark-1.3",
        }

    def test_omits_model_when_none(self) -> None:
        acc = usage._UsageAccumulator()
        acc.add_line({"event_id": "e1", "output_tokens": 5})
        body = usage._usage_post_body(acc)
        assert "model" not in body
        assert body["cumulative_output_tokens"] == 5


class TestStateRoundTrip:
    def test_write_then_read(self, tmp_path: Path) -> None:
        acc = usage._UsageAccumulator(offset=12, seen={"e1"})
        acc.totals.input_tokens = 10
        acc.totals.model = "muse-spark-1.3"
        usage._write_usage_state(tmp_path, "muse-1", acc)
        loaded = usage._read_usage_state(tmp_path, "muse-1")
        assert loaded.totals.input_tokens == 10
        assert loaded.totals.model == "muse-spark-1.3"
        assert loaded.offset == 12
        assert loaded.seen == {"e1"}

    def test_missing_state_is_cold(self, tmp_path: Path) -> None:
        loaded = usage._read_usage_state(tmp_path, "muse-1")
        assert (loaded.totals.input_tokens, loaded.offset, loaded.seen) == (0, 0, set())

    def test_clear_removes_state(self, tmp_path: Path) -> None:
        usage._write_usage_state(tmp_path, "muse-1", usage._UsageAccumulator())
        usage.clear_muse_usage_state(tmp_path, "muse-1")
        assert usage._read_usage_state(tmp_path, "muse-1").seen == set()

    def test_clear_noop_when_absent(self, tmp_path: Path) -> None:
        usage.clear_muse_usage_state(tmp_path, "muse-1")  # must not raise


class TestSummarizeCli:
    def test_summarize_session_file(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        target = tmp_path / "session.jsonl"
        target.write_text(_direct_line("e1") + "\n" + _direct_line("e2") + "\n")
        assert usage._main(["summarize", "--session-file", str(target)]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["input_tokens"] == 200
        assert out["events"] == 2


class _CtxRecordingClient:
    """Async httpx-client stub (records POSTs, 200) usable as ``async with``."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []

    async def __aenter__(self) -> _CtxRecordingClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def post(self, url: str, *, json: dict) -> httpx.Response:
        self.posts.append((url, json))
        return httpx.Response(200, request=httpx.Request("POST", url))


async def _run_loop_until(
    monkeypatch: pytest.MonkeyPatch,
    *,
    client: _CtxRecordingClient | None = None,
    max_wait_s: float = 3.0,
    **kwargs: object,
) -> _CtxRecordingClient:
    """Run the real poll loop with a recording client until posts land."""
    from omnigent import cli_auth

    client = client or _CtxRecordingClient()
    monkeypatch.setattr(cli_auth, "open_server_client", _FakeClientFactory(client))
    task = asyncio.create_task(usage.forward_muse_usage_to_session(**kwargs))  # type: ignore[arg-type]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_wait_s
    try:
        while loop.time() < deadline:
            if [p for p in client.posts if p[1].get("type") == "external_session_usage"]:
                return client
            await asyncio.sleep(0.01)
        raise AssertionError("usage POST was not made before timeout")
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class _FakeClientFactory:
    """Stands in for ``open_server_client`` (an async context manager)."""

    def __init__(self, client: _CtxRecordingClient) -> None:
        self._client = client

    def __call__(self, *args: object, **kwargs: object) -> _CtxRecordingClient:
        return self._client


def _write_muse_session(root: Path, muse_session_id: str, *event_ids: str) -> Path:
    session_dir = root / "2026" / muse_session_id
    session_dir.mkdir(parents=True)
    target = session_dir / "session.jsonl"
    target.write_text(
        "\n".join(_direct_line(e, muse_session_id=muse_session_id) for e in event_ids) + "\n"
    )
    return target


@pytest.mark.asyncio
class TestForwardLoop:
    async def test_posts_cumulative_and_persists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sessions = tmp_path / "sessions"
        _write_muse_session(sessions, "muse-1", "e1", "e2")
        state = tmp_path / "state"
        client = await _run_loop_until(
            monkeypatch,
            base_url="http://test",
            headers={},
            session_id="conv_1",
            muse_session_id="muse-1",
            state_dir=state,
            sessions_root=sessions,
            poll_interval_s=0.01,
        )
        url, body = client.posts[0]
        assert url == "/v1/sessions/conv_1/events"
        assert body["type"] == "external_session_usage"
        assert body["data"] == {
            "cumulative_input_tokens": 200,
            "cumulative_output_tokens": 20,
            "cumulative_cache_read_input_tokens": 120,
            "model": "muse-spark-1.3",
        }
        persisted = usage._read_usage_state(state, "muse-1")
        assert persisted.seen == {"outer-e1", "outer-e2"}

    async def test_no_repost_when_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from omnigent import cli_auth

        sessions = tmp_path / "sessions"
        _write_muse_session(sessions, "muse-1", "e1")
        state = tmp_path / "state"
        client = _CtxRecordingClient()
        monkeypatch.setattr(cli_auth, "open_server_client", _FakeClientFactory(client))
        task = asyncio.create_task(
            usage.forward_muse_usage_to_session(
                base_url="http://test",
                headers={},
                session_id="conv_1",
                muse_session_id="muse-1",
                state_dir=state,
                sessions_root=sessions,
                poll_interval_s=0.01,
            )
        )
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 3.0
            while loop.time() < deadline:
                if client.posts:
                    break
                await asyncio.sleep(0.01)
            assert client.posts, "expected the initial usage POST"
            await asyncio.sleep(0.1)  # extra polls: nothing new, no 2nd POST
            assert len(client.posts) == 1
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def test_failed_post_is_not_persisted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from omnigent import cli_auth

        class _FailingClient(_CtxRecordingClient):
            async def post(self, url: str, *, json: dict) -> httpx.Response:
                self.posts.append((url, json))
                raise httpx.ConnectError("boom")

        sessions = tmp_path / "sessions"
        _write_muse_session(sessions, "muse-1", "e1")
        state = tmp_path / "state"
        client = _FailingClient()
        monkeypatch.setattr(cli_auth, "open_server_client", _FakeClientFactory(client))
        task = asyncio.create_task(
            usage.forward_muse_usage_to_session(
                base_url="http://test",
                headers={},
                session_id="conv_1",
                muse_session_id="muse-1",
                state_dir=state,
                sessions_root=sessions,
                poll_interval_s=0.01,
            )
        )
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 3.0
            while loop.time() < deadline:
                if client.posts:
                    break
                await asyncio.sleep(0.01)
            assert client.posts  # it tried
            # Failed flush must NOT advance persisted state.
            assert usage._read_usage_state(state, "muse-1").seen == set()
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


class TestReportMuseTokenGrowth:
    def _seed_reported(self, state: Path, payload: dict) -> None:
        (state / usage._REPORT_STATE_FILE).write_text(json.dumps(payload))

    def test_reports_growth_as_model_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from omnigent import usage_history

        sessions = tmp_path / "sessions"
        _write_muse_session(sessions, "muse-1", "e1", "e2")
        state = tmp_path / "state"
        calls: list[dict] = []
        monkeypatch.setattr(
            usage_history, "append_model_call", lambda *a, **k: calls.append({"a": a, "k": k})
        )
        monkeypatch.setattr(usage, "_last_report_s", 0.0)
        assert usage.report_muse_token_growth(sessions, state_dir=state, now_s=1000.0) == 1
        assert len(calls) == 1
        assert calls[0]["a"][0] == "muse-spark-1.3"
        assert calls[0]["k"]["input_tokens"] == 200
        assert calls[0]["k"]["session_id"] == "muse-1"
        assert calls[0]["k"]["source"] == "muse-tail"

    def test_no_growth_no_line(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from omnigent import usage_history

        sessions = tmp_path / "sessions"
        _write_muse_session(sessions, "muse-1", "e1")
        state = tmp_path / "state"
        calls: list[dict] = []
        monkeypatch.setattr(
            usage_history, "append_model_call", lambda *a, **k: calls.append({"a": a, "k": k})
        )
        monkeypatch.setattr(usage, "_last_report_s", 0.0)
        assert usage.report_muse_token_growth(sessions, state_dir=state, now_s=1000.0) == 1
        # Second sweep past the throttle: nothing new, no line.
        assert usage.report_muse_token_growth(sessions, state_dir=state, now_s=2000.0) == 0
        assert len(calls) == 1

    def test_throttled_sweep_reports_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from omnigent import usage_history

        sessions = tmp_path / "sessions"
        _write_muse_session(sessions, "muse-1", "e1")
        state = tmp_path / "state"
        calls: list[dict] = []
        monkeypatch.setattr(
            usage_history, "append_model_call", lambda *a, **k: calls.append({"a": a, "k": k})
        )
        monkeypatch.setattr(usage, "_last_report_s", 0.0)
        assert usage.report_muse_token_growth(sessions, state_dir=state, now_s=1000.0) == 1
        assert usage.report_muse_token_growth(sessions, state_dir=state, now_s=1001.0) == 0
        assert len(calls) == 1

    def test_reports_only_growth_over_prior_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from omnigent import usage_history

        sessions = tmp_path / "sessions"
        _write_muse_session(sessions, "muse-1", "e1", "e2")
        state = tmp_path / "state"
        state.mkdir()
        self._seed_reported(
            state, {"muse-1": {"input_tokens": 100, "output_tokens": 5, "cached_tokens": 60}}
        )
        calls: list[dict] = []
        monkeypatch.setattr(
            usage_history, "append_model_call", lambda *a, **k: calls.append({"a": a, "k": k})
        )
        monkeypatch.setattr(usage, "_last_report_s", 0.0)
        assert usage.report_muse_token_growth(sessions, state_dir=state, now_s=1000.0) == 1
        assert calls[0]["k"]["input_tokens"] == 100  # 200 total minus 100 reported
        assert calls[0]["k"]["output_tokens"] == 15  # 20 total minus 5 reported


def test_module_summarize_entrypoint(tmp_path: Path) -> None:
    target = tmp_path / "session.jsonl"
    target.write_text(_direct_line("e1") + "\n")
    env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    import subprocess

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "omnigent.muse_native_usage",
            "summarize",
            "--session-file",
            str(target),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["events"] == 1
