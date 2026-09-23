"""Tests for the worker supervisor (``omnigent.supervisor``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from omnigent import supervisor
from omnigent.supervisor import Backend, Knowledge, Question, SupervisorError

JEV = Backend(name="jev", url="https://decide.example/v1", model="typesafe/jev-1.13", key="k")
LOCAL = Backend(name="local", url="http://localhost:8008/v1/systemone", model="local")


@pytest.fixture(autouse=True)
def _ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "asks.jsonl"
    monkeypatch.setenv("OMNIGENT_SUPERVISOR_LEDGER", str(path))
    monkeypatch.delenv("OMNIGENT_SUPERVISOR_MIN_CONFIDENCE", raising=False)
    return path


def _serve(monkeypatch: pytest.MonkeyPatch, handler: Any) -> list[httpx.Request]:
    """Route backend calls to *handler*; returns the requests it saw."""
    seen: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(
        supervisor, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(_record))
    )
    return seen


def _answer(entry: dict[str, Any], cost: float = 0.00001) -> httpx.Response:
    return httpx.Response(200, json={"answers": {"q": entry}, "usage": {"cost": cost}})


@pytest.mark.parametrize(
    ("question", "message"),
    [
        (Question("yes_no", " "), "empty"),
        (Question("yes_no", "x" * 1001), "under 1000"),
        (Question("yes_no", "Done?", ("a", "b")), "no options"),
        (Question("choice", "Which?", ("a",)), "2-12"),
        (Question("choice", "Which?", ("a", "a")), "distinct"),
        (Question("score", "How bad?", ("low", "")), "2-12"),
    ],
)
def test_validate_refuses_questions_it_cannot_answer_well(
    question: Question, message: str
) -> None:
    with pytest.raises(SupervisorError, match=message):
        supervisor.validate(question)


def test_state_keeps_the_orchestrators_knowledge_and_the_newest_progress() -> None:
    knowledge = Knowledge(
        request="Make mean([]) return 0.0",
        brief="Edit stats.py only; add a test.",
        notes="Tests live in tests/test_stats.py.",
        progress="step-old " + "x" * 200_000 + " step-new",
    )
    state = supervisor.render_state(knowledge, evidence="pytest: 1 failed")

    assert (
        state.index("Make mean") < state.index("Edit stats.py") < state.index("tests/test_stats")
    )
    assert "pytest: 1 failed" in state
    assert state.endswith("step-new")
    assert "step-old" not in state
    assert len(state) <= 90_000


def test_questions_translate_to_typesafes_format() -> None:
    assert supervisor.jev_question(Question("yes_no", "Done?")) == {
        "type": "noul",
        "instructions": "Done?",
    }
    assert supervisor.jev_question(Question("choice", "Which file?", ("a.py", "b.py"))) == {
        "type": "choice",
        "instructions": "Which file?",
        "criteria": {"a.py": "", "b.py": ""},
    }
    assert supervisor.jev_question(Question("score", "Risk?", ("low", "high")))["criteria"] == [
        "low",
        "high",
    ]


@pytest.mark.asyncio
async def test_a_confident_yes_comes_back_sure_and_is_logged(
    monkeypatch: pytest.MonkeyPatch, _ledger: Path
) -> None:
    seen = _serve(monkeypatch, lambda r: _answer({"type": "noul", "noul": 0.93}))

    verdict = await supervisor.ask(
        Question("yes_no", "Should I start editing now?"),
        Knowledge(brief="Fix the loop"),
        backends=[JEV],
        session_id="w1",
        parent_id="p1",
    )

    assert (verdict.answer, verdict.sure, verdict.backend) == ("yes", True, "jev")
    assert verdict.probabilities == {"yes": 0.93, "no": 0.07}
    body = json.loads(seen[0].content)
    assert body["model"] == "typesafe/jev-1.13" and "Fix the loop" in body["state"]
    assert seen[0].headers["authorization"] == "Bearer k"
    line = json.loads(_ledger.read_text().splitlines()[0])
    assert (line["session"], line["parent"], line["answer"], line["sure"]) == (
        "w1",
        "p1",
        "yes",
        True,
    )
    assert "Supervisor: yes" in supervisor.format_for_worker(verdict)


@pytest.mark.asyncio
async def test_an_unsure_answer_tells_the_worker_to_check_or_escalate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(
        monkeypatch,
        lambda r: _answer(
            {"type": "choice", "choice": "a.py", "probabilities": {"a.py": 0.55, "b.py": 0.45}}
        ),
    )
    question = Question("choice", "Which file holds the bug?", ("a.py", "b.py"))

    verdict = await supervisor.ask(question, Knowledge(), backends=[JEV])

    assert (verdict.answer, verdict.sure) == ("a.py", False)
    text = supervisor.format_for_worker(verdict)
    assert "not sure" in text and "orchestrator" in text


@pytest.mark.asyncio
async def test_a_firewall_refusal_falls_back_to_the_local_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "decide.example":
            return httpx.Response(403, text="<!DOCTYPE html><html>blocked</html>")
        return _answer(
            {"type": "score", "score": 1.2, "probabilities": {"0": 0.1, "1": 0.8, "2": 0.1}},
            cost=0,
        )

    _serve(monkeypatch, handler)
    question = Question(
        "score", "How far along is the worker?", ("not started", "halfway", "done")
    )

    verdict = await supervisor.ask(question, Knowledge(), backends=[JEV, LOCAL])

    assert (verdict.answer, verdict.backend, verdict.sure) == ("halfway", "local", True)


@pytest.mark.asyncio
async def test_no_backend_answering_is_a_verdict_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(monkeypatch, lambda r: httpx.Response(502, text="bad gateway"))

    verdict = await supervisor.ask(Question("yes_no", "Done?"), Knowledge(), backends=[JEV])

    assert (verdict.answer, verdict.sure) == ("", False)
    assert "HTTP 502" in verdict.error
    assert "unavailable" in supervisor.format_for_worker(verdict)


def test_the_supervisor_is_off_without_a_key_or_a_local_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMNIGENT_SUPERVISOR_KEY_FILE", str(tmp_path / "missing"))
    monkeypatch.delenv("OMNIGENT_SUPERVISOR_FALLBACK_URL", raising=False)
    assert supervisor.configured_backends() == []

    (tmp_path / "key").write_text("sk-test\n")
    monkeypatch.setenv("OMNIGENT_SUPERVISOR_KEY_FILE", str(tmp_path / "key"))
    monkeypatch.setenv("OMNIGENT_SUPERVISOR_FALLBACK_URL", "http://localhost:8008/v1/systemone")
    assert [(b.name, b.key) for b in supervisor.configured_backends()] == [
        ("jev", "sk-test"),
        ("local", ""),
    ]


# --- runner side: what the supervisor reads, and the tool's wiring -----------------

from omnigent.runner import supervisor_tool  # noqa: E402


def _msg(role: str, text: str) -> dict[str, Any]:
    return {"type": "message", "role": role, "content": [{"type": "input_text", "text": text}]}


def _call_item(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {
        "type": "function_call",
        "name": name,
        "arguments": json.dumps(args),
        "call_id": call_id,
    }


def _out(call_id: str, text: str) -> dict[str, Any]:
    return {"type": "function_call_output", "call_id": call_id, "output": text}


WORKER_ITEMS = [
    _msg("user", "Old brief: rename the helper."),
    _msg("assistant", "Renamed."),
    _msg("user", "IMPLEMENT: make mean([]) return 0.0 in stats.py and add a test."),
    _call_item("sys_os_shell", {"command": "pytest tests/test_stats.py"}, "c1"),
    _out("c1", "1 failed: ZeroDivisionError"),
    _msg("user", "[STILL IN PROGRESS -- continue]"),
    _call_item("Edit", {"file_path": "/repo/stats.py"}, "c2"),
    _out("c2", "ok"),
]
PARENT_ITEMS = [
    _msg("user", "make the stats module safe on empty input"),
    _msg("assistant", "Plan: stats.py mean() divides by len; worker adds the guard and a test."),
    _msg("user", "[System: sub-agent task finished]"),
    _msg("assistant", "Sent the order to the worker."),
]


def test_the_brief_is_the_last_real_order_and_continuations_do_not_count() -> None:
    brief, turn = supervisor_tool.split_turn(WORKER_ITEMS)
    assert brief.startswith("IMPLEMENT: make mean([])")
    progress = supervisor_tool.render_progress(turn)
    assert "called sys_os_shell: pytest tests/test_stats.py" in progress
    assert "1 failed: ZeroDivisionError" in progress
    assert "called Edit: /repo/stats.py" in progress
    assert "rename the helper" not in progress


def test_the_orchestrators_view_skips_framework_notices() -> None:
    request, notes = supervisor_tool.orchestrator_view(PARENT_ITEMS)
    assert request == "make the stats module safe on empty input"
    assert notes.index("Plan: stats.py") < notes.index("Sent the order")


@pytest.mark.asyncio
async def test_the_tool_reads_the_orchestrator_and_the_worker_before_asking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def server(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/sessions/child":
            return httpx.Response(200, json={"parent_session_id": "parent"})
        if path == "/v1/sessions/child/items":
            return httpx.Response(200, json={"data": list(reversed(WORKER_ITEMS))})
        if path == "/v1/sessions/parent/items":
            return httpx.Response(200, json={"data": list(reversed(PARENT_ITEMS))})
        return httpx.Response(404)

    asked: list[tuple[supervisor.Question, supervisor.Knowledge, str]] = []

    async def fake_ask(question, knowledge, *, session_id="", parent_id="", backends=None):  # type: ignore[no-untyped-def]
        asked.append((question, knowledge, parent_id))
        return supervisor.Verdict(
            answer="yes", probabilities={"yes": 0.9, "no": 0.1}, confidence=0.9, sure=True
        )

    monkeypatch.setattr(supervisor, "ask", fake_ask)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(server), base_url="http://server"
    ) as client:
        text = await supervisor_tool.ask_via_rest(
            {"kind": "yes_no", "question": "Is the guard in stats.py enough?", "evidence": "diff"},
            "child",
            client,
        )

    assert text.startswith("Supervisor: yes")
    question, knowledge, parent = asked[0]
    assert (question.kind, question.evidence, parent) == ("yes_no", "diff", "parent")
    assert knowledge.request == "make the stats module safe on empty input"
    assert knowledge.brief.startswith("IMPLEMENT")
    assert "Plan: stats.py" in knowledge.notes and "ZeroDivisionError" in knowledge.progress


@pytest.mark.asyncio
async def test_a_malformed_question_is_answered_with_how_to_fix_it() -> None:
    text = await supervisor_tool.ask_via_rest(
        {"kind": "choice", "question": "Which?", "options": ["a"]}, "c", None
    )
    assert text.startswith("sys_ask_supervisor:") and "2-12" in text


def test_workers_get_the_tool_and_the_rule_only_while_it_is_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.runner import tool_dispatch
    from omnigent.spec.types import AgentSpec
    from omnigent.tools.manager import ToolManager

    (tmp_path / "key").write_text("sk-test")
    monkeypatch.setenv("OMNIGENT_SUPERVISOR_KEY_FILE", str(tmp_path / "key"))
    spec = AgentSpec(spec_version=1)

    monkeypatch.setenv("OMNIGENT_SUPERVISOR", "off")
    assert "sys_ask_supervisor" not in {
        s["function"]["name"] for s in ToolManager(spec).get_tool_schemas()
    }
    assert tool_dispatch._with_supervisor_note("Do X.") == "Do X."

    monkeypatch.setenv("OMNIGENT_SUPERVISOR", "on")
    assert "sys_ask_supervisor" in {
        s["function"]["name"] for s in ToolManager(spec).get_tool_schemas()
    }
    noted = tool_dispatch._with_supervisor_note("Do X.")
    assert (
        noted.startswith("Do X.\n\n[Your supervisor:")
        and tool_dispatch._with_supervisor_note(noted) == noted
    )
