"""A mirrored SendUserFile call puts the file in the transcript.

The harness tool only answers the model, so without this a chart the model
"sent" reached nobody -- the failure the reader saw as "I don't see any chart".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from omnigent.server.routes._sessions import helpers


@pytest.fixture(autouse=True)
def _recorder(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def _attach(_store, _files, _artifacts, session_id, response_id, path, *, caption=None):
        calls.append({"session": session_id, "response_id": response_id, "path": str(path), "caption": caption})
        return f"file_{len(calls)}"

    monkeypatch.setattr(helpers, "attach_assistant_file", _attach)
    helpers._attached_tool_calls.clear()
    return calls


async def _run(data: Any, **kwargs: Any) -> list[str]:
    return await helpers.attach_files_from_tool_call(
        data, conversation_store=object(), file_store=object(), artifact_store=object(),
        session_id="conv_x", **kwargs,
    )


def _call(files: list[str], **extra: Any) -> dict[str, Any]:
    return {"name": "SendUserFile", "call_id": "call_1", "response_id": "resp_1",
            "arguments": json.dumps({"files": files, **extra})}


@pytest.mark.asyncio
async def test_each_named_file_is_attached_to_the_turn(tmp_path: Path, _recorder) -> None:
    a, b = tmp_path / "chart.png", tmp_path / "second.png"
    a.write_bytes(b"x"); b.write_bytes(b"y")

    ids = await _run(_call([str(a), str(b)], caption="both charts"))

    assert ids == ["file_1", "file_2"]
    assert [c["path"] for c in _recorder] == [str(a), str(b)]
    assert [c["response_id"] for c in _recorder] == ["resp_1", "resp_1"]
    # The caption describes the send, so it must not repeat under every file.
    assert [c["caption"] for c in _recorder] == ["both charts", None]


@pytest.mark.asyncio
async def test_a_re_mirrored_call_does_not_attach_twice(tmp_path: Path, _recorder) -> None:
    f = tmp_path / "chart.png"; f.write_bytes(b"x")
    assert await _run(_call([str(f)])) == ["file_1"]
    assert await _run(_call([str(f)])) == []
    assert len(_recorder) == 1


@pytest.mark.asyncio
async def test_other_tool_calls_and_junk_are_ignored(tmp_path: Path, _recorder) -> None:
    f = tmp_path / "chart.png"; f.write_bytes(b"x")
    assert await _run({"name": "Bash", "arguments": json.dumps({"files": [str(f)]})}) == []
    assert await _run({"name": "SendUserFile", "arguments": "not json"}) == []
    assert await _run({"name": "SendUserFile", "arguments": json.dumps({"files": "oops"})}) == []
    assert await _run(None) == []
    assert _recorder == []


@pytest.mark.asyncio
async def test_missing_and_oversized_files_are_skipped(tmp_path: Path, monkeypatch, _recorder) -> None:
    big, ok = tmp_path / "big.bin", tmp_path / "ok.png"
    big.write_bytes(b"x"); ok.write_bytes(b"y")
    monkeypatch.setattr(helpers, "_MAX_ATTACHMENT_BYTES", 0)
    assert await _run(_call([str(big), str(tmp_path / "gone.png")])) == []
    monkeypatch.setattr(helpers, "_MAX_ATTACHMENT_BYTES", 25 * 1024 * 1024)
    assert await _run({**_call([str(ok)]), "call_id": "call_2"}) == ["file_1"]


@pytest.mark.asyncio
async def test_a_relative_path_resolves_against_the_workspace(tmp_path: Path, _recorder) -> None:
    (tmp_path / "chart.png").write_bytes(b"x")
    assert await _run(_call(["chart.png"]), workspace=str(tmp_path)) == ["file_1"]
    assert _recorder[0]["path"] == str(tmp_path / "chart.png")
