from __future__ import annotations

from omnigent.server.todo_extract import extract_todos, strip_todo_block


def test_real_sample_with_four_sections() -> None:
    text = """
I have analyzed the repository and planned the remaining tasks.

## To-do, updated

### Pending
- [ ] Task 1: Initialize repository and **setup** environment
- [ ] Task 2: Implement core parser logic

### In Progress
- [ ] Task 3: Running integration tests

### Blocked
- [ ] Task 4: Awaiting third-party approval

### Done
- [ ] Task 5: Design architectural draft
- [x] Task 6: Review security checklist

## Next Steps
We will proceed with implementation shortly.
"""
    result = extract_todos(text)
    assert result.found is True
    assert len(result.todos) == 6
    assert result.todos == [
        {
            "content": "Task 1: Initialize repository and setup environment",
            "status": "pending",
        },
        {
            "content": "Task 2: Implement core parser logic",
            "status": "pending",
        },
        {
            "content": "Task 3: Running integration tests",
            "status": "pending",
        },
        {
            "content": "Task 4: Awaiting third-party approval",
            "status": "pending",
        },
        {
            "content": "Task 5: Design architectural draft",
            "status": "completed",
        },
        {
            "content": "Task 6: Review security checklist",
            "status": "completed",
        },
    ]


def test_message_with_no_todo_block() -> None:
    text = "Everything looks good! All 42 tests are passing."
    result = extract_todos(text)
    assert result.found is False
    assert result.todos == []


def test_nested_indented_items() -> None:
    text = """
## To-do
- [ ] Parent task
  - [ ] Child task 1
    - [x] Grandchild completed task
- [x] Another root task
"""
    result = extract_todos(text)
    assert result.found is True
    assert result.todos == [
        {"content": "Parent task", "status": "pending"},
        {"content": "Child task 1", "status": "pending"},
        {"content": "Grandchild completed task", "status": "completed"},
        {"content": "Another root task", "status": "completed"},
    ]


def test_item_containing_markdown_link() -> None:
    text = """
## To-do
- [ ] Check the [official guide](https://example.com/guide) for details
- [x] Read the **[changelog](https://example.com/changes)** carefully
"""
    result = extract_todos(text)
    assert result.found is True
    assert result.todos == [
        {
            "content": "Check the official guide for details",
            "status": "pending",
        },
        {
            "content": "Read the changelog carefully",
            "status": "completed",
        },
    ]


def test_heading_case_insensitivity_and_variants() -> None:
    for heading in ["## To-do", "## TO-DO", "## To-do, updated", "## Todo", "## to-do: final"]:
        text = f"{heading}\n- [x] Item 1\n- [ ] Item 2\n"
        result = extract_todos(text)
        assert result.found is True, f"Failed for heading {heading}"
        assert len(result.todos) == 2
        assert result.todos[0] == {"content": "Item 1", "status": "completed"}
        assert result.todos[1] == {"content": "Item 2", "status": "pending"}


def test_strip_todo_block() -> None:
    text = """Here is what happened today.

## To-do, updated
- [x] Done thing
- [ ] Pending thing

## Final Summary
All done for now.
"""
    stripped = strip_todo_block(text)
    assert "## To-do" not in stripped
    assert "Done thing" not in stripped
    assert "Pending thing" not in stripped
    assert "Here is what happened today." in stripped
    assert "## Final Summary" in stripped
    assert "All done for now." in stripped


def test_strip_todo_block_when_absent() -> None:
    text = "Simple message with no to-do block.\n\n## Other heading\nDetails."
    assert strip_todo_block(text) == text


def test_update_session_todos_block_present_updates_cache_and_emits_event(
    monkeypatch,
) -> None:
    from omnigent.server.routes._sessions.common import _session_todos_cache
    from omnigent.server.routes._sessions.helpers import _update_session_todos_from_text

    published: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "omnigent.server.routes._sessions.helpers.session_stream.publish",
        lambda sid, ev: published.append((sid, ev)),
    )

    session_id = "conv_test_block_present"
    _session_todos_cache.pop(session_id, None)

    text = """
Great work so far!

## To-do
- [ ] Implement feature
- [x] Fix the bug
"""
    updated = _update_session_todos_from_text(session_id, text)
    assert updated is True

    # Cache is updated
    assert session_id in _session_todos_cache
    cached = _session_todos_cache[session_id]
    assert cached == [
        {"content": "Implement feature", "status": "pending", "activeForm": ""},
        {"content": "Fix the bug", "status": "completed", "activeForm": ""},
    ]

    # session.todos event is emitted
    assert len(published) == 1
    assert published[0][0] == session_id
    assert published[0][1]["type"] == "session.todos"
    assert published[0][1]["conversation_id"] == session_id
    assert published[0][1]["todos"] == cached

    _session_todos_cache.pop(session_id, None)


def test_update_session_todos_no_block_keeps_previous_list(
    monkeypatch,
) -> None:
    from omnigent.server.routes._sessions.common import _session_todos_cache
    from omnigent.server.routes._sessions.helpers import _update_session_todos_from_text

    published: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "omnigent.server.routes._sessions.helpers.session_stream.publish",
        lambda sid, ev: published.append((sid, ev)),
    )

    session_id = "conv_test_no_block"
    previous_todos = [
        {"content": "Existing task", "status": "pending", "activeForm": ""},
    ]
    _session_todos_cache[session_id] = list(previous_todos)

    text = "Just a conversational reply with no to-do block."
    updated = _update_session_todos_from_text(session_id, text)
    assert updated is False

    # Previous list kept untouched
    assert _session_todos_cache[session_id] == previous_todos
    # No event emitted
    assert len(published) == 0

    _session_todos_cache.pop(session_id, None)


def test_rebuild_session_todos_from_history_empty_cache() -> None:
    from omnigent.entities import ConversationItem, MessageData
    from omnigent.server.routes._sessions.common import _session_todos_cache
    from omnigent.server.routes._sessions.helpers import _rebuild_session_todos_from_history

    session_id = "conv_test_empty_cache_rebuild"
    _session_todos_cache.pop(session_id, None)

    # Historical items: older message without todos, then newer message with todos
    old_item = ConversationItem(
        id="item_1",
        conversation_id=session_id,
        type="message",
        response_id="resp_1",
        created_at=100.0,
        status="completed",
        data=MessageData(
            type="message",
            role="assistant",
            agent="test-agent",
            content=[{"type": "output_text", "text": "Old message"}],
        ),
    )
    new_item = ConversationItem(
        id="item_2",
        conversation_id=session_id,
        type="message",
        response_id="resp_2",
        created_at=200.0,
        status="completed",
        data=MessageData(
            type="message",
            role="assistant",
            agent="test-agent",
            content=[
                {
                    "type": "output_text",
                    "text": "Latest reply.\n\n## To-do\n- [x] Step 1\n- [ ] Step 2",
                }
            ],
        ),
    )

    todos = _rebuild_session_todos_from_history(session_id, items=[old_item, new_item])
    assert todos == [
        {"content": "Step 1", "status": "completed", "activeForm": ""},
        {"content": "Step 2", "status": "pending", "activeForm": ""},
    ]
    assert _session_todos_cache.get(session_id) == todos

    _session_todos_cache.pop(session_id, None)


def test_build_session_response_rebuilds_todos_on_cache_miss() -> None:
    from omnigent.entities import Conversation, ConversationItem, MessageData
    from omnigent.server.routes._sessions.common import _session_todos_cache
    from omnigent.server.routes._sessions.orchestration import _build_session_response

    session_id = "conv_test_snapshot_rebuild"
    _session_todos_cache.pop(session_id, None)

    conv = Conversation(
        id=session_id,
        root_conversation_id=session_id,
        agent_id="test-agent",
        created_at=100,
        updated_at=200,
        parent_conversation_id=None,
        kind="default",
    )
    item = ConversationItem(
        id="item_1",
        conversation_id=session_id,
        type="message",
        response_id="resp_1",
        created_at=150.0,
        status="completed",
        data=MessageData(
            type="message",
            role="assistant",
            agent="test-agent",
            content=[
                {
                    "type": "output_text",
                    "text": "Here is the plan:\n\n## To-do\n- [ ] Task A",
                }
            ],
        ),
    )

    resp = _build_session_response(
        conv,
        [item],
        "idle",
        agent_name="test-agent",
    )
    assert resp.todos == [{"content": "Task A", "status": "pending", "activeForm": ""}]
    assert _session_todos_cache.get(session_id) == resp.todos

    _session_todos_cache.pop(session_id, None)

