"""OpenAI-compatible proxy that keeps Omnigent inside the free daily token pools.

Harnesses (codex, openai-agents) reach OpenAI through a ``gateway`` provider
whose ``base_url`` is this route, so every model call passes here:

1. **Admission.** Before a call leaves, its worst case (request bytes / 2 as
   input, plus its output cap) is checked against the pool's tokens already
   spent today plus calls still in flight. A call that could overrun the pool,
   or a model in no free pool, is refused with OpenAI's own
   ``insufficient_quota`` error, so nothing is billed.
2. **Metering.** The real usage is read from OpenAI's reply (the
   ``response.completed`` event, or the JSON body) and added to
   :mod:`omnigent.openai_token_budget`, the ledger the composer tray reads.

The real API key stays in ``~/.omnigent/openai-key`` and never leaves the
server; harnesses authenticate with a local token from
``~/.omnigent/openai-budget-token``.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from omnigent import openai_token_budget as budget
from omnigent.install_ledger import state_dir

logger = logging.getLogger(__name__)

UPSTREAM_BASE_URL = "https://api.openai.com/v1"

#: POST paths that spend tokens and whose usage this proxy can read.
METERED_PATHS = frozenset({"responses", "responses/compact", "chat/completions"})

#: Output cap forced onto a call that sets none, so its worst case is bounded.
DEFAULT_OUTPUT_RESERVE = 32_000

#: JSON bytes per token, low on purpose so the input estimate errs high: a
#: request that crosses the pool's limit is billed in full.
_BYTES_PER_TOKEN = 2

_REQUEST_DROP_HEADERS = frozenset(
    {"host", "authorization", "content-length", "accept-encoding", "connection"}
)
_RESPONSE_DROP_HEADERS = frozenset(
    {"content-length", "content-encoding", "transfer-encoding", "connection"}
)

#: Worst-case tokens of admitted calls that have not reported usage yet.
_inflight: dict[str, int] = {}
_admit_lock = asyncio.Lock()


def api_key_path() -> Path:
    """Return the file holding the real OpenAI API key."""
    return state_dir() / "openai-key"


def proxy_token_path() -> Path:
    """Return the file holding the local token harnesses present to the proxy."""
    return state_dir() / "openai-budget-token"


def ensure_proxy_token() -> str:
    """Return the proxy token, creating it (mode 600, no newline) if missing."""
    path = proxy_token_path()
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if not token:
        token = secrets.token_urlsafe(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token)
    return token


def _error(status: int, code: str, message: str) -> JSONResponse:
    """Build an OpenAI-shaped error, which harnesses already know how to show."""
    return JSONResponse(
        {"error": {"message": message, "type": code, "param": None, "code": code}},
        status_code=status,
    )


def _bound_output(path: str, payload: dict[str, Any]) -> int:
    """Return the call's output cap, forcing :data:`DEFAULT_OUTPUT_RESERVE` if unset.

    Without a cap a call's output (reasoning included) is unbounded, so no
    admission check could promise it fits the pool.
    """
    keys = (
        ("max_output_tokens",)
        if path.startswith("responses")
        else (
            "max_completion_tokens",
            "max_tokens",
        )
    )
    for key in keys:
        cap = payload.get(key)
        if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0:
            return cap
    payload[keys[0]] = DEFAULT_OUTPUT_RESERVE
    return DEFAULT_OUTPUT_RESERVE


def _refusal(model: str, pool_id: str | None, estimate: int) -> JSONResponse | None:
    """Return the error refusing this call, or ``None`` when it fits its pool."""
    if pool_id is None or pool_id == budget.UNLISTED:
        return _error(
            429,
            "insufficient_quota",
            f"Omnigent refused {model}: it is in no free daily token pool, so every "
            "token would be billed.",
        )
    row = next(r for r in budget.pool_usage() if r["id"] == pool_id)
    cap = int(row["daily_tokens"])
    used = int(row["tokens"]) + _inflight.get(pool_id, 0)
    if used + estimate <= cap:
        return None
    left = max(0, cap - used)
    return _error(
        429,
        "insufficient_quota",
        f"Omnigent stopped this {model} call to stay inside the free {row['label']} "
        f"pool: {budget.format_tokens(left)} of {budget.format_tokens(cap)} tokens left "
        f"today, and this call may use up to {budget.format_tokens(estimate)}. "
        "The pool resets at 00:00 UTC.",
    )


def usage_counts(usage: dict[str, Any]) -> dict[str, int]:
    """Map a Responses or Chat Completions ``usage`` object to ledger fields."""
    if "input_tokens" in usage:
        total_in = budget.token_count(usage.get("input_tokens"))
        details = usage.get("input_tokens_details")
        output = budget.token_count(usage.get("output_tokens"))
    else:
        total_in = budget.token_count(usage.get("prompt_tokens"))
        details = usage.get("prompt_tokens_details")
        output = budget.token_count(usage.get("completion_tokens"))
    cached = budget.token_count(details.get("cached_tokens")) if isinstance(details, dict) else 0
    cached = min(cached, total_in)
    return {
        "input_tokens": total_in - cached,
        "cache_read_input_tokens": cached,
        "output_tokens": output,
    }


class SseUsageScanner:
    """Pick the final ``usage`` object out of a server-sent event stream."""

    def __init__(self) -> None:
        self._buffer = b""
        self.usage: dict[str, Any] | None = None

    def feed(self, chunk: bytes) -> None:
        """Consume one raw chunk; complete ``data:`` lines are inspected."""
        self._buffer += chunk
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            data = line.rstrip(b"\r")
            if not data.startswith(b"data:") or b'"usage"' not in data:
                continue
            try:
                event = json.loads(data[5:])
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            response = event.get("response")
            usage = response.get("usage") if isinstance(response, dict) else event.get("usage")
            if isinstance(usage, dict):
                self.usage = usage


def _record(model: str, usage: dict[str, Any] | None, body: bytes) -> None:
    """Add a finished call to the ledger; estimate its input if no usage came back."""
    try:
        if usage is not None:
            budget.record(model, usage_counts(usage), source="proxy")
        else:
            # Billed but unreported (e.g. the client hung up mid-stream).
            budget.record(
                model, {"input_tokens": len(body) // _BYTES_PER_TOKEN}, source="proxy-estimate"
            )
    except Exception as exc:  # noqa: BLE001 - never break the reply over metering
        logger.warning("openai budget proxy could not record %s usage: %s", model, exc)


def _response_headers(upstream: httpx.Response) -> dict[str, str]:
    return {k: v for k, v in upstream.headers.items() if k.lower() not in _RESPONSE_DROP_HEADERS}


def create_openai_budget_proxy_router(
    *,
    upstream_base_url: str = UPSTREAM_BASE_URL,
    transport: httpx.AsyncBaseTransport | None = None,
) -> APIRouter:
    """Build the router for ``/openai-budget/v1/*``.

    :param upstream_base_url: Where admitted calls go, e.g.
        ``"https://api.openai.com/v1"``.
    :param transport: Optional httpx transport, for tests.
    """
    router = APIRouter()
    ensure_proxy_token()

    @router.api_route(
        "/openai-budget/v1/{path:path}", methods=["GET", "POST"], include_in_schema=False
    )
    async def proxy(path: str, request: Request) -> Response:
        presented = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not presented or not hmac.compare_digest(presented, ensure_proxy_token()):
            return _error(401, "invalid_api_key", "Omnigent OpenAI budget proxy: bad token.")
        try:
            api_key = api_key_path().read_text(encoding="utf-8").strip()
        except OSError:
            api_key = ""
        if not api_key:
            return _error(503, "server_error", "No OpenAI key in ~/.omnigent/openai-key.")

        headers = {
            k: v for k, v in request.headers.items() if k.lower() not in _REQUEST_DROP_HEADERS
        }
        headers["authorization"] = f"Bearer {api_key}"
        headers["accept-encoding"] = "identity"
        url = f"{upstream_base_url}/{path}"
        if request.url.query:
            url = f"{url}?{request.url.query}"
        timeout = httpx.Timeout(600.0, connect=15.0)

        if request.method == "GET":
            async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
                upstream = await client.get(url, headers=headers)
            return Response(
                upstream.content,
                status_code=upstream.status_code,
                headers=_response_headers(upstream),
            )

        if path not in METERED_PATHS:
            return _error(
                403,
                "permission_denied",
                f"Omnigent OpenAI budget proxy cannot meter POST /{path}, so it refuses it.",
            )
        body = await request.body()
        try:
            payload = json.loads(body)
        except ValueError:
            payload = None
        model = payload.get("model") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or not isinstance(model, str) or not model:
            return _error(400, "invalid_request_error", "Request body needs a 'model'.")
        if path == "chat/completions" and payload.get("stream"):
            # Streamed chat only reports usage when asked to.
            options = payload.get("stream_options")
            payload["stream_options"] = {
                **(options if isinstance(options, dict) else {}),
                "include_usage": True,
            }
        free_id = budget.free_model_id(model)
        if free_id is not None:
            # Pin aliases to the listed free snapshot.
            payload["model"] = free_id
        output_cap = _bound_output(path, payload)
        body = json.dumps(payload).encode()

        pool_id = budget.pool_for(model)
        estimate = len(body) // _BYTES_PER_TOKEN + output_cap
        async with _admit_lock:
            refusal = _refusal(model, pool_id, estimate)
            if refusal is None and pool_id is not None:
                _inflight[pool_id] = _inflight.get(pool_id, 0) + estimate
        if refusal is not None or pool_id is None:
            logger.info("openai budget proxy refused %s (estimate %d)", model, estimate)
            return refusal or _error(429, "insufficient_quota", "refused")

        def release() -> None:
            _inflight[pool_id] = max(0, _inflight.get(pool_id, 0) - estimate)

        client = httpx.AsyncClient(timeout=timeout, transport=transport)
        try:
            upstream = await client.send(
                client.build_request("POST", url, headers=headers, content=body), stream=True
            )
        except httpx.HTTPError as exc:
            await client.aclose()
            release()
            return _error(502, "server_error", f"OpenAI unreachable: {type(exc).__name__}")

        if "text/event-stream" not in upstream.headers.get("content-type", ""):
            try:
                content = await upstream.aread()
            finally:
                await upstream.aclose()
                await client.aclose()
            if upstream.status_code < 400:
                try:
                    parsed = json.loads(content)
                except ValueError:
                    parsed = None
                usage = parsed.get("usage") if isinstance(parsed, dict) else None
                _record(model, usage if isinstance(usage, dict) else None, body)
            release()
            return Response(
                content, status_code=upstream.status_code, headers=_response_headers(upstream)
            )

        scanner = SseUsageScanner()

        async def relay() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_bytes():
                    scanner.feed(chunk)
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()
                if upstream.status_code < 400:
                    _record(model, scanner.usage, body)
                release()

        return StreamingResponse(
            relay(), status_code=upstream.status_code, headers=_response_headers(upstream)
        )

    return router
