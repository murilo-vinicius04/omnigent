"""The inbound pass has two jobs: translate, or repair what dictation mis-heard."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest

from omnigent.server.inbound_translation import (
    build_inbound_repair_prompt,
    build_inbound_translation_prompt,
    inbound_pass_enabled,
    inbound_translation_enabled,
    reader_writes_english,
    translate_inbound_message,
)


def test_an_english_reader_still_goes_through_the_pass() -> None:
    """Skipping English lost the dictation repair the layer already knew how to do.

    Speech-to-text mis-hears words the surrounding sentence settles ("meet
    Gemini" for "keep Gemini"), and the answering model should never see the
    mis-hearing -- whichever language the reader writes in.
    """
    assert inbound_pass_enabled("en-US") is True
    assert inbound_pass_enabled("pt-BR") is True
    # Translation proper still only applies to a reader not writing English.
    assert inbound_translation_enabled("en-US") is False
    assert inbound_translation_enabled("pt-BR") is True


def test_an_unset_language_skips_the_pass() -> None:
    """With no reader language there is nothing to trust about the message."""
    for tag in ("", "   ", "auto", None):
        assert inbound_pass_enabled(tag) is False


def test_the_kill_switch_still_disables_everything() -> None:
    with patch.dict(os.environ, {"OMNIGENT_INBOUND_TRANSLATION_ENABLED": "0"}):
        assert inbound_pass_enabled("pt-BR") is False
        assert inbound_pass_enabled("en-US") is False


def test_english_tags_are_recognised() -> None:
    for tag in ("en", "en-US", "EN-GB", " en-us "):
        assert reader_writes_english(tag) is True
    for tag in ("pt-BR", "es", "", None):
        assert reader_writes_english(tag) is False


def test_the_repair_prompt_never_asks_for_a_translation() -> None:
    """A repair that rewrote into another language would be a silent regression."""
    prompt = build_inbound_repair_prompt("oi", "DELIM")
    assert "English" not in prompt
    assert "speech-to-text" in prompt
    # It must protect the things that are never mis-hearings.
    for kept in ("code", "commands", "file paths", "identifiers", "URLs"):
        assert kept in prompt
    assert "unchanged" in prompt


@pytest.mark.asyncio
async def test_an_english_message_takes_the_repair_prompt() -> None:
    seen: list[str] = []

    async def _capture(prompt: str, **kwargs: Any) -> str:
        seen.append(prompt)
        return "repaired text"

    with patch("omnigent.server.spoken_summary.run_agy_prompt", _capture):
        await translate_inbound_message("keep gemini", source_language="en-US")
    assert seen and "Repair the message below" in seen[0]
    assert "Restate the message below in English" not in seen[0]


@pytest.mark.asyncio
async def test_a_portuguese_message_still_takes_the_translation_prompt() -> None:
    seen: list[str] = []

    async def _capture(prompt: str, **kwargs: Any) -> str:
        seen.append(prompt)
        return "translated text"

    with patch("omnigent.server.spoken_summary.run_agy_prompt", _capture):
        await translate_inbound_message("mantem o gemini", source_language="pt-BR")
    assert seen and "Restate the message below in English" in seen[0]


def test_the_translation_prompt_is_untouched() -> None:
    prompt = build_inbound_translation_prompt("oi", "pt-BR", "DELIM")
    assert "Restate the message below in English" in prompt
    assert "pt-BR" in prompt
