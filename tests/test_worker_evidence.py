"""A finished worker's result carries what its tools did, not only what it says it did."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from omnigent.runner.worker_evidence import (
    HEADER,
    attach_worker_evidence,
    extract_evidence,
    git_changes,
    worker_dirs,
)


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
    assert "Edits after it: none" in evidence
    assert evidence.rstrip().endswith("OK")


def test_an_edit_after_the_last_test_run_marks_its_output_stale() -> None:
    items = [
        _user("fix it"),
        _call("1", "terminal", command="python3 -m pytest tests -x -q"),
        _out("1", json.dumps({"output": "3 passed"})),
        _call("2", "patch", path="/w/inventory/store.py", old_string="a", new_string="b"),
        _out("2", json.dumps({"success": True})),
    ]

    evidence = extract_evidence(items)

    assert evidence is not None
    assert "Edits after it: 1 edit-tool call(s); its output may be stale." in evidence


def test_the_last_lint_or_type_check_is_shown_beside_the_last_test() -> None:
    items = [
        _user("build the page"),
        _call("1", "run_command", CommandLine="npx vitest run src/Usage.test.tsx"),
        _out("1", "Tests  4 passed (4)"),
        _call("2", "run_command", CommandLine="npx tsc -b && npx oxlint src/Usage.tsx"),
        _out("2", "Found 0 warnings and 0 errors."),
    ]

    evidence = extract_evidence(items)

    assert evidence is not None
    assert "Last test command: npx vitest run src/Usage.test.tsx" in evidence
    assert "- tsc, oxlint: npx tsc -b && npx oxlint src/Usage.tsx (no edits after it)" in evidence
    assert "Found 0 warnings and 0 errors." in evidence


def test_each_check_tool_shows_its_own_last_run_even_from_an_earlier_turn() -> None:
    items = [
        _user("build the page"),
        _call("1", "run_command", CommandLine="node node_modules/typescript/bin/tsc -b"),
        _out("1", ""),
        _call("2", "run_command", CommandLine="npx oxlint src/Usage.tsx"),
        _out("2", "Found 0 warnings and 0 errors."),
        _call("3", "replace_file_content", TargetFile="/w/src/Usage.tsx"),
        _call("4", "run_command", CommandLine="npx prettier --check src/Usage.tsx"),
        _out("4", "All matched files use Prettier code style!"),
        _user("IMPLEMENT (supervisor check before your report goes to the orchestrator)"),
        _call(
            "5",
            "run_command",
            CommandLine="pytest -q && ruff check x.py && ruff format --check x.py",
        ),
        _out("5", "3 passed\nAll checks passed!\n1 file already formatted"),
    ]

    evidence = extract_evidence(items)

    assert evidence is not None
    assert (
        "- tsc: node node_modules/typescript/bin/tsc -b"
        " (1 edit-tool call(s) after it; may be stale)" in evidence
    )
    assert "  Output: (none; most linters print nothing when clean)" in evidence
    assert "- oxlint: npx oxlint src/Usage.tsx (1 edit-tool call(s)" in evidence
    assert "- prettier: npx prettier --check src/Usage.tsx (no edits after it)" in evidence
    # A chained command is both the last test and the last ruff run.
    assert "Last test command: pytest -q && ruff check" in evidence
    assert "- ruff check, ruff format: pytest -q && ruff check" in evidence


def test_a_repo_lint_script_counts_as_a_check() -> None:
    items = [
        _user("fix it"),
        _call(
            "1",
            "run_command",
            CommandLine=".venv/bin/python dev/lint/lint_no_global_asyncio_patch.py tests/a.py",
        ),
        _out("1", ""),
    ]

    evidence = extract_evidence(items)

    assert evidence is not None
    assert "- lint_no_global_asyncio_patch.py: .venv/bin/python dev/lint/" in evidence
    assert "(none; most linters print nothing when clean)" in evidence


def test_a_turn_with_no_lint_or_type_check_adds_no_line_for_it() -> None:
    evidence = extract_evidence([_user("x"), _call("1", "terminal", command="pytest -q")])

    assert evidence is not None and "lint/type-check" not in evidence


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


def test_a_compaction_restatement_does_not_start_a_new_turn() -> None:
    """Hermes re-injects the task as user messages when it compacts; earlier work still counts."""
    items = [
        _user("write the tests"),
        _call("1", "patch", path="/w/tests/test_a.py"),
        _call("2", "terminal", command="pytest tests/test_a.py"),
        _out("2", "1 failed, 2 passed"),
        _user("[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted..."),
        _user("[STILL IN PROGRESS — this is the active request, restated...] write the tests"),
        _call("3", "terminal", command="ls"),
    ]
    evidence = extract_evidence(items)
    assert evidence is not None
    assert "Files edited (1): /w/tests/test_a.py" in evidence
    assert "1 failed, 2 passed" in evidence


def test_worker_dirs_come_from_cd_working_dirs_and_edited_files() -> None:
    items = [
        _user("go"),
        _call("1", "terminal", command="cd /repo && python3 - << 'PY'\nopen('a.py','w')\nPY"),
        _call("2", "terminal", command="cd '/other dir' && ls"),
        _call("3", "run_command", CommandLine="pytest", Cwd="/agy/project"),
        _call("4", "patch", path="/edited/pkg/mod.py"),
        _call("5", "terminal", command="cd /repo && git diff"),
    ]
    assert worker_dirs(items) == ["/repo", "/agy/project", "/edited/pkg"]


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_files_written_through_the_shell_show_up_from_git(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "a.py").write_text("old\n")
    subprocess.run([*git, "add", "a.py"], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    (repo / "a.py").write_text("new\n")
    (repo / "b.py").write_text("x\n")
    items_desc = list(
        reversed(
            [
                _user("go"),
                _call("1", "terminal", command=f"cd {repo} && python3 - << 'PY'\nwrite\nPY"),
                _out("1", json.dumps({"output": ""})),
            ]
        )
    )
    payload = {"type": "sub_agent", "status": "completed", "output": "done"}
    result = _run_attach(payload, lambda _r: httpx.Response(200, json={"data": items_desc}))
    assert "Files edited (0): none" in result["output"]
    assert f"Uncommitted changes in {repo.resolve()}" in result["output"]
    assert "M a.py" in result["output"] and "?? b.py" in result["output"]


def test_directories_outside_git_add_nothing(tmp_path: Path) -> None:
    assert asyncio.run(git_changes([str(tmp_path), "/does/not/exist"])) == []
