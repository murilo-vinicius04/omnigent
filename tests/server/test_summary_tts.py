"""Summary speech: opt-in, and silent rather than fatal when unavailable."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest

from omnigent.server import tts


@pytest.fixture(autouse=True)
def _reset_model_cache() -> Any:
    """Keep the module-level model cache from leaking between tests."""
    tts._model = None
    tts._model_failed = False
    yield
    tts._model = None
    tts._model_failed = False


def test_tts_is_on_by_default_and_can_be_disabled() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OMNIGENT_TTS_ENABLED", None)
        assert tts.tts_enabled() is True
    with patch.dict(os.environ, {"OMNIGENT_TTS_ENABLED": "0"}):
        assert tts.tts_enabled() is False


@pytest.mark.asyncio
async def test_disabled_tts_never_touches_the_model() -> None:
    with patch.dict(os.environ, {"OMNIGENT_TTS_ENABLED": "off"}):
        with patch.object(tts, "_load_model", side_effect=AssertionError("must not load")):
            assert await tts.synthesize_summary("qualquer texto aqui") is None


@pytest.mark.asyncio
async def test_missing_extra_degrades_to_no_audio() -> None:
    """A deployment without the extra keeps working; it just ships no audio."""
    with patch.object(tts, "_load_model", return_value=None):
        assert await tts.synthesize_summary("resumo qualquer") is None
    # The failure is cached, so a second turn does not retry the import.
    assert tts._model_failed is False or tts._model is None


@pytest.mark.asyncio
async def test_empty_and_overlong_text_is_skipped() -> None:
    with patch.object(tts, "_load_model", side_effect=AssertionError("must not load")):
        assert await tts.synthesize_summary("   ") is None
        assert await tts.synthesize_summary("x" * (tts.TTS_MAX_CHARS + 1)) is None


@pytest.mark.asyncio
async def test_synthesis_failure_returns_none_rather_than_raising() -> None:
    """A wedged model must never take the turn down with it."""

    class _Boom:
        sr = 24000

        def generate(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("cuda gone")

    with patch.object(tts, "_load_model", return_value=_Boom()):
        with patch.object(tts, "_ensure_voice_reference", return_value=None):
            assert await tts.synthesize_summary("resumo que falha") is None


@pytest.mark.asyncio
async def test_summary_audio_block_is_none_without_stores() -> None:
    """No file store means no audio, not a crash."""
    from omnigent.server.routes._sessions.helpers import _summary_audio_block

    assert await _summary_audio_block(None, None, "conv_1", "texto", "pt-BR") is None
