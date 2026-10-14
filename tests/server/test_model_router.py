"""The hub's tier judge: questions to the decision model, its answers, the shadow log."""

from __future__ import annotations

import json

import httpx
import pytest

from omnigent import supervisor
from omnigent.server import model_router
from omnigent.server.model_router import RouterTask
from omnigent.supervisor import Backend

BACKEND = Backend(name="reflex", url="http://reflex.test/v1/systemone", model="reflex")


def _answers(questions: dict) -> dict:
    out = {
        "tier": {
            "choice": "top",
            "probabilities": {"economical": 0.1, "middle": 0.2, "top": 0.7},
        },
        "effort_economical": {
            "choice": "xhigh",
            "probabilities": {"low": 0.05, "medium": 0.05, "high": 0.2, "xhigh": 0.7},
        },
        "effort_middle": {
            "choice": "medium",
            "probabilities": {"low": 0.1, "medium": 0.45, "high": 0.3, "xhigh": 0.15},
        },
        "effort_top": {
            "choice": "low",
            "probabilities": {"low": 0.65, "medium": 0.2, "high": 0.1, "xhigh": 0.05},
        },
        "cheap_redo": {"noul": 0.81},
        "difficulty": {"score": 3.2, "probabilities": {"3": 0.6, "4": 0.3}},
        "category": {"choice": "debug", "probabilities": {"debug": 0.9, "code": 0.1}},
    }
    if "fixes_previous" in questions:
        out["fixes_previous"] = {"noul": 0.93}
    return out


@pytest.fixture
def reflex(monkeypatch):
    """A fake reflex server: records each request body and answers every question."""
    bodies: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        return httpx.Response(200, json={"answers": _answers(body["questions"]), "usage": {}})

    monkeypatch.setattr(
        supervisor, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )
    return bodies


async def test_a_request_is_judged_on_tier_effort_redo_risk_difficulty_and_kind(reflex) -> None:
    task = RouterTask(request="Find why the gripper drifts after homing.", project="RAL")

    decision = await model_router.judge(task, BACKEND)

    assert decision.tier == "top" and decision.tier_probs["top"] == 0.7
    # Each tier's effort is its own; the chosen tier's is the one used. Effort
    # leaves high only when the model is sure: medium at 45% stays high.
    assert decision.effort == "low" and decision.effort_probs["low"] == 0.65
    assert decision.efforts_by_tier == {"economical": "xhigh", "middle": "high", "top": "low"}
    assert (decision.p_cheap_redo, decision.difficulty, decision.category) == (0.81, 3.2, "debug")
    # No previous request: nothing to call a fix.
    assert decision.p_fixes_previous is None and decision.error == ""
    body = reflex[0]
    assert set(body["questions"]) == {
        "tier",
        "effort_economical",
        "effort_middle",
        "effort_top",
        "cheap_redo",
        "difficulty",
        "category",
    }
    assert set(body["questions"]["tier"]["criteria"]) == {"economical", "middle", "top"}
    assert set(body["questions"]["effort_top"]["criteria"]) == {"low", "medium", "high", "xhigh"}
    assert "Haiku 5.5" in body["questions"]["effort_economical"]["instructions"]
    assert body["state"].startswith("## Request sent to the project\nFind why the gripper")
    assert "## Project\nRAL" in body["state"]


async def test_a_follow_up_is_asked_whether_it_fixes_the_previous_result(reflex) -> None:
    task = RouterTask(
        request="It still drifts; the offset is applied twice.",
        project="RAL",
        previous="Find why the gripper drifts after homing.",
        previous_result="Fixed: the homing offset was missing.",
    )

    decision = await model_router.judge(task, BACKEND)

    assert decision.p_fixes_previous == 0.93
    state = reflex[0]["state"]
    assert "## The project's previous request\nFind why the gripper drifts" in state
    assert "## What the project reported back for it\nFixed: the homing offset" in state


async def test_a_backend_that_is_down_is_logged_not_raised(monkeypatch) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(
        supervisor, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    )

    decision = await model_router.judge(RouterTask(request="hi", project="RAL"), BACKEND)

    assert decision.tier == "" and decision.error.startswith("ConnectError")


async def test_an_answer_without_tier_numbers_is_an_error(monkeypatch) -> None:
    def bare(request: httpx.Request) -> httpx.Response:
        answers = _answers({})
        answers["tier"] = {"choice": "top"}
        return httpx.Response(200, json={"answers": answers})

    monkeypatch.setattr(
        supervisor, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(bare))
    )

    decision = await model_router.judge(RouterTask(request="hi", project="RAL"), BACKEND)

    assert decision.tier == "" and "no tier probabilities" in decision.error


def test_shadow_decisions_are_on_only_when_switched_on(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OMNIGENT_HUB_ROUTER_URL", raising=False)
    assert model_router.configured(tmp_path) is None

    (tmp_path / model_router.FLAG_NAME).write_text("")
    assert model_router.configured(tmp_path).url == model_router.DEFAULT_URL

    (tmp_path / model_router.FLAG_NAME).write_text("http://gpu:9000/v1/systemone\n")
    assert model_router.configured(tmp_path).url == "http://gpu:9000/v1/systemone"

    monkeypatch.setenv("OMNIGENT_HUB_ROUTER_URL", "off")
    assert model_router.configured(tmp_path) is None


def test_the_log_keeps_the_decision_beside_the_model_that_ran_it(tmp_path) -> None:
    task = RouterTask(
        request="x" * 5000,
        project="RAL",
        session_id="s1",
        provider="Claude",
        model="claude-opus-5-5",
        effort="high",
    )
    decision = model_router.TierDecision(tier="middle", p_cheap_redo=0.4, latency_ms=120)

    model_router.log_decision(tmp_path / "router.jsonl", task, decision)

    line = json.loads((tmp_path / "router.jsonl").read_text())
    assert line["ran_on"] == {"provider": "Claude", "model": "claude-opus-5-5", "effort": "high"}
    assert (line["tier"], line["p_cheap_redo"], line["latency_ms"]) == ("middle", 0.4, 120)
    assert len(line["request"]) == 2000 and line["had_previous"] is False
