"""A finished worker's result carries what its tools did, not only what it says it did."""

from __future__ import annotations

import asyncio
import json

import httpx

from omnigent.runner.worker_evidence import HEADER, attach_worker_evidence, extract_evidence


def _user(text: str) -> dict:
    return {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}


def _call(call_id: str, name: str, **arguments: object) -> dict:
    return {
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": json.dumps(arguments),
    }


def _out(call_id: str, output: str) -> dict:
    return {"type": "function_call_output", "call_id": call_id, "output": output}


def test_hermes_turn_lists_edits_and_the_real_test_output() -> None:
    items = [
        _user("fix it"),
        _call("1", "terminal", command="ls -la"),
        _out("1", json.dumps({"output": "total 16"})),
        _call("2", "patch", path="/w/inventory/store.py", old_string="a", new_string="b"),
        _out("2", json.dumps({"success": True})),
        _call("3", "write_file", path="/w/tests/test_low_stock.py", content="x"),
        _call("4", "terminal", command="python3 -m unittest discover -s tests"),
        _out("4", json.dumps({"output": "Ran 12 tests in 0.000s\n\nOK"})),
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "total 16"}],
        },
    ]
    evidence = extract_evidence(items)
    assert evidence is not None and evidence.startswith(HEADER)
    assert "Files edited (2): /w/inventory/store.py, /w/tests/test_low_stock.py" in evidence
    assert "Last test command: python3 -m unittest discover -s tests" in evidence
    assert evidence.rstrip().endswith("OK")


def test_only_the_latest_turn_counts() -> None:
    items = [
        _user("first task"),
        _call("1", "patch", path="/w/old.py"),
        _call("2", "terminal", command="pytest"),
        _out("2", "1 failed"),
        _user("second task"),
        _call("3", "terminal", command="ls"),
    ]
    evidence = extract_evidence(items)
    assert evidence is not None
    assert "Files edited (0): none" in evidence
    assert "none found in this turn" in evidence
    assert "/w/old.py" not in evidence and "1 failed" not in evidence


def test_codex_and_antigravity_shapes() -> None:
    codex = [
        _user("go"),
        _call(
            "1",
            "apply_patch",
            changes=[{"path": "/r/a.py", "kind": {"type": "update"}}, {"path": "/r/b.py"}],
        ),
        _call("2", "shell", command="/bin/bash -lc 'uv run pytest tests/test_a.py -q'"),
        _out("2", "3 passed in 0.1s"),
    ]
    evidence = extract_evidence(codex)
    assert evidence is not None
    assert "Files edited (2): /r/a.py, /r/b.py" in evidence and "3 passed" in evidence

    agy = [
        _user("go"),
        _call("1", "replace_file_content", TargetFile="/r/c.py", Instruction="fix"),
        _call("2", "run_command", CommandLine="python -m pytest tests", Cwd="/r"),
        _out("2", "5 passed"),
    ]
    evidence = extract_evidence(agy)
    assert evidence is not None
    assert (
        "Files edited (1): /r/c.py" in evidence
        and "Last test command: python -m pytest tests" in evidence
    )


def test_a_turn_without_tool_calls_adds_nothing() -> None:
    assert extract_evidence([_user("hi"), {"type": "message", "role": "assistant"}]) is None


def _run_attach(payload: dict, respond) -> dict:
    async def run() -> dict:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond), base_url="http://s"
        ) as client:
            return await attach_worker_evidence(
                payload, server_client=client, child_id="conv_child"
            )

    return asyncio.run(run())


def test_attach_appends_evidence_without_touching_the_original() -> None:
    items_desc = list(
        reversed([_user("go"), _call("1", "terminal", command="pytest -q"), _out("1", "2 passed")])
    )
    seen: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"data": items_desc})

    payload = {"type": "sub_agent", "status": "completed", "output": "done"}
    result = _run_attach(payload, respond)
    assert payload["output"] == "done"
    assert result["output"].startswith("done\n\n" + HEADER)
    assert "2 passed" in result["output"]
    assert seen and "/v1/sessions/conv_child/items" in seen[0] and "order=desc" in seen[0]


def test_attach_never_blocks_a_result() -> None:
    payload = {"type": "sub_agent", "status": "completed", "output": "done"}
    assert _run_attach(payload, lambda _r: httpx.Response(500)) == payload
    running = {"type": "sub_agent", "status": "running", "output": ""}
    assert _run_attach(running, lambda _r: httpx.Response(200, json={"data": []})) == running
    task = {"type": "async_task", "status": "completed", "output": "x"}
    assert _run_attach(task, lambda _r: httpx.Response(200, json={"data": []})) == task
