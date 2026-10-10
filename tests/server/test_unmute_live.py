"""Omnigent calls on a local Kyutai Unmute stack: relay and brain."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent import openai_token_budget as budget
from omnigent.server import unmute_live
from omnigent.server.routes import unmute_live as routes
from omnigent.server.routes.openai_budget_proxy import ensure_proxy_token


def _sse(*events: dict[str, Any]) -> bytes:
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
    return body.encode()


class _Body(httpx.AsyncByteStream):
    """A streamed body, like a real upstream; content= responses arrive pre-read."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def __aiter__(self):  # type: ignore[override]
        yield self._data


def _text(text: str) -> dict[str, Any]:
    return {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}


def _tool_call(call_id: str, question: str, name: str = "ask_claude") -> dict[str, Any]:
    arguments = json.dumps({"question": question})
    return {
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": call_id,
                            "function": {"name": name, "arguments": arguments},
                        }
                    ]
                },
                "finish_reason": "tool_calls",
            }
        ]
    }


class _Model:
    """A scripted OpenAI chat endpoint that records what it was sent."""

    def __init__(self, *replies: bytes) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            content=self.replies.pop(0),
            headers={"content-type": "text/event-stream"},
        )


async def _speak(
    call: unmute_live.UnmuteCall, model: _Model, messages: list[dict[str, Any]]
) -> str:
    async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
        parts = [
            text
            async for text in unmute_live.speak(
                call,
                messages,
                model="gpt-5.6-terra",
                client=client,
                url="http://model/v1/chat/completions",
                headers={},
            )
        ]
    return "".join(parts)


def test_unmute_events_become_the_gemini_frames_the_page_reads() -> None:
    t = routes.translate_upstream
    assert t({"type": "session.updated", "session": {}}) == {"setupComplete": {}}
    assert t({"type": "response.audio.delta", "delta": "AAA="}) == {
        "serverContent": {
            "modelTurn": {
                "parts": [{"inlineData": {"mimeType": "audio/pcm;rate=24000", "data": "AAA="}}]
            }
        }
    }
    heard = {"type": "conversation.item.input_audio_transcription.delta", "delta": "hello"}
    assert t(heard) == {"serverContent": {"inputTranscription": {"text": " hello"}}}
    said = {"type": "response.text.delta", "delta": "Sure."}
    assert t(said) == {"serverContent": {"outputTranscription": {"text": " Sure."}}}
    assert t({"type": "unmute.interrupted_by_vad"}) == {"serverContent": {"interrupted": True}}
    assert t({"type": "response.audio.done"}) == {"serverContent": {"turnComplete": True}}
    # The speech-to-text opens with an empty word; debug outputs are noise.
    empty = {"type": "conversation.item.input_audio_transcription.delta", "delta": ""}
    assert t(empty) is None
    assert t({"type": "unmute.additional_outputs", "args": {}}) is None


async def test_a_handoff_waits_for_the_page_and_the_voice_carries_on_with_its_answer() -> None:
    call = unmute_live.open_call("s1", "BRIEFING")
    model = _Model(
        _sse(_text("One moment."), _tool_call("call_1", "check the logs")),
        _sse(_text(" Sent to Claude, bye.")),
    )

    async def page() -> None:
        frame = await call.outbox.get()
        [asked] = frame["toolCall"]["functionCalls"]
        assert asked == {
            "id": "call_1",
            "name": "ask_claude",
            "args": {"question": "check the logs"},
        }
        call.resolve("call_1", "I've sent that to Claude.")

    unmute_system = {"role": "system", "content": f"Unmute prompt omnigent-call:{call.call_id}"}
    heard = {"role": "user", "content": "yes, send it"}
    try:
        said, _ = await asyncio.gather(_speak(call, model, [unmute_system, heard]), page())
    finally:
        unmute_live.close_call(call)

    assert said == "One moment. Sent to Claude, bye."
    first, second = model.requests
    # Omnigent's briefing replaces Unmute's, with the handoff tool attached.
    assert first["messages"] == [{"role": "system", "content": "BRIEFING"}, heard]
    assert first["tools"][0]["function"]["name"] == "ask_claude"
    # OpenAI allows function tools on chat completions only at this effort.
    assert first["reasoning_effort"] == "none"
    assert second["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "I've sent that to Claude.",
    }


async def test_an_answer_the_reader_talked_over_reaches_the_next_reply() -> None:
    call = unmute_live.open_call("s1", "BRIEFING")
    model = _Model(_sse(_tool_call("call_1", "q")))
    try:
        task = asyncio.create_task(_speak(call, model, [{"role": "user", "content": "go"}]))
        await call.outbox.get()
        task.cancel()  # Unmute drops the reply when the reader interrupts
        with pytest.raises(asyncio.CancelledError):
            await task
        call.resolve("call_1", "The answer is 42.")
        messages = unmute_live.conversation_messages(call, [{"role": "user", "content": "and?"}])
    finally:
        unmute_live.close_call(call)

    assert "The answer is 42." in messages[1]["content"]
    assert messages[-1] == {"role": "user", "content": "and?"}


async def test_a_narration_reads_the_summary_verbatim_without_a_model_call() -> None:
    call = unmute_live.open_call("s1", None)
    call.narration = unmute_live.narration_text(
        "Read the following status update aloud ... Just say it:\n\nThe build passed."
    )
    model = _Model()
    try:
        said = await _speak(call, model, [{"role": "user", "content": "Hello!"}])
    finally:
        unmute_live.close_call(call)
    assert said == "The build passed."
    assert model.requests == []


def test_a_narration_words_the_amounts_unmute_would_misread() -> None:
    framed = "Read the following status update aloud ... Just say it:\n\nIt cost $1.73."
    assert unmute_live.narration_text(framed) == "It cost 1 dollar and 73 cents."


def test_the_session_update_asks_unmute_for_pcm_and_no_unprompted_speech() -> None:
    call = unmute_live.open_call("s1", "BRIEFING")
    narration = unmute_live.open_call("s1", None)
    try:
        talk = unmute_live.session_update(call)["session"]
        read = unmute_live.session_update(narration)["session"]
    finally:
        unmute_live.close_call(call)
        unmute_live.close_call(narration)
    assert unmute_live.find_call(talk["instructions"]["text"]) is None  # closed
    assert talk["audio_format"] == "pcm16" and talk["input_sample_rate"] == 16_000
    assert talk["greet"] is False and talk["nudge_on_silence"] is False
    assert talk["interrupt_on_vad"] is False
    # A narration "greets" with its text, off silence the relay feeds it.
    assert read["greet"] is True and read["input_sample_rate"] == 24_000


def test_a_call_speaks_with_the_voice_the_page_chose() -> None:
    upbeat = unmute_live.open_call("s1", "BRIEFING", "ex03-happy")
    unknown = unmute_live.open_call("s1", "BRIEFING", "../etc/passwd")
    try:
        assert unmute_live.session_update(upbeat)["session"]["voice"] == unmute_live.voice(
            "ex03-happy"
        )
        # Anything but a listed id is the default, never a path passed through.
        assert unmute_live.session_update(unknown)["session"]["voice"] == unmute_live.voice(
            unmute_live.DEFAULT_VOICE_ID
        )
        assert "ex03-ex01_happy" in unmute_live.voice("ex03-happy")
    finally:
        unmute_live.close_call(upbeat)
        unmute_live.close_call(unknown)


def _app(**kwargs: Any) -> TestClient:
    app = FastAPI()
    app.include_router(routes.create_unmute_live_router(**kwargs), prefix="/v1")
    return TestClient(app)


def test_the_brain_refuses_callers_without_the_budget_token() -> None:
    client = _app(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    response = client.post("/v1/live/unmute/brain/v1/chat/completions", json={"model": "m"})
    assert response.status_code == 401


def test_unmutes_per_call_health_check_is_answered_without_a_network_hop() -> None:
    def model(request: httpx.Request) -> httpx.Response:
        raise AssertionError("the model list must not be fetched upstream")

    response = _app(transport=httpx.MockTransport(model)).get(
        "/v1/live/unmute/brain/v1/models",
        headers={"Authorization": f"Bearer {ensure_proxy_token()}"},
    )
    assert response.status_code == 200
    listed = {m["id"] for m in response.json()["data"]}
    assert listed and all(budget.pool_for(m) not in (None, budget.UNLISTED) for m in listed)


def test_unmutes_own_page_passes_through_to_the_model_untouched() -> None:
    seen: list[tuple[str, bytes]] = []

    def model(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.content))
        return httpx.Response(
            200, stream=_Body(_sse(_text("hi"))), headers={"content-type": "text/event-stream"}
        )

    body = {
        "model": "m",
        "stream": True,
        "messages": [{"role": "system", "content": "a character"}],
    }
    response = _app(transport=httpx.MockTransport(model)).post(
        "/v1/live/unmute/brain/v1/chat/completions",
        json=body,
        headers={"Authorization": f"Bearer {ensure_proxy_token()}"},
    )
    assert response.status_code == 200
    assert "hi" in response.text
    [(url, content)] = seen
    assert url.endswith("/v1/openai-budget/v1/chat/completions")
    assert json.loads(content) == body


class _FakeUnmute:
    """An Unmute realtime socket the test scripts from the other side."""

    subprotocol = "realtime"

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.inbox: asyncio.Queue[str | None] = asyncio.Queue()

    async def send(self, message: str) -> None:
        event = json.loads(message)
        self.sent.append(event)
        if event["type"] == "session.update":
            await self.inbox.put(
                json.dumps({"type": "session.updated", "session": event["session"]})
            )
        elif event["type"] == "input_audio_buffer.append":
            # Answer the first audio as Unmute would a finished sentence.
            for reply in (
                {"type": "conversation.item.input_audio_transcription.delta", "delta": "hi"},
                {"type": "response.audio.delta", "delta": "AAAA"},
                {"type": "response.text.delta", "delta": "Hello"},
                {"type": "response.audio.done"},
            ):
                await self.inbox.put(json.dumps(reply))

    async def close(self) -> None:
        await self.inbox.put(None)

    def __aiter__(self) -> _FakeUnmute:
        return self

    async def __anext__(self) -> str:
        item = await self.inbox.get()
        if item is None:
            raise StopAsyncIteration
        return item


def test_the_page_talks_gemini_frames_through_the_relay() -> None:
    unmute = _FakeUnmute()
    urls: list[tuple[str, list[str]]] = []

    async def connect(url: str, subprotocols: list[str]) -> _FakeUnmute:
        urls.append((url, subprotocols))
        return unmute

    client = _app(upstream_connect=connect)
    with client.websocket_connect("/v1/live/unmute/ws?session_id=s1&voice=ex03-happy") as ws:
        assert json.loads(ws.receive_text()) == {"setupComplete": {}}
        ws.send_text(json.dumps({"realtimeInput": {"audio": {"data": "AQI=", "mimeType": "x"}}}))
        frames = [json.loads(ws.receive_text()) for _ in range(4)]

    assert urls == [("ws://127.0.0.1:8089/unmute/api/v1/realtime", ["realtime"])]
    update, audio = unmute.sent[:2]
    assert update["session"]["instructions"]["text"].startswith("omnigent-call:")
    assert update["session"]["voice"] == unmute_live.voice("ex03-happy")
    # Audio passes through as-is: both sides carry base64 PCM16.
    assert audio == {"type": "input_audio_buffer.append", "audio": "AQI="}
    assert frames[0] == {"serverContent": {"inputTranscription": {"text": " hi"}}}
    assert "modelTurn" in frames[1]["serverContent"]
    assert frames[2] == {"serverContent": {"outputTranscription": {"text": " Hello"}}}
    assert frames[3] == {"serverContent": {"turnComplete": True}}


def test_the_input_trace_shows_whether_the_page_sent_speech_or_silence() -> None:
    import base64

    import numpy as np

    trace = routes._InputTrace(started=100.0)
    loud = (np.sin(np.arange(1600) / 5) * 8000).astype("<i2").tobytes()
    trace.audio(100.2, base64.b64encode(loud).decode())
    trace.audio(101.5, base64.b64encode(bytes(3200)).decode())  # a muted mic
    trace.word(100.4)
    trace.reply(102.0)

    summary = trace.summary()
    assert summary.startswith("mic_dbfs_per_s=[-15,-90]")
    assert "words_at=[0.4]" in summary and "replies_at=[2.0]" in summary


async def test_a_lookup_goes_to_the_page_under_its_own_name() -> None:
    call = unmute_live.open_call("s1", "BRIEFING")
    model = _Model(
        _sse(_tool_call("call_7", "how full is the disk", name="check_machine")),
        _sse(_text("About 97 percent.")),
    )

    async def page() -> None:
        frame = await call.outbox.get()
        [asked] = frame["toolCall"]["functionCalls"]
        assert asked == {
            "id": "call_7",
            "name": "check_machine",
            "args": {"question": "how full is the disk"},
        }
        call.resolve("call_7", "Looked it up: 97% used.")

    heard = {"role": "user", "content": "how full is the disk?"}
    try:
        said, _ = await asyncio.gather(_speak(call, model, [heard]), page())
    finally:
        unmute_live.close_call(call)
    assert said == "About 97 percent."
    first = model.requests[0]
    assert [t["function"]["name"] for t in first["tools"]] == ["ask_claude", "check_machine"]


# -- the hub's voice on Unmute -----------------------------------------------------


class _HubRouter:
    async def perform(self, task, *, timeout_s=None):
        return "done"

    def remember(self, text):
        pass


def _hub(tmp_path, sent: list[tuple[str, str]]):
    from omnigent.server.hub import Hub, HubBackend, HubSession, HubState, TurnState

    sessions = [HubSession(id="s-brasa", title="Orders", nickname="brasa")]

    async def listed():
        return list(sessions)

    async def activity(session_id):
        return f"status of {session_id}"

    async def send(session_id, text):
        sent.append((session_id, text))

    async def check(session_id, question):
        return "18 GB free"

    async def turn(session_id, since):
        return TurnState(status="running")

    async def router():
        return _HubRouter()

    hub = Hub("u1", router_factory=router, state=HubState("u1", root=tmp_path), poll_s=3600)
    hub.bind(HubBackend(sessions=listed, activity=activity, send=send, check=check, turn=turn))
    return hub


def _hub_tool_call(call_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": call_id,
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                    ]
                },
                "finish_reason": "tool_calls",
            }
        ]
    }


async def test_the_hubs_voice_runs_the_hubs_tools_on_the_server(tmp_path) -> None:
    sent: list[tuple[str, str]] = []
    hub = _hub(tmp_path, sent)
    call = unmute_live.open_call("hubconv", "HUB BRIEFING")
    call.hub = hub
    model = _Model(
        _sse(_hub_tool_call("c1", "ask_claude", {"session": "brasa", "message": "run tests"})),
        _sse(_text("Sent it to brasa.")),
    )
    try:
        said = await _speak(call, model, [{"role": "user", "content": "send brasa the tests"}])
        shown = [call.outbox.get_nowait(), call.outbox.get_nowait()]
    finally:
        unmute_live.close_call(call)
        await hub.close()

    assert said == "Sent it to brasa."
    assert sent == [("s-brasa", "run tests")]
    first, second = model.requests
    names = [t["function"]["name"] for t in first["tools"]]
    assert names == ["session_status", "ask_claude", "check_machine", "set_model"]
    assert first["tools"][1]["function"]["parameters"]["type"] == "object"
    # The model hears the hub's own result; the page only shows the call.
    assert second["messages"][-1]["content"] == (
        "Sent to brasa. You will be told when it finishes."
    )
    assert shown[0]["toolCall"]["functionCalls"][0]["name"] == "ask_claude"
    assert shown[1]["omnigentToolDone"]["project"] == "brasa"
    # Sent from the call, it lands in the hub conversation like a typed send.
    assert [(e.kind, e.via) for e in hub.state.entries] == [("sent", "voice")]


def test_news_during_the_call_is_told_in_the_next_reply() -> None:
    call = unmute_live.open_call("hubconv", "HUB BRIEFING")
    call.updates.append("brasa finished: all tests pass.")
    try:
        # A reply the relay asked for arrives as Unmute's silence marker.
        messages = unmute_live.conversation_messages(call, [{"role": "user", "content": "..."}])
        again = unmute_live.conversation_messages(call, [{"role": "user", "content": "ok"}])
    finally:
        unmute_live.close_call(call)
    assert "brasa finished: all tests pass." in messages[1]["content"]
    assert messages[-1] == {"role": "user", "content": "(listening)"}
    assert len(again) == 2  # told once


class _HubStore:
    def __init__(self) -> None:
        import time
        from types import SimpleNamespace

        self.convs = {
            "hubconv": SimpleNamespace(
                id="hubconv",
                title="Hub",
                labels={"omnigent.hub": "1"},
                updated_at=time.time(),
                live_status="idle",
                pending_elicitation_count=0,
                workspace=None,
            ),
            "s-brasa": SimpleNamespace(
                id="s-brasa",
                title="Orders",
                labels={},
                updated_at=time.time(),
                live_status="idle",
                pending_elicitation_count=0,
                workspace="/tmp",
            ),
        }

    def get_conversation(self, session_id):
        return self.convs.get(session_id)

    def list_conversations(self, **kwargs):
        from types import SimpleNamespace

        return SimpleNamespace(data=list(self.convs.values()))

    def list_items(self, session_id, limit=100, order="asc", type=None):
        from types import SimpleNamespace

        return SimpleNamespace(data=[])


def test_a_call_from_the_hub_conversation_is_the_hubs_voice(monkeypatch, tmp_path) -> None:
    from omnigent.server import hub as hub_module

    monkeypatch.setattr(hub_module, "HUB_DIR", tmp_path / "hub")
    monkeypatch.setattr(hub_module, "_HUBS", {})
    opened: list[unmute_live.UnmuteCall] = []
    original = unmute_live.open_call

    def capture(*args, **kwargs):
        call = original(*args, **kwargs)
        opened.append(call)
        return call

    monkeypatch.setattr(unmute_live, "open_call", capture)
    unmute = _FakeUnmute()

    async def connect(url: str, subprotocols: list[str]) -> _FakeUnmute:
        return unmute

    client = _app(upstream_connect=connect, conversation_store=_HubStore())
    with client.websocket_connect("/v1/live/unmute/ws?session_id=hubconv") as ws:
        hello = json.loads(ws.receive_text())
        assert hello == {"omnigentSession": {"model": "unmute", "toolsBlock": True, "hub": True}}
        assert json.loads(ws.receive_text()) == {"setupComplete": {}}
        hub = hub_module._HUBS["local"]
        hub.state.append(role="hub", kind="sent", text="Sent to Orders.")
        hub.state.append(role="hub", kind="finished", text="Orders finished: green.")
        for _ in range(100):
            if any(e["type"] == "unmute.response.create" for e in unmute.sent):
                break
            import time

            time.sleep(0.02)
    [call] = opened
    assert call.hub is hub
    assert '"Orders" · idle' in (call.instructions or "")  # briefed on every session
    assert "hubconv" not in (call.instructions or "")  # never offers itself
    asked = [e for e in unmute.sent if e["type"] == "unmute.response.create"]
    assert len(asked) == 1  # the send is not news; the finish is
    assert call.updates == ["Orders finished: green."]
    assert hub.chat.session_id == "hubconv"


def test_an_ordinary_call_is_not_the_hub(monkeypatch, tmp_path) -> None:
    from omnigent.server import hub as hub_module

    monkeypatch.setattr(hub_module, "_HUBS", {})
    unmute = _FakeUnmute()

    async def connect(url: str, subprotocols: list[str]) -> _FakeUnmute:
        return unmute

    client = _app(upstream_connect=connect, conversation_store=_HubStore())
    with client.websocket_connect("/v1/live/unmute/ws?session_id=s-brasa") as ws:
        assert json.loads(ws.receive_text()) == {"setupComplete": {}}
    assert hub_module._HUBS == {}
