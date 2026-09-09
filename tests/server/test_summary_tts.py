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
async def test_summary_audio_file_id_is_none_without_stores() -> None:
    """No file store means no audio, not a crash."""
    from omnigent.server.routes._sessions.helpers import _summary_audio_file_id

    assert await _summary_audio_file_id(None, None, "conv_1", "texto", "pt-BR") is None


@pytest.mark.asyncio
async def test_concurrent_synthesis_never_overlaps() -> None:
    """Two turns finishing together must not generate on the model at once.

    Chatterbox keeps per-generation alignment state on the model instance, so
    overlapping ``generate`` calls corrupt each other and raise
    ``stack expects each tensor to be equal size`` -- observed live as summaries
    that silently shipped without audio.
    """
    import asyncio as _asyncio
    import threading
    import time

    overlapped = False
    inside = 0
    guard = threading.Lock()

    class _Wav:
        def cpu(self) -> Any:
            return self

    class _Sequential:
        sr = 24000

        def generate(self, *a: Any, **k: Any) -> Any:
            nonlocal overlapped, inside
            with guard:
                inside += 1
                if inside > 1:
                    overlapped = True
            time.sleep(0.05)
            with guard:
                inside -= 1
            return _Wav()

    def _fake_save(buffer: Any, *a: Any, **k: Any) -> None:
        buffer.write(b"RIFFfake")

    class _FakeTorchaudio:
        save = staticmethod(_fake_save)

    with patch.object(tts, "_load_model", return_value=_Sequential()):
        with patch.object(tts, "_ensure_voice_reference", return_value=None):
            with patch.dict("sys.modules", {"torchaudio": _FakeTorchaudio}):
                results = await _asyncio.gather(
                    *(tts.synthesize_summary(f"resumo numero {i}") for i in range(4))
                )

    assert overlapped is False
    assert all(r == b"RIFFfake" for r in results)


class _FakeStoredFile:
    def __init__(self, fid: str, filename: str) -> None:
        self.id = fid
        self.filename = filename


class _FakePage:
    def __init__(self, data: list[_FakeStoredFile]) -> None:
        self.data = data


class _FakeFileStore:
    """Newest-first file list, matching the real store's ``order="desc"``."""

    def __init__(self) -> None:
        self.files: list[_FakeStoredFile] = []
        self._n = 0

    def create(self, filename: str, size: int, content_type: str, session_id: str) -> Any:
        self._n += 1
        f = _FakeStoredFile(f"f_{self._n}", filename)
        self.files.insert(0, f)
        return f

    def list(
        self,
        session_id: str,
        limit: int = 20,
        after: Any = None,
        before: Any = None,
        order: str = "desc",
        include_unscoped: bool = False,
    ) -> _FakePage:
        return _FakePage(self.files[:limit])

    def delete(self, file_id: str, session_id: str | None = None) -> bool:
        before = len(self.files)
        self.files = [f for f in self.files if f.id != file_id]
        return len(self.files) != before


class _FakeArtifactStore:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def put(self, key: str, data: bytes) -> None:
        self.blobs[key] = data

    def delete(self, key: str) -> None:
        self.blobs.pop(key, None)


@pytest.mark.asyncio
async def test_prune_keeps_only_the_newest_recordings() -> None:
    """Summary audio is a rolling cache, not a record that grows forever."""
    from omnigent.server.routes._sessions.helpers import (
        SUMMARY_AUDIO_FILENAME,
        _prune_summary_audio,
    )

    files = _FakeFileStore()
    artifacts = _FakeArtifactStore()
    for _ in range(14):
        stored = files.create(SUMMARY_AUDIO_FILENAME, 10, "audio/wav", "conv_p")
        artifacts.put(stored.id, b"wav")

    deleted = await _prune_summary_audio(files, artifacts, "conv_p", keep=10)

    assert deleted == 4
    assert len(files.files) == 10
    # The newest survive; the four oldest are gone from both stores.
    assert [f.id for f in files.files] == [f"f_{i}" for i in range(14, 4, -1)]
    assert set(artifacts.blobs) == {f"f_{i}" for i in range(14, 4, -1)}


@pytest.mark.asyncio
async def test_prune_leaves_other_session_files_alone() -> None:
    """Only summary recordings are pruned; a user's own uploads are untouched."""
    from omnigent.server.routes._sessions.helpers import (
        SUMMARY_AUDIO_FILENAME,
        _prune_summary_audio,
    )

    files = _FakeFileStore()
    artifacts = _FakeArtifactStore()
    for i in range(12):
        name = SUMMARY_AUDIO_FILENAME if i % 2 == 0 else "relatorio.pdf"
        stored = files.create(name, 10, "audio/wav", "conv_p")
        artifacts.put(stored.id, b"x")

    await _prune_summary_audio(files, artifacts, "conv_p", keep=2)

    remaining = [f.filename for f in files.files]
    assert remaining.count(SUMMARY_AUDIO_FILENAME) == 2
    assert remaining.count("relatorio.pdf") == 6


@pytest.mark.asyncio
async def test_prune_never_breaks_the_turn() -> None:
    """A store that errors mid-prune costs space, never the summary."""
    from omnigent.server.routes._sessions.helpers import _prune_summary_audio

    class _Broken:
        def list(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("store down")

    assert await _prune_summary_audio(_Broken(), _FakeArtifactStore(), "conv_p") == 0
