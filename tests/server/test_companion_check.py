"""The companion looking things up: commands, refusals, limits, and what the UI sees."""

from __future__ import annotations

import shutil
import stat
import sys
import textwrap

import pytest

from omnigent.server import companion_commands, discussion
from omnigent.server.companion_commands import CommandResult
from omnigent.server.discussion import MAX_CHECK_COMMANDS, DiscussionSession

#: A CLI that plays one scripted check: run a command, then answer from it.
#: Markers in the question pick the other paths.
_SCRIPTED_AGY = """\
import json, os, sys, time
def reply(text):
    event = {"event": "result", "result": {"response": text}}
    sys.stdout.write(json.dumps(event) + "\\n")
    sys.stdout.flush()
def say(obj):
    reply(json.dumps(obj))
always = False
for line in sys.stdin:
    if not line.strip():
        continue
    content = json.loads(line)["message"]["content"]
    stall = os.environ.get("FAKE_STALL_FILE")
    if "STALL" in content and stall and ("ALWAYS" in content or not os.path.exists(stall)):
        open(stall, "w").close()
        time.sleep(60)  # a turn agy never answers: it hung before reaching Google
    slow = os.environ.get("FAKE_SLOW_FIRST")
    if slow and not os.path.exists(slow):
        open(slow, "w").close()
        time.sleep(1.0)  # a cold start: agy signs in before it reads the question
    always = always or "ALWAYS_RUN" in content
    if always:
        say({"run": "wc -l notes.txt"})
    elif "[Refused" in content:
        say({"claude": "that needs a change to the files"})
    elif "WANTS_TO_DELETE" in content:
        say({"run": "rm -rf build"})
    elif "[Output]" in content:
        say({"answer": "It has three lines."})
    elif "ASK_CLAUDE" in content:
        say({"claude": "it is about the code"})
    elif "NOT_JSON" in content and "not one line of JSON" not in content:
        reply("hmm let me see")
    else:
        say({"run": "wc -l notes.txt"})
"""

needs_bwrap = pytest.mark.skipif(shutil.which("bwrap") is None, reason="bwrap not installed")


@pytest.fixture
def scripted_agy(tmp_path):
    script = tmp_path / "scripted_agy"
    script.write_text(f"#!{sys.executable}\n{textwrap.dedent(_SCRIPTED_AGY)}", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


@pytest.fixture
def session(scripted_agy, tmp_path, monkeypatch):
    monkeypatch.setattr(discussion, "LEDGER_DIR", tmp_path / "ledgers")
    return DiscussionSession("s-check", binary=scripted_agy, model="test-model")


@pytest.fixture
def fake_run(monkeypatch):
    """Replace the sandboxed runner, recording what it was asked to run."""
    calls: list[str] = []

    async def run(command, *, cwd=None, timeout_s=20.0, home=None):
        companion_commands.validate(command)
        calls.append(command)
        return CommandResult(command=command, exit_code=0, output="3 notes.txt\n", seconds=0.01)

    monkeypatch.setattr(companion_commands, "run", run)
    return calls


async def test_a_check_runs_a_command_then_answers(session, fake_run):
    result = await session.check("how many lines are in notes.txt?")
    assert result.answer == "It has three lines."
    assert result.needs_claude is None
    assert fake_run == ["wc -l notes.txt"]
    assert [c.command for c in result.commands] == ["wc -l notes.txt"]
    commands = [e for e in session.context if e.kind == "command"]
    assert len(commands) == 1
    assert commands[0].text.startswith("$ wc -l notes.txt [exit 0] 3 notes.txt")
    assert session.as_dict()["activity"] is None


async def test_a_write_is_refused_recorded_and_handed_to_claude(session, fake_run):
    result = await session.check("WANTS_TO_DELETE the build folder")
    assert result.answer is None
    assert result.needs_claude == "that needs a change to the files"
    assert fake_run == []
    refused = [e.text for e in session.context if e.kind == "command"]
    assert refused and refused[0].startswith("refused 'rm -rf build'")


async def test_it_can_say_claude_is_needed_without_running_anything(session, fake_run):
    result = await session.check("ASK_CLAUDE why does the parser fail?")
    assert result.needs_claude == "it is about the code"
    assert fake_run == []


async def test_commands_per_check_are_capped(session, fake_run):
    result = await session.check("ALWAYS_RUN forever")
    assert len(fake_run) == MAX_CHECK_COMMANDS
    assert result.answer is None
    assert result.needs_claude


async def test_a_reply_that_is_not_json_gets_one_more_chance(session, fake_run):
    result = await session.check("NOT_JSON first")
    assert result.answer == "It has three lines."


async def test_the_ui_sees_the_command_while_it_runs(session, monkeypatch):
    seen: list[dict] = []

    async def run(command, *, cwd=None, timeout_s=20.0, home=None):
        seen.append(dict(session.as_dict()["activity"]))
        return CommandResult(command=command, exit_code=0, output="3\n", seconds=0.0)

    monkeypatch.setattr(companion_commands, "run", run)
    await session.check("how many lines?")
    assert seen[0]["state"] == "running"
    assert seen[0]["command"] == "wc -l notes.txt"
    assert seen[0]["question"] == "how many lines?"
    assert session.as_dict()["activity"] is None


async def test_command_entries_survive_a_restart(session, fake_run, scripted_agy):
    await session.check("how many lines?")
    reborn = DiscussionSession("s-check", binary=scripted_agy, model="test-model")
    assert [e.kind for e in reborn.context if e.kind == "command"] == ["command"]


@needs_bwrap
async def test_end_to_end_in_the_real_sandbox(session, tmp_path):
    (tmp_path / "notes.txt").write_text("a\nb\nc\n")
    result = await session.check("how many lines are in notes.txt?", cwd=str(tmp_path))
    assert result.answer == "It has three lines."
    assert result.commands[0].exit_code == 0
    assert result.commands[0].output.split()[0] == "3"


def test_the_check_route_returns_the_answer_and_commands(
    scripted_agy, tmp_path, monkeypatch, fake_run
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from omnigent.server.discussion import DiscussionRegistry
    from omnigent.server.routes.discussion import create_discussion_router

    monkeypatch.setattr(discussion, "LEDGER_DIR", tmp_path / "ledgers")
    monkeypatch.setenv("OMNIGENT_DISCUSSION_AGY_BIN", scripted_agy)
    companions = DiscussionRegistry()
    app = FastAPI()
    app.include_router(
        create_discussion_router(registry_provider=lambda: companions), prefix="/v1"
    )
    with TestClient(app) as client:
        response = client.post("/v1/discussion/s-route/check", json={"text": "how many lines?"})
        assert response.status_code == 200
        body = response.json()
        assert body["answer"] == "It has three lines."
        assert body["needs_claude"] is None
        assert body["commands"][0]["command"] == "wc -l notes.txt"
        state = client.get("/v1/discussion/s-route").json()
        assert state["activity"] is None
        assert any(e["kind"] == "command" for e in state["context"])


async def test_a_first_turn_that_stalls_is_asked_again_in_a_fresh_process(
    session, fake_run, tmp_path, monkeypatch
):
    monkeypatch.setenv("FAKE_STALL_FILE", str(tmp_path / "stalled"))
    monkeypatch.setattr(discussion, "_CHECK_TURN_TIMEOUT_S", 0.5)
    monkeypatch.setattr(discussion, "_CHECK_COLD_TURN_TIMEOUT_S", 0.5)
    monkeypatch.setattr(discussion, "_CHECK_RETRY_MIN_S", 0.5)
    result = await session.check("STALL how many lines are in notes.txt?", timeout_s=10)
    assert result.answer == "It has three lines."
    assert fake_run == ["wc -l notes.txt"]
    assert (tmp_path / "stalled").exists()
    await session.close()


async def test_a_check_that_keeps_stalling_says_so(session, fake_run, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_STALL_FILE", str(tmp_path / "stalled"))
    monkeypatch.setattr(discussion, "_CHECK_TURN_TIMEOUT_S", 0.3)
    monkeypatch.setattr(discussion, "_CHECK_COLD_TURN_TIMEOUT_S", 0.3)
    monkeypatch.setattr(discussion, "_CHECK_RETRY_MIN_S", 0.3)
    result = await session.check("STALL ALWAYS how many lines?", timeout_s=5)
    assert result.answer is None
    assert result.needs_claude is not None and "no answer within" in result.needs_claude
    assert fake_run == []
    assert session.as_dict()["activity"] is None


async def test_a_cold_start_is_not_mistaken_for_a_stall(session, fake_run, tmp_path, monkeypatch):
    # agy signs in before it reads the question; only the first turn pays for it.
    monkeypatch.setenv("FAKE_SLOW_FIRST", str(tmp_path / "started"))
    monkeypatch.setenv("FAKE_STALL_FILE", str(tmp_path / "stalled"))
    monkeypatch.setattr(discussion, "_CHECK_TURN_TIMEOUT_S", 0.5)
    monkeypatch.setattr(discussion, "_CHECK_COLD_TURN_TIMEOUT_S", 3.0)
    result = await session.check("how many lines are in notes.txt?", timeout_s=10)
    assert result.answer == "It has three lines."
    assert not (tmp_path / "stalled").exists()  # answered by the first process
    await session.close()
