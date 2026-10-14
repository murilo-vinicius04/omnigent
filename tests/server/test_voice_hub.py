"""The voice hub: one call across projects — live status, tools, and the relay."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent.server import discussion, gemini_live, voice_hub
from omnigent.server import hub as hub_module
from omnigent.server.discussion import DiscussionRegistry
from omnigent.server.hub import (
    Hub,
    HubBackend,
    HubCandidate,
    HubSession,
    HubState,
    ProjectRecord,
    TurnState,
)
from omnigent.server.routes import gemini_live as gemini_live_route
from omnigent.server.routes.gemini_live import create_gemini_live_router
from omnigent.server.voice_hub import render_activity

SESSIONS = [
    HubSession(
        id="s-amarras", title="Chain scanner", nickname="amarras", first_ask="scan the chain"
    ),
    HubSession(id="s-brasa", title="Orders SaaS", nickname="brasa"),
    HubSession(id="s-audio", title="Corrigir áudio", status="running", now="Bash: pytest"),
]

#: Each chat is its own project, named as they call it.
PROJECTS = [
    ProjectRecord(id="pa" + "0" * 30, name="amarras", chat_ids=("s-amarras",), created_at=3),
    ProjectRecord(id="pb" + "0" * 30, name="brasa", chat_ids=("s-brasa",), created_at=2),
    ProjectRecord(id="pc" + "0" * 30, name="Corrigir áudio", chat_ids=("s-audio",), created_at=1),
]


def _item(kind: str, response_id: str, **data) -> dict:
    return {"type": kind, "response_id": response_id, "data": data}


def test_live_status_reads_the_current_turn_mid_task() -> None:
    items = [  # newest first, as the store returns them
        _item(
            "function_call", "r2", name="Bash", arguments='{"command": "pytest -q"}', call_id="c3"
        ),
        _item("function_call_output", "r2", call_id="c2", output="ok"),
        _item(
            "function_call",
            "r2",
            name="Edit",
            arguments='{"file_path": "/x/scanner.py"}',
            call_id="c2",
        ),
        _item(
            "message",
            "r2",
            role="assistant",
            content=[{"type": "output_text", "text": "Fixing the scan."}],
        ),
        _item(
            "message",
            "r1u",
            role="user",
            content=[{"type": "input_text", "text": "fix the scanner"}],
        ),
        _item("function_call", "r0", name="Bash", arguments='{"command": "old"}', call_id="c0"),
    ]
    text = render_activity(items, "running", writing="Now running the tests")
    assert text.startswith("Claude is working on it right now.")
    assert "The task: fix the scanner" in text
    assert "- Edit: scanner.py" in text
    assert "- Bash: pytest -q (running now)" in text
    assert "old" not in text  # the previous turn is not this one
    assert "Writing right now: Now running the tests" in text


def test_live_status_of_an_idle_session_says_it_finished() -> None:
    items = [
        _item("resource_event", "s1", event_type="session.resource.deleted"),
        _item(
            "message",
            "r1",
            role="assistant",
            content=[{"type": "output_text", "text": "Done: 3 fixed."}],
        ),
    ]
    text = render_activity(items, "idle", writing="stale")
    assert text.startswith("Claude is idle")
    assert "Said last: Done: 3 fixed." in text
    assert "stale" not in text


def test_the_briefing_lists_every_session_with_its_handle() -> None:
    sessions = [HubSession(**{**s.__dict__, "handle": s.id[2:6]}) for s in SESSIONS]
    briefing = voice_hub.hub_briefing(sessions)
    assert briefing.startswith(voice_hub.HUB_ROLE)
    assert '[amar] amarras · "Chain scanner" · idle' in briefing
    assert 'started with: "scan the chain"' in briefing
    assert "[audi]" in briefing and "WORKING (now: Bash: pytest)" in briefing


def test_the_briefing_carries_the_hub_chat_a_call_opens_on(tmp_path) -> None:
    state = HubState("u1", root=tmp_path)
    state.append(role="hub", kind="project", text="Made project x.")
    state.append(role="you", kind="message", text="pode começar então", via="typed")
    state.append(
        role="hub",
        kind="sent",
        text="Sent to RAL.",
        session_name="RAL",
        detail="Comece o marco 1.",
    )
    report = "RAL finished: " + "o MPC segue o alvo. " * 40
    state.append(role="hub", kind="finished", text=report, session_name="RAL")
    briefing = voice_hub.hub_briefing(SESSIONS, recent=state.entries)
    chat = briefing.split("[The hub chat just before this call")[1]
    assert "They: pode começar então" in chat
    assert "Hub sent to RAL: Comece o marco 1." in chat
    # The report being read aloud when the mic opened, long enough to carry on from.
    assert "Report from RAL: RAL finished: o MPC segue o alvo." in chat
    assert len(chat.split("Report from RAL: ")[1]) > 600
    assert "Made project" not in chat
    assert chat.index("They:") < chat.index("Report from RAL")  # oldest first
    assert "[The hub chat" not in voice_hub.hub_briefing(SESSIONS)


def test_the_briefing_keeps_only_the_newest_lines(tmp_path) -> None:
    state = HubState("u1", root=tmp_path)
    for n in range(10):
        state.append(role="you", kind="message", text=f"message {n}", via="typed")
    chat = voice_hub.recent_block(state.entries)
    assert chat.splitlines() == [f"They: message {n}" for n in range(4, 10)]


def test_words_the_message_already_carries_are_not_repeated() -> None:
    message = "Manda pro brasa: roda os testes de pedidos."
    assert voice_hub.with_their_words(message, "roda os testes de pedidos", "pt-BR") == message
    assert voice_hub.with_their_words(message, "  ", "pt-BR") == message


def test_their_words_are_labelled_in_the_chats_language_and_keep_the_end() -> None:
    said = "começo " + "x" * voice_hub.MAX_HEARD_CHARS + " fim do pedido"
    out = voice_hub.with_their_words("Plano?", said, "pt-BR")
    assert "Transcrição do que foi dito na chamada de voz" in out
    assert out.endswith("fim do pedido") and "começo" not in out
    assert "Transcript of what was said" in voice_hub.with_their_words("Plan?", "and audio", "")


class _Router:
    def __init__(self) -> None:
        self.rewrite = "done"
        self.down = False
        self.tasks: list[str] = []

    async def perform(self, task, *, timeout_s=None):
        if self.down:
            raise discussion.DiscussionUnavailable("no answer within 25s")
        self.tasks.append(task)
        return self.rewrite

    def remember(self, text):
        pass

    def tell(self, text):
        pass


class _FakeServer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.checked: list[tuple[str, str]] = []
        self.sessions = list(SESSIONS)
        self.fail = False
        self.router = _Router()

    def hub(self, tmp_path) -> Hub:
        async def sessions(ids):
            return [s for s in self.sessions if s.id in ids]

        async def listing():
            return [HubCandidate(id=s.id, title=s.title) for s in self.sessions]

        async def activity(session_id):
            return f"status of {session_id}"

        async def send(session_id, text, files=()):
            if self.fail:
                raise RuntimeError("runner gone")
            self.sent.append((session_id, text))

        async def check(session_id, question):
            self.checked.append((session_id, question))
            return "18 GB free"

        async def turn(session_id, since):
            return TurnState(status="running")

        async def router():
            return self.router

        async def projects():
            return list(PROJECTS)

        async def unused(*args):
            raise AssertionError("the voice never changes projects")

        hub = Hub("u1", router_factory=router, state=HubState("u1", root=tmp_path), poll_s=60)
        hub.bind(
            HubBackend(
                sessions=sessions,
                listing=listing,
                activity=activity,
                send=send,
                check=check,
                turn=turn,
                projects=projects,
                create_project=unused,
                rename_project=unused,
                delete_project=unused,
                file_chat=unused,
            )
        )
        return hub


async def test_tools_route_to_the_named_session_and_land_in_the_hub_chat(tmp_path) -> None:
    fake = _FakeServer()
    hub = fake.hub(tmp_path)
    assert await voice_hub.run_hub_tool(hub, "session_status", {"session": "amarras"}) == (
        "amarras",
        "amarras: status of s-amarras",
    )
    name, out = await voice_hub.run_hub_tool(
        hub, "ask_claude", {"session": "brassa", "message": "run the order tests"}
    )
    assert name == "brasa"
    assert out == (
        "Sent to brasa. Its answer comes back in the hub chat, which reads it out. "
        "Say that in one short sentence: the call ends right after."
    )
    assert fake.sent == [("s-brasa", "run the order tests")]
    assert await voice_hub.run_hub_tool(
        hub, "check_machine", {"session": "amarras", "question": "disk?"}
    ) == ("amarras", "18 GB free")
    assert fake.checked == [("s-amarras", "disk?")]
    logged = [(e.kind, e.via, e.session_name) for e in hub.state.entries]
    assert logged == [("sent", "voice", "brasa"), ("check", "voice", "amarras")]
    assert "s-brasa" in hub._watches  # its answer will be reported back
    await hub.close()


HEARD = "pergunta o plano dos agentes. E o áudio corta quando falo por cima da leitura"


async def test_a_voice_send_is_rewritten_to_carry_everything_they_asked(tmp_path) -> None:
    fake = _FakeServer()
    complete = "Qual é o plano dos agentes? E o áudio corta quando falo por cima da leitura."
    fake.router.rewrite = complete
    hub = fake.hub(tmp_path)
    _, out = await voice_hub.run_hub_tool(
        hub, "ask_claude", {"session": "brasa", "message": "Qual é o plano?"}, heard=HEARD
    )
    assert out.startswith(voice_hub.SENT_PREFIX)
    # A summary that has the question the voice left out, not the transcript.
    assert fake.sent == [("s-brasa", complete)]
    [task] = fake.router.tasks
    assert "Qual é o plano?" in task and HEARD in task and "brasa" in task
    assert hub.state.entries[0].detail == complete  # the hub shows what went
    await hub.close()


async def test_a_failed_rewrite_sends_their_words_rather_than_lose_them(tmp_path) -> None:
    for broken in ("down", "short"):
        fake = _FakeServer()
        fake.router.down = broken == "down"
        fake.router.rewrite = "ok"  # less than the voice wrote
        hub = fake.hub(tmp_path / broken)
        await voice_hub.run_hub_tool(
            hub, "ask_claude", {"session": "brasa", "message": "Qual é o plano?"}, heard=HEARD
        )
        [(_, text)] = fake.sent
        assert text.startswith("Qual é o plano?\n\nTranscript of what was said")
        assert text.endswith("> " + HEARD)
        await hub.close()


async def test_words_the_voice_already_sent_need_no_rewrite(tmp_path) -> None:
    fake = _FakeServer()
    hub = fake.hub(tmp_path)
    await voice_hub.run_hub_tool(
        hub,
        "ask_claude",
        {"session": "brasa", "message": "roda os testes"},
        heard="roda os testes",
    )
    assert fake.sent == [("s-brasa", "roda os testes")] and fake.router.tasks == []
    await hub.close()


async def test_an_unknown_session_sends_nothing(tmp_path) -> None:
    fake = _FakeServer()
    hub = fake.hub(tmp_path)
    _, out = await voice_hub.run_hub_tool(
        hub, "ask_claude", {"session": "kitchen", "message": "x"}
    )
    assert "No project called 'kitchen'" in out and "amarras, brasa, Corrigir áudio" in out
    assert fake.sent == [] and hub.state.entries == []


async def test_every_session_at_once(tmp_path) -> None:
    name, out = await voice_hub.run_hub_tool(
        _FakeServer().hub(tmp_path), "session_status", {"session": "all"}
    )
    assert name == "all"
    assert "amarras:\nstatus of s-amarras" in out and "Corrigir áudio:\nstatus of s-audio" in out


async def test_a_failing_send_still_answers_the_model(tmp_path) -> None:
    fake = _FakeServer()
    fake.fail = True
    hub = fake.hub(tmp_path)
    _, out = await voice_hub.run_hub_tool(
        hub, "ask_claude", {"session": "amarras", "message": "x"}
    )
    assert out == "Sending to amarras failed (RuntimeError); nothing was sent."


# --- The relay: hub mode end to end, with fake stores and a fake Google ---


class _FakeUpstream:
    def __init__(self) -> None:
        self.sent: list[object] = []
        self.incoming: asyncio.Queue[object] = asyncio.Queue()
        self.loop: asyncio.AbstractEventLoop | None = None

    async def send(self, message: object) -> None:
        self.sent.append(message)

    async def recv(self) -> object:
        return await self.incoming.get()

    async def close(self) -> None:
        pass

    def push(self, item: object) -> None:
        assert self.loop is not None
        asyncio.run_coroutine_threadsafe(self.incoming.put(item), self.loop).result()


class _Stores:
    """Sessions and their items as the real store hands them out."""

    def __init__(self) -> None:
        self.convs = [
            SimpleNamespace(
                id="s-amarras",
                title="Scanner",
                workspace="/tmp",
                live_status="running",
                updated_at=time.time(),
                pending_elicitation_count=0,
                project_id="p-scanner",
            ),
            SimpleNamespace(
                id="s-brasa",
                title="Orders",
                workspace="/tmp",
                live_status="idle",
                updated_at=time.time(),
                pending_elicitation_count=0,
                project_id="p-orders",
            ),
        ]

    def list_conversations(self, **kwargs):
        wanted = {"scanner": "p-scanner", "orders": "p-orders"}.get(kwargs.get("project") or "")
        return SimpleNamespace(data=[c for c in self.convs if c.project_id == wanted])

    def get_conversation(self, session_id):
        return next(c for c in self.convs if c.id == session_id)

    def list_items(self, session_id, limit=100, order="asc", type=None):
        if type == "message":
            return SimpleNamespace(data=[])
        return SimpleNamespace(
            data=[
                _item(
                    "function_call",
                    "r1",
                    name="Bash",
                    arguments='{"command": "make scan"}',
                    call_id="c1",
                )
            ]
        )


def _hub_app(monkeypatch, tmp_path, upstream: _FakeUpstream) -> FastAPI:
    env = tmp_path / "gemini.env"
    env.write_text("GEMINI_API_KEY=test-key\n", encoding="utf-8")
    monkeypatch.setattr(gemini_live, "KEY_PATH", env)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(discussion, "LEDGER_DIR", tmp_path / "ledgers")
    monkeypatch.setenv("OMNIGENT_HUB_MODEL", "test-hub-model")  # never asks the real agy
    monkeypatch.setattr(hub_module, "HUB_DIR", tmp_path / "hub")
    monkeypatch.setattr(hub_module, "_HUBS", {})

    async def connect(url: str):
        upstream.loop = asyncio.get_running_loop()
        return upstream

    stores = _Stores()
    companions = DiscussionRegistry()
    # A chat voice is only briefed (and given tools) once its companion has notes.
    companions.note("s-amarras", "summary", "Scan coverage is 92%.")
    app = FastAPI()

    @app.get("/v1/projects")
    async def projects():
        return {
            "data": [
                {"id": "p-scanner", "name": "scanner", "created_at": 1, "config": {}},
                {"id": "p-orders", "name": "orders", "created_at": 1, "config": {}},
            ]
        }

    app.include_router(
        create_gemini_live_router(
            upstream_connect=connect,
            registry_provider=lambda: companions,
            conversation_store=stores,
        ),
        prefix="/v1",
    )
    return app


def _wait_for(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


def test_the_hub_is_briefed_on_every_session_and_answers_its_own_tools(
    monkeypatch, tmp_path
) -> None:
    run_hub_tool = voice_hub.run_hub_tool
    heard: list[str] = []

    async def run(hub, name, args, **kwargs):
        # This app cannot deliver a send; the relay's flag is what is tested here.
        if name == "ask_claude":
            heard.append(kwargs.get("heard", ""))
            return "orders", f"{voice_hub.SENT_PREFIX}orders. The call ends right after."
        return await run_hub_tool(hub, name, args)

    monkeypatch.setattr(voice_hub, "run_hub_tool", run)
    monkeypatch.setattr(gemini_live_route, "SEND_QUIET_S", 0.0)
    upstream = _FakeUpstream()
    with TestClient(_hub_app(monkeypatch, tmp_path, upstream)) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws?mode=hub") as ws:
            hello = json.loads(ws.receive_text())
            assert hello["omnigentSession"]["hub"] is True
            _wait_for(lambda: upstream.sent)
            setup = json.loads(upstream.sent[0])["setup"]
            names = [d["name"] for d in setup["tools"][0]["functionDeclarations"]]
            assert names == ["session_status", "ask_claude", "check_machine", "set_model"]
            instruction = setup["systemInstruction"]["parts"][0]["text"]
            assert 'scanner · "Scanner" · WORKING (now: Bash: make scan)' in instruction
            assert 'orders · "Orders" · idle' in instruction

            call = {"id": "t1", "name": "session_status", "args": {"session": "scaner"}}
            upstream.push(json.dumps({"toolCall": {"functionCalls": [call]}}))
            assert "toolCall" in ws.receive_text()  # the page sees the call, to show it
            done = json.loads(ws.receive_text())["omnigentToolDone"]
            assert done["project"] == "scanner"
            assert "Bash: make scan (running now)" in done["output"]
            assert done["sent"] is False  # a look keeps the call open

            for words in ("manda pro orders", " e o fim também"):
                said = {"serverContent": {"inputTranscription": {"text": words}}}
                upstream.push(json.dumps(said))
                assert "inputTranscription" in ws.receive_text()
            send = {
                "id": "t4",
                "name": "ask_claude",
                "args": {"session": "orders", "message": "x"},
            }
            upstream.push(json.dumps({"toolCall": {"functionCalls": [send]}}))
            assert "toolCall" in ws.receive_text()
            done = json.loads(ws.receive_text())["omnigentToolDone"]
            assert done["sent"] is True  # the page ends the call after the goodbye
            # The send carries their own words, not only the voice's summary of them.
            assert heard == ["manda pro orders e o fim também"]

            # A second send carries only what was said after the first.
            upstream.push(
                json.dumps({"serverContent": {"inputTranscription": {"text": "agora o brasa"}}})
            )
            assert "inputTranscription" in ws.receive_text()
            upstream.push(json.dumps({"toolCall": {"functionCalls": [{**send, "id": "t5"}]}}))
            assert "toolCall" in ws.receive_text()
            assert json.loads(ws.receive_text())["omnigentToolDone"]["sent"] is True
            assert heard[1] == "agora o brasa"

            # Asked again with nothing new said: told it went, and nothing is sent twice.
            upstream.push(json.dumps({"toolCall": {"functionCalls": [{**send, "id": "t6"}]}}))
            assert "toolCall" in ws.receive_text()
            again = json.loads(ws.receive_text())["omnigentToolDone"]
            assert again["sent"] is True and "already" in again["output"]
            assert len(heard) == 2
            _wait_for(lambda: any("toolResponse" in str(m) for m in upstream.sent))
    response = next(json.loads(m) for m in upstream.sent if "toolResponse" in str(m))
    reply = response["toolResponse"]["functionResponses"][0]
    assert reply["id"] == "t1" and reply["name"] == "session_status"
    assert reply["response"]["scheduling"] == "WHEN_IDLE"
    assert "Claude is working on it right now." in reply["response"]["output"]


def test_the_chat_voice_can_ask_what_claude_is_doing_now(monkeypatch, tmp_path) -> None:
    upstream = _FakeUpstream()
    with TestClient(_hub_app(monkeypatch, tmp_path, upstream)) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws?session_id=s-amarras") as ws:
            ws.receive_text()  # hello
            _wait_for(lambda: upstream.sent)
            setup = json.loads(upstream.sent[0])["setup"]
            names = [d["name"] for d in setup["tools"][0]["functionDeclarations"]]
            assert names == ["ask_claude", "check_machine", "claude_status"]
            upstream.push(
                json.dumps(
                    {"toolCall": {"functionCalls": [{"id": "t2", "name": "claude_status"}]}}
                )
            )
            ws.receive_text()  # the call, relayed
            done = json.loads(ws.receive_text())["omnigentToolDone"]
            assert done["name"] == "claude_status" and "make scan" in done["output"]
            # ask_claude is still the page's to answer, not the relay's.
            upstream.push(
                json.dumps({"toolCall": {"functionCalls": [{"id": "t3", "name": "ask_claude"}]}})
            )
            ws.receive_text()
            time.sleep(0.1)
    answered = [
        json.loads(m)["toolResponse"]["functionResponses"][0]["id"]
        for m in upstream.sent
        if "toolResponse" in str(m)
    ]
    assert answered == ["t2"]


def test_an_open_hub_call_is_told_when_a_session_finishes(monkeypatch, tmp_path) -> None:
    upstream = _FakeUpstream()
    with TestClient(_hub_app(monkeypatch, tmp_path, upstream)) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws?mode=hub") as ws:
            ws.receive_text()  # hello
            _wait_for(lambda: upstream.sent)
            hub = hub_module._HUBS["local"]
            hub.state.append(role="hub", kind="sent", text="Sent to Orders.")
            hub.state.append(
                role="hub",
                kind="finished",
                text="Orders finished: all tests pass.",
                session_id="s-brasa",
            )
            _wait_for(lambda: any("clientContent" in str(m) for m in upstream.sent))
    told = [json.loads(m)["clientContent"] for m in upstream.sent if "clientContent" in str(m)]
    assert len(told) == 1  # the send itself is not news
    text = told[0]["turns"][0]["parts"][0]["text"]
    assert "Orders finished: all tests pass." in text and told[0]["turnComplete"] is True


def test_no_stores_no_hub(monkeypatch, tmp_path) -> None:
    env = tmp_path / "gemini.env"
    env.write_text("GEMINI_API_KEY=test-key\n", encoding="utf-8")
    monkeypatch.setattr(gemini_live, "KEY_PATH", env)
    app = FastAPI()
    app.include_router(create_gemini_live_router(), prefix="/v1")
    from starlette.websockets import WebSocketDisconnect

    with TestClient(app) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws?mode=hub") as ws:
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_text()
    assert closed.value.code == 1008


def test_the_voice_button_inside_the_hub_chat_is_the_hub(monkeypatch, tmp_path) -> None:
    upstream = _FakeUpstream()
    app = _hub_app(monkeypatch, tmp_path, upstream)
    hub_conv = SimpleNamespace(
        id="s-hubchat",
        title="Hub",
        workspace=None,
        live_status="idle",
        updated_at=time.time(),
        pending_elicitation_count=0,
        labels={"omnigent.hub": "1"},
    )
    original = _Stores.get_conversation

    def get_conversation(self, session_id):
        return hub_conv if session_id == "s-hubchat" else original(self, session_id)

    monkeypatch.setattr(_Stores, "get_conversation", get_conversation)
    with TestClient(app) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws?session_id=s-hubchat") as ws:
            hello = json.loads(ws.receive_text())
            assert hello["omnigentSession"]["hub"] is True
            _wait_for(lambda: upstream.sent)
    setup = json.loads(upstream.sent[0])["setup"]
    names = [d["name"] for d in setup["tools"][0]["functionDeclarations"]]
    assert names == ["session_status", "ask_claude", "check_machine", "set_model"]
    # What the hub says on this call is written into the hub conversation.
    assert hub_module._HUBS["local"].chat.session_id == "s-hubchat"


async def test_a_send_waits_for_the_reader_to_finish() -> None:
    log = gemini_live_route._TurnLog(time.monotonic())
    log.frame(time.monotonic(), {"voiceActivity": {"type": "ACTIVITY_START"}}, "")
    started = time.monotonic()
    # Mid-utterance: it waits, up to the cap.
    await log.reader_quiet(0.1, 0.5)
    assert time.monotonic() - started >= 0.45
    log.frame(time.monotonic(), {"voiceActivity": {"type": "ACTIVITY_END"}}, "")
    words = {"serverContent": {"inputTranscription": {"text": "e o fim"}}}
    log.frame(time.monotonic(), words, "")
    started = time.monotonic()
    await log.reader_quiet(0.3, 2.0)
    waited = time.monotonic() - started
    assert 0.25 <= waited < 1.0  # quiet long enough, then it goes
    assert log.heard_since_send() == "e o fim"
