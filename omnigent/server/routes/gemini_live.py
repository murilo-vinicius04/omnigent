"""Browser-facing proxy for the Gemini Live voice WebSocket.

The browser opens ``WS /v1/live/gemini/ws`` against Omnigent; the server
opens the upstream socket to Google and relays frames both ways, so the
API key stays server-side exactly as the OpenAI live path keeps its key
(see :mod:`omnigent.server.routes.live_voice`). Text frames relay as
text, binary as binary. A missing key or a refused upstream closes the
client socket with a reason instead of hanging.

The upstream connection uses the ``websockets`` library (a declared
dependency, pinned ``<15``); the client-facing side is Starlette's own
WebSocket, as in the dictation route.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final

import websockets
from fastapi import (
    APIRouter,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    WebSocketException,
)
from starlette import status

from omnigent.server import gemini_live, hub_chat, voice_hub
from omnigent.server import hub as hub_module
from omnigent.server.auth import AuthProvider
from omnigent.server.discussion import DiscussionRegistry, registry, voice_briefing
from omnigent.server.spoken_summary import concrete_language, selected_language

_logger = logging.getLogger(__name__)

_WS_CLOSE_INTERNAL_ERROR: Final[int] = 1011

#: Deliberate policy stops (session cap, keepalive timeout), not server
#: faults: a client must not treat these as retryable server errors.
_WS_CLOSE_POLICY_VIOLATION: Final[int] = 1008

#: Runaway guard, not a UX timeout: the reader closes sessions by hand.
#: A session that somehow outlives its page still stops billing here.
#: Unlike the WebRTC path, the server holds the upstream socket, so a
#: half-open client (closed lid, dropped wifi) would otherwise leave a
#: billed session relaying with nothing to end it.
MAX_SESSION_S: Final[float] = 30 * 60

#: The hub is meant to stay on while they work, so its guard is a working day.
MAX_HUB_SESSION_S: Final[float] = 8 * 3600

#: New connections one session may resume onto before the relay gives up.
MAX_RESUMES: Final[int] = 100

#: The handshake is one round trip; a slow one means a broken session.
_HANDSHAKE_TIMEOUT_S: Final[float] = 30.0

#: Keepalive for the client side: a live voice session carries
#: near-continuous client audio, so silence this long means a half-open
#: client rather than a paused mic. This is an ANY-FRAME timer, not a
#: speech timer: the browser client must keep sending frames or periodic
#: keepalive pings ({"omnigentPing":true} every 20s) for as long as the
#: mic is open or narration is active. The server resets this timer on every
#: client frame (and drops pings before upstream relay), while a client that
#: goes silent with neither audio nor pings for 60s is disconnected with 1008.
#:
#: Frames coming DOWN from Google count as activity too. A narration sends one
#: text turn and then only listens, so a long summary used to trip this guard
#: mid-sentence while Gemini was still speaking; a call with audio flowing in
#: either direction is alive, whatever the microphone is doing.
_CLIENT_IDLE_TIMEOUT_S: Final[float] = 60.0


class _ClientTimedOut(Exception):
    """The client went silent past :data:`_CLIENT_IDLE_TIMEOUT_S`."""


#: Factory for the upstream connection. Async callable taking the socket
#: URL, returning an object with the ``websockets`` client surface used
#: here (``send``, ``recv``, ``close``); overridable for tests.
UpstreamConnect = Callable[[str], Awaitable[Any]]


async def _default_connect(url: str) -> Any:
    """Open the upstream socket with the pinned ``websockets`` client."""
    return await websockets.connect(url)


#: 24 kHz mono PCM16 is what the Live API returns, so one second of speech is
#: this many bytes. Used to compare audio delivered against time spent.
_PLAYBACK_BYTES_PER_S: Final[int] = 24_000 * 2


def _inline_audio_bytes(message: str | bytes | None) -> int:
    """Return the decoded PCM byte count of a server message's audio parts.

    Counts only ``inlineData`` audio, so transcripts and tool frames do not
    inflate the throughput figure.

    :param message: One raw frame as it arrived from the upstream socket.
    :returns: Decoded audio bytes, or 0 when the frame carries none.
    """
    if message is None:
        return 0
    try:
        text = message if isinstance(message, str) else message.decode("utf-8")
        parsed = json.loads(text)
    except (UnicodeDecodeError, ValueError):
        return 0
    if not isinstance(parsed, dict):
        return 0
    content = parsed.get("serverContent")
    if not isinstance(content, dict):
        return 0
    turn = content.get("modelTurn")
    if not isinstance(turn, dict):
        return 0
    total = 0
    for part in turn.get("parts") or []:
        if not isinstance(part, dict):
            continue
        inline = part.get("inlineData")
        if not isinstance(inline, dict):
            continue
        if not str(inline.get("mimeType", "")).startswith("audio/"):
            continue
        data = inline.get("data")
        if isinstance(data, str):
            # base64 -> bytes, without paying to actually decode it.
            total += (len(data) * 3) // 4 - data.count("=")
    return total


#: Lead the browser schedules a reply's first chunk with (geminiLive.ts
#: JITTER_LEAD_S). Mirrored so the server can tell when that queue ran dry.
_CLIENT_LEAD_S: Final[float] = 0.15


class _ReplyMeter:
    """Audio delivery for one reply, replayed against the browser's queue.

    The session total cannot show a stutter: it averages over the time the
    reader spends talking. Per reply it can, and replaying arrivals through the
    same schedule the browser uses says exactly when its queue would have run
    dry -- which is what a freeze is.
    """

    def __init__(self) -> None:
        self.audio_s = 0.0
        self.first_at: float | None = None
        self.last_at: float | None = None
        self.queue_end = 0.0
        self.dry = 0
        self.dry_s = 0.0

    def add(self, now: float, pcm_bytes: int) -> None:
        """Account one audio chunk arriving at monotonic time *now*."""
        if pcm_bytes <= 0:
            return
        seconds = pcm_bytes / _PLAYBACK_BYTES_PER_S
        if self.first_at is None:
            self.first_at = now
            self.queue_end = now + _CLIENT_LEAD_S
        elif now >= self.queue_end:
            # Arrived after everything queued had already played: silence.
            self.dry += 1
            self.dry_s += now - self.queue_end
            self.queue_end = now + _CLIENT_LEAD_S
        self.queue_end += seconds
        self.audio_s += seconds
        self.last_at = now

    def summary(self) -> str | None:
        """Return ``"<audio>s@<ratio>x/<dry>dry<secs>s"``, or ``None`` without audio."""
        if self.first_at is None or self.last_at is None:
            return None
        span = self.last_at - self.first_at
        ratio = f"{self.audio_s / span:.2f}" if span > 0 else "inf"
        return f"{self.audio_s:.1f}s@{ratio}x/{self.dry}dry{self.dry_s:.1f}s"


class _TurnLog:
    """What became of each thing the reader said: when Google heard it, what came back.

    The session counters cannot tell a slow reply from none, nor say whether
    Google's own speech detection ever closed the reader's turn. Per turn this
    records the transcribed span, the first reply (audio or a tool call) and its
    delay, and keeps the last frames that were not audio so an unanswered turn
    can be read raw.
    """

    def __init__(self, start: float) -> None:
        self.start = start
        self.kinds: dict[str, int] = {}
        self.turns: list[_HeardTurn] = []
        self.usage: dict[str, int] = {}
        self.recent: list[str] = []
        self.speech_end: float | None = None
        self.replied_at = -1.0
        #: Turns whose words a send already carried.
        self.sent_upto = 0

    def _open_turn(self) -> _HeardTurn | None:
        if self.turns and self.turns[-1].reply is None:
            return self.turns[-1]
        return None

    def frame(self, now: float, parsed: object, raw: str | bytes) -> None:
        """Account one upstream frame received at monotonic time *now*."""
        if not isinstance(parsed, dict):
            self.kinds["unparsed"] = self.kinds.get("unparsed", 0) + 1
            return
        at = now - self.start
        content = parsed.get("serverContent")
        content = content if isinstance(content, dict) else {}
        for kind in [
            *(k for k in parsed if k != "serverContent"),
            *(f"serverContent.{k}" for k in content),
        ]:
            self.kinds[kind] = self.kinds.get(kind, 0) + 1
        activity = parsed.get("voiceActivity")
        if isinstance(activity, dict) and activity.get("type") == "ACTIVITY_END":
            self.speech_end = at
        usage = parsed.get("usageMetadata")
        if isinstance(usage, dict):
            self.usage = {k: v for k, v in usage.items() if isinstance(v, int)}
        heard = content.get("inputTranscription")
        text = heard.get("text") if isinstance(heard, dict) else None
        if isinstance(text, str) and text.strip():
            turn = self._open_turn()
            if turn is None:
                turn = _HeardTurn(start=at)
                self.turns.append(turn)
            turn.end = at
            turn.chars += len(text)
            turn.text += text
        model_turn = content.get("modelTurn")
        parts = model_turn.get("parts") if isinstance(model_turn, dict) else None
        audio = isinstance(parts, list) and any(
            isinstance(p, dict) and "inlineData" in p for p in parts
        )
        reply = "toolCall" if "toolCall" in parsed else ("audio" if audio else None)
        turn = self._open_turn()
        if reply and turn is not None:
            turn.reply = reply
            turn.after = at - turn.end
            # Only an end of speech since the previous reply belongs to this turn.
            if self.speech_end is not None and self.speech_end > self.replied_at:
                turn.after_speech = at - self.speech_end
            self.replied_at = at
        if not audio:
            body = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
            self.recent.append(f"{at:.1f}s {body[:240]}")
            del self.recent[:-12]

    def heard_since_send(self) -> str:
        """Everything the reader said since the last send, as Google transcribed it."""
        return " ".join(t.text.strip() for t in self.turns[self.sent_upto :] if t.text.strip())

    def mark_sent(self) -> None:
        """The words heard so far went with a send; the next one starts after them."""
        self.sent_upto = len(self.turns)

    def unanswered(self) -> bool:
        """Whether the last thing Google heard got no reply at all."""
        return self._open_turn() is not None

    def summary(self, end: float) -> str:
        """Return one ``heard@<from>-<to>s(<chars>ch)-><reply>+<delay>s`` per turn."""
        parts = []
        for turn in self.turns[:30]:
            span = f"heard@{turn.start:.1f}-{turn.end:.1f}s({turn.chars}ch)"
            if turn.reply is None:
                parts.append(f"{span}->NONE(waited {end - self.start - turn.end:.1f}s)")
            else:
                since_end = (
                    f"(end+{turn.after_speech:.1f}s)" if turn.after_speech is not None else ""
                )
                parts.append(f"{span}->{turn.reply}+{turn.after:.1f}s{since_end}")
        return "[" + ",".join(parts) + "]"


@dataclass
class _HeardTurn:
    """One stretch of the reader's speech as Google transcribed it, and its reply."""

    start: float
    end: float = 0.0
    chars: int = 0
    text: str = ""
    reply: str | None = None
    after: float = 0.0
    #: From Google's own end-of-speech mark to the reply. The transcript can
    #: land with the reply, which makes ``after`` read ~0 on a long wait.
    after_speech: float | None = None


def _resumption_update(message: str | bytes) -> str | None:
    """The new handle in a ``sessionResumptionUpdate``, when it is resumable."""
    try:
        parsed = json.loads(message)
    except ValueError:
        return None
    update = parsed.get("sessionResumptionUpdate") if isinstance(parsed, dict) else None
    if not isinstance(update, dict) or not update.get("resumable"):
        return None
    handle = update.get("newHandle")
    return handle if isinstance(handle, str) and handle else None


def _realtime_factor(audio_bytes: int, first_at: float | None, last_at: float | None) -> str:
    """Return audio delivered per second of delivery, as a printable ratio.

    Below 1.0 the browser drains the queue faster than Google fills it, which
    is a stutter no jitter buffer can hide.

    :param audio_bytes: Decoded PCM bytes received this session.
    :param first_at: Monotonic time of the first audio frame, if any.
    :param last_at: Monotonic time of the last audio frame, if any.
    :returns: The ratio to two decimals, or ``"n/a"`` when it cannot be formed.
    """
    if first_at is None or last_at is None or last_at <= first_at:
        return "n/a"
    return f"{(audio_bytes / _PLAYBACK_BYTES_PER_S) / (last_at - first_at):.2f}"


def create_gemini_live_router(
    *,
    auth_provider: AuthProvider | None = None,
    upstream_connect: UpstreamConnect | None = None,
    registry_provider: Callable[[], DiscussionRegistry] | None = None,
    conversation_store: Any = None,
    max_session_s: float = MAX_SESSION_S,
    max_hub_session_s: float = MAX_HUB_SESSION_S,
    handshake_timeout_s: float = _HANDSHAKE_TIMEOUT_S,
    client_idle_timeout_s: float = _CLIENT_IDLE_TIMEOUT_S,
) -> APIRouter:
    """Build the router carrying the Gemini Live proxy WebSocket.

    Wired into the FastAPI app under the ``/v1`` prefix in
    :func:`omnigent.server.app.create_app`.

    :param auth_provider: Optional provider used to authenticate the
        WebSocket handshake. ``None`` preserves single-user/dev
        behavior (open).
    :param upstream_connect: Async connection factory override for
        tests. Defaults to the real ``websockets`` client.
    :param registry_provider: Companion registry factory override for tests.
        Defaults to the process-wide registry.
    :param conversation_store: Sessions and their stored items, for the live
        status tools and the hub. ``None`` leaves both off.
    :param max_session_s: Whole-session runaway cap, overridable so
        tests can use a fraction of a second.
    :param handshake_timeout_s: Upstream connect timeout, overridable
        for tests.
    :param client_idle_timeout_s: Client keepalive window, overridable
        for tests.
    :returns: An :class:`APIRouter` carrying the proxy route.
    """
    router = APIRouter()
    connect = upstream_connect or _default_connect
    companions = registry_provider or registry

    def _hub(websocket: WebSocket, chat_id: str | None) -> hub_module.Hub:
        """The caller's hub, writing into its conversation, sending as the caller."""
        user_id = auth_provider.get_user_id(websocket) if auth_provider is not None else None
        hub = hub_module.hub_for(user_id)
        hub_chat.attach(hub, conversation_store, chat_id or hub_chat.known_hub_id(user_id))
        # Sends go through the app's own events route, as the page's do, with
        # the caller's credentials: one dispatch path, one set of checks.
        hub_chat.bind_request(
            hub,
            conversation_store=conversation_store,
            user_id=user_id,
            app=websocket.app,
            headers=websocket.headers,
            companions=companions,
        )
        return hub

    async def _live_activity(session_id: str) -> str:
        """What a session's Claude is doing now, from its newest stored items."""
        return await hub_module.live_activity(conversation_store, session_id)

    def _require_user(request: Request) -> None:
        if auth_provider is not None and auth_provider.get_user_id(request) is None:
            raise HTTPException(status_code=401, detail="authentication required")

    @router.get("/live/gemini/availability")
    async def gemini_live_availability(request: Request) -> dict[str, Any]:
        """Report whether Gemini live voice is configured, for UI choice.

        Read-only: returns only the boolean and the model name. The API
        key never appears in the body, headers, or logs.
        """
        _require_user(request)
        return {"configured": gemini_live.available(), "model": gemini_live.MODEL}

    @router.websocket("/live/gemini/ws")
    async def gemini_live_ws(websocket: WebSocket, session_id: str | None = None) -> None:
        """Relay one Gemini Live session, keeping the key server-side."""
        if auth_provider is not None and auth_provider.get_user_id(websocket) is None:
            raise WebSocketException(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="authentication required",
            )
        await websocket.accept()
        session_param = (
            session_id if session_id is not None else websocket.query_params.get("session_id")
        )
        session_id_clean = (session_param or "").strip()
        mode_param = websocket.query_params.get("mode")
        mode = (mode_param or "").strip().lower()
        narrating = mode == "narrate"
        hub = mode == "hub"
        # The voice button inside the hub conversation is the hub's voice.
        hub_conv_id: str | None = None
        if not hub and session_id_clean and conversation_store is not None:
            with contextlib.suppress(Exception):
                conv = await asyncio.to_thread(
                    conversation_store.get_conversation, session_id_clean
                )
                if conv is not None and hub_chat.is_hub(conv):
                    hub, hub_conv_id = True, session_id_clean
        if hub and conversation_store is None:
            with contextlib.suppress(RuntimeError):
                await websocket.close(
                    code=status.WS_1008_POLICY_VIOLATION, reason="voice hub unavailable"
                )
            return

        system_instruction: str | None = None
        briefed = False
        the_hub: hub_module.Hub | None = None
        if hub:
            the_hub = _hub(websocket, hub_conv_id)
            try:
                hub_sessions = await the_hub.sessions()
            except Exception:  # noqa: BLE001 - a hub with no list still routes
                _logger.warning("voice hub briefing failed", exc_info=True)
                hub_sessions = []
            system_instruction = voice_hub.hub_briefing(hub_sessions, recent=the_hub.state.entries)
            briefed = True
            _logger.info(
                "voice hub briefed | sessions=%s",
                [(s.handle, s.name) for s in hub_sessions],
            )
        elif not narrating:
            if not session_id_clean:
                _logger.info("gemini live briefing skipped | reason=missing-session")
            else:
                try:
                    companion = companions().peek(session_id_clean)
                    if companion is None or not getattr(companion, "context", None):
                        _logger.info("gemini live briefing skipped | reason=no-context")
                    else:
                        system_instruction = voice_briefing(
                            companion, can_check=True, can_watch=conversation_store is not None
                        )
                        briefed = bool(system_instruction and system_instruction.strip())
                        if not briefed:
                            _logger.info("gemini live briefing skipped | reason=no-context")
                except Exception:  # noqa: BLE001 - briefing errors skip briefing, never block session
                    _logger.info("gemini live briefing skipped | reason=error")
        try:
            key = gemini_live.api_key()
        except gemini_live.GeminiLiveUnavailable as exc:
            _logger.warning("gemini live refused: %s", exc)
            with contextlib.suppress(RuntimeError):
                await websocket.close(
                    code=_WS_CLOSE_INTERNAL_ERROR,
                    reason="gemini live unavailable",
                )
            return
        try:
            upstream: Any = await asyncio.wait_for(
                connect(gemini_live.socket_url(key)),
                timeout=handshake_timeout_s,
            )
        except asyncio.TimeoutError:
            _logger.warning(
                "gemini live upstream connect timed out after %ss", handshake_timeout_s
            )
            with contextlib.suppress(RuntimeError):
                await websocket.close(
                    code=_WS_CLOSE_INTERNAL_ERROR,
                    reason="gemini live upstream connect timed out",
                )
            return
        except Exception as exc:  # noqa: BLE001 - any refusal must close, not hang
            _logger.warning("gemini live upstream refused: %s", exc)
            with contextlib.suppress(RuntimeError):
                await websocket.close(
                    code=_WS_CLOSE_INTERNAL_ERROR,
                    reason="gemini live upstream refused",
                )
            return
        session_start = time.monotonic()
        # Last traffic in EITHER direction; the keepalive window runs from here.
        last_activity = session_start
        client_frames = 0
        upstream_frames = 0
        tool_calls = 0
        handed_off = False
        ended_by = "client"
        upstream_close = "none"
        close_reason: str | None = None

        setup_complete_count = 0
        input_transcription_count = 0
        output_transcription_count = 0
        interrupted_count = 0
        turn_complete_count = 0
        generation_complete_count = 0
        tool_call_count = 0
        go_away_count = 0
        tool_response_count = 0
        audio_stream_end_count = 0
        ping_count = 0

        setup_ms: float | None = None
        first_audio_ms: float | None = None
        # Audio actually delivered, against the wall clock spent delivering it.
        # A reply stutters when this drops below 1.0: the browser plays faster
        # than Google sends, and no jitter buffer can cover a real shortfall.
        audio_pcm_bytes = 0
        first_audio_at: float | None = None
        last_audio_at: float | None = None
        timeline: list[str] = []
        tool_latencies: list[str] = []
        reply = _ReplyMeter()
        replies: list[str] = []
        turn_log = _TurnLog(session_start)
        pending_tool_calls: list[tuple[float, asyncio.Task[None]]] = []
        # Tools the relay runs itself (the hub's, live status), still running.
        server_tasks: set[asyncio.Task[None]] = set()

        def _contains(msg: str | bytes | None, target: str) -> bool:
            if msg is None:
                return False
            if isinstance(msg, str):
                return target in msg
            return target.encode("utf-8") in msg

        def record_timeline_event(name: str) -> None:
            if len(timeline) < 20:
                elapsed = time.monotonic() - session_start
                timeline.append(f"{name}@{elapsed:.1f}s")

        async def _tool_call_timeout_watcher() -> None:
            try:
                await asyncio.sleep(15.0)
                _logger.warning("gemini live toolCall received no toolResponse within 15s")
            except asyncio.CancelledError:
                pass

        def on_tool_call_received() -> None:
            nonlocal tool_call_count
            tool_call_count += 1
            record_timeline_event("toolCall")
            watcher = asyncio.create_task(_tool_call_timeout_watcher())
            pending_tool_calls.append((time.monotonic(), watcher))

        def on_tool_response_received() -> None:
            nonlocal tool_response_count
            tool_response_count += 1
            record_timeline_event("toolResponse")
            if pending_tool_calls:
                call_start_t, watcher = pending_tool_calls.pop(0)
                watcher.cancel()
                lat_ms = round((time.monotonic() - call_start_t) * 1000)
                tool_latencies.append(f"{lat_ms}ms")

        def on_go_away_received() -> None:
            nonlocal go_away_count
            go_away_count += 1
            record_timeline_event("goAway")
            _logger.warning("gemini live received goAway from upstream")

        def record_upstream_close(exc: websockets.ConnectionClosed) -> None:
            nonlocal upstream_close
            if upstream_close != "none":
                return
            rcvd = getattr(exc, "rcvd", None)
            sent = getattr(exc, "sent", None)
            if rcvd is not None:
                code = getattr(rcvd, "code", None)
                reason = getattr(rcvd, "reason", "")
                upstream_close = f"{code}/{reason}" if reason else str(code)
            elif sent is not None:
                code = getattr(sent, "code", None)
                reason = getattr(sent, "reason", "")
                upstream_close = f"sent {code}/{reason}" if reason else f"sent {code}"
            else:
                code = getattr(exc, "code", None)
                reason = getattr(exc, "reason", "")
                if code is not None:
                    upstream_close = f"{code}/{reason}" if reason else str(code)

        # The voice speaks the chat's language selector. Narration names the
        # chat it reads for (`language_of`), and the summary's own language
        # (`lang`) stands in when that chat has none set.
        if narrating:
            language = await selected_language(
                (websocket.query_params.get("language_of") or "").strip(), conversation_store
            ) or concrete_language(websocket.query_params.get("lang"))
        elif the_hub is not None:
            language = concrete_language(await the_hub.language())
        else:
            language = await selected_language(session_id_clean, conversation_store)
        try:
            # The proven setup frame must precede anything else upstream.
            setup_kwargs: dict[str, Any]
            if narrating:
                setup_kwargs = {"mode": "narrate"}
            elif hub:
                setup_kwargs = {
                    "system_instruction": system_instruction,
                    "tools": voice_hub.HUB_TOOLS,
                }
            else:
                watch = (
                    [voice_hub.CLAUDE_STATUS_TOOL]
                    if conversation_store is not None and session_id_clean
                    else None
                )
                setup_kwargs = {"system_instruction": system_instruction, "extra_tools": watch}
            setup_kwargs["language"] = language
            _logger.info(
                "gemini live language | mode=%s language=%s",
                mode or "converse",
                language or "free",
            )
            frame = gemini_live.setup_frame(**setup_kwargs)
            await upstream.send(json.dumps(frame))
            if not narrating:
                # The page's stall watchdog needs to know whether silence during
                # a tool call is the model waiting or the model wedged.
                model_name = frame["setup"]["model"]
                await websocket.send_text(
                    json.dumps(
                        {
                            "omnigentSession": {
                                "model": model_name.removeprefix("models/"),
                                "toolsBlock": gemini_live.tools_block(model_name),
                                "hub": hub,
                            }
                        }
                    )
                )

            session_cap = asyncio.create_task(
                asyncio.sleep(max_hub_session_s if hub else max_session_s)
            )
            resume_handle: str | None = None
            resumes = 0
            resuming: asyncio.Task[bool] | None = None

            async def resume_upstream() -> bool:
                """Carry the session onto a new connection with the latest handle."""
                nonlocal upstream, resumes
                if narrating or not resume_handle or resumes >= MAX_RESUMES:
                    return False
                fresh: Any = None
                try:
                    fresh = await asyncio.wait_for(
                        connect(gemini_live.socket_url(key)), timeout=handshake_timeout_s
                    )
                    await fresh.send(
                        json.dumps(
                            gemini_live.setup_frame(**setup_kwargs, resume_handle=resume_handle)
                        )
                    )
                    first = await asyncio.wait_for(fresh.recv(), timeout=handshake_timeout_s)
                    if not _contains(first, '"setupComplete"'):
                        raise RuntimeError(f"no setupComplete: {str(first)[:200]}")
                except Exception as exc:  # noqa: BLE001 - a failed resume ends the call
                    _logger.warning("gemini live resume failed: %r", exc)
                    if fresh is not None:
                        with contextlib.suppress(Exception):
                            await fresh.close()
                    return False
                old, upstream = upstream, fresh
                resumes += 1
                record_timeline_event("resumed")
                _logger.info("gemini live resumed onto a new connection | resumes=%d", resumes)
                with contextlib.suppress(Exception):
                    await old.close()
                return True

            def resume_soon() -> None:
                """Start one resume, unless one is already under way."""
                nonlocal resuming
                if resuming is None or resuming.done():
                    resuming = asyncio.create_task(resume_upstream())
                    server_tasks.add(resuming)

            async def send_up(payload: str | bytes) -> None:
                """Send upstream, following the session onto a resumed connection."""
                target = upstream
                try:
                    await target.send(payload)
                except websockets.ConnectionClosed:
                    if resuming is not None:
                        await resuming
                    if upstream is target and not await resume_upstream():
                        raise
                    await upstream.send(payload)

            def _server_answers(call: dict[str, Any]) -> bool:
                """Whether the relay runs this tool itself instead of the page."""
                if hub:
                    return True
                return (
                    call.get("name") in voice_hub.SESSION_SERVER_TOOLS
                    and conversation_store is not None
                    and bool(session_id_clean)
                )

            async def _answer_server_tool(call: dict[str, Any]) -> None:
                """Run a server tool, answer Google, and tell the page it is done."""
                name = str(call.get("name", ""))
                args = call.get("args") if isinstance(call.get("args"), dict) else {}
                started = time.monotonic()
                project = ""
                try:
                    if hub and the_hub is not None:
                        project, output = await voice_hub.run_hub_tool(
                            the_hub, name, args, heard=turn_log.heard_since_send()
                        )
                        if name == "ask_claude" and output.startswith(voice_hub.SENT_PREFIX):
                            turn_log.mark_sent()
                    else:
                        output = await _live_activity(session_id_clean)
                except Exception as exc:  # noqa: BLE001 - the model must get an answer
                    _logger.warning("voice server tool %s failed", name, exc_info=True)
                    output = f"That failed ({type(exc).__name__}); nothing was done."
                response: dict[str, Any] = {"output": output}
                if not gemini_live.tools_block(frame["setup"]["model"]):
                    response["scheduling"] = "WHEN_IDLE"
                reply = {"id": call.get("id"), "name": name, "response": response}
                with contextlib.suppress(Exception):
                    await send_up(json.dumps({"toolResponse": {"functionResponses": [reply]}}))
                    on_tool_response_received()
                with contextlib.suppress(Exception):
                    await websocket.send_text(
                        json.dumps(
                            {
                                "omnigentToolDone": {
                                    "id": call.get("id"),
                                    "name": name,
                                    "project": project,
                                    "output": output[:600],
                                    # The page ends the call once the voice has said so.
                                    "sent": name == "ask_claude"
                                    and output.startswith(voice_hub.SENT_PREFIX),
                                }
                            }
                        )
                    )
                _logger.info(
                    "voice server tool | %s project=%r args=%r took=%.1fs output=%r",
                    name,
                    project,
                    {k: str(v)[:200] for k, v in args.items()},
                    time.monotonic() - started,
                    output[:300],
                )

            async def client_to_upstream() -> None:
                nonlocal client_frames, handed_off, ping_count, audio_stream_end_count
                nonlocal last_activity
                while True:
                    receive = asyncio.ensure_future(websocket.receive())
                    while True:
                        remaining = client_idle_timeout_s - (time.monotonic() - last_activity)
                        if remaining <= 0:
                            receive.cancel()
                            raise _ClientTimedOut
                        idle_cap = asyncio.ensure_future(asyncio.sleep(remaining))
                        done, _ = await asyncio.wait(
                            {receive, idle_cap}, return_when=asyncio.FIRST_COMPLETED
                        )
                        idle_cap.cancel()
                        if receive in done:
                            break
                        # The window elapsed, but upstream frames may have moved
                        # it; re-check rather than hang up on a talking model.
                    message = receive.result()
                    last_activity = time.monotonic()
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("text") is not None:
                        text_payload = message["text"]
                        if '"omnigentPing"' in text_payload:
                            ping_count += 1
                            continue
                        if '"omnigentStats"' in text_payload:
                            # What the page's speakers got; never Google's business.
                            _logger.info("gemini live page playback | %s", text_payload[:500])
                            continue
                        if '"toolResponse"' in text_payload:
                            on_tool_response_received()
                            try:
                                parsed_cl = json.loads(text_payload)
                                if isinstance(parsed_cl, dict):
                                    tr = parsed_cl.get("toolResponse")
                                    if isinstance(tr, dict):
                                        frs = tr.get("functionResponses")
                                        if isinstance(frs, list):
                                            for fr in frs:
                                                if isinstance(fr, dict):
                                                    resp_obj = fr.get("response")
                                                    if isinstance(resp_obj, dict):
                                                        out = str(resp_obj.get("output", ""))
                                                        if "I've sent that to Claude" in out:
                                                            handed_off = True
                            except Exception:  # noqa: BLE001
                                pass
                        if '"audioStreamEnd"' in text_payload:
                            audio_stream_end_count += 1
                        await send_up(text_payload)
                        client_frames += 1
                    else:
                        raw_bytes = message.get("bytes") or b""
                        if b'"omnigentPing"' in raw_bytes:
                            ping_count += 1
                            continue
                        if b'"toolResponse"' in raw_bytes:
                            on_tool_response_received()
                        if b'"audioStreamEnd"' in raw_bytes:
                            audio_stream_end_count += 1
                        await send_up(raw_bytes)
                        client_frames += 1

            async def upstream_to_client() -> None:
                nonlocal upstream_frames, tool_calls, last_activity
                nonlocal setup_complete_count, setup_ms, input_transcription_count
                nonlocal output_transcription_count, interrupted_count
                nonlocal turn_complete_count, generation_complete_count, first_audio_ms
                nonlocal audio_pcm_bytes, first_audio_at, last_audio_at, reply, resume_handle
                while True:
                    reading = upstream
                    try:
                        message = await reading.recv()
                    except asyncio.CancelledError:
                        raise
                    except websockets.ConnectionClosed as exc:
                        if resuming is not None and not resuming.done():
                            await resuming
                        # Swapped for a resumed connection, or resumable now.
                        if upstream is not reading or await resume_upstream():
                            continue
                        record_upstream_close(exc)
                        return
                    # Resumption bookkeeping is the relay's, not the page's: a
                    # frame the page counted as Gemini talking would hide a stall.
                    if _contains(message, '"sessionResumptionUpdate"'):
                        update = _resumption_update(message)
                        if update:
                            resume_handle = update
                        continue
                    try:
                        turn_log.frame(time.monotonic(), json.loads(message), message)
                    except ValueError:
                        turn_log.frame(time.monotonic(), None, message)
                    if _contains(message, '"setupComplete"'):
                        setup_complete_count += 1
                        if setup_ms is None:
                            setup_ms = (time.monotonic() - session_start) * 1000
                        record_timeline_event("setupComplete")
                    if _contains(message, '"inputTranscription"'):
                        input_transcription_count += 1
                    if _contains(message, '"outputTranscription"'):
                        output_transcription_count += 1
                    if _contains(message, '"interrupted"'):
                        interrupted_count += 1
                        record_timeline_event("interrupted")
                    if _contains(message, '"turnComplete"'):
                        turn_complete_count += 1
                        record_timeline_event("turnComplete")
                    if _contains(message, '"generationComplete"'):
                        generation_complete_count += 1
                    if _contains(message, '"toolCall"'):
                        on_tool_call_received()
                        try:
                            parsed_up = json.loads(
                                message if isinstance(message, str) else message.decode("utf-8")
                            )
                            if isinstance(parsed_up, dict):
                                tc = parsed_up.get("toolCall")
                                if isinstance(tc, dict):
                                    fcs = tc.get("functionCalls")
                                    if isinstance(fcs, list):
                                        tool_calls += len(fcs)
                                        for fc in fcs:
                                            if isinstance(fc, dict) and _server_answers(fc):
                                                server_tasks.add(
                                                    asyncio.create_task(_answer_server_tool(fc))
                                                )
                                    else:
                                        tool_calls += 1
                        except Exception:  # noqa: BLE001
                            pass
                    if _contains(message, '"goAway"'):
                        on_go_away_received()
                        # Move before Google closes this connection.
                        resume_soon()
                        continue
                    if _contains(message, '"inlineData"') or _contains(message, '"audio/pcm"'):
                        now_mono = time.monotonic()
                        if first_audio_ms is None:
                            first_audio_ms = (now_mono - session_start) * 1000
                            first_audio_at = now_mono
                        last_audio_at = now_mono
                        chunk_bytes = _inline_audio_bytes(message)
                        audio_pcm_bytes += chunk_bytes
                        reply.add(now_mono, chunk_bytes)
                    # After the audio: a frame can carry a reply's last chunk
                    # and its turnComplete together.
                    if _contains(message, '"interrupted"') or _contains(message, '"turnComplete"'):
                        done = reply.summary()
                        if done is not None and len(replies) < 30:
                            replies.append(done)
                        reply = _ReplyMeter()

                    if isinstance(message, str):
                        await websocket.send_text(message)
                    else:
                        await websocket.send_bytes(message)
                    upstream_frames += 1
                    last_activity = time.monotonic()

            async def hub_updates(the: hub_module.Hub) -> None:
                """Tell the call when a session the hub watches finishes or needs them."""
                seen = the.state.last_id
                while True:
                    for entry in await the.state.wait_after(seen, 30.0):
                        seen = max(seen, entry.id)
                        if entry.kind not in hub_module.SPOKEN_KINDS:
                            continue
                        note = (
                            f"[Update, not something they said: {entry.text}] "
                            "Tell them this in one short sentence now, then carry on."
                        )
                        await send_up(
                            json.dumps(
                                {
                                    "clientContent": {
                                        "turns": [{"role": "user", "parts": [{"text": note}]}],
                                        "turnComplete": True,
                                    }
                                }
                            )
                        )
                        _logger.info("voice hub update sent | %s", entry.text[:200])

            client_task = asyncio.create_task(client_to_upstream())
            upstream_task = asyncio.create_task(upstream_to_client())
            if hub and the_hub is not None:
                server_tasks.add(asyncio.create_task(hub_updates(the_hub)))

            done, pending = await asyncio.wait(
                {client_task, upstream_task, session_cap},
                timeout=None,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if session_cap in done and not (done - {session_cap}):
                close_reason = "gemini live session cap reached"
                ended_by = "cap"
                _logger.warning("gemini live session hit the %ss runaway cap", max_session_s)
            for task in pending:
                task.cancel()
            # The page hanging up first is the ordinary end. A relay frame sent to
            # it in the same instant then fails, and that is not an error.
            hung_up = client_task in done and client_task.exception() is None
            for task in sorted(done, key=lambda t: t is not client_task):
                if task is session_cap:
                    continue
                try:
                    task.result()
                    if task is client_task:
                        ended_by = "client"
                    elif task is upstream_task:
                        ended_by = "upstream"
                except WebSocketDisconnect:
                    ended_by = "client"
                except RuntimeError as exc:
                    if not hung_up:
                        ended_by = "error"
                        _logger.warning("gemini live relay error: %r", exc)
                except _ClientTimedOut:
                    close_reason = "gemini live client keepalive timed out"
                    ended_by = "idle"
                    _logger.warning(
                        "gemini live client went silent past %ss", client_idle_timeout_s
                    )
                except websockets.ConnectionClosed as exc:
                    record_upstream_close(exc)
                    ended_by = "upstream"
                except Exception as exc:  # noqa: BLE001 - relay errors end the session
                    ended_by = "error"
                    _logger.warning("gemini live relay error: %r", exc)
        except websockets.ConnectionClosed as exc:
            record_upstream_close(exc)
            ended_by = "upstream"
        except Exception as exc:  # noqa: BLE001
            ended_by = "error"
            _logger.warning("gemini live relay error: %r", exc)
        finally:
            for _, watcher in pending_tool_calls:
                watcher.cancel()
            for task in server_tasks:
                task.cancel()
            with contextlib.suppress(Exception):
                await upstream.close()
            duration_s = max(0.0, time.monotonic() - session_start)
            _logger.info(
                "gemini live session ended | client_frames=%d upstream_frames=%d "
                "duration_s=%.1f ended_by=%s upstream_close=%s briefed=%s "
                "tool_calls=%d handed_off=%s setup_ms=%s first_audio_ms=%s "
                "audio_s=%.1f realtime_x=%s "
                "events={setupComplete:%d,inputTranscription:%d,outputTranscription:%d,"
                "interrupted:%d,turnComplete:%d,generationComplete:%d,toolCall:%d,"
                "goAway:%d,toolResponse:%d,audioStreamEnd:%d,pings:%d} "
                "timeline=%s tool_latencies=%s replies=%s turns=%s kinds=%s usage=%s",
                client_frames,
                upstream_frames,
                duration_s,
                ended_by,
                upstream_close,
                "yes" if briefed else "no",
                tool_calls,
                "yes" if handed_off else "no",
                f"{setup_ms:.0f}" if setup_ms is not None else "none",
                f"{first_audio_ms:.0f}" if first_audio_ms is not None else "none",
                audio_pcm_bytes / _PLAYBACK_BYTES_PER_S,
                _realtime_factor(audio_pcm_bytes, first_audio_at, last_audio_at),
                setup_complete_count,
                input_transcription_count,
                output_transcription_count,
                interrupted_count,
                turn_complete_count,
                generation_complete_count,
                tool_call_count,
                go_away_count,
                tool_response_count,
                audio_stream_end_count,
                ping_count,
                "[" + ",".join(timeline) + "]",
                "[" + ",".join(tool_latencies) + "]",
                # Per reply: audio, delivery rate, and how often and how long
                # the browser's queue ran dry -- i.e. the freezes it heard.
                "[" + ",".join([*replies, *filter(None, [reply.summary()])]) + "]",
                turn_log.summary(time.monotonic()),
                json.dumps(turn_log.kinds, separators=(",", ":")),
                json.dumps(turn_log.usage, separators=(",", ":")),
            )
            if turn_log.unanswered():
                # Heard and never answered: the raw non-audio frames are the evidence.
                _logger.warning(
                    "gemini live unanswered turn | last upstream frames: %s",
                    " || ".join(turn_log.recent),
                )
        if close_reason is None:
            with contextlib.suppress(RuntimeError):
                await websocket.close()
        else:
            with contextlib.suppress(RuntimeError):
                await websocket.close(code=_WS_CLOSE_POLICY_VIOLATION, reason=close_reason)

    return router
