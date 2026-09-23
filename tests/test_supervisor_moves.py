"""Tests for the supervisor's next-move check (``omnigent.supervisor_moves``)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from omnigent import hermes_native_bridge, supervisor, supervisor_moves
from omnigent.inner import hermes_move_hook
from omnigent.supervisor import Backend

JEV = Backend(name="jev", url="https://decide.example/v1", model="typesafe/jev-1.13", key="k")

BRIEF = (
    "IMPLEMENT. Make stats.mean return 0.0 for []. Done when: pytest tests/test_stats.py passes."
)


@pytest.fixture(autouse=True)
def _ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "asks.jsonl"
    monkeypatch.setenv("OMNIGENT_SUPERVISOR_LEDGER", str(path))
    monkeypatch.delenv("OMNIGENT_SUPERVISOR_ACT_ABOVE", raising=False)
    return path


def _serve_jev(monkeypatch: pytest.MonkeyPatch, probabilities: dict[str, float]) -> list[Any]:
    bodies: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        pick = max(probabilities, key=lambda k: probabilities[k])
        answer = {"type": "choice", "choice": pick, "probabilities": probabilities}
        return httpx.Response(200, json={"answers": {"move": answer}, "usage": {"cost": 5e-5}})

    monkeypatch.setattr(
        supervisor, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    return bodies


@pytest.mark.parametrize(
    ("tool", "args", "move"),
    [
        ("patch", {"path": "a.py"}, "edit"),
        ("read_file", {"path": "a.py"}, "investigate"),
        ("terminal", {"command": "cd /repo && sed -n 1,40p a.py"}, "investigate"),
        ("terminal", {"command": "uv run pytest tests/test_a.py -q"}, "test"),
        ("terminal", {"command": "python3 - <<'EOF'\nprint(1)\nEOF"}, None),
        ("web_search", {"query": "x"}, None),
    ],
)
def test_pending_calls_are_named_by_the_kind_of_move(
    tool: str, args: dict[str, Any], move: str | None
) -> None:
    assert supervisor_moves.classify_call(tool, args) == move


@pytest.mark.asyncio
async def test_a_confident_different_pick_skips_the_call_with_a_redirect(
    monkeypatch: pytest.MonkeyPatch, _ledger: Path
) -> None:
    bodies = _serve_jev(monkeypatch, {"investigate": 0.05, "edit": 0.9, "test": 0.05})

    verdict = await supervisor_moves.decide_move(
        "state", "investigate", "terminal", backends=[JEV]
    )

    question = bodies[0]["questions"]["move"]
    assert question["type"] == "choice"
    assert set(question["criteria"]) == {m.key for m in supervisor_moves.MOVES}
    assert verdict.pick == "edit"
    assert verdict.message.startswith("[Supervisor] Skipped this terminal call.")
    assert "make the change the brief asks for" in verdict.message
    line = json.loads(_ledger.read_text().splitlines()[-1])
    assert (line["kind"], line["pick"], line["worker_move"], line["acted"]) == (
        "next_move",
        "edit",
        "investigate",
        True,
    )


@pytest.mark.parametrize(
    ("probabilities", "worker_move"),
    [
        ({"investigate": 0.1, "edit": 0.9}, "edit"),  # agrees with the worker
        ({"investigate": 0.3, "test": 0.7}, "investigate"),  # not confident
        ({"edit": 0.95, "test": 0.05}, None),  # the pending call is unclear
    ],
)
@pytest.mark.asyncio
async def test_agreement_doubt_or_an_unclear_call_lets_the_call_run(
    monkeypatch: pytest.MonkeyPatch, probabilities: dict[str, float], worker_move: str | None
) -> None:
    _serve_jev(monkeypatch, probabilities)

    verdict = await supervisor_moves.decide_move("state", worker_move, "patch", backends=[JEV])

    assert verdict.message == ""


@pytest.mark.asyncio
async def test_backend_trouble_lets_the_call_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        supervisor,
        "_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(403, text="<html>no</html>"))
        ),
    )

    verdict = await supervisor_moves.decide_move("state", "edit", "patch", backends=[JEV])

    assert verdict.message == "" and "firewall" in verdict.error


@pytest.mark.asyncio
async def test_the_check_reads_the_brief_and_this_turns_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": BRIEF}]},
        {
            "type": "function_call",
            "name": "terminal",
            "call_id": "c1",
            "arguments": json.dumps({"command": "cat stats.py"}),
        },
        {"type": "function_call_output", "call_id": "c1", "output": "def mean(xs): ..."},
    ]

    def server(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/sessions/w/items":
            return httpx.Response(200, json={"data": list(reversed(items))})
        return httpx.Response(404)

    bodies = _serve_jev(monkeypatch, {"investigate": 0.02, "edit": 0.95, "test": 0.03})
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(server), base_url="http://server"
    ) as client:
        out = await supervisor_moves.check_next_move(
            client, "w", "terminal", {"command": "cat tests/test_stats.py"}, backends=[JEV]
        )

    assert out["decision"] == "block" and "make the change" in out["reason"]
    state = bodies[0]["state"]
    assert "Make stats.mean return 0.0" in state and "cat stats.py" in state
    assert "(1 tool calls, 0 edits, 0 test runs)" in state
    assert "terminal: cat tests/test_stats.py" in state


def test_the_hook_checks_every_eighth_call_and_caps_redirects_per_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    checked: list[str] = []

    async def fake_check(_url: str, _sid: str, tool: str, _args: Any) -> dict[str, str]:
        checked.append(tool)
        return {"decision": "block", "reason": "redirect"}

    monkeypatch.setattr(hermes_move_hook, "_check", fake_check)

    def call(turn: str, tool: str = "terminal") -> dict[str, str]:
        payload = {"tool_name": tool, "tool_input": {}, "extra": {"turn_id": turn}}
        return hermes_move_hook.decide(payload, "http://server", "w")

    results = [call("t1") for _ in range(40)]
    assert [i + 1 for i, r in enumerate(results) if r] == [8, 16]  # capped at 2 a turn
    assert call("t1", "mcp_omnigent_sys_ask_supervisor") == {}
    assert [call("t2") for _ in range(8)][-1] == {"decision": "block", "reason": "redirect"}
    assert len(checked) == 3


def test_hermes_gets_both_checks_while_they_are_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = tmp_path / "key"
    key.write_text("k")
    monkeypatch.setenv("OMNIGENT_SUPERVISOR_KEY_FILE", str(key))
    monkeypatch.setenv("OMNIGENT_SUPERVISOR_STOP_CHECK", "on")
    monkeypatch.setenv("OMNIGENT_SUPERVISOR_NEXT_MOVE", "on")
    bridge_dir = tmp_path / "bridge"
    bridge_dir.mkdir()

    home = hermes_native_bridge.write_policy_hook_config(bridge_dir, "http://localhost:6767", "w")

    config = json.loads((home / "config.yaml").read_text())
    move = home / "omnigent-move-hook.sh"
    commands = [h["command"] for h in config["hooks"]["pre_tool_call"]]
    assert commands == [str(home / "omnigent-policy-hook.sh"), str(move)]
    assert "hermes_move_hook.py" in move.read_text()
    approvals = json.loads((home / "shell-hooks-allowlist.json").read_text())["approvals"]
    assert [a["event"] for a in approvals] == ["pre_tool_call", "pre_verify", "pre_tool_call"]
