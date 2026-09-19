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
from typing import Any, Final

import websockets
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketException
from starlette import status

from omnigent.server import gemini_live
from omnigent.server.auth import AuthProvider
from omnigent.server.discussion import DiscussionRegistry, registry, voice_briefing

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


def create_gemini_live_router(
    *,
    auth_provider: AuthProvider | None = None,
    upstream_connect: UpstreamConnect | None = None,
    registry_provider: Callable[[], DiscussionRegistry] | None = None,
    max_session_s: float = MAX_SESSION_S,
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
    async def gemini_live_ws(
        websocket: WebSocket, session_id: str | None = None
    ) -> None:
        """Relay one Gemini Live session, keeping the key server-side."""
        if auth_provider is not None and auth_provider.get_user_id(websocket) is None:
            raise WebSocketException(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="authentication required",
            )
        await websocket.accept()
        session_param = (
            session_id
            if session_id is not None
            else websocket.query_params.get("session_id")
        )
        session_id_clean = (session_param or "").strip()
        mode_param = websocket.query_params.get("mode")
        mode = (mode_param or "").strip().lower()
        narrating = mode == "narrate"

        system_instruction: str | None = None
        briefed = False
        if not narrating:
            if not session_id_clean:
                _logger.info("gemini live briefing skipped | reason=missing-session")
            else:
                try:
                    companion = companions().peek(session_id_clean)
                    if companion is None or not getattr(companion, "context", None):
                        _logger.info("gemini live briefing skipped | reason=no-context")
                    else:
                        system_instruction = voice_briefing(companion)
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
        timeline: list[str] = []
        tool_latencies: list[str] = []
        pending_tool_calls: list[tuple[float, asyncio.Task[None]]] = []

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
                _logger.warning(
                    "gemini live toolCall received no toolResponse within 15s"
                )
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

        try:
            # The proven setup frame must precede anything else upstream.
            frame = (
                gemini_live.setup_frame(mode="narrate")
                if narrating
                else gemini_live.setup_frame(system_instruction=system_instruction)
            )
            await upstream.send(json.dumps(frame))

            session_cap = asyncio.create_task(asyncio.sleep(max_session_s))

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
                        await upstream.send(text_payload)
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
                        await upstream.send(raw_bytes)
                        client_frames += 1

            async def upstream_to_client() -> None:
                nonlocal upstream_frames, tool_calls, last_activity
                nonlocal setup_complete_count, setup_ms, input_transcription_count
                nonlocal output_transcription_count, interrupted_count
                nonlocal turn_complete_count, generation_complete_count, first_audio_ms
                while True:
                    try:
                        message = await upstream.recv()
                    except asyncio.CancelledError:
                        raise
                    except websockets.ConnectionClosed as exc:
                        record_upstream_close(exc)
                        return
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
                                    else:
                                        tool_calls += 1
                        except Exception:  # noqa: BLE001
                            pass
                    if _contains(message, '"goAway"'):
                        on_go_away_received()
                    if _contains(message, '"inlineData"') or _contains(message, '"audio/pcm"'):
                        if first_audio_ms is None:
                            first_audio_ms = (time.monotonic() - session_start) * 1000

                    if isinstance(message, str):
                        await websocket.send_text(message)
                    else:
                        await websocket.send_bytes(message)
                    upstream_frames += 1
                    last_activity = time.monotonic()

            client_task = asyncio.create_task(client_to_upstream())
            upstream_task = asyncio.create_task(upstream_to_client())

            done, pending = await asyncio.wait(
                {client_task, upstream_task, session_cap},
                timeout=None,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if session_cap in done and not (done - {session_cap}):
                close_reason = "gemini live session cap reached"
                ended_by = "cap"
                _logger.warning(
                    "gemini live session hit the %ss runaway cap", max_session_s
                )
            for task in pending:
                task.cancel()
            for task in done:
                if task is session_cap:
                    continue
                try:
                    task.result()
                    if task is client_task:
                        ended_by = "client"
                    elif task is upstream_task:
                        ended_by = "upstream"
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
                    _logger.warning("gemini live relay error: %s", exc)
        except websockets.ConnectionClosed as exc:
            record_upstream_close(exc)
            ended_by = "upstream"
        except Exception as exc:  # noqa: BLE001
            ended_by = "error"
            _logger.warning("gemini live relay error: %s", exc)
        finally:
            for _, watcher in pending_tool_calls:
                watcher.cancel()
            with contextlib.suppress(Exception):
                await upstream.close()
            duration_s = max(0.0, time.monotonic() - session_start)
            _logger.info(
                "gemini live session ended | client_frames=%d upstream_frames=%d "
                "duration_s=%.1f ended_by=%s upstream_close=%s briefed=%s "
                "tool_calls=%d handed_off=%s setup_ms=%s first_audio_ms=%s "
                "events={setupComplete:%d,inputTranscription:%d,outputTranscription:%d,"
                "interrupted:%d,turnComplete:%d,generationComplete:%d,toolCall:%d,"
                "goAway:%d,toolResponse:%d,audioStreamEnd:%d,pings:%d} "
                "timeline=%s tool_latencies=%s",
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
            )
        if close_reason is None:
            with contextlib.suppress(RuntimeError):
                await websocket.close()
        else:
            with contextlib.suppress(RuntimeError):
                await websocket.close(
                    code=_WS_CLOSE_POLICY_VIOLATION, reason=close_reason
                )

    return router
