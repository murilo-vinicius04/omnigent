"""
Tests for server-side spoken summary generation and persistence.

Validates that when enabled on completed top-level turns with responses > 320 chars,
a short speech-friendly summary is attached to MessageData.content as a second part:
    {"type": "spoken_summary", "text": "...", "lang": "..."}
while preserving content[0] ("output_text") completely unchanged.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest

from omnigent.entities import Conversation, ConversationItem
from omnigent.server.routes._sessions.helpers import _flush_relay_text
from omnigent.server.spoken_summary import (
    clamp_sentences,
    detect_bcp47_language,
    generate_spoken_summary,
    resolve_spoken_summary_model,
    resolve_spoken_summary_settings,
    should_generate_spoken_summary,
    strip_markdown_for_speech,
)
from omnigent.stores.conversation_store.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)

_LONG_RESPONSE_TEXT = (
    "I have analyzed the database migration scripts and resolved the connection pool "
    "exhaustion issue. The problem was caused by unclosed connections in the background "
    "worker loop when handling retryable timeout errors. I updated the connection pool "
    "configuration to enforce strict lease lifetimes, added automatic connection cleanup "
    "in the exception handlers, and verified the fix with load testing under 500 concurrent "
    "transactions. All unit and integration test suites are now passing cleanly."
)


# ── Fakes and Stubs ───────────────────────────────────────────────────


class MockBlock:
    """Mock LLM response content block."""

    def __init__(self, text: str) -> None:
        self.text = text


class MockUsage:
    """Mock LLM token usage object."""

    def __init__(self, input_tokens: int = 20, output_tokens: int = 30) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = input_tokens + output_tokens


class MockResponse:
    """Mock LLM create response."""

    def __init__(
        self,
        text: str,
        input_tokens: int = 20,
        output_tokens: int = 30,
    ) -> None:
        self.text = text
        self.output = [MockBlock(text)]
        self.usage = MockUsage(input_tokens, output_tokens)


class MockLLMClient:
    """Mock LLM client capturing calls and returning scripted responses."""

    def __init__(
        self,
        response_text: str = "Connection leak was fixed by updating pool leases. All tests pass.",
        *,
        should_fail: bool = False,
        delay_s: float = 0.0,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response_text = response_text
        self.should_fail = should_fail
        self.delay_s = delay_s
        self.responses = self

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.delay_s > 0:
            await asyncio.sleep(self.delay_s)
        if self.should_fail:
            raise RuntimeError("LLM service unavailable (500)")
        return MockResponse(self.response_text)


@dataclass
class _FakeConversationStore:
    """In-memory stub capturing appended items and conversation lookups."""

    conversation: Conversation | None = None
    appended: list[Any] = field(default_factory=list)
    project_config: dict[str, Any] = field(default_factory=dict)
    usage_increments: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        if self.conversation is not None:
            return self.conversation
        return Conversation(
            id=conversation_id,
            created_at=1,
            updated_at=1,
            root_conversation_id=conversation_id,
            parent_conversation_id=None,
            kind="default",
        )

    def get_project_config(self, project_id: str) -> dict[str, Any]:
        return self.project_config

    def append(self, conversation_id: str, items: list[Any]) -> list[ConversationItem]:
        result = []
        for i, item in enumerate(items):
            self.appended.append(item)
            result.append(
                ConversationItem(
                    id=f"item_{i}",
                    type=item.type,
                    response_id=item.response_id,
                    data=item.data,
                    created_at=1,
                    status="completed",
                )
            )
        return result

    def increment_session_usage(
        self, conversation_id: str, delta: dict[str, Any]
    ) -> dict[str, Any]:
        self.usage_increments.append((conversation_id, delta))
        return delta


# ── Unit tests for spoken summary utilities ──────────────────────────


def test_strip_markdown_for_speech() -> None:
    """Markdown fences, links, headings, and formatting are stripped."""
    raw = (
        "## Title Summary\n\n"
        "Here is `code_snippet()` in inline code.\n\n"
        "```python\ndef test(): pass\n```\n\n"
        "Check [our docs](https://example.com/docs) for more details.\n"
        "| Col1 | Col2 |\n|---|---|\n| val1 | val2 |\n"
        "- Bullet point 1\n* Bullet point 2\n"
        "**Bold** and *italic* text."
    )
    cleaned = strip_markdown_for_speech(raw)
    assert "```python" not in cleaned
    assert "https://example.com" not in cleaned
    assert "our docs" in cleaned
    assert "Bold and italic text" in cleaned
    assert "def test" not in cleaned


def test_clamp_sentences() -> None:
    """Sentence clamping restricts text to at most max_sentences."""
    four_sentences = "First sentence. Second sentence! Third sentence? Fourth sentence."
    clamped = clamp_sentences(four_sentences, max_sentences=3)
    assert clamped == "First sentence. Second sentence! Third sentence?"

    one_sentence = "Just one sentence."
    assert clamp_sentences(one_sentence, max_sentences=3) == "Just one sentence."


def test_detect_bcp47_language() -> None:
    """Language detection accurately recognizes common spoken languages."""
    en_text = "The migration finished and all automated tests passed successfully."
    assert detect_bcp47_language(en_text) == "en-US"

    pt_text = "A migração foi concluída com sucesso e todos os testes passaram."
    assert detect_bcp47_language(pt_text) == "pt-BR"

    es_text = "La migración se completó con éxito y todas las pruebas pasaron."
    assert detect_bcp47_language(es_text) == "es-ES"

    fr_text = "La migration est terminée avec succès et tous les tests ont réussi."
    assert detect_bcp47_language(fr_text) == "fr-FR"

    de_text = "Die Migration wurde erfolgreich abgeschlossen und alle Tests bestanden."
    assert detect_bcp47_language(de_text) == "de-DE"


def test_resolve_spoken_summary_model() -> None:
    """Model fallback defaults to configured constant without hardcoding."""
    assert resolve_spoken_summary_model(None) != ""
    assert resolve_spoken_summary_model("custom/model") == "custom/model"


def test_resolve_spoken_summary_settings() -> None:
    """Settings resolve from overrides, labels, project config, or env fallback."""
    conv = Conversation(
        id="conv_1",
        root_conversation_id="conv_1",
        created_at=1,
        updated_at=1,
        labels={"spoken_summary_enabled": "true", "spoken_summary_language": "es-ES"},
    )
    enabled, lang = resolve_spoken_summary_settings(conv, None)
    assert enabled is True
    assert lang == "es-ES"

    # Override takes precedence
    enabled, lang = resolve_spoken_summary_settings(conv, None, override_enabled=False)
    assert enabled is False


def test_should_generate_spoken_summary_matrix() -> None:
    """Verify all four gating conditions strictly."""
    conv = Conversation(
        id="conv_1",
        root_conversation_id="conv_1",
        created_at=1,
        updated_at=1,
        parent_conversation_id=None,
        kind="default",
    )
    # Valid top-level turn
    assert (
        should_generate_spoken_summary(
            conv,
            _LONG_RESPONSE_TEXT,
            is_terminal_completion=True,
            deny_reason=None,
            enabled=True,
        )
        is True
    )

    # Disabled
    assert (
        should_generate_spoken_summary(
            conv,
            _LONG_RESPONSE_TEXT,
            is_terminal_completion=True,
            deny_reason=None,
            enabled=False,
        )
        is False
    )

    # Short text
    assert (
        should_generate_spoken_summary(
            conv,
            "Short text",
            is_terminal_completion=True,
            deny_reason=None,
            enabled=True,
        )
        is False
    )

    # Policy deny
    assert (
        should_generate_spoken_summary(
            conv,
            _LONG_RESPONSE_TEXT,
            is_terminal_completion=True,
            deny_reason="deny",
            enabled=True,
        )
        is False
    )

    # Not terminal completion
    assert (
        should_generate_spoken_summary(
            conv,
            _LONG_RESPONSE_TEXT,
            is_terminal_completion=False,
            deny_reason=None,
            enabled=True,
        )
        is False
    )

    # Child session
    child_conv = Conversation(
        id="conv_child",
        root_conversation_id="conv_1",
        created_at=1,
        updated_at=1,
        parent_conversation_id="conv_1",
        kind="sub_agent",
    )
    assert (
        should_generate_spoken_summary(
            child_conv,
            _LONG_RESPONSE_TEXT,
            is_terminal_completion=True,
            deny_reason=None,
            enabled=True,
        )
        is False
    )


# ── Case 1: Summary attached on completed top-level turn when enabled ──


@pytest.mark.asyncio
async def test_summary_attached_on_completed_top_level_turn_when_enabled() -> None:
    """
    On a completed top-level turn with enabled spoken summary and long text,
    a spoken summary part is attached after output_text and tokens are attributed.
    """
    client = MockLLMClient("The database migration succeeded and all tests are passing.")
    conv = Conversation(
        id="conv_top_1",
        root_conversation_id="conv_top_1",
        created_at=1,
        updated_at=1,
        parent_conversation_id=None,
        kind="default",
    )
    store = _FakeConversationStore(conversation=conv)
    text_acc = [_LONG_RESPONSE_TEXT]

    await _flush_relay_text(
        store,  # type: ignore[arg-type]
        "conv_top_1",
        text_acc,
        "resp_1",
        "test-agent",
        is_terminal_completion=True,
        spoken_summary_enabled=True,
        llm_client=client,
    )

    assert len(store.appended) == 1
    content = store.appended[0].data.content
    assert len(content) == 2

    # Wire contract: content[0] is output_text, byte-identical
    assert content[0]["type"] == "output_text"
    assert content[0]["text"] == _LONG_RESPONSE_TEXT

    # Wire contract: content[1] is spoken_summary
    assert content[1]["type"] == "spoken_summary"
    assert content[1]["text"] == "The database migration succeeded and all tests are passing."
    assert content[1]["lang"] == "en-US"

    # LLM was invoked once
    assert len(client.calls) == 1

    # Token usage attributed
    assert len(store.usage_increments) == 1
    assert store.usage_increments[0][0] == "conv_top_1"
    usage_delta = store.usage_increments[0][1]
    assert usage_delta["input_tokens"] == 20
    assert usage_delta["output_tokens"] == 30
    assert usage_delta["total_tokens"] == 50


# ── Case 2: NO summary when session has parent (sub-session) ──────────


@pytest.mark.asyncio
async def test_no_summary_when_session_has_parent() -> None:
    """Sub-sessions, child sessions, and sub-agents NEVER get spoken summaries."""
    client = MockLLMClient()

    # Sub-agent kind
    conv_sub_agent = Conversation(
        id="conv_child_1",
        root_conversation_id="conv_parent_1",
        created_at=1,
        updated_at=1,
        parent_conversation_id="conv_parent_1",
        kind="sub_agent",
    )
    store = _FakeConversationStore(conversation=conv_sub_agent)
    text_acc = [_LONG_RESPONSE_TEXT]

    await _flush_relay_text(
        store,  # type: ignore[arg-type]
        "conv_child_1",
        text_acc,
        "resp_child",
        "sub-agent",
        is_terminal_completion=True,
        spoken_summary_enabled=True,
        llm_client=client,
    )

    assert len(client.calls) == 0
    assert len(store.appended) == 1
    content = store.appended[0].data.content
    assert len(content) == 1
    assert content[0]["type"] == "output_text"


# ── Case 3: NO summary when disabled ─────────────────────────────────


@pytest.mark.asyncio
async def test_no_summary_when_disabled() -> None:
    """When spoken summary is disabled, no model call is made and no summary attached."""
    client = MockLLMClient()
    conv = Conversation(
        id="conv_disabled",
        root_conversation_id="conv_disabled",
        created_at=1,
        updated_at=1,
        parent_conversation_id=None,
        kind="default",
    )
    store = _FakeConversationStore(conversation=conv)
    text_acc = [_LONG_RESPONSE_TEXT]

    await _flush_relay_text(
        store,  # type: ignore[arg-type]
        "conv_disabled",
        text_acc,
        "resp_disabled",
        "test-agent",
        is_terminal_completion=True,
        spoken_summary_enabled=False,
        llm_client=client,
    )

    assert len(client.calls) == 0
    assert len(store.appended) == 1
    content = store.appended[0].data.content
    assert len(content) == 1
    assert content[0]["type"] == "output_text"


# ── Case 4: NO summary on failed, cancelled turns, or policy deny ──────


@pytest.mark.asyncio
async def test_no_summary_on_failed_cancelled_or_deny() -> None:
    """Non-completed terminal events (failed, cancelled) and policy denies skip summary."""
    client = MockLLMClient()
    conv = Conversation(
        id="conv_failed",
        root_conversation_id="conv_failed",
        created_at=1,
        updated_at=1,
        parent_conversation_id=None,
        kind="default",
    )
    store = _FakeConversationStore(conversation=conv)

    # 1. Failed or cancelled turn (is_terminal_completion=False)
    text_acc = [_LONG_RESPONSE_TEXT]
    await _flush_relay_text(
        store,  # type: ignore[arg-type]
        "conv_failed",
        text_acc,
        "resp_fail",
        "test-agent",
        is_terminal_completion=False,
        spoken_summary_enabled=True,
        llm_client=client,
    )
    assert len(client.calls) == 0
    assert len(store.appended[0].data.content) == 1

    # 2. Policy deny
    store.appended.clear()
    text_acc = [_LONG_RESPONSE_TEXT]
    await _flush_relay_text(
        store,  # type: ignore[arg-type]
        "conv_failed",
        text_acc,
        "resp_deny",
        "test-agent",
        deny_reason="harmful response detected",
        is_terminal_completion=True,
        spoken_summary_enabled=True,
        llm_client=client,
    )
    assert len(client.calls) == 0
    persisted_text = store.appended[0].data.content[0]["text"]
    assert "[Denied by policy: harmful response detected]" in persisted_text


# ── Case 5: Short-response threshold skip (<= 320 chars) ──────────────


@pytest.mark.asyncio
async def test_short_response_threshold_skip() -> None:
    """Responses <= 320 chars are not summarized because they are already concise."""
    client = MockLLMClient()
    conv = Conversation(
        id="conv_short",
        root_conversation_id="conv_short",
        created_at=1,
        updated_at=1,
        parent_conversation_id=None,
        kind="default",
    )
    store = _FakeConversationStore(conversation=conv)
    short_text = "All unit tests pass and code is formatted cleanly."
    assert len(short_text) <= 320

    text_acc = [short_text]
    await _flush_relay_text(
        store,  # type: ignore[arg-type]
        "conv_short",
        text_acc,
        "resp_short",
        "test-agent",
        is_terminal_completion=True,
        spoken_summary_enabled=True,
        llm_client=client,
    )

    assert len(client.calls) == 0
    assert len(store.appended) == 1
    content = store.appended[0].data.content
    assert len(content) == 1
    assert content[0]["text"] == short_text


# ── Case 6: Rewrite failure or timeout ships message intact ───────────


@pytest.mark.asyncio
async def test_rewrite_failure_still_ships_intact() -> None:
    """When the summary model call raises or times out, message ships with output_text intact."""
    failing_client = MockLLMClient(should_fail=True)
    conv = Conversation(
        id="conv_err",
        root_conversation_id="conv_err",
        created_at=1,
        updated_at=1,
        parent_conversation_id=None,
        kind="default",
    )
    store = _FakeConversationStore(conversation=conv)
    text_acc = [_LONG_RESPONSE_TEXT]

    await _flush_relay_text(
        store,  # type: ignore[arg-type]
        "conv_err",
        text_acc,
        "resp_err",
        "test-agent",
        is_terminal_completion=True,
        spoken_summary_enabled=True,
        llm_client=failing_client,
    )

    assert len(failing_client.calls) == 1
    assert len(store.appended) == 1
    content = store.appended[0].data.content
    assert len(content) == 1
    assert content[0]["type"] == "output_text"
    assert content[0]["text"] == _LONG_RESPONSE_TEXT


@pytest.mark.asyncio
async def test_rewrite_timeout_still_ships_intact() -> None:
    """Timeout during rewrite fails open without blocking or modifying the message."""
    slow_client = MockLLMClient(delay_s=0.5)
    with patch("omnigent.server.spoken_summary.SPOKEN_SUMMARY_TIMEOUT_S", 0.05):
        summary_part, usage = await generate_spoken_summary(
            _LONG_RESPONSE_TEXT,
            llm_client=slow_client,
            timeout_s=0.05,
        )
    assert summary_part is None
    assert usage is None


# ── Case 7: Explicit language setting forces summary language tag ──────


@pytest.mark.asyncio
async def test_explicit_language_forces_language_tag() -> None:
    """Explicit spoken_summary_language overrides auto-detection."""
    client = MockLLMClient("A migração foi concluída com sucesso.")
    conv = Conversation(
        id="conv_lang",
        root_conversation_id="conv_lang",
        created_at=1,
        updated_at=1,
        parent_conversation_id=None,
        kind="default",
    )
    store = _FakeConversationStore(conversation=conv)
    text_acc = [_LONG_RESPONSE_TEXT]

    await _flush_relay_text(
        store,  # type: ignore[arg-type]
        "conv_lang",
        text_acc,
        "resp_lang",
        "test-agent",
        is_terminal_completion=True,
        spoken_summary_enabled=True,
        spoken_summary_language="pt-BR",
        llm_client=client,
    )

    content = store.appended[0].data.content
    assert len(content) == 2
    assert content[1]["type"] == "spoken_summary"
    assert content[1]["lang"] == "pt-BR"
    # Prompt contains Portuguese instruction
    prompt_sent = client.calls[0]["input"][0]["content"]
    assert "pt-BR" in prompt_sent


# ── Case 8: Round-trip persistence in SqlAlchemyConversationStore ─────


@pytest.mark.asyncio
async def test_roundtrip_persistence_in_sqlalchemy_store(db_uri: str) -> None:
    """
    Spoken summary round-trips through real SqlAlchemyConversationStore.
    Clients reading only output_text or content[0] are completely unaffected.
    """
    store = SqlAlchemyConversationStore(db_uri)
    conv = store.create_conversation()
    session_id = conv.id

    client = MockLLMClient("Summary: migration complete, database connections stabilized.")
    text_acc = [_LONG_RESPONSE_TEXT]

    await _flush_relay_text(
        store,
        session_id,
        text_acc,
        "resp_db",
        "test-agent",
        is_terminal_completion=True,
        spoken_summary_enabled=True,
        llm_client=client,
    )

    # Reload from database
    items = store.list_items(session_id).data
    assert len(items) == 1
    item = items[0]
    data = item.data
    assert data.role == "assistant"
    assert len(data.content) == 2

    # Wire contract: content[0] is unchanged output_text
    output_part = data.content[0]
    assert output_part["type"] == "output_text"
    assert output_part["text"] == _LONG_RESPONSE_TEXT

    # Wire contract: content[1] is spoken_summary
    spoken_part = data.content[1]
    assert spoken_part["type"] == "spoken_summary"
    assert spoken_part["text"] == "Summary: migration complete, database connections stabilized."
    assert spoken_part["lang"] == "en-US"

    # API dict serialization preserves both blocks
    api_dict = item.to_api_dict()
    assert api_dict["content"][0]["type"] == "output_text"
    assert api_dict["content"][1]["type"] == "spoken_summary"

    # Client reading only output_text is completely unaffected
    legacy_client_text = next(
        b["text"] for b in api_dict["content"] if b.get("type") == "output_text"
    )
    assert legacy_client_text == _LONG_RESPONSE_TEXT

    # Verify session usage persistence
    updated_conv = store.get_conversation(session_id)
    assert updated_conv is not None
    assert updated_conv.session_usage.get("total_tokens") == 50


@pytest.mark.asyncio
async def test_project_config_enables_spoken_summary() -> None:
    """Project configuration enables spoken summary and sets language without caller overrides."""
    client = MockLLMClient("Résumé en français de l'intervention.")
    conv = Conversation(
        id="conv_proj_1",
        root_conversation_id="conv_proj_1",
        created_at=1,
        updated_at=1,
        project_id="proj_alpha",
        parent_conversation_id=None,
        kind="default",
    )
    store = _FakeConversationStore(
        conversation=conv,
        project_config={
            "spoken_summary_enabled": True,
            "spoken_summary_language": "fr-FR",
        },
    )
    text_acc = [_LONG_RESPONSE_TEXT]

    await _flush_relay_text(
        store,  # type: ignore[arg-type]
        "conv_proj_1",
        text_acc,
        "resp_proj",
        "test-agent",
        is_terminal_completion=True,
        # No explicit overrides — must resolve from project config
        llm_client=client,
    )

    assert len(store.appended) == 1
    content = store.appended[0].data.content
    assert len(content) == 2
    assert content[1]["type"] == "spoken_summary"
    assert content[1]["lang"] == "fr-FR"


@pytest.mark.asyncio
async def test_conversation_label_enables_spoken_summary() -> None:
    """Conversation labels can enable spoken summary per-session."""
    client = MockLLMClient("Resumo falado em português.")
    conv = Conversation(
        id="conv_lbl_1",
        root_conversation_id="conv_lbl_1",
        created_at=1,
        updated_at=1,
        parent_conversation_id=None,
        kind="default",
        labels={
            "spoken_summary_enabled": "true",
            "spoken_summary_language": "pt-BR",
        },
    )
    store = _FakeConversationStore(conversation=conv)
    text_acc = [_LONG_RESPONSE_TEXT]

    await _flush_relay_text(
        store,  # type: ignore[arg-type]
        "conv_lbl_1",
        text_acc,
        "resp_lbl",
        "test-agent",
        is_terminal_completion=True,
        llm_client=client,
    )

    assert len(store.appended) == 1
    content = store.appended[0].data.content
    assert len(content) == 2
    assert content[1]["type"] == "spoken_summary"
    assert content[1]["lang"] == "pt-BR"


@pytest.mark.asyncio
async def test_subagent_with_parent_session_id_produces_no_summary_and_zero_calls() -> None:
    """Sub-agent session with parent_session_id or parent_conversation_id
    makes ZERO model calls.
    """
    client = MockLLMClient()
    conv = Conversation(
        id="conv_sub_2",
        root_conversation_id="conv_root",
        created_at=1,
        updated_at=1,
        parent_conversation_id="conv_root",
        kind="sub_agent",
    )
    # Also attach parent_session_id attribute if present
    conv.parent_session_id = "conv_root"  # type: ignore[attr-defined]
    store = _FakeConversationStore(
        conversation=conv,
        project_config={"spoken_summary_enabled": True, "spoken_summary_language": "pt-BR"},
    )
    text_acc = [_LONG_RESPONSE_TEXT]

    await _flush_relay_text(
        store,  # type: ignore[arg-type]
        "conv_sub_2",
        text_acc,
        "resp_sub",
        "sub-agent",
        is_terminal_completion=True,
        llm_client=client,
    )

    assert len(client.calls) == 0
    assert len(store.appended) == 1
    content = store.appended[0].data.content
    assert len(content) == 1
    assert content[0]["type"] == "output_text"


@pytest.mark.asyncio
async def test_nested_project_config_language_passthrough_verbatim() -> None:
    """Nested project config spoken_summary dictionary passes language through verbatim."""
    client = MockLLMClient("Resumo da tarefa concluída.")
    conv = Conversation(
        id="conv_nested_proj",
        root_conversation_id="conv_nested_proj",
        created_at=1,
        updated_at=1,
        project_id="proj_nested",
        parent_conversation_id=None,
        kind="default",
    )
    store = _FakeConversationStore(
        conversation=conv,
        project_config={
            "spoken_summary": {
                "enabled": True,
                "language": "pt-BR",
            }
        },
    )
    text_acc = [_LONG_RESPONSE_TEXT]

    await _flush_relay_text(
        store,  # type: ignore[arg-type]
        "conv_nested_proj",
        text_acc,
        "resp_nested",
        "test-agent",
        is_terminal_completion=True,
        llm_client=client,
    )

    assert len(client.calls) == 1
    assert len(store.appended) == 1
    content = store.appended[0].data.content
    assert len(content) == 2
    assert content[1]["type"] == "spoken_summary"
    assert content[1]["lang"] == "pt-BR"
    assert content[1]["text"] == "Resumo da tarefa concluída."


@pytest.mark.asyncio
async def test_disabled_project_config_makes_zero_model_calls() -> None:
    """When spoken summary is disabled in project config, exactly ZERO model calls are made."""
    client = MockLLMClient()
    conv = Conversation(
        id="conv_off",
        root_conversation_id="conv_off",
        created_at=1,
        updated_at=1,
        project_id="proj_off",
        parent_conversation_id=None,
        kind="default",
    )
    store = _FakeConversationStore(
        conversation=conv,
        project_config={
            "spoken_summary_enabled": False,
            "spoken_summary_language": "pt-BR",
        },
    )
    text_acc = [_LONG_RESPONSE_TEXT]

    await _flush_relay_text(
        store,  # type: ignore[arg-type]
        "conv_off",
        text_acc,
        "resp_off",
        "test-agent",
        is_terminal_completion=True,
        llm_client=client,
    )

    assert len(client.calls) == 0
    assert len(store.appended) == 1
    content = store.appended[0].data.content
    assert len(content) == 1
    assert content[0]["type"] == "output_text"
