"""Wake a sub-agent's parent when the child repeats one tool call in a loop."""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import threading
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from omnigent.entities.conversation import Conversation
    from omnigent.stores import ConversationStore

WakeDispatch = Callable[[str, "Conversation", str], Awaitable[bool]]
_THRESHOLD = 5
_WAKE_RETRIES = 2
_WAKE_RETRY_BACKOFF_S = 0.5


class SubagentLoopNotifier:
    """Observe tool-call events and wake an immediate parent once per streak."""

    def __init__(self, conversation_store: ConversationStore, wake_dispatch: WakeDispatch,
                 loop: asyncio.AbstractEventLoop) -> None:
        self._conversation_store = conversation_store
        self._wake_dispatch = wake_dispatch
        self._loop = loop
        self._lock = threading.Lock()
        self._streaks: dict[str, tuple[str, int, bool, Any]] = {}
        self._inflight: set[concurrent.futures.Future[None]] = set()

    def observe(self, conversation_id: str, event: dict[str, Any]) -> None:
        if event.get("type") == "response.output_item.done":
            item = event.get("item")
            is_function_call = isinstance(item, dict) and item.get("type") == "function_call"
        elif event.get("type") == "external_conversation_item":
            data = event.get("data")
            is_function_call = (
                isinstance(data, dict) and data.get("item_type") == "function_call"
            )
            item = data.get("item_data") if is_function_call else None
        else:
            return
        if not is_function_call or not isinstance(item, dict):
            return
        name, arguments = item.get("name"), item.get("arguments")
        call_id = item.get("call_id")
        identity = hashlib.sha256(
            f"{name}\x00{arguments!r}".encode("utf-8", "replace")
        ).hexdigest()
        with self._lock:
            previous = self._streaks.get(conversation_id)
            if previous and call_id is not None and previous[3] == call_id:
                return
            count = previous[1] + 1 if previous and previous[0] == identity else 1
            notified = previous[2] if previous and previous[0] == identity else False
            self._streaks[conversation_id] = (identity, count, notified, call_id)
            if count != _THRESHOLD or notified:
                return
            self._streaks[conversation_id] = (identity, count, True, call_id)
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._handle(conversation_id, name, arguments, count), self._loop
            )
        except RuntimeError:
            return
        with self._lock:
            self._inflight.add(future)
        future.add_done_callback(self._inflight.discard)

    async def _handle(self, conversation_id: str, name: Any, arguments: Any, count: int) -> None:
        child = await asyncio.to_thread(self._conversation_store.get_conversation, conversation_id)
        if child is None or not child.parent_conversation_id:
            return
        shown_arguments = str(arguments)
        if len(shown_arguments) > 200:
            shown_arguments = shown_arguments[:197] + "..."
        notice = (
            f"[System: child {child.title or child.id} repeated tool call "
            f"{name}({shown_arguments}) {count} times in a row; it may be stuck.]"
        )
        for attempt in range(_WAKE_RETRIES + 1):
            try:
                if await self._wake_dispatch(child.parent_conversation_id, child, notice):
                    return
            except Exception:
                pass
            if attempt < _WAKE_RETRIES:
                await asyncio.sleep(_WAKE_RETRY_BACKOFF_S)

    def close(self) -> None:
        with self._lock:
            futures = list(self._inflight)
            self._inflight.clear()
        for future in futures:
            future.cancel()
