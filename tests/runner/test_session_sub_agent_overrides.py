"""The runner half of the per-sub-agent harness/model pick.

Three seams, each of which was empty at some point while the feature looked
finished from the outside: the init envelope that carries the picks to a
reconnecting runner, the per-session registry the dispatcher reads them from,
and the blob parser that must never raise.
"""

from __future__ import annotations

from omnigent.entities import Conversation
from omnigent.runner.app import (
    _parse_sub_harness_override,
    forget_session_sub_agent_overrides,
    note_session_sub_agent_overrides,
    session_sub_agent_effort,
    session_sub_agent_harness,
    session_sub_agent_model,
)
from omnigent.runner.session_init_protocol import (
    build_runner_session_init_payload,
    parse_runner_session_init_envelope,
)


def _conversation(**overrides: object) -> Conversation:
    """Build a minimal bound conversation.

    :param overrides: Fields to set on it, e.g. ``sub_harness_override``.
    :returns: A conversation the init-payload builder accepts.
    """
    return Conversation(
        id="conv_team",
        created_at=10,
        updated_at=11,
        root_conversation_id="conv_team",
        agent_id="agent_team",
        **overrides,  # type: ignore[arg-type]
    )


def test_init_envelope_carries_the_sub_agent_picks() -> None:
    """The envelope ships what the session stored, verbatim.

    The declared field was there from the start and the builder never filled
    it, so a reconnecting runner read ``None`` and dispatched the bundle's
    declared team. Stored as the compact JSON string, not a nested object, so
    a runner that predates the field round-trips it untouched.
    """
    conversation = _conversation(
        sub_harness_override='{"gpt":"antigravity-native"}',
        sub_model_override='{"gpt":"gemini-3.8-flash-low"}',
        sub_effort_override='{"claude":"high"}',
    )
    envelope = parse_runner_session_init_envelope(
        build_runner_session_init_payload(conversation, server_version="0.6.0.dev0")
    )
    assert envelope is not None
    assert envelope.snapshot.sub_harness_override == '{"gpt":"antigravity-native"}'
    assert envelope.snapshot.sub_model_override == '{"gpt":"gemini-3.8-flash-low"}'
    assert envelope.snapshot.sub_effort_override == '{"claude":"high"}'


def test_init_envelope_omits_picks_a_session_never_made() -> None:
    """No pick reads back as ``None``, not as an empty team."""
    envelope = parse_runner_session_init_envelope(
        build_runner_session_init_payload(_conversation(), server_version="0.6.0.dev0")
    )
    assert envelope is not None
    assert envelope.snapshot.sub_harness_override is None
    assert envelope.snapshot.sub_model_override is None
    assert envelope.snapshot.sub_effort_override is None


def test_registry_answers_per_head() -> None:
    """A pick is addressed by the head's declared name, and only that head."""
    note_session_sub_agent_overrides(
        "conv_reg",
        harnesses={"gpt": "antigravity-native"},
        models={"gpt": "gemini-3.8-flash-low"},
        efforts={"gpt": "high"},
    )
    try:
        assert session_sub_agent_harness("conv_reg", "gpt") == "antigravity-native"
        assert session_sub_agent_model("conv_reg", "gpt") == "gemini-3.8-flash-low"
        assert session_sub_agent_effort("conv_reg", "gpt") == "high"
        # An untouched head keeps whatever its spec declares.
        assert session_sub_agent_harness("conv_reg", "claude") is None
        assert session_sub_agent_model("conv_reg", "claude") is None
        assert session_sub_agent_effort("conv_reg", "claude") is None
        # As does an unrelated session.
        assert session_sub_agent_harness("conv_other", "gpt") is None
    finally:
        forget_session_sub_agent_overrides("conv_reg")
    assert session_sub_agent_harness("conv_reg", "gpt") is None


def test_absent_picks_do_not_clear_remembered_ones() -> None:
    """A forward that omits the field means "unchanged", not "cleared".

    The server omits both keys for every session that picked nothing, and a
    single such body must not wipe what the session-init envelope taught the
    runner one message earlier.
    """
    note_session_sub_agent_overrides("conv_keep", harnesses={"gpt": "pi"}, models=None)
    try:
        note_session_sub_agent_overrides("conv_keep", harnesses=None, models=None)
        assert session_sub_agent_harness("conv_keep", "gpt") == "pi"
    finally:
        forget_session_sub_agent_overrides("conv_keep")


def test_unreadable_blob_falls_back_to_the_declared_team() -> None:
    """A malformed pick is ignored, never raised.

    Refusing to spawn over an unreadable preference would be worse than
    running what the bundle declares, which is the behaviour before the field
    existed.
    """
    assert _parse_sub_harness_override(None) is None
    assert _parse_sub_harness_override("") is None
    assert _parse_sub_harness_override("not json") is None
    assert _parse_sub_harness_override('["a","list"]') is None
    # Non-string values are dropped rather than coerced -- a harness name is
    # a string, and stringifying whatever arrived would invent one.
    assert _parse_sub_harness_override('{"gpt":"pi","bad":5}') == {"gpt": "pi"}
