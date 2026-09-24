"""Tests for the supervisor's checklist check on worker results."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest

from omnigent import supervisor, supervisor_checklist
from omnigent.runner import tool_dispatch
from omnigent.supervisor import Backend

JEV = Backend(name="jev", url="https://decide.example/v1", model="typesafe/jev-1.13", key="k")
CHECKLIST = ("Cache-write tokens are counted", "gpt-oss is never recorded")


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNIGENT_SUPERVISOR_LEDGER", str(tmp_path / "asks.jsonl"))
    monkeypatch.setenv("OMNIGENT_SUPERVISOR_CHECKLIST", "on")
    monkeypatch.delenv("OMNIGENT_SUPERVISOR_CHECKLIST_BELOW", raising=False)
    monkeypatch.delenv("OMNIGENT_SUPERVISOR_MIN_CONFIDENCE", raising=False)
    monkeypatch.setattr(supervisor, "configured_backends", lambda: [JEV])
    supervisor_checklist._rounds.clear()
    supervisor_checklist._last_unmet.clear()


def _jev_says(monkeypatch: pytest.MonkeyPatch, p_met: dict[str, float]) -> list[dict[str, Any]]:
    """Answer each checklist question with the P(met) of the item it names."""
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        answers = {
            qid: {"noul": next(p for item, p in p_met.items() if item in q["instructions"])}
            for qid, q in body["questions"].items()
        }
        return httpx.Response(200, json={"answers": answers, "usage": {"cost": 0}})

    monkeypatch.setattr(
        supervisor, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    return seen


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "budget.py").write_text("def count(u):\n    return u['input']\n")
    for args in (
        ["init", "-q"],
        ["add", "-A"],
        ["-c", "user.email=b@b", "-c", "user.name=b", "commit", "-qm", "start"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    (repo / "budget.py").write_text("def count(u):\n    return u['input'] + u['output']\n")
    (repo / "ledger.py").write_text("LEDGER = {}\n")
    (repo / "uv.lock").write_text("lock\n")
    (repo / ".codex-tmp").mkdir()
    (repo / ".codex-tmp" / "rollout.jsonl").write_text("{}\n")
    return repo


def _worker_items(repo: Path, brief: str) -> list[dict[str, Any]]:
    return [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": brief}]},
        {
            "type": "function_call",
            "name": "terminal",
            "call_id": "c1",
            "arguments": json.dumps({"command": f"cd {repo} && uv run pytest -q"}),
        },
        {"type": "function_call_output", "call_id": "c1", "output": "3 passed in 0.1s"},
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Done."}],
        },
    ]


def _server(items: list[dict[str, Any]]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/sessions/child/items":
            return httpx.Response(200, json={"data": list(reversed(items))})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://server")


BRIEF = (
    "IMPLEMENT the counter.\n\nCHECKLIST:\n- Cache-write tokens are counted\n"
    "- gpt-oss is never recorded\n\nReport the test output."
)
PAYLOAD = {
    "type": "sub_agent",
    "status": "completed",
    "agent": "codex-plan",
    "title": "budget",
    "output": "Done.",
}


@pytest.mark.parametrize(
    "brief",
    [
        "Do it.\nCHECKLIST:\n- a\n- b\nThen report.",
        "Do it.\n**Checklist:**\n* a\n* b",
        "## Checklist\n\n1. a\n2) b\n",
        "CHECKLIST:\n- [ ] a\n- [x] b\n- a",
    ],
)
def test_the_checklist_is_the_list_right_after_its_header(brief: str) -> None:
    assert supervisor_checklist.parse_checklist(brief) == ("a", "b")


def test_a_brief_without_a_checklist_has_none() -> None:
    assert supervisor_checklist.parse_checklist("IMPLEMENT x.\n- not a checklist") == ()


def test_a_fix_round_without_a_checklist_uses_the_earlier_one() -> None:
    def user(text: str) -> dict[str, Any]:
        return {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        }

    items = [
        user(BRIEF),
        user("IMPLEMENT (fix round): rename the labels."),
        user("[STILL IN PROGRESS] ..."),
    ]

    assert supervisor_checklist.latest_checklist(items) == CHECKLIST


async def test_the_diff_covers_changed_and_new_files_but_not_locks_or_tool_homes(
    tmp_path: Path,
) -> None:
    diff = await supervisor_checklist.collect_diff([str(_repo(tmp_path))])

    assert "+    return u['input'] + u['output']" in diff
    assert "new file ledger.py:\nLEDGER = {}" in diff
    assert "uv.lock" not in diff and "rollout.jsonl" not in diff


async def test_items_split_at_the_send_back_and_confidence_bars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _jev_says(monkeypatch, {"counted": 0.45, "gpt-oss": 0.6})

    verdict = await supervisor_checklist.decide("state", CHECKLIST, backends=[JEV])

    assert verdict.unmet == ("Cache-write tokens are counted",)
    assert verdict.unsure == ("gpt-oss is never recorded",)


async def test_an_unmet_item_sends_the_worker_back_and_tells_the_orchestrator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    seen = _jev_says(monkeypatch, {"counted": 0.3, "gpt-oss": 0.9})

    async with _server(_worker_items(repo, BRIEF)) as client:
        review = await supervisor_checklist.review(PAYLOAD, server_client=client, child_id="child")

    # Jev reads the code and the runner's evidence, not the worker's report.
    state = seen[0]["state"]
    assert "u['input'] + u['output']" in state and "3 passed" in state and "Done." not in state
    assert review.send_back is not None and "- Cache-write tokens are counted" in review.send_back
    assert "gpt-oss" not in review.send_back
    assert review.notice is not None and "sent back" in review.notice["output"]
    assert "not met (0.30): Cache-write tokens are counted" in review.payload["output"]


async def test_a_met_checklist_delivers_the_result_with_the_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _jev_says(monkeypatch, {"counted": 0.9, "gpt-oss": 0.6})

    async with _server(_worker_items(_repo(tmp_path), BRIEF)) as client:
        review = await supervisor_checklist.review(PAYLOAD, server_client=client, child_id="child")

    assert review.send_back is None
    assert review.payload["output"].startswith("Done.\n\n[Supervisor checklist")
    assert "unsure (0.60): gpt-oss is never recorded" in review.payload["output"]


async def test_the_same_unmet_items_are_not_sent_back_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Jev may simply be unable to see an item in a diff ("X is gone"): the
    # worker reporting it done again goes to the orchestrator, not round two.
    _jev_says(monkeypatch, {"counted": 0.2, "gpt-oss": 0.9})
    async with _server(_worker_items(_repo(tmp_path), BRIEF)) as client:
        first = await supervisor_checklist.review(PAYLOAD, server_client=client, child_id="child")
        supervisor_checklist.count_round(first.round_key, first.unmet)

        second = await supervisor_checklist.review(PAYLOAD, server_client=client, child_id="child")

    assert first.send_back is not None
    assert second.send_back is None
    assert "not met (0.20)" in second.payload["output"]
    assert "already sent back on these same items" in second.payload["output"]


async def test_a_checklist_sends_the_worker_back_at_most_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rounds = iter(
        [
            {"counted": 0.2, "gpt-oss": 0.9},
            {"counted": 0.9, "gpt-oss": 0.2},
            {"counted": 0.2, "gpt-oss": 0.9},
        ]
    )
    async with _server(_worker_items(_repo(tmp_path), BRIEF)) as client:
        for _ in range(supervisor_checklist.MAX_ROUNDS):
            _jev_says(monkeypatch, next(rounds))
            review = await supervisor_checklist.review(
                PAYLOAD, server_client=client, child_id="child"
            )
            assert review.send_back is not None
            supervisor_checklist.count_round(review.round_key, review.unmet)
        _jev_says(monkeypatch, next(rounds))

        review = await supervisor_checklist.review(PAYLOAD, server_client=client, child_id="child")

    assert review.send_back is None
    assert "not met (0.20)" in review.payload["output"]


async def test_nothing_changes_when_off_or_without_a_checklist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _jev_says(monkeypatch, {"counted": 0.1, "gpt-oss": 0.1})
    repo = _repo(tmp_path)
    async with _server(_worker_items(repo, "IMPLEMENT the counter.")) as client:
        without = await supervisor_checklist.review(
            PAYLOAD, server_client=client, child_id="child"
        )
    monkeypatch.setenv("OMNIGENT_SUPERVISOR_CHECKLIST", "off")
    async with _server(_worker_items(repo, BRIEF)) as client:
        off = await supervisor_checklist.review(PAYLOAD, server_client=client, child_id="child")

    assert without.payload is PAYLOAD and off.payload is PAYLOAD and seen == []


async def test_the_inbox_sends_the_worker_back_through_the_normal_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict[str, Any]] = []

    async def fake_review(payload: dict[str, Any], **_: Any) -> supervisor_checklist.Review:
        return supervisor_checklist.Review(
            {**payload, "output": "annotated"},
            "fix it",
            {**payload, "output": "notice"},
            ("child", "k"),
        )

    async def fake_send(args: dict[str, Any], **_: Any) -> str:
        sent.append(args)
        return '{"task_id": "child", "status": "launching"}'

    monkeypatch.setattr(supervisor_checklist, "review", fake_review)
    monkeypatch.setattr(tool_dispatch, "_execute_subagent_tool", fake_send)

    delivered = await tool_dispatch._checked_against_checklist(
        PAYLOAD,
        inbox=None,
        server_client=None,
        conversation_id="parent",
        agent_spec=None,  # type: ignore[arg-type]
    )

    assert delivered["output"] == "notice"
    assert sent == [
        {
            "agent": "codex-plan",
            "title": "budget",
            "args": {"purpose": "implement", "input": "fix it"},
        }
    ]
    assert supervisor_checklist._rounds == {("child", "k"): 1}


async def test_a_failed_send_back_delivers_the_result_with_the_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_review(payload: dict[str, Any], **_: Any) -> supervisor_checklist.Review:
        return supervisor_checklist.Review(
            {**payload, "output": "annotated"},
            "fix it",
            {**payload, "output": "notice"},
            ("child", "k"),
        )

    async def fake_send(args: dict[str, Any], **_: Any) -> str:
        return "Error: sub-agent 'codex-plan' title 'budget' is already running."

    monkeypatch.setattr(supervisor_checklist, "review", fake_review)
    monkeypatch.setattr(tool_dispatch, "_execute_subagent_tool", fake_send)

    delivered = await tool_dispatch._checked_against_checklist(
        PAYLOAD,
        inbox=None,
        server_client=None,
        conversation_id="parent",
        agent_spec=None,  # type: ignore[arg-type]
    )

    assert delivered["output"] == "annotated" and supervisor_checklist._rounds == {}
