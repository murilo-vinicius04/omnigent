"""Lanes: several agents in one project, a busy lane's messages held, new lanes opened."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from omnigent.server import budget, hub_lanes, hub_place, model_router
from omnigent.server import hub as hub_module
from omnigent.server.discussion import DiscussionRegistry
from omnigent.server.hub import HubProject, HubSession, HubState, TurnState, project_line
from omnigent.server.hub_lanes import BusyLane, LaneRun, LaneSpec
from tests.server.test_hub import AMARRAS, P_AMARRAS, _hub, _Router, _Server, _settle

SIM = "5151aaaa5151aaaa5151aaaa5151aaaa"
NEW = "9e9e0000000000000000000000000000"


class _LaneServer(_Server):
    """The fake server with lanes: live status, opening and closing chats."""

    def __init__(self) -> None:
        super().__init__()
        self.opened: list[tuple[str, LaneSpec]] = []
        self.closed: list[str] = []
        self.renamed: list[tuple[str, str]] = []
        self.paths: dict[str, list[str]] = {}
        self.catalogs = {
            "claude": ["sonnet", "opus", "haiku", "opus[1m]"],
            "codex": ["gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-luna"],
            "gemini": ["gemini-3.8-flash-high", "gemini-3.8-flash-low"],
        }

    def backend(self):
        base = super().backend()

        async def status(session_id):
            found = next((s for s in self.sessions if s.id == session_id), None)
            return None if found is None else (found.status or "")

        async def open_chat(project_id, spec):
            self.opened.append((project_id, spec))
            self.sessions.append(
                HubSession(id=NEW, title=spec.topic, project_id=project_id, lane=spec.topic)
            )
            self.projects = [
                replace(r, chat_ids=(NEW, *r.chat_ids)) if r.id == project_id else r
                for r in self.projects
            ]
            return NEW

        async def catalog(provider, like_id):
            return list(self.catalogs.get(provider, []))

        async def paths_in_use(session_id):
            return list(self.paths.get(session_id, []))

        async def rename_chat(session_id, name):
            self.renamed.append((session_id, name))

        async def close_chat(session_id):
            self.closed.append(session_id)
            self.sessions = [s for s in self.sessions if s.id != session_id]

        return replace(
            base,
            status=status,
            open_chat=open_chat,
            catalog=catalog,
            paths_in_use=paths_in_use,
            rename_chat=rename_chat,
            close_chat=close_chat,
        )

    def set_status(self, session_id: str, status: str) -> None:
        self.sessions = [
            replace(s, status=status) if s.id == session_id else s for s in self.sessions
        ]


def _two_lanes(server: _LaneServer, *, sim: str = "running") -> None:
    """amarras runs two lanes: the sim (busy unless told) and its first chat."""
    server.sessions.append(
        HubSession(
            id=SIM,
            title="Franka sim",
            status=sim,
            last_ask="tune the MPC gains",
            project_id=P_AMARRAS,
            lane="sim",
            run=hub_module.SessionRun(provider="Claude Code", model="claude-opus-5-5"),
        )
    )
    server.projects[0] = replace(server.projects[0], chat_ids=(SIM, AMARRAS))


# -- the helpers -----------------------------------------------------------------


def test_a_tier_word_launches_the_newest_model_its_cli_lists() -> None:
    codex = ["gpt-6.1-sol", "gpt-6-sol", "gpt-5.6-sol", "gpt-6-luna", "gpt-5.6-luna"]
    assert hub_lanes.launch_model("codex", "Sol", "xhigh", codex) == ("gpt-6.1-sol", "xhigh")
    assert hub_lanes.launch_model("codex", "Luna", "high", codex) == ("gpt-6-luna", "high")
    # The plain id, not the long-window variant of the same model.
    assert hub_lanes.launch_model("claude", "Opus", "high", ["opus[1m]", "opus"]) == (
        "opus",
        "high",
    )
    # agy's effort is in the id, so none rides separately.
    agy = ["gemini-3.8-flash-high", "gemini-3.8-flash-low", "claude-opus-5-5-high"]
    assert hub_lanes.launch_model("gemini", "Flash", "low", agy) == ("gemini-3.8-flash-low", "")
    assert hub_lanes.launch_model("gemini", "Argon", "high", agy) is None


@pytest.mark.parametrize(
    ("words", "named"),
    [
        ("use Luna", ("codex", "Luna")),
        ("Opus please", ("claude", "Opus")),
        ("on Codex", ("codex", "")),
        ("gemini", ("gemini", "")),
        ("", ("", "")),
        ("whatever is cheapest", ("", "")),
    ],
)
def test_the_model_they_name_is_read_from_their_words(words, named) -> None:
    assert hub_lanes.named_run(words) == named


def test_the_checkouts_a_lane_writes_in_are_found_from_its_paths(tmp_path) -> None:
    repo = tmp_path / "spot-teleop"
    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()
    worktree = tmp_path / "wt" / "lane"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: elsewhere")

    used = hub_lanes.repos_in_use(
        [str(repo / "src" / "mpc.py"), str(repo / "src"), str(worktree / "a.py"), "/nowhere/x"]
    )

    assert used == [str(repo), str(worktree)]
    assert hub_lanes.repo_root(str(tmp_path)) is None


def test_a_new_lanes_brief_names_the_busy_lanes_and_their_checkouts() -> None:
    alone = hub_lanes.brief("RAL", "joystick plan", [])
    beside = hub_lanes.brief(
        "RAL",
        "Joystick plan!",
        [BusyLane(name="RAL · sim", task="tune the MPC gains", repos=("/home/u/spot-teleop",))],
    )

    assert (
        alone == "[The hub opened this chat as a new lane of the project RAL, for: joystick plan.]"
    )
    assert '- RAL · sim on "tune the MPC gains"; writing in /home/u/spot-teleop' in beside
    assert "branch (hub/joystick-plan)" in beside


def test_a_project_with_several_lanes_lists_each_under_its_own_handle() -> None:
    lanes = (
        HubSession(
            id=SIM, title="Franka sim", nickname="amarras · sim", handle="5151", status="running"
        ),
        HubSession(id=AMARRAS, title="Docs", nickname="amarras · Docs", handle="d14b"),
    )
    line = project_line(
        HubProject(
            id=P_AMARRAS, name="amarras", handle="aaaa", chats=3, chat=lanes[0], lanes=lanes
        )
    )

    head, sim, docs, older = line.splitlines()
    assert head.startswith("[aaaa] amarras · 2 lanes")
    assert sim.startswith('  - lane [5151] amarras · sim · "Franka sim" · WORKING')
    assert docs.startswith('  - lane [d14b] amarras · Docs · "Docs" · idle')
    assert older == "  (1 older chats, not lanes)"


# -- sending to a lane, and holding for a busy one ---------------------------------


async def test_a_send_naming_a_lane_goes_to_that_lane(tmp_path) -> None:
    router = _Router(
        '{"send": "aaaa", "lane": "d14b", "message": "Fix the docs.", "say": "Sent."}'
    )
    server = _LaneServer()
    _two_lanes(server)
    server.turns = [TurnState(status="running")]
    hub = _hub(tmp_path, router, server)

    hub.submit("manda a amarras arrumar a doc")
    await _settle(hub, until=lambda: server.sent)
    await hub.close()

    assert server.sent == [(AMARRAS, "Fix the docs.")]
    assert "lane [5151] amarras · sim" in router.prompts[0]
    sent = hub.state.entries[-1]
    assert (sent.kind, sent.session_name) == ("sent", "amarras · Project Analysis")


async def test_a_message_for_a_busy_lane_waits_until_it_finishes(tmp_path, monkeypatch) -> None:
    # The router decides alone (it has learned where their messages go).
    monkeypatch.setattr(hub_place, "trusted", lambda path: True)
    router = _Router(
        '{"send": "5151", "message": "Also log the gains.", "say": "Vai quando terminar."}',
        '{"send": "5151", "message": "And plot them.", "say": "Vai quando terminar."}',
    )
    server = _LaneServer()
    _two_lanes(server)
    server.turns = [TurnState(status="running")]
    hub = _hub(tmp_path, router, server)

    hub.submit("e manda logar os ganhos")
    await _settle(hub, until=lambda: any(e.kind == "held" for e in hub.state.entries))
    hub.submit("e plotar")
    await _settle(hub, until=lambda: len([e for e in hub.state.entries if e.kind == "held"]) == 2)
    assert server.sent == []
    held = [e for e in hub.state.entries if e.kind == "held"]
    assert [(e.text, e.detail, e.session_id) for e in held] == [
        ("Vai quando terminar.", "Also log the gains.", SIM),
        ("Vai quando terminar.", "And plot them.", SIM),
    ]
    # Kept on disk, so a restart does not lose them.
    assert json.loads((tmp_path / "u1" / "held.json").read_text())[SIM]["name"] == "amarras · sim"

    server.set_status(SIM, "idle")
    await _settle(hub, until=lambda: server.sent)
    await hub.close()

    # Both go together, once, after the lane stayed idle.
    assert server.sent == [(SIM, "Also log the gains.\n\nAnd plot them.")]
    assert hub.state.held == {}
    assert hub.state.entries[-1].kind == "sent"


async def test_tell_it_now_goes_into_the_busy_lane_at_once(tmp_path) -> None:
    router = _Router(
        '{"send": "5151", "message": "Stop, the gains are wrong.", "say": "Sent.", "now": true}'
    )
    server = _LaneServer()
    _two_lanes(server)
    server.turns = [TurnState(status="running")]
    hub = _hub(tmp_path, router, server)

    hub.submit("fala agora pra ele parar")
    await _settle(hub, until=lambda: server.sent)
    await hub.close()

    assert server.sent == [(SIM, "Stop, the gains are wrong.")]
    assert not any(e.kind == "held" for e in hub.state.entries)


async def test_tell_it_now_delivers_what_was_held_once_and_never_again(tmp_path) -> None:
    router = _Router(
        '{"send": "5151", "message": "Are the two ZEDs placed for coverage?", "say": "Held."}',
        # The router restates the held question when told "now".
        '{"send": "5151", "message": "Are the two ZEDs placed for best coverage?",'
        ' "say": "Sent.", "now": true}',
        '{"send": "5151", "message": "", "say": "Held."}',
    )
    server = _LaneServer()
    _two_lanes(server)
    server.turns = [TurnState(status="running")]
    hub = _hub(tmp_path, router, server)

    hub.submit("as zeds estão otimizadas?")
    await _settle(hub, until=lambda: hub.state.held)
    hub.submit("fala agora")
    await _settle(hub, until=lambda: server.sent)
    assert server.sent == [(SIM, "Are the two ZEDs placed for coverage?")]
    assert hub.state.held == {}
    # Told "now", the router saw what was held; afterwards nothing is.
    hub.submit("e o que mais?")
    await _settle(hub, until=lambda: len(router.prompts) == 3)
    assert "1 message(s) held for it until it finishes" in router.prompts[1]
    assert "message(s) held for it" not in router.prompts[2]

    server.set_status(SIM, "idle")
    server.turns = [TurnState(status="idle", reply="Placed by hand.")]
    await _settle(hub, until=lambda: len(server.sent) == 2, timeout=3.0)
    await hub.close()
    # Only the third message is delivered when the lane finishes; the first went once.
    assert server.sent[1] == (SIM, "e o que mais?")


async def test_tell_it_now_with_something_new_sends_it_with_what_was_held(tmp_path) -> None:
    server = _LaneServer()
    _two_lanes(server)
    hub = _hub(tmp_path, _Router(), server)
    sim = HubSession(id=SIM, title="sim", nickname="amarras · sim", status="running")
    await hub.hold(sim, "Plot the gains.", via="typed")

    await hub.send_held_now(sim, "Stop: the gains are wrong.", via="typed")
    await hub.close()

    assert server.sent == [(SIM, "Plot the gains.\n\nStop: the gains are wrong.")]
    assert hub.state.held == {}


async def test_a_held_message_waits_for_the_lanes_report_and_survives_a_restart(tmp_path) -> None:
    server = _LaneServer()
    _two_lanes(server, sim="idle")
    state = HubState("u1", root=tmp_path)
    state.hold(SIM, "amarras · sim", {"message": "Then plot it.", "via": "voice", "files": []})
    server.turns = [TurnState(status="running")]
    hub = _hub(tmp_path, _Router(), server)

    # A watch still waiting for the lane's report holds the delivery back.
    hub.watch(HubSession(id=SIM, title="sim", nickname="amarras · sim"), time.time())
    assert hub.resume_watches() == 1
    await asyncio.sleep(0.1)
    assert server.sent == []

    server.turns = [TurnState(status="idle", reply="Gains tuned.")]
    await _settle(hub, until=lambda: server.sent)
    await hub.close()

    assert server.sent == [(SIM, "Then plot it.")]
    kinds = [e.kind for e in hub.state.entries]
    assert kinds.index("finished") < kinds.index("sent")


async def test_a_lane_closed_while_holding_says_what_was_not_sent(tmp_path) -> None:
    server = _LaneServer()
    _two_lanes(server)
    hub = _hub(tmp_path, _Router(), server)
    await hub.hold(
        HubSession(id=SIM, title="sim", nickname="amarras · sim"), "Plot it.", via="typed"
    )

    server.sessions = [s for s in server.sessions if s.id != SIM]
    await _settle(hub, until=lambda: hub.state.entries[-1].kind == "attention")
    await hub.close()

    assert (
        hub.state.entries[-1].text
        == "amarras · sim was closed, so what I held for it was not sent."
    )
    assert hub.state.entries[-1].detail == "Plot it." and server.sent == []


# -- opening a lane ----------------------------------------------------------------


@pytest.fixture
def judged(monkeypatch):
    """The decision model says economical / high; Codex has the most budget left."""
    decision = model_router.TierDecision(
        tier="economical",
        tier_probs={"economical": 0.7},
        effort="high",
        efforts_by_tier={"economical": "high", "middle": "high", "top": "xhigh"},
    )
    seen: list[model_router.RouterTask] = []

    async def judge(task, backend):
        seen.append(task)
        return decision

    budgets = [
        budget.ProviderBudget("claude", 45.0, None, daily=12.1, remaining=3.0),
        budget.ProviderBudget("codex", 0.0, None, daily=12.1, remaining=24.0),
    ]
    monkeypatch.setattr(model_router, "judge", judge)
    monkeypatch.setattr(model_router, "configured", lambda hub_dir: object())
    monkeypatch.setattr(budget, "today", lambda hub_dir: budgets)
    return seen


async def test_new_work_beside_a_busy_lane_opens_a_lane_on_the_routers_pick(
    tmp_path, judged, monkeypatch
) -> None:
    repo = tmp_path / "spot-teleop"
    (repo / ".git").mkdir(parents=True)
    router = _Router(
        '{"new_lane": "aaaa", "topic": "joystick plan", "message": "Plan the joystick work.",'
        ' "say": "Abri uma frente nova no amarras."}'
    )
    server = _LaneServer()
    _two_lanes(server)
    server.paths[SIM] = [str(repo / "mpc.py")]
    server.turns = [TurnState(status="running")]
    hub = _hub(tmp_path, router, server)
    hub._judge_tiers = True
    monkeypatch.setattr(hub_module, "HUB_DIR", tmp_path)

    hub.submit("abre outra frente pra planejar o joystick")
    await _settle(hub, until=lambda: server.sent)
    await hub.close()

    project_id, spec = server.opened[0]
    assert project_id == P_AMARRAS and spec.topic == "joystick plan"
    # Economical tier, Codex by quota, high effort kept within the tier.
    assert spec.run == LaneRun(
        provider="codex",
        model="gpt-6-luna",
        effort="high",
        tier="economical",
        reason=spec.run.reason,
    )
    assert "Codex: 24.0% of its week left today" in spec.run.reason
    # It starts beside the project's newest chat; its folder is not a checkout.
    assert spec.like == SIM and spec.branch == ""
    # The request goes first, then the brief; the hub chat quotes only the request.
    ((sent_to, text),) = server.sent
    assert sent_to == NEW and text.startswith("Plan the joystick work.\n\n[The hub opened")
    assert f"writing in {repo}" in text
    lane, sent = hub.state.entries[-2:]
    assert (lane.kind, lane.session_id, lane.session_name) == (
        "lane",
        NEW,
        "amarras · joystick plan",
    )
    assert lane.text.startswith("New lane: joystick plan, on Codex · gpt-6-luna · high.")
    assert (sent.kind, sent.detail, sent.text) == (
        "sent",
        "Plan the joystick work.",
        "Abri uma frente nova no amarras.",
    )
    # Judged once, logged once, beside what the lane runs on.
    assert len(judged) == 1
    (row,) = [
        json.loads(line) for line in (tmp_path / "u1" / "router.jsonl").read_text().splitlines()
    ]
    assert row["session_id"] == NEW and row["lane"] == "joystick plan"
    assert row["ran_on"] == {"provider": "Codex", "model": "gpt-6-luna", "effort": "high"}
    assert row["proposal"]["provider"] == "codex"


async def test_a_lane_they_name_a_model_for_runs_on_it(tmp_path, judged, monkeypatch) -> None:
    router = _Router(
        '{"new_lane": "aaaa", "topic": "docs", "message": "Write the docs.", "say": "Ok.",'
        ' "use": "Sonnet"}'
    )
    server = _LaneServer()
    _two_lanes(server)
    server.turns = [TurnState(status="running")]
    hub = _hub(tmp_path, router, server)
    hub._judge_tiers = True
    monkeypatch.setattr(hub_module, "HUB_DIR", tmp_path)

    hub.submit("abre uma frente com o sonnet pra doc")
    await _settle(hub, until=lambda: server.sent)
    await hub.close()

    run = server.opened[0][1].run
    assert (run.provider, run.model, run.effort, run.reason) == (
        "claude",
        "sonnet",
        "high",
        "You asked for Sonnet.",
    )


async def test_no_lane_opens_when_no_provider_has_budget_for_its_tier(
    tmp_path, judged, monkeypatch
) -> None:
    spent = [
        budget.ProviderBudget(
            "claude", 99.0, None, daily=12.1, remaining=0.0, blocked="today's budget is spent"
        ),
        budget.ProviderBudget(
            "codex", 99.0, None, daily=12.1, remaining=0.0, blocked="today's budget is spent"
        ),
    ]
    monkeypatch.setattr(budget, "today", lambda hub_dir: spent)
    router = _Router(
        '{"new_lane": "aaaa", "topic": "docs", "message": "Write docs.", "say": "Ok."}'
    )
    server = _LaneServer()
    _two_lanes(server)
    hub = _hub(tmp_path, router, server)
    hub._judge_tiers = True
    monkeypatch.setattr(hub_module, "HUB_DIR", tmp_path)

    hub.submit("abre uma frente pra doc")
    await _settle(hub)
    await hub.close()

    assert server.opened == [] and server.sent == []
    said = hub.state.entries[-1]
    assert said.kind == "attention" and "economical tier" in said.text
    assert '"use Sonnet"' in said.text


async def test_without_the_decision_model_a_lane_runs_like_the_projects_newest(tmp_path) -> None:
    router = _Router(
        '{"new_lane": "aaaa", "topic": "docs", "message": "Write docs.", "say": "Ok."}'
    )
    server = _LaneServer()
    _two_lanes(server)
    server.turns = [TurnState(status="running")]
    hub = _hub(tmp_path, router, server)

    hub.submit("abre uma frente pra doc")
    await _settle(hub, until=lambda: server.sent)
    await hub.close()

    run = server.opened[0][1].run
    assert (run.provider, run.model) == ("claude", "claude-opus-5-5")
    assert run.reason == "Same as amarras · sim: the router is off."


async def test_a_lanes_folder_that_is_a_busy_checkout_gets_its_own_worktree(tmp_path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    router = _Router('{"new_lane": "aaaa", "topic": "Fix CI", "message": "Fix CI.", "say": "Ok."}')
    server = _LaneServer()
    _two_lanes(server)
    server.projects[0] = replace(server.projects[0], folder=str(repo))
    server.paths[SIM] = [str(repo / "a.py")]
    server.turns = [TurnState(status="running")]
    hub = _hub(tmp_path, router, server)

    hub.submit("abre outra frente pra arrumar o CI")
    await _settle(hub, until=lambda: server.sent)
    await hub.close()

    assert server.opened[0][1].branch == "hub/fix-ci"


# -- renaming and closing lanes ----------------------------------------------------


async def test_a_lane_is_renamed_and_closed_after_its_handoff_note(tmp_path) -> None:
    server = _LaneServer()
    _two_lanes(server, sim="idle")
    server.turns = [
        TurnState(status="running"),
        TurnState(status="idle", reply="Wrote HANDOFF.md."),
    ]
    hub = _hub(tmp_path, _Router(), server)
    sim = next(
        lane
        for p in await hub.projects(shown=hub_module.CHATS_SHOWN)
        for lane in p.lanes
        if lane.id == SIM
    )

    await hub.rename_lane(sim, "  Franka   MPC ")
    entry = await hub.close_lane(sim, handoff=True)
    await _settle(hub, until=lambda: server.closed)
    await hub.close()

    assert server.renamed == [(SIM, "Franka MPC")]
    assert entry.kind == "sent" and server.sent == [(SIM, hub_module.HANDOFF_REQUEST)]
    assert server.closed == [SIM]
    kinds = [e.kind for e in hub.state.entries]
    assert kinds[-2:] == ["finished", "project"]


async def test_a_working_lane_is_not_closed(tmp_path) -> None:
    server = _LaneServer()
    _two_lanes(server)
    hub = _hub(tmp_path, _Router(), server)
    sim = HubSession(id=SIM, title="sim", status="running")

    with pytest.raises(ValueError, match="is working"):
        await hub.close_lane(sim, handoff=False)
    assert server.closed == []


# -- the store backend -------------------------------------------------------------


async def test_a_lane_is_created_through_the_sessions_route_beside_its_chat() -> None:
    like = SimpleNamespace(id=SIM, host_id="host1", workspace="/home/u", labels={})
    store = SimpleNamespace(get_conversation=lambda sid: like if sid == SIM else None)
    app = FastAPI()
    created: list[dict] = []

    @app.get("/v1/agents")
    async def agents():
        return {"data": [{"id": "ag-codex", "name": "codex-native-ui"}]}

    @app.post("/v1/sessions")
    async def create(body: dict):
        created.append(body)
        return {"id": NEW}

    registry = DiscussionRegistry()
    backend = hub_module.store_backend(
        store, user_id="morys5", app=app, headers={}, companions=lambda: registry
    )
    run = LaneRun(provider="codex", model="gpt-6-luna", effort="high")

    made = await backend.open_chat(P_AMARRAS, LaneSpec(topic="joystick", run=run, like=SIM))

    assert made == NEW
    assert created == [
        {
            "agent_id": "ag-codex",
            "project_id": P_AMARRAS,
            "host_id": "host1",
            "workspace": "/home/u",
            "title": "joystick",
            "labels": {
                "omnigent.ui": "terminal",
                "omnigent.wrapper": "codex-native-ui",
                "omnigent.hub.lane": "joystick",
            },
            "model_override": "gpt-6-luna",
            "reasoning_effort": "high",
        }
    ]
