"""Tests for the supervisor's check before a worker finishes (``omnigent.supervisor_stop``)."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from omnigent import hermes_native_bridge, supervisor, supervisor_stop
from omnigent.inner import hermes_verify_hook
from omnigent.supervisor import Backend

JEV = Backend(name="jev", url="https://decide.example/v1", model="typesafe/jev-1.13", key="k")
LOCAL = Backend(name="local", url="http://localhost:8008/v1/systemone", model="local")

BRIEF = (
    "IMPLEMENT. Make stats.mean return 0.0 for []. Done when: pytest tests/test_stats.py passes."
)


@pytest.fixture(autouse=True)
def _ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "asks.jsonl"
    monkeypatch.setenv("OMNIGENT_SUPERVISOR_LEDGER", str(path))
    monkeypatch.delenv("OMNIGENT_SUPERVISOR_BLOCK_BELOW", raising=False)
    return path


def _serve_jev(monkeypatch: pytest.MonkeyPatch, handler: Any) -> list[dict[str, Any]]:
    """Route backend calls to *handler*; returns the request bodies it saw."""
    bodies: list[dict[str, Any]] = []

    def _record(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return handler(request)

    monkeypatch.setattr(
        supervisor, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(_record))
    )
    return bodies


def _nouls(**p_yes: float) -> httpx.Response:
    answers = {key: {"type": "noul", "noul": value} for key, value in p_yes.items()}
    return httpx.Response(200, json={"answers": answers, "usage": {"cost": 0.00008}})


def test_the_state_is_only_the_brief_the_answer_and_the_evidence() -> None:
    brief = "TASK " + "x" * 9000 + " DONE WHEN tests pass\n\n" + supervisor.WORKER_RULE

    state = supervisor_stop.render_stop_state(brief, "All done.", "Files edited (1): a.py")

    assert supervisor.WORKER_RULE not in state
    assert "TASK" in state and "DONE WHEN tests pass" in state
    assert "[...middle of the brief cut]" in state and len(state) < 9000
    assert "All done." in state and "Files edited (1): a.py" in state


@pytest.mark.asyncio
async def test_a_confident_no_keeps_the_worker_going_with_that_instruction(
    monkeypatch: pytest.MonkeyPatch, _ledger: Path
) -> None:
    bodies = _serve_jev(
        monkeypatch,
        lambda _r: _nouls(ran_tests=0.04, tests_pass=0.5, claims_backed=0.9, brief_done=0.08),
    )

    verdict = await supervisor_stop.decide("state", backends=[JEV], session_id="w")

    assert len(bodies) == 1
    assert set(bodies[0]["questions"]) == {c.key for c in supervisor_stop.CHECKS}
    assert verdict.failed == ("ran_tests", "brief_done")
    assert "Run the tests or checks the brief names now" in verdict.message
    assert "Part of the brief is not done yet" in verdict.message
    assert "last test run is not clean" not in verdict.message
    line = json.loads(_ledger.read_text().splitlines()[-1])
    assert (line["kind"], line["blocked"], line["session"]) == ("stop_check", True, "w")


@pytest.mark.asyncio
async def test_an_unsure_answer_lets_the_worker_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve_jev(
        monkeypatch,
        lambda _r: _nouls(ran_tests=0.3, tests_pass=0.3, claims_backed=0.18, brief_done=0.55),
    )

    verdict = await supervisor_stop.decide("state", backends=[JEV])

    assert (verdict.failed, verdict.message, verdict.backend) == ((), "", "jev")


@pytest.mark.asyncio
async def test_backend_trouble_never_traps_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "decide.example":
            return httpx.Response(403, text="<!DOCTYPE html><html>blocked</html>")
        return httpx.Response(200, json={"answers": {"ran_tests": {"noul": 0.0}}})

    _serve_jev(monkeypatch, handler)

    verdict = await supervisor_stop.decide("state", backends=[JEV, LOCAL])

    assert verdict.message == ""
    assert "firewall" in verdict.error and "local: malformed reply" in verdict.error


@pytest.mark.asyncio
async def test_the_hook_reads_the_workers_brief_and_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_items = [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": BRIEF}]},
        {
            "type": "function_call",
            "name": "patch",
            "call_id": "c1",
            "arguments": json.dumps({"path": "stats.py"}),
        },
        {"type": "function_call_output", "call_id": "c1", "output": "ok"},
    ]

    def server(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/sessions/w/items":
            return httpx.Response(200, json={"data": list(reversed(worker_items))})
        if request.url.path == "/v1/sessions/w":
            return httpx.Response(200, json={"parent_session_id": "p"})
        return httpx.Response(404)

    bodies = _serve_jev(
        monkeypatch,
        lambda _r: _nouls(ran_tests=0.02, tests_pass=0.1, claims_backed=0.7, brief_done=0.6),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(server), base_url="http://server"
    ) as client:
        out = await supervisor_stop.check_worker_stop(client, "w", "Done.", backends=[JEV])

    assert out["decision"] == "block"
    assert "Run the tests" in out["reason"] and "last test run is not clean" in out["reason"]
    state = bodies[0]["state"]
    assert "Make stats.mean return 0.0" in state and "Files edited (1): stats.py" in state
    assert "the worker ran no tests" in state


def test_the_hook_script_lets_the_worker_finish_on_any_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {"hook_event_name": "pre_verify", "extra": {"final_response": "Done."}}
    monkeypatch.setenv("_OMNIGENT_SERVER_URL", "http://server")
    monkeypatch.setenv("_OMNIGENT_SESSION_ID", "w")

    async def broken(*_args: Any) -> dict[str, str]:
        raise RuntimeError("server down")

    monkeypatch.setattr(hermes_verify_hook, "_check", broken)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    hermes_verify_hook.main()
    assert json.loads(capsys.readouterr().out) == {}

    async def blocks(_url: str, _sid: str, answer: str) -> dict[str, str]:
        return {"decision": "block", "reason": f"saw {answer}"}

    monkeypatch.setattr(hermes_verify_hook, "_check", blocks)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    hermes_verify_hook.main()
    assert json.loads(capsys.readouterr().out) == {"decision": "block", "reason": "saw Done."}


@pytest.mark.parametrize("state", ["on", "off"])
def test_hermes_gets_the_hook_only_while_the_check_is_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    key = tmp_path / "key"
    key.write_text("k")
    monkeypatch.setenv("OMNIGENT_SUPERVISOR_KEY_FILE", str(key))
    monkeypatch.setenv("OMNIGENT_SUPERVISOR_STOP_CHECK", state)
    bridge_dir = tmp_path / "bridge"
    bridge_dir.mkdir()

    home = hermes_native_bridge.write_policy_hook_config(bridge_dir, "http://localhost:6767", "w")

    config = json.loads((home / "config.yaml").read_text())
    approvals = json.loads((home / "shell-hooks-allowlist.json").read_text())["approvals"]
    if state == "off":
        assert "pre_verify" not in config["hooks"]
        assert [a["event"] for a in approvals] == ["pre_tool_call"]
        return
    wrapper = home / "omnigent-verify-hook.sh"
    assert wrapper.stat().st_mode & 0o777 == 0o700
    assert "hermes_verify_hook.py" in wrapper.read_text()
    assert config["hooks"]["pre_verify"] == [
        {"command": str(wrapper), "timeout": supervisor_stop.HOOK_TIMEOUT_S}
    ]
    assert config["agent"]["max_verify_nudges"] == supervisor_stop.MAX_NUDGES
    assert {"event": "pre_verify", "command": str(wrapper)} in approvals
