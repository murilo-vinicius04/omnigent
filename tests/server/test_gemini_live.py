"""Tests for omnigent.server.gemini_live: key resolution, setup frame, URL."""

from __future__ import annotations

import pytest

from omnigent.server import gemini_live
from omnigent.server.live_voice import NARRATOR_INSTRUCTIONS


def test_env_key_wins(monkeypatch, tmp_path):
    env_file = tmp_path / "gemini.env"
    env_file.write_text('GEMINI_API_KEY="from-file"\n', encoding="utf-8")
    monkeypatch.setattr(gemini_live, "KEY_PATH", env_file)
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    assert gemini_live.api_key() == "from-env"


def test_file_key_used_when_env_absent(monkeypatch, tmp_path):
    env_file = tmp_path / "gemini.env"
    env_file.write_text("GEMINI_API_KEY = 'from-file' \n\n", encoding="utf-8")
    monkeypatch.setattr(gemini_live, "KEY_PATH", env_file)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert gemini_live.api_key() == "from-file"


def test_no_key_available_false(monkeypatch, tmp_path):
    monkeypatch.setattr(gemini_live, "KEY_PATH", tmp_path / "missing.env")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert gemini_live.available() is False


def test_no_key_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(gemini_live, "KEY_PATH", tmp_path / "missing.env")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(gemini_live.GeminiLiveUnavailable):
        gemini_live.api_key()


def test_setup_frame():
    frame = gemini_live.setup_frame()
    setup = frame["setup"]
    assert setup["generationConfig"]["responseModalities"] == ["AUDIO"]
    assert setup["inputAudioTranscription"] == {}
    assert setup["outputAudioTranscription"] == {}
    assert setup["model"].startswith("models/")
    assert "systemInstruction" not in setup


def test_conversation_and_narration_speak_in_the_same_voice():
    # Omitting speechConfig hands the turn to Google's default voice, so the
    # companion read in one voice and talked in another.
    talking = gemini_live.setup_frame()["setup"]["generationConfig"]["speechConfig"]
    reading = gemini_live.setup_frame(mode="narrate")["setup"]["generationConfig"]["speechConfig"]
    assert talking == reading
    assert talking["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == gemini_live.DEFAULT_VOICE
    chosen = gemini_live.setup_frame(voice="Charon")["setup"]["generationConfig"]["speechConfig"]
    assert chosen["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Charon"


def test_setup_frame_without_briefing_matches_today():
    frame_plain = gemini_live.setup_frame()
    assert gemini_live.setup_frame(system_instruction=None) == frame_plain
    assert gemini_live.setup_frame(system_instruction="") == frame_plain
    assert gemini_live.setup_frame(system_instruction="   ") == frame_plain
    assert "systemInstruction" not in frame_plain["setup"]
    assert "tools" not in frame_plain["setup"]


def test_setup_frame_with_briefing():
    frame = gemini_live.setup_frame(system_instruction="Role and background notes")
    setup = frame["setup"]
    assert setup["generationConfig"]["responseModalities"] == ["AUDIO"]
    assert setup["inputAudioTranscription"] == {}
    assert setup["outputAudioTranscription"] == {}
    assert setup["model"] == gemini_live.MODEL
    assert setup["systemInstruction"] == {"parts": [{"text": "Role and background notes"}]}
    assert "tools" in setup
    assert len(setup["tools"]) == 1
    funcs = setup["tools"][0]["functionDeclarations"]
    assert len(funcs) == 1
    assert funcs[0]["name"] == "ask_claude"
    assert funcs[0]["parameters"]["type"] == "OBJECT"
    assert "question" in funcs[0]["parameters"]["properties"]
    assert funcs[0]["parameters"]["properties"]["question"]["type"] == "STRING"
    assert funcs[0]["parameters"]["required"] == ["question"]


def test_setup_frame_model_prefix_once():
    assert gemini_live.setup_frame()["setup"]["model"] == gemini_live.MODEL
    bare = gemini_live.MODEL.removeprefix("models/")
    assert gemini_live.setup_frame(model=bare)["setup"]["model"] == gemini_live.MODEL
    assert gemini_live.setup_frame(model=gemini_live.MODEL)["setup"]["model"] == gemini_live.MODEL


def test_socket_url_carries_key():
    url = gemini_live.socket_url("k-123")
    assert url.startswith(gemini_live.ENDPOINT)
    assert url.endswith("?key=k-123")


# --- Step 2: the browser-facing proxy WebSocket ---------------------------

# Appended section: mid-file imports are deliberate (append-only file).
import asyncio  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import time  # noqa: E402

import websockets  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402
from websockets.frames import Close  # noqa: E402

from omnigent.server.discussion import DiscussionRegistry, voice_briefing  # noqa: E402
from omnigent.server.routes import gemini_live as gemini_live_routes  # noqa: E402
from omnigent.server.routes.gemini_live import create_gemini_live_router  # noqa: E402


class _FakeUpstream:
    """In-memory upstream socket: records sends, replays a queue."""

    def __init__(self) -> None:
        self.sent: list[object] = []
        self.incoming: asyncio.Queue[object] = asyncio.Queue()
        self.closed = False
        # The route's event loop, captured when the connection opens.
        self.loop: asyncio.AbstractEventLoop | None = None

    async def send(self, message: object) -> None:
        self.sent.append(message)

    async def recv(self) -> object:
        item = await self.incoming.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        self.closed = True

    def push_from_test_thread(self, item: object) -> None:
        """Feed an upstream frame from the (synchronous) test thread."""
        assert self.loop is not None
        asyncio.run_coroutine_threadsafe(self.incoming.put(item), self.loop).result()


def _proxy_app(**router_kwargs: object) -> FastAPI:
    app = FastAPI()
    app.include_router(create_gemini_live_router(**router_kwargs), prefix="/v1")
    return app


def _no_key(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(gemini_live, "KEY_PATH", tmp_path / "missing.env")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def _with_key(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / "gemini.env"
    env_file.write_text("GEMINI_API_KEY=test-key\n", encoding="utf-8")
    monkeypatch.setattr(gemini_live, "KEY_PATH", env_file)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def test_missing_key_closes_client_socket(monkeypatch, tmp_path) -> None:
    """No key: the client socket is closed with a reason, not served."""
    _no_key(monkeypatch, tmp_path)
    with TestClient(_proxy_app()) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws") as ws:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                while True:
                    ws.receive_text()
    assert exc_info.value.code == 1011
    assert "gemini live unavailable" in (exc_info.value.reason or "")


def test_setup_frame_is_first_upstream_message(monkeypatch, tmp_path) -> None:
    """The proven setup frame precedes everything else upstream."""
    _with_key(monkeypatch, tmp_path)
    upstream = _FakeUpstream()
    connect_calls: list[str] = []

    async def fake_connect(url: str):
        connect_calls.append(url)
        return upstream

    with TestClient(_proxy_app(upstream_connect=fake_connect)) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws"):
            # The route sends the setup frame as soon as it connects.
            deadline = 50
            while not upstream.sent and deadline:
                deadline -= 1
                time.sleep(0.02)
    assert connect_calls == [gemini_live.socket_url("test-key")]
    assert upstream.sent
    setup_text = upstream.sent[0]
    assert isinstance(setup_text, str)
    assert json.loads(setup_text) == gemini_live.setup_frame()


def test_relay_both_directions(monkeypatch, tmp_path) -> None:
    """Client text reaches upstream; upstream frames reach the client."""
    _with_key(monkeypatch, tmp_path)
    upstream = _FakeUpstream()

    async def fake_connect(url: str):
        upstream.loop = asyncio.get_running_loop()
        return upstream

    with TestClient(_proxy_app(upstream_connect=fake_connect)) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws") as ws:
            ws.send_text('{"realtimeInput":{}}')
            ws.send_bytes(b"\x01\x02audio")
            for _ in range(100):
                if len(upstream.sent) >= 3:
                    break
                time.sleep(0.02)
            upstream.push_from_test_thread('{"serverContent":{}}')
            upstream.push_from_test_thread(b"\x03pcm-bytes")
            assert ws.receive_text() == '{"serverContent":{}}'
            assert ws.receive_bytes() == b"\x03pcm-bytes"
    # Setup frame, then the relayed client text and binary.
    setup_text = upstream.sent[0]
    assert isinstance(setup_text, str)
    assert json.loads(setup_text) == gemini_live.setup_frame()
    assert upstream.sent[1] == '{"realtimeInput":{}}'
    assert upstream.sent[2] == b"\x01\x02audio"


# --- Appended: runaway guards (session cap, connect timeout, keepalive) ---


def test_session_cap_closes_open_session(monkeypatch, tmp_path) -> None:
    """A still-open session is closed with a reason when the cap fires."""
    _with_key(monkeypatch, tmp_path)
    upstream = _FakeUpstream()

    async def fake_connect(url: str):
        return upstream

    app = _proxy_app(
        upstream_connect=fake_connect,
        max_session_s=0.05,
        client_idle_timeout_s=10.0,
    )
    with TestClient(app) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws") as ws:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                while True:
                    ws.receive_text()
    assert exc_info.value.code == 1008
    assert "session cap reached" in (exc_info.value.reason or "")
    assert upstream.closed is True


def test_connect_timeout_closes_client(monkeypatch, tmp_path) -> None:
    """An upstream that never completes its connect hits the timeout."""
    _with_key(monkeypatch, tmp_path)

    async def hang_connect(url: str):
        await asyncio.sleep(30)

    app = _proxy_app(upstream_connect=hang_connect, handshake_timeout_s=0.05)
    with TestClient(app) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws") as ws:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                while True:
                    ws.receive_text()
    assert exc_info.value.code == 1011
    assert "connect timed out" in (exc_info.value.reason or "")


def test_client_keepalive_closes_silent_client(monkeypatch, tmp_path) -> None:
    """A silent client past the keepalive window is closed with a reason."""
    _with_key(monkeypatch, tmp_path)
    upstream = _FakeUpstream()

    async def fake_connect(url: str):
        return upstream

    app = _proxy_app(upstream_connect=fake_connect, client_idle_timeout_s=0.05)
    with TestClient(app) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws") as ws:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                while True:
                    ws.receive_text()
    assert exc_info.value.code == 1008
    assert "keepalive timed out" in (exc_info.value.reason or "")
    assert upstream.closed is True


def test_keepalive_survives_client_traffic(monkeypatch, tmp_path) -> None:
    """The keepalive resets on client frames; active sessions stay open."""
    _with_key(monkeypatch, tmp_path)
    upstream = _FakeUpstream()

    async def fake_connect(url: str):
        return upstream

    app = _proxy_app(upstream_connect=fake_connect, client_idle_timeout_s=0.1)
    with TestClient(app) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws") as ws:
            for _ in range(3):
                time.sleep(0.04)
                ws.send_text('{"realtimeInput":{}}')  # resets the window
            assert not upstream.closed


# --- Step 3: the availability JSON endpoint -------------------------------


class _FakeAuth:
    """AuthProvider stub: refuses unless the right header is present."""

    def get_user_id(self, request):
        if request.headers.get("x-test-user") == "alice":
            return "alice"
        return None


def test_availability_configured_true(monkeypatch, tmp_path) -> None:
    """A key is present: configured true and the model matches gemini_live.MODEL."""
    _with_key(monkeypatch, tmp_path)
    with TestClient(_proxy_app()) as tc:
        resp = tc.get("/v1/live/gemini/availability")
    assert resp.status_code == 200
    assert resp.json() == {"configured": True, "model": gemini_live.MODEL}


def test_availability_configured_false(monkeypatch, tmp_path) -> None:
    """No key anywhere: HTTP 200, configured false, no exception."""
    _no_key(monkeypatch, tmp_path)
    with TestClient(_proxy_app()) as tc:
        resp = tc.get("/v1/live/gemini/availability")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["model"] == gemini_live.MODEL


def test_availability_never_leaks_key(monkeypatch, tmp_path) -> None:
    """The key string must not appear anywhere in the raw response text."""
    secret = "super-secret-key-xyzzy"
    env_file = tmp_path / "gemini.env"
    env_file.write_text(f"GEMINI_API_KEY='{secret}'\n", encoding="utf-8")
    monkeypatch.setattr(gemini_live, "KEY_PATH", env_file)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with TestClient(_proxy_app()) as tc:
        resp = tc.get("/v1/live/gemini/availability")
    assert resp.status_code == 200
    assert secret not in resp.text
    assert resp.text  # non-empty body sanity


def test_availability_refuses_unauthenticated(monkeypatch, tmp_path) -> None:
    """With auth_provider set, an unauthenticated request gets HTTP 401."""
    _with_key(monkeypatch, tmp_path)
    app = _proxy_app(auth_provider=_FakeAuth())
    with TestClient(app) as tc:
        refused = tc.get("/v1/live/gemini/availability")
        allowed = tc.get("/v1/live/gemini/availability", headers={"x-test-user": "alice"})
    assert refused.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["configured"] is True


def test_session_summary_logged_with_counts(monkeypatch, tmp_path, caplog) -> None:
    """Relaying frames logs the one-line summary with correct counts."""
    _with_key(monkeypatch, tmp_path)
    upstream = _FakeUpstream()

    async def fake_connect(url: str):
        upstream.loop = asyncio.get_running_loop()
        return upstream

    caplog.set_level(logging.INFO)
    with TestClient(_proxy_app(upstream_connect=fake_connect)) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws") as ws:
            ws.send_text('{"realtimeInput":{}}')
            ws.send_bytes(b"\x01\x02audio")
            for _ in range(100):
                if len(upstream.sent) >= 3:
                    break
                time.sleep(0.02)
            upstream.push_from_test_thread('{"serverContent":{}}')
            assert ws.receive_text() == '{"serverContent":{}}'

    summary_records = [
        r.getMessage() for r in caplog.records if "gemini live session ended | " in r.getMessage()
    ]
    assert len(summary_records) == 1
    summary = summary_records[0]
    assert "client_frames=2" in summary
    assert "upstream_frames=1" in summary
    assert "ended_by=client" in summary
    assert "upstream_close=none" in summary
    assert "duration_s=" in summary


def test_upstream_close_code_and_reason_logged(monkeypatch, tmp_path, caplog) -> None:
    """An upstream closing with code/reason logs it and closes client as before."""
    _with_key(monkeypatch, tmp_path)
    upstream = _FakeUpstream()

    async def fake_connect(url: str):
        upstream.loop = asyncio.get_running_loop()
        return upstream

    caplog.set_level(logging.INFO)
    exc = websockets.ConnectionClosed(Close(1007, "invalid frame"), None)
    with TestClient(_proxy_app(upstream_connect=fake_connect)) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws") as ws:
            upstream.push_from_test_thread(exc)
            with pytest.raises(WebSocketDisconnect) as exc_info:
                while True:
                    ws.receive_text()
            assert exc_info.value.code == 1000

    summary_records = [
        r.getMessage() for r in caplog.records if "gemini live session ended | " in r.getMessage()
    ]
    assert len(summary_records) == 1
    summary = summary_records[0]
    assert "ended_by=upstream" in summary
    assert "upstream_close=1007/invalid frame" in summary


# --- Step 4: session briefing parity --------------------------------------


def test_route_with_session_context_sends_briefed_setup_frame(
    monkeypatch, tmp_path, caplog
) -> None:
    """A session with context sends an upstream setup frame with the briefing."""
    _with_key(monkeypatch, tmp_path)
    upstream = _FakeUpstream()

    async def fake_connect(url: str):
        return upstream

    reg = DiscussionRegistry()
    reg.note("conv_a", "summary", "Claude finished the migration")
    companion = reg.peek("conv_a")
    assert companion is not None
    expected_briefing = voice_briefing(companion)

    caplog.set_level(logging.INFO)
    app = _proxy_app(upstream_connect=fake_connect, registry_provider=lambda: reg)
    with TestClient(app) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws?session_id=conv_a"):
            deadline = 50
            while not upstream.sent and deadline:
                deadline -= 1
                time.sleep(0.02)
    assert upstream.sent
    sent_frame = json.loads(upstream.sent[0])
    assert sent_frame["setup"]["systemInstruction"]["parts"][0]["text"] == expected_briefing
    assert not any("gemini live briefing skipped" in r.getMessage() for r in caplog.records)
    summary = next(
        r.getMessage() for r in caplog.records if "gemini live session ended | " in r.getMessage()
    )
    assert "briefed=yes" in summary
    for r in caplog.records:
        assert "test-key" not in r.getMessage()


def test_route_missing_session_id_skips_briefing_and_logs(monkeypatch, tmp_path, caplog) -> None:
    """Missing session_id sends plain setup frame and logs reason=missing-session."""
    _with_key(monkeypatch, tmp_path)
    upstream = _FakeUpstream()

    async def fake_connect(url: str):
        return upstream

    caplog.set_level(logging.INFO)
    with TestClient(_proxy_app(upstream_connect=fake_connect)) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws"):
            deadline = 50
            while not upstream.sent and deadline:
                deadline -= 1
                time.sleep(0.02)
    assert upstream.sent
    sent_frame = json.loads(upstream.sent[0])
    assert "systemInstruction" not in sent_frame["setup"]
    assert any(
        "gemini live briefing skipped | reason=missing-session" in r.getMessage()
        for r in caplog.records
    )
    summary = next(
        r.getMessage() for r in caplog.records if "gemini live session ended | " in r.getMessage()
    )
    assert "briefed=no" in summary


def test_route_unknown_session_id_skips_briefing_and_logs(monkeypatch, tmp_path, caplog) -> None:
    """Unknown session_id sends plain setup frame and logs reason=no-context."""
    _with_key(monkeypatch, tmp_path)
    upstream = _FakeUpstream()

    async def fake_connect(url: str):
        return upstream

    reg = DiscussionRegistry()
    caplog.set_level(logging.INFO)
    app = _proxy_app(upstream_connect=fake_connect, registry_provider=lambda: reg)
    with TestClient(app) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws?session_id=unknown-session"):
            deadline = 50
            while not upstream.sent and deadline:
                deadline -= 1
                time.sleep(0.02)
    assert upstream.sent
    sent_frame = json.loads(upstream.sent[0])
    assert "systemInstruction" not in sent_frame["setup"]
    assert any(
        "gemini live briefing skipped | reason=no-context" in r.getMessage()
        for r in caplog.records
    )
    summary = next(
        r.getMessage() for r in caplog.records if "gemini live session ended | " in r.getMessage()
    )
    assert "briefed=no" in summary


def test_route_empty_context_skips_briefing_and_logs(monkeypatch, tmp_path, caplog) -> None:
    """A session with empty context logs reason=no-context and sends plain frame."""
    _with_key(monkeypatch, tmp_path)
    upstream = _FakeUpstream()

    async def fake_connect(url: str):
        return upstream

    reg = DiscussionRegistry()
    asyncio.run(reg.get("empty_session"))
    caplog.set_level(logging.INFO)
    app = _proxy_app(upstream_connect=fake_connect, registry_provider=lambda: reg)
    with TestClient(app) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws?session_id=empty_session"):
            deadline = 50
            while not upstream.sent and deadline:
                deadline -= 1
                time.sleep(0.02)
    assert upstream.sent
    sent_frame = json.loads(upstream.sent[0])
    assert "systemInstruction" not in sent_frame["setup"]
    assert any(
        "gemini live briefing skipped | reason=no-context" in r.getMessage()
        for r in caplog.records
    )
    summary = next(
        r.getMessage() for r in caplog.records if "gemini live session ended | " in r.getMessage()
    )
    assert "briefed=no" in summary


def test_route_builder_error_skips_briefing_and_logs(monkeypatch, tmp_path, caplog) -> None:
    """When briefing resolution raises, connect as plain frame and log reason=error."""
    _with_key(monkeypatch, tmp_path)
    upstream = _FakeUpstream()

    async def fake_connect(url: str):
        return upstream

    class _BrokenRegistry:
        def peek(self, session_id: str):
            raise RuntimeError("registry exploded")

    caplog.set_level(logging.INFO)
    app = _proxy_app(upstream_connect=fake_connect, registry_provider=_BrokenRegistry)
    with TestClient(app) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws?session_id=err_session"):
            deadline = 50
            while not upstream.sent and deadline:
                deadline -= 1
                time.sleep(0.02)
    assert upstream.sent
    sent_frame = json.loads(upstream.sent[0])
    assert "systemInstruction" not in sent_frame["setup"]
    assert any(
        "gemini live briefing skipped | reason=error" in r.getMessage() for r in caplog.records
    )
    summary = next(
        r.getMessage() for r in caplog.records if "gemini live session ended | " in r.getMessage()
    )
    assert "briefed=no" in summary


def test_route_briefed_frame_declares_ask_claude_tool_and_unbriefed_has_no_tools(
    monkeypatch, tmp_path, caplog
) -> None:
    """A briefed setup frame declares ask_claude; unbriefed has no tools; key not in logs."""
    _with_key(monkeypatch, tmp_path)
    upstream = _FakeUpstream()

    async def fake_connect(url: str):
        return upstream

    reg = DiscussionRegistry()
    reg.note("conv_b", "summary", "Work in progress")
    caplog.set_level(logging.INFO)

    app = _proxy_app(upstream_connect=fake_connect, registry_provider=lambda: reg)
    with TestClient(app) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws?session_id=conv_b"):
            deadline = 50
            while not upstream.sent and deadline:
                deadline -= 1
                time.sleep(0.02)
    assert upstream.sent
    briefed_frame = json.loads(upstream.sent[0])
    assert "tools" in briefed_frame["setup"]
    assert len(briefed_frame["setup"]["tools"]) == 1
    decls = briefed_frame["setup"]["tools"][0]["functionDeclarations"]
    assert len(decls) == 1
    assert decls[0]["name"] == "ask_claude"
    assert decls[0]["parameters"]["required"] == ["question"]

    # Unbriefed frame has no tools key
    unbriefed = gemini_live.setup_frame()
    assert "tools" not in unbriefed["setup"]

    # Key never appears in log record
    for r in caplog.records:
        assert "test-key" not in r.getMessage()


def test_session_summary_logs_tool_calls_and_handoff(monkeypatch, tmp_path, caplog) -> None:
    """Tool calls from upstream and handoff from client are reflected in end-of-session log."""
    _with_key(monkeypatch, tmp_path)
    upstream = _FakeUpstream()

    async def fake_connect(url: str):
        upstream.loop = asyncio.get_running_loop()
        return upstream

    caplog.set_level(logging.INFO)
    with TestClient(_proxy_app(upstream_connect=fake_connect)) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws") as ws:
            # upstream sends a toolCall
            upstream.push_from_test_thread(
                json.dumps(
                    {
                        "toolCall": {
                            "functionCalls": [
                                {
                                    "id": "call_1",
                                    "name": "ask_claude",
                                    "args": {"question": "test?"},
                                }
                            ]
                        }
                    }
                )
            )
            assert ws.receive_text()
            # client responds with handed_off response
            ws.send_text(
                json.dumps(
                    {
                        "toolResponse": {
                            "functionResponses": [
                                {
                                    "id": "call_1",
                                    "name": "ask_claude",
                                    "response": {
                                        "output": (
                                            "I've sent that to Claude. Its answer will show "
                                            "up in the chat, so I'm ending the call now."
                                        )
                                    },
                                }
                            ]
                        }
                    }
                )
            )
            for _ in range(50):
                if len(upstream.sent) >= 2:
                    break
                time.sleep(0.02)

    summary = next(
        r.getMessage() for r in caplog.records if "gemini live session ended | " in r.getMessage()
    )
    assert "tool_calls=1" in summary
    assert "handed_off=yes" in summary


# --- Step 5: narrate mode parity ------------------------------------------


def test_setup_frame_narrate_mode() -> None:
    """Narrate mode produces narrator systemInstruction, voice in speechConfig, and no tools."""
    frame = gemini_live.setup_frame(mode="narrate")
    setup = frame["setup"]
    assert setup["model"] == gemini_live.MODEL
    assert setup["generationConfig"]["responseModalities"] == ["AUDIO"]
    voice_config = setup["generationConfig"]["speechConfig"]["voiceConfig"]
    assert voice_config["prebuiltVoiceConfig"]["voiceName"] == gemini_live.DEFAULT_VOICE
    assert setup["systemInstruction"] == {"parts": [{"text": NARRATOR_INSTRUCTIONS}]}
    assert "tools" not in setup
    assert "inputAudioTranscription" not in setup

    # Custom voice override
    frame_custom = gemini_live.setup_frame(mode="narrate", voice="Puck")
    custom_voice = frame_custom["setup"]["generationConfig"]["speechConfig"]["voiceConfig"]
    assert custom_voice["prebuiltVoiceConfig"]["voiceName"] == "Puck"


def test_setup_frame_unknown_mode_falls_back_to_conversation() -> None:
    """Unknown mode falls back to conversation mode frame."""
    frame_plain = gemini_live.setup_frame()
    assert gemini_live.setup_frame(mode="unknown") == frame_plain
    assert gemini_live.setup_frame(mode="conversation") == frame_plain


def test_route_narrate_mode_sends_narrator_setup_frame(monkeypatch, tmp_path) -> None:
    """mode=narrate sends setup frame with narrator instructions, voice, and skips briefing."""
    _with_key(monkeypatch, tmp_path)
    upstream = _FakeUpstream()

    async def fake_connect(url: str):
        return upstream

    reg = DiscussionRegistry()
    reg.note("conv_narrate", "summary", "Claude finished")

    app = _proxy_app(upstream_connect=fake_connect, registry_provider=lambda: reg)
    with TestClient(app) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws?mode=narrate&session_id=conv_narrate"):
            deadline = 50
            while not upstream.sent and deadline:
                deadline -= 1
                time.sleep(0.02)
    assert upstream.sent
    sent_frame = json.loads(upstream.sent[0])
    setup = sent_frame["setup"]
    assert setup["systemInstruction"]["parts"][0]["text"] == NARRATOR_INSTRUCTIONS
    voice_config = setup["generationConfig"]["speechConfig"]["voiceConfig"]
    assert voice_config["prebuiltVoiceConfig"]["voiceName"] == gemini_live.DEFAULT_VOICE
    assert "tools" not in setup
    assert "inputAudioTranscription" not in setup


def test_route_unknown_mode_falls_back_to_conversation(monkeypatch, tmp_path) -> None:
    """An unknown mode falls back to conversation mode frame."""
    _with_key(monkeypatch, tmp_path)
    upstream = _FakeUpstream()

    async def fake_connect(url: str):
        return upstream

    app = _proxy_app(upstream_connect=fake_connect)
    with TestClient(app) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws?mode=other_mode"):
            deadline = 50
            while not upstream.sent and deadline:
                deadline -= 1
                time.sleep(0.02)
    assert upstream.sent
    sent_frame = json.loads(upstream.sent[0])
    assert sent_frame == gemini_live.setup_frame()


# --- Step 6: keepalive ping, event timeline, latencies, and security ---


def test_ping_is_not_relayed_upstream_and_resets_idle_timer(monkeypatch, tmp_path) -> None:
    """A client ping resets the idle timer and is NEVER forwarded upstream."""
    _with_key(monkeypatch, tmp_path)
    upstream = _FakeUpstream()

    async def fake_connect(url: str):
        upstream.loop = asyncio.get_running_loop()
        return upstream

    app = _proxy_app(upstream_connect=fake_connect, client_idle_timeout_s=0.1)
    with TestClient(app) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws") as ws:
            for _ in range(3):
                time.sleep(0.04)
                ws.send_text(json.dumps({"omnigentPing": True}))
            assert not upstream.closed

    # Only setup frame reached upstream; ping was dropped
    assert len(upstream.sent) == 1
    assert "omnigentPing" not in str(upstream.sent[0])


def test_narrate_session_with_only_pings_stays_open_past_idle_timeout(
    monkeypatch, tmp_path
) -> None:
    """A narration session sending only pings after the initial turn stays open."""
    _with_key(monkeypatch, tmp_path)
    upstream = _FakeUpstream()

    async def fake_connect(url: str):
        upstream.loop = asyncio.get_running_loop()
        return upstream

    app = _proxy_app(upstream_connect=fake_connect, client_idle_timeout_s=0.1)
    with TestClient(app) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws?mode=narrate") as ws:
            ws.send_text(json.dumps({"clientContent": {"turns": []}}))
            for _ in range(4):
                time.sleep(0.04)
                ws.send_text(json.dumps({"omnigentPing": True}))
            assert not upstream.closed


def test_narration_stays_open_while_only_the_model_is_talking(monkeypatch, tmp_path) -> None:
    """Audio coming down from Google holds the call open with a silent client.

    A narration sends one text turn and then only listens, so a summary longer
    than the keepalive window used to be cut off mid-sentence.
    """
    _with_key(monkeypatch, tmp_path)
    upstream = _FakeUpstream()

    async def fake_connect(url: str):
        upstream.loop = asyncio.get_running_loop()
        return upstream

    app = _proxy_app(upstream_connect=fake_connect, client_idle_timeout_s=0.1)
    with TestClient(app) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws?mode=narrate") as ws:
            ws.send_text(json.dumps({"clientContent": {"turns": []}}))
            # The client says nothing further; only Gemini speaks.
            for _ in range(5):
                time.sleep(0.04)
                upstream.push_from_test_thread(
                    json.dumps({"serverContent": {"modelTurn": {"parts": [{"inlineData": {}}]}}})
                )
                ws.receive_text()
            assert not upstream.closed


def test_session_summary_tracks_timeline_latencies_and_never_logs_text(
    monkeypatch, tmp_path, caplog
) -> None:
    """The session summary counts events, latencies, timeline, and contains no secret text."""
    _with_key(monkeypatch, tmp_path)
    upstream = _FakeUpstream()

    async def fake_connect(url: str):
        upstream.loop = asyncio.get_running_loop()
        return upstream

    caplog.set_level(logging.INFO)
    unique_transcript = "SECRET_TRANSCRIPT_ALPHA_12345"
    unique_briefing = "SECRET_BRIEFING_OMEGA_67890"

    reg = DiscussionRegistry()
    reg.note("sec_conv", "summary", unique_briefing)

    app = _proxy_app(upstream_connect=fake_connect, registry_provider=lambda: reg)
    with TestClient(app) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws?session_id=sec_conv") as ws:
            time.sleep(0.02)
            # Upstream sends setupComplete
            upstream.push_from_test_thread(json.dumps({"setupComplete": {}}))
            assert ws.receive_text()

            # Upstream sends audio chunk
            upstream.push_from_test_thread(
                json.dumps(
                    {
                        "serverContent": {
                            "modelTurn": {
                                "parts": [
                                    {
                                        "inlineData": {
                                            "mimeType": "audio/pcm;rate=24000",
                                            "data": "AAAA",
                                        }
                                    }
                                ]
                            }
                        }
                    }
                )
            )
            assert ws.receive_text()

            # Upstream sends transcript
            upstream.push_from_test_thread(
                json.dumps({"serverContent": {"outputTranscription": {"text": unique_transcript}}})
            )
            assert ws.receive_text()

            # Upstream sends interrupted
            upstream.push_from_test_thread(json.dumps({"serverContent": {"interrupted": True}}))
            assert ws.receive_text()

            # Upstream sends toolCall
            upstream.push_from_test_thread(
                json.dumps({"toolCall": {"functionCalls": [{"id": "c1", "name": "ask_claude"}]}})
            )
            assert ws.receive_text()

            # Client sends toolResponse and ping
            ws.send_text(
                json.dumps(
                    {
                        "toolResponse": {
                            "functionResponses": [
                                {"id": "c1", "name": "ask_claude", "response": {"output": "ok"}}
                            ]
                        }
                    }
                )
            )
            ws.send_text(json.dumps({"omnigentPing": True}))
            for _ in range(50):
                if len(upstream.sent) >= 2:
                    break
                time.sleep(0.01)

            # Upstream sends turnComplete
            upstream.push_from_test_thread(json.dumps({"serverContent": {"turnComplete": True}}))
            assert ws.receive_text()

    summary_records = [
        r.getMessage() for r in caplog.records if "gemini live session ended | " in r.getMessage()
    ]
    assert len(summary_records) == 1
    summary = summary_records[0]

    assert "setupComplete:1" in summary
    assert "interrupted:1" in summary
    assert "turnComplete:1" in summary
    assert "toolCall:1" in summary
    assert "toolResponse:1" in summary
    assert "pings:1" in summary
    assert "setup_ms=" in summary
    assert "first_audio_ms=" in summary
    assert "tool_latencies=[" in summary
    assert "timeline=[" in summary
    assert "setupComplete@" in summary
    assert "interrupted@" in summary
    assert "toolCall@" in summary
    assert "toolResponse@" in summary
    assert "turnComplete@" in summary

    # Verify secrecy: NO transcript or briefing text anywhere in caplog
    for record in caplog.records:
        msg = record.getMessage()
        assert unique_transcript not in msg
        assert unique_briefing not in msg


def test_go_away_logs_warning(monkeypatch, tmp_path, caplog) -> None:
    """Upstream goAway frame logs a warning."""
    _with_key(monkeypatch, tmp_path)
    upstream = _FakeUpstream()

    async def fake_connect(url: str):
        upstream.loop = asyncio.get_running_loop()
        return upstream

    caplog.set_level(logging.WARNING)
    with TestClient(_proxy_app(upstream_connect=fake_connect)) as tc:
        with tc.websocket_connect("/v1/live/gemini/ws") as ws:
            upstream.push_from_test_thread(json.dumps({"goAway": {}}))
            assert ws.receive_text()

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("received goAway from upstream" in w for w in warnings)


def test_the_voice_must_ask_before_sending_anything_to_claude():
    """Handing off ends the call, so it is the reader's decision, not the model's.

    Without this the voice delegated the moment it could not answer, which cut
    the reader off mid-discussion and shipped a half-formed request.
    """
    setup = gemini_live.setup_frame(system_instruction="notes")["setup"]
    tool = setup["tools"][0]["functionDeclarations"][0]
    assert tool["name"] == "ask_claude"
    description = tool["description"].lower()
    assert "after the user has agreed" in description
    assert "ends the call" in description
    assert "only once they say yes" in description


def test_realtime_factor_shows_when_audio_arrives_slower_than_it_plays():
    """Below 1.0 is a real shortfall, which no client-side buffer can hide."""
    one_second = 24_000 * 2
    # Two seconds of audio delivered over two seconds: keeping up exactly.
    assert gemini_live_routes._realtime_factor(2 * one_second, 10.0, 12.0) == "1.00"
    # Two seconds of audio that took four to arrive: the queue runs dry.
    assert gemini_live_routes._realtime_factor(2 * one_second, 10.0, 14.0) == "0.50"
    # Narration arrives far faster than it plays, which is why it never stutters.
    assert gemini_live_routes._realtime_factor(8 * one_second, 10.0, 12.0) == "4.00"
    assert gemini_live_routes._realtime_factor(0, None, None) == "n/a"
    assert gemini_live_routes._realtime_factor(one_second, 10.0, 10.0) == "n/a"


def test_only_audio_parts_count_toward_throughput():
    """Transcripts and tool frames must not inflate the delivered-audio figure."""
    audio = json.dumps(
        {
            "serverContent": {
                "modelTurn": {
                    "parts": [
                        {"inlineData": {"mimeType": "audio/pcm;rate=24000", "data": "AAAA"}},
                        {"text": "not audio"},
                    ]
                }
            }
        }
    )
    assert gemini_live_routes._inline_audio_bytes(audio) == 3
    transcript = json.dumps({"serverContent": {"outputTranscription": {"text": "hello"}}})
    assert gemini_live_routes._inline_audio_bytes(transcript) == 0
    assert gemini_live_routes._inline_audio_bytes("not json") == 0
    assert gemini_live_routes._inline_audio_bytes(None) == 0


def test_the_voice_cannot_claim_a_handoff_it_did_not_make():
    """On 2026-09-21 the voice said "vou pedir pra ele olhar", never called the
    tool, and then invented an off-channel link to Claude to explain the
    silence. Both the role and the tool must rule that out."""
    from omnigent.server.discussion import LIVE_VOICE_ROLE

    role = LIVE_VOICE_ROLE.lower()
    assert "no other channel to claude" in role
    assert "unless you called" in role
    setup = gemini_live.setup_frame(system_instruction="notes")["setup"]
    tool = setup["tools"][0]["functionDeclarations"][0]["description"].lower()
    assert "only way to reach claude" in tool
    assert "then call it at once" in tool


def test_reply_meter_counts_the_freezes_the_browser_hears():
    """A reply that arrives slower than it plays leaves the queue dry: that is
    the freeze, and the session-wide average hides it."""
    one_second = 24_000 * 2
    steady = gemini_live_routes._ReplyMeter()
    for i in range(4):  # 1s of audio every 0.5s: always ahead of playback
        steady.add(10.0 + 0.5 * i, one_second)
    assert steady.dry == 0
    assert steady.summary() == "4.0s@2.67x/0dry0.0s"

    starved = gemini_live_routes._ReplyMeter()
    starved.add(10.0, one_second // 2)  # plays 10.15 -> 10.65
    starved.add(12.0, one_second // 2)  # lands 1.35s after the queue ran out
    assert starved.dry == 1
    assert round(starved.dry_s, 2) == 1.35
    assert "/1dry" in starved.summary()

    assert gemini_live_routes._ReplyMeter().summary() is None
