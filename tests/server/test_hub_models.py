"""What each session runs on, as the hub shows it and changes it."""

from __future__ import annotations

import asyncio
import time

import pytest

from omnigent.server import voice_hub
from omnigent.server.hub import (
    Hub,
    HubBackend,
    HubCandidate,
    HubSession,
    HubState,
    ProjectRecord,
    TurnState,
    parse_decision,
    session_line,
)
from omnigent.server.hub_models import (
    ModelChoice,
    SessionRun,
    match_effort,
    match_model,
    run_from_snapshot,
)

AMARRAS = "d14bc7496a6a42b6b6a0730b3f4f5f20"
SOL = "ba9b8af460dd40f5b333b58f72d0683c"

CLAUDE_SNAPSHOT = {
    "labels": {"omnigent.wrapper": "claude-code-native-ui", "omnigent.ui": "terminal"},
    "harness": "claude-native",
    "llm_model": "claude-opus-5-5",
    "model_override": "opus",
    "reasoning_effort": "xhigh",
    "model_options": [
        {"id": "opus", "model": "claude-opus-5-5", "displayName": "Opus 5.5", "isDefault": True},
        {"id": "sonnet", "model": "claude-sonnet-5-5", "displayName": "Sonnet 5.5"},
        {"id": "haiku", "model": "claude-haiku-5-5", "displayName": "Haiku 5.5"},
    ],
}


def _efforts(*names: str) -> list[dict[str, str]]:
    return [{"reasoningEffort": n} for n in names]


CODEX_SNAPSHOT = {
    "labels": {"omnigent.wrapper": "codex-native-ui"},
    "harness": "codex-native",
    "llm_model": "gpt-6-luna",
    "reasoning_effort": "high",
    "model_options": [
        {
            "id": "gpt-6.1-sol",
            "displayName": "GPT-6.1 Sol",
            "isDefault": True,
            "supportedReasoningEfforts": _efforts("low", "medium", "high", "xhigh"),
        },
        {
            "id": "gpt-6-luna",
            "displayName": "GPT-6 Luna",
            "supportedReasoningEfforts": _efforts("low", "medium", "high"),
        },
    ],
}


def test_a_claude_session_shows_its_reported_model_and_claude_efforts() -> None:
    run = run_from_snapshot(CLAUDE_SNAPSHOT)
    assert (run.provider, run.model, run.effort, run.kind) == (
        "Claude Code",
        "claude-opus-5-5",
        "xhigh",
        "claude",
    )
    assert [m.id for m in run.models] == ["opus", "sonnet", "haiku"]
    assert run.efforts == ("low", "medium", "high", "xhigh", "max")
    # The reported wire model reads as its catalog row.
    assert run.summary() == "Claude Code · Opus 5.5 · effort xhigh"


def test_a_codex_session_offers_the_efforts_of_its_current_model() -> None:
    run = run_from_snapshot(CODEX_SNAPSHOT)
    assert (run.provider, run.model, run.efforts) == (
        "Codex",
        "gpt-6-luna",
        ("low", "medium", "high"),
    )


def test_a_pick_it_has_not_reported_yet_shows_as_requested() -> None:
    # "opus" and "claude-opus-5-5" are one model: nothing is on its way.
    assert run_from_snapshot(CLAUDE_SNAPSHOT).requested == ""
    run = run_from_snapshot({**CLAUDE_SNAPSHOT, "model_override": "sonnet"})
    assert (run.model, run.requested) == ("claude-opus-5-5", "sonnet")
    assert run.summary() == "Claude Code · Opus 5.5 · switching to Sonnet 5.5 · effort xhigh"


def test_an_agent_without_a_picker_offers_nothing_to_change() -> None:
    run = run_from_snapshot(
        {"labels": {}, "harness": "claude-sdk", "agent_name": "nessie", "model_override": "x"}
    )
    assert (run.provider, run.model, run.models, run.efforts) == ("nessie", "x", (), ())


def test_the_host_catalog_stands_in_until_the_session_reports_its_own() -> None:
    snapshot = {**CLAUDE_SNAPSHOT, "model_options": []}
    run = run_from_snapshot(snapshot, [{"id": "sonnet", "displayName": "Sonnet 5.5"}])
    assert [m.label for m in run.models] == ["Sonnet 5.5"]


MODELS = (
    ModelChoice(id="opus", label="Opus 5.5", model="claude-opus-5-5"),
    ModelChoice(id="sonnet", label="Sonnet 5.5", model="claude-sonnet-5-5"),
    ModelChoice(id="sonnet_5", label="Sonnet 5", model="claude-sonnet-5"),
)


@pytest.mark.parametrize(
    ("wanted", "ids"),
    [
        ("opus", ["opus"]),
        ("Opus 5.5", ["opus"]),
        ("claude-sonnet-5", ["sonnet_5"]),  # exact beats partial
        ("sonnet", ["sonnet"]),  # its id, exactly
        ("sonnet 5", ["sonnet_5"]),
        ("son", ["sonnet", "sonnet_5"]),  # ambiguous: the caller asks
        ("gpt", []),
    ],
)
def test_a_model_is_matched_by_id_wire_name_or_label(wanted, ids) -> None:
    assert [m.id for m in match_model(MODELS, wanted)] == ids


@pytest.mark.parametrize(
    ("wanted", "found"),
    [
        ("high", "high"),
        ("Extra High", "xhigh"),
        ("máximo", "max"),
        ("alto", "high"),
        ("ultra", None),
    ],
)
def test_efforts_are_matched_in_english_or_portuguese(wanted, found) -> None:
    assert match_effort(("low", "medium", "high", "xhigh", "max"), wanted) == found


def test_a_session_line_says_what_it_runs_and_offers() -> None:
    session = HubSession(
        id=AMARRAS, title="Amarras", handle="d14b", run=run_from_snapshot(CLAUDE_SNAPSHOT)
    )
    line = session_line(session)
    assert "runs Claude Code · Opus 5.5 · effort xhigh" in line
    assert "models it offers: opus (Opus 5.5), sonnet (Sonnet 5.5), haiku (Haiku 5.5)" in line
    assert "efforts: low, medium, high, xhigh, max" in line


def test_a_model_decision_parses() -> None:
    decision = parse_decision('{"model": "d14b", "to": "sonnet", "effort": "high"}')
    assert (decision.action, decision.handle, decision.model, decision.effort) == (
        "model",
        "d14b",
        "sonnet",
        "high",
    )


# -- changing it --------------------------------------------------------------


class _Router:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.remembered: list[str] = []

    async def decide(self, prompt: str, *, said: str, timeout_s: float) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0)

    async def follow_up(self, prompt: str, *, timeout_s: float) -> str:
        return self.replies.pop(0)

    def remember(self, text: str) -> None:
        self.remembered.append(text)


class _Server:
    def __init__(self) -> None:
        self.sessions = [
            HubSession(id=AMARRAS, title="Amarras", run=run_from_snapshot(CLAUDE_SNAPSHOT)),
            HubSession(id=SOL, title="Sol review", run=run_from_snapshot(CODEX_SNAPSHOT)),
        ]
        self.configured: list[tuple[str, str | None, str | None]] = []
        self.fail = False

    def backend(self) -> HubBackend:
        async def sessions(ids):
            return [s for s in self.sessions if s.id in ids]

        async def listing():
            return [HubCandidate(id=s.id, title=s.title) for s in self.sessions]

        async def nothing(*_args):
            return ""

        async def turn(_session_id, _since):
            return TurnState(status="idle")

        async def configure(session_id, model, effort):
            if self.fail:
                raise RuntimeError("runner gone")
            self.configured.append((session_id, model, effort))

        async def projects():
            # Each chat is its own project, named like its chat.
            return [
                ProjectRecord(id=f"{n}{n}{n}{n}" + "0" * 28, name=s.title, chat_ids=(s.id,))
                for n, s in zip("ab", self.sessions, strict=True)
            ]

        return HubBackend(
            sessions=sessions,
            listing=listing,
            activity=nothing,
            send=nothing,
            check=nothing,
            turn=turn,
            projects=projects,
            create_project=nothing,
            rename_project=nothing,
            delete_project=nothing,
            file_chat=nothing,
            configure=configure,
        )


def _hub(tmp_path, router: _Router, server: _Server) -> Hub:
    async def factory():
        return router

    hub = Hub("u1", router_factory=factory, state=HubState("u1", root=tmp_path), poll_s=0.01)
    hub.bind(server.backend())
    return hub


async def _settle(hub: Hub) -> None:
    deadline = time.monotonic() + 2
    while hub._tasks and time.monotonic() < deadline:
        await asyncio.sleep(0.01)


async def _session(hub: Hub, session_id: str) -> HubSession:
    return next(s for s in await hub.sessions() if s.id == session_id)


async def test_asking_for_another_model_changes_it_and_says_so(tmp_path) -> None:
    router = _Router('{"model": "aaaa", "to": "sonnet", "effort": "high"}')
    server = _Server()
    hub = _hub(tmp_path, router, server)
    hub.submit("coloca a amarras no sonnet, esforço alto")
    await _settle(hub)

    assert server.configured == [(AMARRAS, "sonnet", "high")]
    assert "models it offers: opus (Opus 5.5)" in router.prompts[0]
    last = hub.state.entries[-1]
    assert (last.kind, last.session_id) == ("model", AMARRAS)
    assert last.text.startswith("Switched Amarras to Sonnet 5.5 at high effort;")
    assert router.remembered == [last.text]


async def test_a_partial_name_that_fits_one_model_is_enough(tmp_path) -> None:
    hub = _hub(tmp_path, _Router(), server := _Server())
    entry = await hub.set_model(await _session(hub, AMARRAS), "haik", "", via="voice")
    assert server.configured == [(AMARRAS, "haiku", None)]
    assert (entry.kind, entry.via) == ("model", "voice")


async def test_an_ambiguous_or_unknown_model_changes_nothing_and_lists_the_offer(tmp_path) -> None:
    hub = _hub(tmp_path, _Router(), server := _Server())
    amarras = await _session(hub, AMARRAS)
    unknown = await hub.set_model(amarras, "gpt-6.1-sol", "", via="typed")
    assert "has no model like 'gpt-6.1-sol'" in unknown.text
    assert "offers: Opus 5.5, Sonnet 5.5, Haiku 5.5" in unknown.text
    assert server.configured == []


async def test_an_effort_the_new_codex_model_lacks_changes_nothing(tmp_path) -> None:
    hub = _hub(tmp_path, _Router(), server := _Server())
    sol = await _session(hub, SOL)
    # Sol takes xhigh; Luna, which it runs now, does not.
    ok = await hub.set_model(sol, "sol", "xhigh", via="typed")
    assert server.configured == [(SOL, "gpt-6.1-sol", "xhigh")] and ok.kind == "model"
    refused = await hub.set_model(sol, "", "xhigh", via="typed")
    assert "can't take 'xhigh' effort; it offers low, medium, high" in refused.text
    assert len(server.configured) == 1


async def test_default_clears_the_pick(tmp_path) -> None:
    hub = _hub(tmp_path, _Router(), server := _Server())
    entry = await hub.set_model(await _session(hub, AMARRAS), "default", "default", via="typed")
    assert server.configured == [(AMARRAS, "default", "default")]
    assert "its default model at default effort" in entry.text


async def test_a_failed_change_is_reported_as_such(tmp_path) -> None:
    hub = _hub(tmp_path, _Router(), server := _Server())
    server.fail = True
    entry = await hub.set_model(await _session(hub, AMARRAS), "sonnet", "", via="typed")
    assert entry.kind == "error" and "its model is as it was" in entry.text


async def test_a_session_without_a_picker_is_left_alone(tmp_path) -> None:
    hub = _hub(tmp_path, _Router(), server := _Server())
    bare = HubSession(id="x", title="Nessie", run=SessionRun(provider="nessie"))
    entry = await hub.set_model(bare, "opus", "", via="typed")
    assert "offers no model choice here" in entry.text and server.configured == []


async def test_the_voice_changes_models_with_the_same_rules(tmp_path) -> None:
    hub = _hub(tmp_path, _Router(), server := _Server())
    shown, output = await voice_hub.run_hub_tool(
        hub, "set_model", {"project": "sol review", "model": "sol", "effort": "max"}
    )
    assert shown == "Sol review"
    assert "can't take 'max' effort" in output and server.configured == []
    shown, output = await voice_hub.run_hub_tool(
        hub, "set_model", {"project": "sol review", "effort": "medium"}
    )
    assert server.configured == [(SOL, None, "medium")]
    assert output.startswith("Switched Sol review to medium effort;")


def test_every_hub_voice_has_the_tool() -> None:
    assert "set_model" in {t["name"] for t in voice_hub.HUB_TOOLS}
    assert "set_model" in {
        t["function"]["name"] for t in voice_hub.openai_tools(voice_hub.HUB_TOOLS)
    }
