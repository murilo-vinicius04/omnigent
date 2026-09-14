"""The companion's reply is written as a turn the store accepts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from omnigent.server.routes._sessions import orchestration


async def test_the_companions_reply_is_a_valid_assistant_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A reply without ``agent`` fails validation, and the reader's send 500s.
    appended: list[object] = []

    class _Store:
        def append(self, session_id: str, items: list[object]) -> list[SimpleNamespace]:
            appended.extend(items)
            return [SimpleNamespace(id=f"item_{i}", to_api_dict=dict) for i in range(len(items))]

    async def _no_title(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(orchestration, "_build_new_item", lambda *_a, **_k: object())
    monkeypatch.setattr(orchestration, "_seed_missing_title_from_user_message", _no_title)
    monkeypatch.setattr(orchestration, "_publish_input_consumed", lambda *_a: None)
    monkeypatch.setattr(orchestration, "_publish_status", lambda *_a: None)
    monkeypatch.setattr(
        orchestration, "OutputItemDoneEvent", lambda **_k: SimpleNamespace(model_dump=dict)
    )
    monkeypatch.setattr(orchestration.session_stream, "publish", lambda *_a: None)

    item_id = await orchestration._persist_companion_answer(
        "conv_a",
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        _Store(),  # type: ignore[arg-type]
        answer="Hi! What can I help with?",
        asked="hey",
        created_by=None,
    )

    assert item_id == "item_0"
    reply = appended[1].data  # type: ignore[attr-defined]
    assert reply.role == "assistant"
    assert reply.agent == "companion"
    assert {"type": "companion_answer", "asked": "hey"} in reply.content
