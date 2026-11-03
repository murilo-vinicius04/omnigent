"""Runner side of ``sys_duet_say`` and ``sys_duet_inbox``: call the server's duet router."""

from __future__ import annotations

import json
from typing import Any

import httpx


async def say_via_rest(
    args: dict[str, Any], conversation_id: str | None, client: httpx.AsyncClient | None
) -> str:
    """Post a note to the router and wake the lanes it marks urgent.

    :param args: Tool arguments (``topic``, ``text``, optional ``urgency``/``to``).
    :param conversation_id: The calling session.
    :param client: Server client carrying the caller's credentials.
    :returns: JSON result for the model.
    """
    if client is None or conversation_id is None:
        return json.dumps({"error": "sys_duet_say requires server access"})
    text = args.get("text")
    if not isinstance(text, str) or not text.strip():
        return json.dumps({"error": "sys_duet_say requires a non-empty 'text'"})
    body = {
        "from_session_id": conversation_id,
        "topic": str(args.get("topic") or ""),
        "text": text,
        "urgency": "urgent" if args.get("urgency") == "urgent" else "normal",
        "to": str(args.get("to") or ""),
    }
    try:
        resp = await client.post("/v1/duet/say", json=body, timeout=30.0)
    except httpx.HTTPError as exc:
        return json.dumps({"error": f"sys_duet_say failed: {exc}"})
    if resp.status_code >= 400:
        return json.dumps(
            {"error": f"sys_duet_say returned {resp.status_code}", "detail": resp.text[:200]}
        )
    result = resp.json()
    for hop in result.get("routed", []):
        message = hop.pop("message", "")
        if not message:
            hop["woken"] = False
            continue
        # Best-effort push for every note, not just urgent. An idle lane
        # starts a turn immediately; a busy lane buffers the message and the
        # runner injects it mid-turn, so a 30-minute turn still sees notes
        # within seconds instead of waiting for a manual inbox check.
        event = {
            "type": "message",
            "data": {"role": "user", "content": [{"type": "input_text", "text": message}]},
        }
        try:
            woke = await client.post(
                f"/v1/sessions/{hop['to_session_id']}/events", json=event, timeout=30.0
            )
            hop["woken"] = woke.status_code < 400
        except httpx.HTTPError:
            hop["woken"] = False
    return json.dumps(result)


async def inbox_via_rest(conversation_id: str | None, client: httpx.AsyncClient | None) -> str:
    """Read and clear this session's queued notes.

    :param conversation_id: The calling session.
    :param client: Server client carrying the caller's credentials.
    :returns: JSON result for the model.
    """
    if client is None or conversation_id is None:
        return json.dumps({"error": "sys_duet_inbox requires server access"})
    try:
        resp = await client.get(f"/v1/duet/inbox/{conversation_id}", timeout=30.0)
    except httpx.HTTPError as exc:
        return json.dumps({"error": f"sys_duet_inbox failed: {exc}"})
    if resp.status_code >= 400:
        return json.dumps({"error": f"sys_duet_inbox returned {resp.status_code}"})
    return json.dumps(resp.json())
