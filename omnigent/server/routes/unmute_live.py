"""Omnigent's live voice on a local Kyutai Unmute stack: relay and brain.

Two ends of one call (see :mod:`omnigent.server.unmute_live`):

* ``WS /v1/live/unmute/ws`` -- the page's side. It speaks Gemini Live's wire
  format (audio, transcripts, ``toolCall``/``toolResponse``, ``turnComplete``),
  so ``geminiLive.ts`` and ``geminiNarrator.ts`` drive Unmute unchanged; the
  relay translates to and from Unmute's realtime protocol.
* ``/v1/live/unmute/brain/v1/*`` -- the model address Unmute is configured
  with. Omnigent calls get the live voice briefing and the ``ask_claude``
  tool; anything else (Unmute's own page) passes through to the model as is.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Final

import httpx
import websockets
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketException
from starlette import status
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse, Response, StreamingResponse
from websockets.typing import Subprotocol

from omnigent import openai_token_budget as budget
from omnigent.server import unmute_live
from omnigent.server.auth import AuthProvider
from omnigent.server.discussion import DiscussionRegistry, registry, voice_briefing
from omnigent.server.routes.gemini_live import _realtime_factor, _ReplyMeter
from omnigent.server.routes.openai_budget_proxy import ensure_proxy_token

_logger = logging.getLogger(__name__)

#: Runaway guard, as for Gemini: the reader ends calls by hand.
MAX_SESSION_S: Final[float] = 30 * 60
_HANDSHAKE_TIMEOUT_S: Final[float] = 15.0
#: Any-frame keepalive, as for Gemini: the page pings every 20s.
_CLIENT_IDLE_TIMEOUT_S: Final[float] = 60.0

_PLAYBACK_BYTES_PER_S: Final[int] = 24_000 * 2
#: 80 ms of 24 kHz PCM16 silence: what a narration feeds Unmute in place of a mic.
_SILENCE_FRAME_S: Final[float] = 0.08
_SILENCE_B64: Final[str] = base64.b64encode(bytes(2 * 1920)).decode("ascii")

_REQUEST_DROP_HEADERS: Final[frozenset[str]] = frozenset(
    {"host", "authorization", "content-length", "connection", "accept-encoding"}
)
_RESPONSE_DROP_HEADERS: Final[frozenset[str]] = frozenset(
    {"content-length", "content-encoding", "transfer-encoding", "connection"}
)

#: Opens the upstream socket: ``(url, subprotocols) -> connection``.
UpstreamConnect = Callable[[str, list[str]], Awaitable[Any]]


class _ClientTimedOut(Exception):
    """The page went silent past the keepalive window."""


async def _default_connect(url: str, subprotocols: list[str]) -> Any:
    offered = [Subprotocol(p) for p in subprotocols]
    return await websockets.connect(url, subprotocols=offered, max_size=None)


def translate_upstream(event: dict[str, Any]) -> dict[str, Any] | None:
    """Turn one Unmute server event into the Gemini Live frame the page expects.

    :returns: The frame, or ``None`` for events the page has no use for.
    """
    kind = event.get("type")
    if kind == "session.updated":
        # Unmute answers the session update only once speech-to-text is up.
        return {"setupComplete": {}}
    if kind == "response.audio.delta":
        audio = {"mimeType": "audio/pcm;rate=24000", "data": event.get("delta", "")}
        return {"serverContent": {"modelTurn": {"parts": [{"inlineData": audio}]}}}
    if kind in ("conversation.item.input_audio_transcription.delta", "response.text.delta"):
        word = str(event.get("delta") or "").strip()
        if not word:
            return None
        # Unmute sends bare words; the page concatenates transcript chunks.
        key = "inputTranscription" if kind.startswith("conversation") else "outputTranscription"
        return {"serverContent": {key: {"text": f" {word}"}}}
    if kind == "unmute.interrupted_by_vad":
        return {"serverContent": {"interrupted": True}}
    if kind == "response.audio.done":
        return {"serverContent": {"turnComplete": True}}
    return None


def _audio_bytes(b64: str) -> int:
    return (len(b64) * 3) // 4 - b64.count("=")


def create_unmute_live_router(
    *,
    auth_provider: AuthProvider | None = None,
    upstream_connect: UpstreamConnect | None = None,
    registry_provider: Callable[[], DiscussionRegistry] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    max_session_s: float = MAX_SESSION_S,
    client_idle_timeout_s: float = _CLIENT_IDLE_TIMEOUT_S,
) -> APIRouter:
    """Build the router for Omnigent calls on Unmute, mounted under ``/v1``.

    :param auth_provider: Session auth for the page's routes; the brain
        checks the OpenAI budget token instead, as Unmute has no session.
    :param upstream_connect: Unmute socket factory override, for tests.
    :param registry_provider: Companion registry override, for tests.
    :param transport: HTTP transport override for the brain's model calls
        and the health check, for tests.
    :param max_session_s: Whole-call runaway cap.
    :param client_idle_timeout_s: Page keepalive window.
    :returns: The router.
    """
    router = APIRouter()
    connect = upstream_connect or _default_connect
    companions = registry_provider or registry

    def _require_user(request: Request) -> None:
        if auth_provider is not None and auth_provider.get_user_id(request) is None:
            raise HTTPException(status_code=401, detail="authentication required")

    @router.get("/live/unmute/availability")
    async def unmute_live_availability(request: Request) -> dict[str, Any]:
        """Report whether the Unmute stack is up, for the engine picker."""
        _require_user(request)
        url = f"{unmute_live.unmute_base_url()}/unmute/api/v1/health"
        try:
            async with httpx.AsyncClient(timeout=5.0, transport=transport) as client:
                health = (await client.get(url)).json()
            up = all(health.get(k) for k in ("tts_up", "stt_up", "llm_up"))
        except (httpx.HTTPError, ValueError, AttributeError):
            up = False
        return {"configured": up, "voice": unmute_live.voice()}

    @router.websocket("/live/unmute/ws")
    async def unmute_live_ws(websocket: WebSocket) -> None:
        """Relay one call between the page (Gemini frames) and Unmute."""
        if auth_provider is not None and auth_provider.get_user_id(websocket) is None:
            raise WebSocketException(
                code=status.WS_1008_POLICY_VIOLATION, reason="authentication required"
            )
        await websocket.accept()
        session_id = (websocket.query_params.get("session_id") or "").strip()
        narrating = (websocket.query_params.get("mode") or "").strip().lower() == "narrate"

        instructions: str | None = None
        briefed = False
        if not narrating:
            companion = None
            with contextlib.suppress(Exception):
                companion = companions().peek(session_id) if session_id else None
            briefed = bool(companion is not None and getattr(companion, "context", None))
            instructions = f"{voice_briefing(companion)}\n\n{unmute_live.SPOKEN_RULES}"
        call = unmute_live.open_call(session_id, instructions)

        base = unmute_live.unmute_base_url()
        ws_base = base.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
        try:
            upstream: Any = await asyncio.wait_for(
                connect(f"{ws_base}/unmute/api/v1/realtime", ["realtime"]),
                timeout=_HANDSHAKE_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 - any refusal must close, not hang
            unmute_live.close_call(call)
            _logger.warning("unmute live upstream refused: %s", exc)
            with contextlib.suppress(RuntimeError):
                await websocket.close(
                    code=status.WS_1011_INTERNAL_ERROR,
                    reason=f"Unmute is not running (expected on {base})",
                )
            return

        started = time.monotonic()
        last_activity = started
        narration_given = asyncio.Event()
        counts = {"audio_in": 0, "pings": 0, "interrupted": 0, "turns": 0, "tool_responses": 0}
        handed_off = False
        ended_by = "client"
        close_code = status.WS_1000_NORMAL_CLOSURE
        close_reason = ""
        upstream_error: str | None = None
        setup_ms: float | None = None
        first_audio_ms: float | None = None
        audio_bytes = 0
        first_audio_at: float | None = None
        last_audio_at: float | None = None
        reply = _ReplyMeter()
        replies: list[str] = []

        async def client_to_upstream() -> None:
            nonlocal last_activity, handed_off
            while True:
                receive = asyncio.ensure_future(websocket.receive())
                while True:
                    remaining = client_idle_timeout_s - (time.monotonic() - last_activity)
                    if remaining <= 0:
                        receive.cancel()
                        raise _ClientTimedOut
                    done, _ = await asyncio.wait({receive}, timeout=remaining)
                    if receive in done:
                        break
                message = receive.result()
                last_activity = time.monotonic()
                if message["type"] == "websocket.disconnect":
                    return
                raw = message.get("text")
                if raw is None:
                    raw = (message.get("bytes") or b"").decode("utf-8", "replace")
                try:
                    frame = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(frame, dict):
                    continue
                if frame.get("omnigentPing"):
                    counts["pings"] += 1
                    continue
                audio = (frame.get("realtimeInput") or {}).get("audio") or {}
                if isinstance(audio, dict) and isinstance(audio.get("data"), str):
                    counts["audio_in"] += 1
                    await upstream.send(
                        json.dumps({"type": "input_audio_buffer.append", "audio": audio["data"]})
                    )
                    continue
                for response in (frame.get("toolResponse") or {}).get("functionResponses") or []:
                    if not isinstance(response, dict):
                        continue
                    counts["tool_responses"] += 1
                    output = str((response.get("response") or {}).get("output", ""))
                    if "I've sent that to Claude" in output:
                        handed_off = True
                    call.resolve(str(response.get("id") or ""), output)
                turns = (frame.get("clientContent") or {}).get("turns") or []
                if narrating and turns:
                    text = " ".join(
                        str(part.get("text", ""))
                        for turn in turns
                        if isinstance(turn, dict)
                        for part in turn.get("parts") or []
                        if isinstance(part, dict)
                    )
                    call.narration = unmute_live.narration_text(text)
                    narration_given.set()

        async def upstream_to_client() -> None:
            nonlocal last_activity, setup_ms, first_audio_ms, audio_bytes
            nonlocal first_audio_at, last_audio_at, reply, upstream_error
            async for raw in upstream:
                last_activity = time.monotonic()
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "error":
                    error = event.get("error") or {}
                    if error.get("type") != "warning":
                        upstream_error = str(error.get("message") or "Unmute error")
                    _logger.warning("unmute live upstream error: %s", error)
                    continue
                frame = translate_upstream(event)
                if frame is None:
                    continue
                now = time.monotonic()
                content = frame.get("serverContent") or {}
                if "setupComplete" in frame and setup_ms is None:
                    setup_ms = (now - started) * 1000
                if "modelTurn" in content:
                    size = _audio_bytes(str(event.get("delta", "")))
                    if first_audio_ms is None:
                        first_audio_ms = (now - started) * 1000
                        first_audio_at = now
                    last_audio_at = now
                    audio_bytes += size
                    reply.add(now, size)
                if content.get("interrupted") or content.get("turnComplete"):
                    counts["interrupted" if content.get("interrupted") else "turns"] += 1
                    done = reply.summary()
                    if done is not None and len(replies) < 30:
                        replies.append(done)
                    reply = _ReplyMeter()
                await websocket.send_text(json.dumps(frame))

        async def brain_to_client() -> None:
            while True:
                frame = await call.outbox.get()
                await websocket.send_text(json.dumps(frame))

        async def feed_silence() -> None:
            # Unmute only speaks in step with incoming audio; a narration has no
            # mic, so it gets silence, starting once there is something to read.
            await narration_given.wait()
            frame = json.dumps({"type": "input_audio_buffer.append", "audio": _SILENCE_B64})
            next_at = time.monotonic()
            while True:
                await upstream.send(frame)
                next_at += _SILENCE_FRAME_S
                await asyncio.sleep(max(0.0, next_at - time.monotonic()))

        tasks: dict[asyncio.Task[None], str] = {}
        try:
            await upstream.send(json.dumps(unmute_live.session_update(call)))
            tasks = {
                asyncio.create_task(client_to_upstream()): "client",
                asyncio.create_task(upstream_to_client()): "upstream",
                asyncio.create_task(brain_to_client()): "brain",
                asyncio.create_task(asyncio.sleep(max_session_s)): "cap",
            }
            if narrating:
                tasks[asyncio.create_task(feed_silence())] = "silence"
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            first = next(iter(done))
            ended_by = tasks[first]
            try:
                first.result()
            except _ClientTimedOut:
                ended_by = "idle"
                close_code = status.WS_1008_POLICY_VIOLATION
                close_reason = "unmute live client keepalive timed out"
            except websockets.ConnectionClosed:
                ended_by = "upstream"
            except Exception as exc:  # noqa: BLE001 - relay errors end the call
                ended_by = "error"
                _logger.warning("unmute live relay error: %s", exc)
            if ended_by == "cap":
                close_code = status.WS_1008_POLICY_VIOLATION
                close_reason = "unmute live session cap reached"
            elif ended_by == "upstream" and upstream_error:
                close_code = status.WS_1011_INTERNAL_ERROR
                close_reason = upstream_error[:120]
        except Exception as exc:  # noqa: BLE001
            ended_by = "error"
            _logger.warning("unmute live relay error: %s", exc)
        finally:
            for task in tasks:
                task.cancel()
            unmute_live.close_call(call)
            with contextlib.suppress(Exception):
                await upstream.close()
            _logger.info(
                "unmute live session ended | mode=%s duration_s=%.1f ended_by=%s "
                "briefed=%s tool_calls=%d handed_off=%s setup_ms=%s first_audio_ms=%s "
                "audio_s=%.1f realtime_x=%s events={audioIn:%d,interrupted:%d,"
                "turnComplete:%d,toolResponse:%d,pings:%d} replies=%s error=%s",
                "narrate" if narrating else "conversation",
                time.monotonic() - started,
                ended_by,
                "yes" if briefed else "no",
                call.tool_calls,
                "yes" if handed_off else "no",
                f"{setup_ms:.0f}" if setup_ms is not None else "none",
                f"{first_audio_ms:.0f}" if first_audio_ms is not None else "none",
                audio_bytes / _PLAYBACK_BYTES_PER_S,
                _realtime_factor(audio_bytes, first_audio_at, last_audio_at),
                counts["audio_in"],
                counts["interrupted"],
                counts["turns"],
                counts["tool_responses"],
                counts["pings"],
                "[" + ",".join([*replies, *filter(None, [reply.summary()])]) + "]",
                upstream_error or "none",
            )
        with contextlib.suppress(RuntimeError):
            await websocket.close(code=close_code, reason=close_reason)

    @router.api_route(
        "/live/unmute/brain/v1/{path:path}", methods=["GET", "POST"], include_in_schema=False
    )
    async def unmute_brain(path: str, request: Request) -> Response:
        """The model Unmute talks to: Omnigent's voice for its own calls."""
        presented = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not presented or not hmac.compare_digest(presented, ensure_proxy_token()):
            return JSONResponse(
                {"error": {"message": "bad token", "type": "invalid_request_error"}},
                status_code=401,
            )
        if request.method == "GET" and path == "models":
            # Unmute checks this before every call with a 2s timeout; OpenAI's
            # own list is often slower, which dropped calls. A model outage
            # shows up in the reply instead (``BRAIN_DOWN``).
            return JSONResponse(
                {
                    "object": "list",
                    "data": [
                        {"id": model, "object": "model", "owned_by": "openai"}
                        for pool in budget.POOLS
                        for model in pool.models
                    ],
                }
            )
        upstream_base = unmute_live.brain_upstream(request.scope.get("server"))
        headers = {
            k: v for k, v in request.headers.items() if k.lower() not in _REQUEST_DROP_HEADERS
        }
        headers["authorization"] = f"Bearer {presented}"
        body = await request.body()
        payload: Any = None
        if request.method == "POST":
            with contextlib.suppress(ValueError):
                payload = json.loads(body)
        sent = payload.get("messages") if isinstance(payload, dict) else None
        messages: list[dict[str, Any]] = sent if isinstance(sent, list) else []
        call = None
        if path == "chat/completions" and messages:
            system = next(
                (
                    m.get("content")
                    for m in messages
                    if isinstance(m, dict)
                    and m.get("role") == "system"
                    and isinstance(m.get("content"), str)
                ),
                "",
            )
            call = unmute_live.find_call(system)

        client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0), transport=transport)
        if call is None:
            url = f"{upstream_base}/{path}"
            if request.url.query:
                url = f"{url}?{request.url.query}"
            try:
                upstream = await client.send(
                    client.build_request(
                        request.method,
                        url,
                        headers=headers,
                        content=body if request.method == "POST" else None,
                    ),
                    stream=True,
                )
            except httpx.HTTPError as exc:
                await client.aclose()
                return JSONResponse(
                    {"error": {"message": f"model unreachable: {exc}", "type": "server_error"}},
                    status_code=502,
                )

            async def close() -> None:
                await upstream.aclose()
                await client.aclose()

            return StreamingResponse(
                upstream.aiter_raw(),
                status_code=upstream.status_code,
                headers={
                    k: v
                    for k, v in upstream.headers.items()
                    if k.lower() not in _RESPONSE_DROP_HEADERS
                },
                background=BackgroundTask(close),
            )

        model = payload.get("model")
        if not isinstance(model, str) or not model:
            await client.aclose()
            return JSONResponse(
                {
                    "error": {
                        "message": "Request body needs a 'model'.",
                        "type": "invalid_request_error",
                    }
                },
                status_code=400,
            )

        def chunk(delta: dict[str, str], finish: str | None = None) -> bytes:
            event = {
                "id": f"chatcmpl-omnigent-{call.call_id[:8]}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            return f"data: {json.dumps(event)}\n\n".encode()

        async def reply_stream() -> AsyncIterator[bytes]:
            try:
                async for text in unmute_live.speak(
                    call,
                    messages,
                    model=model,
                    client=client,
                    url=f"{upstream_base}/chat/completions",
                    headers=headers,
                ):
                    yield chunk({"content": text})
                yield chunk({}, "stop")
                yield b"data: [DONE]\n\n"
            finally:
                await client.aclose()

        return StreamingResponse(reply_stream(), media_type="text/event-stream")

    return router
