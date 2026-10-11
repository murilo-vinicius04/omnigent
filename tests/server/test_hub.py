"""The hub chat: routing decisions, the log, sending, watching, and its routes."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent.server import hub as hub_module
from omnigent.server.discussion import DiscussionRegistry, DiscussionUnavailable
from omnigent.server.hub import (
    Hub,
    HubBackend,
    HubCandidate,
    HubSession,
    HubState,
    TurnState,
    assign_handles,
    find_session,
    parse_decision,
    session_line,
)
from omnigent.server.routes.hub import create_hub_router

AMARRAS = "d14bc7496a6a42b6b6a0730b3f4f5f20"
BRASA = "ba9b8af460dd40f5b333b58f72d0683c"
AUDIO = "247c77f5f57748b0a2747e4b14548cac"


@pytest.mark.parametrize(
    ("reply", "action", "handle", "text", "message"),
    [
        (
            '{"send": "d14b", "message": "Run the tests.", "say": "Sent to amarras."}',
            "send",
            "d14b",
            "Sent to amarras.",
            "Run the tests.",
        ),
        ('{"check": "ba9b", "question": "Disk free?"}', "check", "ba9b", "", "Disk free?"),
        ('{"status": "247c"}', "status", "247c", "", ""),
        ('{"ask": "Which one?"}', "ask", None, "Which one?", ""),
        ('Sure! {"answer": "Oi!"} ', "answer", None, "Oi!", ""),
        ("no json here", "none", None, "no json here", ""),
        ('{"answer": ', "none", None, '{"answer":', ""),
    ],
)
def test_router_replies_parse_into_one_action(reply, action, handle, text, message) -> None:
    decision = parse_decision(reply)
    assert (decision.action, decision.handle, decision.text, decision.message) == (
        action,
        handle,
        text,
        message,
    )


def test_a_reply_can_teach_the_hub_a_name() -> None:
    decision = parse_decision('{"answer": "Ok.", "names": {"d14b": "amarras", "x": " "}}')
    assert decision.names == {"d14b": "amarras"}


def test_handles_are_the_shortest_unique_prefix() -> None:
    handles = assign_handles(["abcd1111", "abcd2222", "ffff0000"])
    assert handles == {"abcd1111": "abcd1", "abcd2222": "abcd2", "ffff0000": "ffff"}


SESSIONS = [
    HubSession(
        id=AMARRAS, title="Project Analysis and Organization", nickname="amarras", handle="d14b"
    ),
    HubSession(id=BRASA, title="Análise de projeto clonado", nickname="brasa", handle="ba9b"),
    HubSession(id=AUDIO, title="Corrigir áudio omnigent", handle="247c", status="running"),
]


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("d14b", AMARRAS),
        ("amarras", AMARRAS),
        ("a marras", AMARRAS),
        ("brassa", BRASA),
        ("corrigir audio omnigent", AUDIO),
        ("kitchen", None),
        ("", None),
    ],
)
def test_sessions_are_found_by_handle_nickname_or_title(spoken, expected) -> None:
    found = find_session(SESSIONS, spoken)
    assert (found.id if found else None) == expected


def test_a_session_line_says_what_the_router_needs() -> None:
    line = session_line(
        HubSession(
            id=AUDIO,
            title="Corrigir áudio",
            nickname="audio",
            handle="247c",
            status="running",
            now="Bash: pytest -q",
            first_ask="fix the audio",
            last_ask="is the watchdog live?",
            summary="Moved to Gemini 3.8 Live.",
            pending_approvals=1,
        )
    )
    assert line.startswith('[247c] audio · "Corrigir áudio" · WORKING (now: Bash: pytest -q)')
    assert "waiting for 1 approval(s)" in line
    assert 'started with: "fix the audio"' in line
    assert 'last asked: "is the watchdog live?"' in line
    assert 'latest update: "Moved to Gemini 3.8 Live."' in line


# -- the log -----------------------------------------------------------------


def test_the_log_and_names_survive_a_restart(tmp_path) -> None:
    state = HubState("u1", root=tmp_path)
    state.append(role="you", kind="message", text="oi", via="typed")
    state.append(role="hub", kind="sent", text="Sent.", session_id=AMARRAS, detail="Run it.")
    state.name(AMARRAS, "amarras")
    again = HubState("u1", root=tmp_path)
    assert [(e.id, e.role, e.kind, e.text) for e in again.entries] == [
        (1, "you", "message", "oi"),
        (2, "hub", "sent", "Sent."),
    ]
    assert again.entries[1].detail == "Run it."
    assert again.names == {AMARRAS: "amarras"}


def test_the_log_keeps_only_the_newest_entries(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(hub_module, "MAX_LOG", 3)
    state = HubState("u1", root=tmp_path)
    for n in range(5):
        state.append(role="you", kind="message", text=f"m{n}")
    assert [e.text for e in HubState("u1", root=tmp_path).entries] == ["m2", "m3", "m4"]


async def test_waiting_for_the_next_entry_wakes_on_append(tmp_path) -> None:
    state = HubState("u1", root=tmp_path)
    waiter = asyncio.ensure_future(state.wait_after(0, 5.0))
    await asyncio.sleep(0.01)
    state.append(role="hub", kind="message", text="hello")
    got = await asyncio.wait_for(waiter, 1.0)
    assert [e.text for e in got] == ["hello"]
    assert await state.wait_after(1, 0.01) == []


# -- the hub, with a scripted router and a fake server --------------------------


class _Router:
    """Stands in for the Gemini router: replies are scripted per turn."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.remembered: list[str] = []
        self.performed: list[str] = []
        self.down = False

    async def decide(self, prompt: str, *, said: str, timeout_s: float) -> str:
        if self.down:
            raise DiscussionUnavailable("no answer within 45s")
        self.prompts.append(prompt)
        return self.replies.pop(0)

    async def follow_up(self, prompt: str, *, timeout_s: float) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0)

    async def perform(self, task: str, *, timeout_s: float | None = None) -> str:
        self.performed.append(task)
        return "It fixed the scanner and all tests pass."

    def remember(self, text: str) -> None:
        self.remembered.append(text)


class _Server:
    """The sessions, sends and turn states the hub sees."""

    def __init__(self) -> None:
        self.sessions = [
            HubSession(id=AMARRAS, title="Project Analysis", first_ask="petrobras chains docs"),
            HubSession(id=BRASA, title="Cloned project review"),
        ]
        self.sent: list[tuple[str, str]] = []
        self.checked: list[tuple[str, str]] = []
        self.turns: list[TurnState] = []
        self.fail_send = False

    def backend(self) -> HubBackend:
        async def sessions(ids):
            return [s for s in self.sessions if s.id in ids]

        async def listing():
            return [
                HubCandidate(id=s.id, title=s.title, updated_at=s.updated_at)
                for s in self.sessions
            ]

        async def activity(session_id):
            return f"Claude is idle: the last task finished. ({session_id[:4]})"

        async def send(session_id, text):
            if self.fail_send:
                raise RuntimeError("runner gone")
            self.sent.append((session_id, text))

        async def check(session_id, question):
            self.checked.append((session_id, question))
            return "18 GB free."

        async def turn(session_id, since):
            return self.turns.pop(0) if len(self.turns) > 1 else self.turns[0]

        return HubBackend(
            sessions=sessions,
            listing=listing,
            activity=activity,
            send=send,
            check=check,
            turn=turn,
        )


def _hub(tmp_path, router: _Router, server: _Server) -> Hub:
    async def factory():
        return router

    hub = Hub("u1", router_factory=factory, state=HubState("u1", root=tmp_path), poll_s=0.01)
    hub.bind(server.backend())
    return hub


async def _settle(hub: Hub, *, until=None, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if until is None and not hub._tasks:
            return
        if until is not None and until():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("hub did not settle")


async def test_a_message_goes_to_the_session_it_is_about_and_comes_back(tmp_path) -> None:
    router = _Router(
        '{"send": "d14b", "message": "Summarize the mean error.", "say": "Mandei pra amarras.",'
        ' "names": {"d14b": "amarras"}}'
    )
    server = _Server()
    server.turns = [
        TurnState(status="running"),
        TurnState(status="idle", reply="Mean error is 3 mm."),
        TurnState(status="idle", reply="Mean error is 3 mm across all 40 frets."),
    ]
    hub = _hub(tmp_path, router, server)

    said = hub.submit("manda a amarras resumir o erro médio")
    assert (said.role, said.kind, said.via) == ("you", "message", "typed")
    await _settle(hub, until=lambda: any(e.kind == "finished" for e in hub.state.entries))

    assert server.sent == [(AMARRAS, "Summarize the mean error.")]
    assert "[d14b]" in router.prompts[0] and "manda a amarras" in router.prompts[0]
    kinds = [(e.role, e.kind) for e in hub.state.entries]
    assert kinds == [("you", "message"), ("hub", "sent"), ("hub", "finished")]
    sent, finished = hub.state.entries[1], hub.state.entries[2]
    assert sent.text == "Mandei pra amarras." and sent.detail == "Summarize the mean error."
    assert sent.session_name == "amarras"  # learned from this very reply
    assert finished.text == "amarras finished: It fixed the scanner and all tests pass."
    # The newest reply, read after the session stayed idle, is the one reported.
    assert finished.detail == "Mean error is 3 mm across all 40 frets."
    assert "Mean error is 3 mm across all 40 frets." in router.performed[0]
    assert hub.state.names == {AMARRAS: "amarras"}


async def test_an_unknown_session_sends_nothing(tmp_path) -> None:
    router = _Router('{"send": "zzzz", "message": "x", "say": "Sent."}')
    server = _Server()
    hub = _hub(tmp_path, router, server)
    hub.submit("send it to the kitchen")
    await _settle(hub)
    assert server.sent == []
    assert "couldn't tell which session" in hub.state.entries[-1].text
    assert "Project Analysis, Cloned project review" in hub.state.entries[-1].text


async def test_a_status_question_reads_the_session_live(tmp_path) -> None:
    router = _Router('{"status": "ba9b"}', '{"answer": "Brasa terminou e está parada."}')
    server = _Server()
    hub = _hub(tmp_path, router, server)
    hub.submit("o que a brasa tá fazendo?")
    await _settle(hub)
    assert "Claude is idle" in router.prompts[1] and "(ba9b)" in router.prompts[1]
    last = hub.state.entries[-1]
    assert (last.kind, last.text, last.session_id) == (
        "status",
        "Brasa terminou e está parada.",
        BRASA,
    )


async def test_a_check_runs_read_only_and_reports(tmp_path) -> None:
    router = _Router('{"check": "d14b", "question": "How much disk is free?"}')
    server = _Server()
    hub = _hub(tmp_path, router, server)
    hub.submit("quanto de disco livre?")
    await _settle(hub)
    assert server.checked == [(AMARRAS, "How much disk is free?")]
    assert [e.text for e in hub.state.entries if e.kind == "check"] == [
        "Checking on Project Analysis…",
        "18 GB free.",
    ]


async def test_a_router_that_cannot_answer_says_so_and_sends_nothing(tmp_path) -> None:
    router = _Router()
    router.down = True
    server = _Server()
    hub = _hub(tmp_path, router, server)
    hub.submit("manda pra brasa")
    await _settle(hub)
    assert server.sent == []
    assert hub.state.entries[-1].kind == "error"
    assert "nothing was sent" in hub.state.entries[-1].text


async def test_a_failed_send_is_reported_and_not_watched(tmp_path) -> None:
    router = _Router('{"send": "ba9b", "message": "Run it.", "say": "Sent."}')
    server = _Server()
    server.fail_send = True
    hub = _hub(tmp_path, router, server)
    hub.submit("run it in brasa")
    await _settle(hub)
    last = hub.state.entries[-1]
    assert last.kind == "error" and "failed (RuntimeError); nothing was sent" in last.text
    assert hub._watches == {}


async def test_a_session_waiting_for_approval_is_flagged_once(tmp_path) -> None:
    router = _Router('{"send": "ba9b", "message": "Deploy.", "say": "Sent."}')
    server = _Server()
    server.turns = [
        TurnState(status="waiting", pending_approvals=1),
        TurnState(status="waiting", pending_approvals=1),
        TurnState(status="idle", reply="Deployed."),
        TurnState(status="idle", reply="Deployed."),
    ]
    hub = _hub(tmp_path, router, server)
    hub.submit("deploy brasa")
    await _settle(hub, until=lambda: any(e.kind == "finished" for e in hub.state.entries))
    attention = [e.text for e in hub.state.entries if e.kind == "attention"]
    assert attention == ["Cloned project review is waiting for your approval."]


async def test_a_session_that_never_starts_is_flagged(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(hub_module, "NOT_STARTED_S", 0.05)
    router = _Router('{"send": "ba9b", "message": "Go.", "say": "Sent."}')
    server = _Server()
    server.turns = [TurnState(status="idle")]
    hub = _hub(tmp_path, router, server)
    hub.submit("go brasa")
    await _settle(hub, until=lambda: any(e.kind == "attention" for e in hub.state.entries))
    assert "hasn't started on it yet" in hub.state.entries[-1].text


# -- which sessions the hub runs ----------------------------------------------


async def test_a_new_hub_starts_with_the_sessions_they_named(tmp_path) -> None:
    HubState("u1", root=tmp_path).name(BRASA, "brasa")
    hub = _hub(tmp_path, _Router(), _Server())
    assert [s.name for s in await hub.sessions()] == ["brasa"]
    assert HubState("u1", root=tmp_path).members == [BRASA]


async def test_without_names_a_new_hub_starts_with_this_weeks_sessions(tmp_path) -> None:
    server = _Server()
    server.sessions[1] = replace(server.sessions[1], updated_at=time.time() - 30 * 24 * 3600)
    hub = _hub(tmp_path, _Router(), server)
    assert [s.id for s in await hub.sessions()] == [AMARRAS]
    # A session started later stays out until they add it.
    server.sessions.append(HubSession(id="new0", title="Greeting"))
    assert [s.id for s in await hub.sessions()] == [AMARRAS]
    assert [c.id for c in await hub.candidates()] == [BRASA, "new0"]


async def test_sessions_are_added_renamed_and_removed(tmp_path) -> None:
    HubState("u1", root=tmp_path).set_members([AMARRAS])
    hub = _hub(tmp_path, _Router(), _Server())
    assert await hub.add("not-theirs") is None
    added = await hub.add(BRASA)
    assert added is not None
    assert (added.kind, added.text) == ("members", "Added Cloned project review to the hub.")
    assert await hub.add(BRASA) is None  # already in
    by_id = {s.id: s for s in await hub.sessions()}
    assert set(by_id) == {AMARRAS, BRASA}

    renamed = await hub.rename(by_id[BRASA], "  brasa ")
    assert renamed.text == "Renamed Cloned project review to brasa."
    by_id = {s.id: s for s in await hub.sessions()}
    with pytest.raises(ValueError, match="brasa is already called that"):
        await hub.rename(by_id[AMARRAS], "Brasa")
    assert (await hub.rename(by_id[BRASA], "")).text == "Renamed brasa to Cloned project review."
    assert BRASA not in HubState("u1", root=tmp_path).names

    by_id = {s.id: s for s in await hub.sessions()}
    removed = await hub.remove(by_id[BRASA])
    assert removed.text == (
        "Took Cloned project review out of the hub; the session itself is untouched."
    )
    assert [s.id for s in await hub.sessions()] == [AMARRAS]
    assert HubState("u1", root=tmp_path).members == [AMARRAS]


# -- reading sessions from the stores -----------------------------------------


def _msg(role: str, text: str, created_at: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(
        type="message",
        response_id="r",
        created_at=created_at,
        data={"role": role, "content": [{"type": "input_text", "text": text}]},
    )


class _ConvStore:
    def __init__(self) -> None:
        now = time.time()
        self.convs = [
            SimpleNamespace(
                id=AMARRAS,
                title="Project Analysis",
                updated_at=now - 60,
                live_status="running",
                workspace="/home/x",
                pending_elicitation_count=0,
            ),
            SimpleNamespace(
                id=BRASA,
                title="Old one",
                updated_at=now - 30 * 24 * 3600,
                live_status="idle",
                workspace="/home/x",
                pending_elicitation_count=0,
            ),
        ]
        self.items = {
            AMARRAS: [
                _msg("user", "look at the petrobras-chains folder", 1.0),
                _msg("assistant", "Organized it.", 2.0),
                _msg("user", "<task-notification>done</task-notification>", 3.0),
                _msg("user", "[System: sub-agent finished]", 4.0),
                _msg("user", "what is the mean error?", 5.0),
                _msg("assistant", "Mean error is 3 mm.", 6.0),
                SimpleNamespace(
                    type="function_call",
                    response_id="r",
                    created_at=7.0,
                    data={"name": "Bash", "arguments": '{"command": "pytest -q"}', "call_id": "c"},
                ),
            ]
        }
        self.listed: list[dict] = []

    def list_conversations(self, **kwargs):
        self.listed.append(kwargs)
        return SimpleNamespace(data=list(self.convs))

    def get_conversation(self, session_id):
        return next((c for c in self.convs if c.id == session_id), None)

    def list_items(self, session_id, limit=100, order="asc", type=None):
        items = [i for i in self.items.get(session_id, []) if type is None or i.type == type]
        if order == "desc":
            items = list(reversed(items))
        return SimpleNamespace(data=items[:limit])


def _store_backend(store: _ConvStore) -> HubBackend:
    registry = DiscussionRegistry()
    return hub_module.store_backend(
        store, user_id="morys5", app=None, headers={}, companions=lambda: registry
    )


async def test_store_sessions_say_what_each_is_about() -> None:
    store = _ConvStore()
    sessions = await _store_backend(store).sessions([AMARRAS, "gone"])
    assert [s.id for s in sessions] == [AMARRAS]  # a deleted session is left out
    amarras = sessions[0]
    assert amarras.first_ask == "look at the petrobras-chains folder"
    assert amarras.last_ask == "what is the mean error?"  # notifications are not asks
    assert amarras.said == "Mean error is 3 mm."
    assert amarras.now == "Bash: pytest -q"


async def test_the_listing_is_theirs_newest_first() -> None:
    store = _ConvStore()
    store.convs[1].labels = {"omnigent.wrapper": "codex-native-ui"}
    listed = await _store_backend(store).listing()
    assert [(c.id, c.title, c.provider) for c in listed] == [
        (AMARRAS, "Project Analysis", ""),
        (BRASA, "Old one", "Codex"),
    ]
    assert store.listed[0]["owned_by"] == "morys5"
    assert store.listed[0]["sort_by"] == "updated_at"


async def test_an_archived_session_drops_out_of_the_hub() -> None:
    store = _ConvStore()
    store.convs[0].archived = True
    assert [s.id for s in await _store_backend(store).sessions([AMARRAS, BRASA])] == [BRASA]


async def test_a_turn_reply_is_the_newest_assistant_message_since_the_send() -> None:
    store = _ConvStore()
    backend = _store_backend(store)
    assert (await backend.turn(AMARRAS, 5.5)).reply == "Mean error is 3 mm."
    assert (await backend.turn(AMARRAS, 100.0)).reply == ""


async def test_the_hub_never_offers_itself_or_an_excluded_session() -> None:
    store = _ConvStore()
    now = time.time()
    store.convs.insert(
        0,
        SimpleNamespace(
            id="hubconv",
            title="Hub",
            updated_at=now,
            live_status="idle",
            workspace=None,
            pending_elicitation_count=0,
            labels={"omnigent.hub": "1"},
        ),
    )
    backend = _store_backend(store)
    assert [c.id for c in await backend.listing()] == [AMARRAS, BRASA]
    assert [s.id for s in await backend.sessions(["hubconv", AMARRAS])] == [AMARRAS]
    registry = DiscussionRegistry()
    backend = hub_module.store_backend(
        store,
        user_id="morys5",
        app=None,
        headers={},
        companions=lambda: registry,
        exclude={AMARRAS},
    )
    assert [c.id for c in await backend.listing()] == [BRASA]
    assert await backend.sessions([AMARRAS]) == []


# -- the routes ------------------------------------------------------------------


def test_the_hub_needs_a_signed_in_user(tmp_path) -> None:
    class _NoUser:
        def get_user_id(self, request):
            return None

    app = FastAPI()
    app.include_router(
        create_hub_router(
            auth_provider=_NoUser(), conversation_store=_ConvStore(), artifact_store=object()
        ),
        prefix="/v1",
    )
    with TestClient(app) as tc:
        assert tc.get("/v1/hub").status_code == 401


async def test_a_voice_ask_returns_what_the_hub_said_with_the_call_in_the_prompt(tmp_path) -> None:
    router = _Router('{"send": "ba9b", "message": "Run the tests.", "say": "Mandei pra brasa."}')
    server = _Server()
    hub = _hub(tmp_path, router, server)
    said = await hub.ask("manda ela rodar", via="voice", context="Me: a brasa quebrou\nVoice: Ok.")
    await hub.close()
    assert [(e.kind, e.text) for e in said] == [("sent", "Mandei pra brasa.")]
    assert "[The voice call so far, oldest first]\nMe: a brasa quebrou" in router.prompts[0]
    assert "[They said, aloud, on a voice call]" in router.prompts[0]
    assert server.sent == [(BRASA, "Run the tests.")]
