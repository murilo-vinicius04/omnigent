"""The hub as a regular conversation: one per user, pinned, opened like any chat.

The hub has no harness and no runner. Messages typed into its conversation
are taken by the events route before any dispatch and answered by the hub
(:mod:`omnigent.server.hub`); what the hub says is written back as ordinary
assistant messages and published on the session stream, so the normal chat
page renders it, the normal composer sends to it, and the chat's voice
button talks to the hub.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import tarfile
import time
from typing import Any, Final

from omnigent.server import hub as hub_module

_logger = logging.getLogger(__name__)

#: Label marking a conversation as a user's hub.
HUB_LABEL: Final[str] = "omnigent.hub"

#: Title of the hub conversation in the sidebar.
HUB_TITLE: Final[str] = "Hub"

#: Name of the hub's session-scoped agent; the chat header shows it.
HUB_AGENT_NAME: Final[str] = "hub"

#: Request headers a send carries on, so it acts as the caller.
FORWARDED_HEADERS: Final[tuple[str, ...]] = (
    "cookie",
    "authorization",
    "x-forwarded-email",
    "origin",
)

#: A finished session's reply is quoted in the hub up to this length.
_REPLY_QUOTE_CHARS: Final[int] = 3000

_HUB_SPEC: Final[str] = """\
spec_version: 1
name: hub
description: One chat for all of your projects.
executor:
  type: omnigent
  config:
    # Never launched: the hub's messages never reach a runner. Named so the
    # chat shows the model that really answers (the router, on Gemini).
    harness: agy
    model: {model}
"""


def is_hub(conv: Any) -> bool:
    """Whether a conversation is a user's hub."""
    labels = getattr(conv, "labels", None) or {}
    return labels.get(HUB_LABEL) == "1"


def _hub_bundle() -> bytes:
    """A gzipped tarball holding the hub's one-file spec."""
    data = _HUB_SPEC.format(model=hub_module.hub_model()).encode("utf-8")
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        info = tarfile.TarInfo("config.yaml")
        info.size = len(data)
        info.mtime = 0
        tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def _id_path(user_id: str | None):
    return hub_module.HUB_DIR / hub_module.user_key(user_id) / "conversation.json"


def known_hub_id(user_id: str | None) -> str | None:
    """The user's hub conversation id, if one was made."""
    try:
        raw = json.loads(_id_path(user_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = raw.get("id") if isinstance(raw, dict) else None
    return value if isinstance(value, str) and value else None


def ensure_hub_conversation(
    user_id: str | None,
    *,
    conversation_store: Any,
    artifact_store: Any,
    permission_store: Any = None,
) -> str:
    """Return the user's hub conversation id, creating it when missing.

    Blocking (store calls); run it in a thread from async code. A hub the
    user archived or deleted is replaced by a fresh one.
    """
    from omnigent.db.utils import generate_agent_id
    from omnigent.stores.conversation_store import pinned_label_key

    existing = known_hub_id(user_id)
    if existing is not None:
        conv = conversation_store.get_conversation(existing)
        if conv is not None and not getattr(conv, "archived", False):
            return existing
    bundle = _hub_bundle()
    agent_id = generate_agent_id()
    location = f"{agent_id}/{hashlib.sha256(bundle).hexdigest()}"
    artifact_store.put(location, bundle)
    created = conversation_store.create_session_with_agent(
        agent_id=agent_id,
        agent_name=HUB_AGENT_NAME,
        agent_bundle_location=location,
        agent_description="One chat for all of your projects.",
        title=HUB_TITLE,
        labels={HUB_LABEL: "1", pinned_label_key(user_id): str(int(time.time() * 1000))},
    )
    session_id = created.conversation.id
    if permission_store is not None and user_id:
        from omnigent.server.auth import LEVEL_OWNER

        permission_store.grant(user_id, session_id, LEVEL_OWNER)
    path = _id_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": session_id}), encoding="utf-8")
    _logger.info("hub conversation created | user=%s session=%s", user_id, session_id)
    return session_id


def render(entry: hub_module.HubEntry, base_url: str = "") -> str:
    """One hub entry as the markdown its chat message carries.

    :param base_url: The app's address as the reader opens it, e.g.
        ``"http://localhost:6767"``. The chat links only absolute URLs (a bare
        ``/c/...`` reads as a file path), so without it there is no link.
    """
    link = ""
    if base_url and entry.session_id:
        link = f"[Open {entry.session_name} ↗]({base_url}/c/{entry.session_id})"
    elif base_url and entry.link:
        link = f"[Start a chat in {entry.session_name} ↗]({base_url}{entry.link})"
    if entry.kind == "sent" and entry.detail:
        return f"{entry.text}\n\n> {_quote(entry.detail)}\n\n{link}".rstrip()
    if entry.kind == "finished" and entry.detail:
        reply = entry.detail
        if len(reply) > _REPLY_QUOTE_CHARS:
            reply = (
                reply[:_REPLY_QUOTE_CHARS].rstrip() + f" … (the rest is in {entry.session_name})"
            )
        return f"{entry.text}\n\n> {_quote(reply)}\n\n{link}".rstrip()
    if entry.kind in ("attention", "finished", "status", "check", "model", "project") and link:
        return f"{entry.text}\n\n{link}"
    return entry.text


def _quote(text: str) -> str:
    return "\n> ".join(text.strip().splitlines())


class HubConversation:
    """Writes what the hub says into its conversation, and shows it working."""

    def __init__(self, conversation_store: Any, session_id: str) -> None:
        self.conversation_store = conversation_store
        self.session_id = session_id
        #: The app's address as the reader last opened it, for links.
        self.base_url = ""

    def post(self, entry: hub_module.HubEntry) -> None:
        """Store one hub entry as an assistant message and publish it."""
        from omnigent.db.utils import generate_task_id
        from omnigent.entities import NewConversationItem
        from omnigent.entities.conversation import parse_item_data
        from omnigent.server.routes._sessions.common import session_stream
        from omnigent.server.schemas import OutputItemDoneEvent

        try:
            item = NewConversationItem(
                type="message",
                response_id=generate_task_id(),
                data=parse_item_data(
                    "message",
                    {
                        "type": "message",
                        "role": "assistant",
                        "agent": HUB_AGENT_NAME,
                        "content": [{"type": "output_text", "text": render(entry, self.base_url)}],
                    },
                ),
            )
            persisted = self.conversation_store.append(self.session_id, [item])
        except Exception:  # noqa: BLE001 - the hub's own log still has it
            _logger.warning("hub message not stored in %s", self.session_id, exc_info=True)
            return
        if persisted:
            session_stream.publish(
                self.session_id,
                OutputItemDoneEvent(
                    type="response.output_item.done", item=persisted[0].to_api_dict()
                ).model_dump(),
            )

    def post_user(self, text: str) -> None:
        """Store what they said on a voice call as their message, and publish it."""
        from omnigent.db.utils import generate_task_id
        from omnigent.entities import NewConversationItem
        from omnigent.entities.conversation import parse_item_data
        from omnigent.server.routes._sessions.helpers import _publish_input_consumed

        if not text.strip():
            return
        try:
            item = NewConversationItem(
                type="message",
                response_id=generate_task_id(),
                data=parse_item_data(
                    "message",
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": f"🎙 {text.strip()}"}],
                    },
                ),
            )
            persisted = self.conversation_store.append(self.session_id, [item])
        except Exception:  # noqa: BLE001 - the hub's own log still has it
            _logger.warning("hub voice line not stored in %s", self.session_id, exc_info=True)
            return
        if persisted:
            _publish_input_consumed(self.session_id, persisted[0])

    def busy(self, working: bool) -> None:
        """Show the chat as thinking while the hub works on a message."""
        from omnigent.server.routes._sessions.helpers import _publish_status

        try:
            _publish_status(self.session_id, "running" if working else "idle", None)
        except Exception:  # noqa: BLE001 - a status edge is cosmetic
            _logger.debug("hub status not published", exc_info=True)


def base_url(headers: Any) -> str:
    """The app's address as this caller sees it: their Origin, else their Host."""
    origin = str(headers.get("origin") or "").rstrip("/")
    if origin.startswith(("http://", "https://")):
        return origin
    host = str(headers.get("host") or "")
    if not host:
        return ""
    scheme = str(headers.get("x-forwarded-proto") or "http").split(",")[0].strip()
    return f"{scheme}://{host}"


def attach(hub: hub_module.Hub, conversation_store: Any, session_id: str | None) -> None:
    """Point the hub at its conversation, so what it says lands there."""
    if session_id is None:
        return
    if hub.chat is None or hub.chat.session_id != session_id:
        hub.chat = HubConversation(conversation_store, session_id)


def bind_request(
    hub: hub_module.Hub,
    *,
    conversation_store: Any,
    user_id: str | None,
    app: Any,
    headers: Any,
    companions: Any,
) -> None:
    """Bind the hub to this caller: sends act as them, through the app's routes."""
    if hub.chat is not None:
        base = base_url(headers)
        if base:
            hub.chat.base_url = base
    hub.bind(
        hub_module.store_backend(
            conversation_store,
            user_id=user_id,
            app=app,
            headers={k: v for k, v in headers.items() if k in FORWARDED_HEADERS},
            companions=companions,
            exclude={hub.chat.session_id} if hub.chat is not None else set(),
        )
    )


async def accept_message(
    *,
    conv: Any,
    body: Any,
    conversation_store: Any,
    user_id: str | None,
    created_by: str | None,
    app: Any,
    headers: Any,
) -> dict[str, bool | str]:
    """Take a message typed into the hub conversation: store it, then answer it.

    Stands in for the runner dispatch: nothing is forwarded anywhere. The
    hub's reply arrives as later assistant messages on the stream.
    """
    from omnigent.db.utils import generate_task_id
    from omnigent.server.discussion import registry
    from omnigent.server.routes._sessions.helpers import (
        _build_new_item,
        _message_text,
        _publish_input_consumed,
    )

    content = body.data.get("content")
    blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
    text = (_message_text(blocks) or "").strip()
    user_item = _build_new_item(body, generate_task_id(), created_by=created_by)
    persisted = await asyncio.to_thread(conversation_store.append, conv.id, [user_item])
    consumed = persisted[0]
    _publish_input_consumed(conv.id, consumed)
    hub = hub_module.hub_for(user_id)
    attach(hub, conversation_store, conv.id)
    bind_request(
        hub,
        conversation_store=conversation_store,
        user_id=user_id,
        app=app,
        headers=headers,
        companions=registry,
    )
    if text:
        hub.submit(text, via="typed")
    return {"queued": True, "item_id": consumed.id}
