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
        assert await tts.synthesize_summary("resumo que falha") is None


def test_short_summary_is_spoken_in_one_go() -> None:
    """Chunking must not fragment a summary that already fits."""
    text = "Terminei o ajuste e rodei os testes. Passou tudo, pode conferir."
    assert tts.split_for_synthesis(text) == [text]


def test_long_summary_is_split_below_the_token_ceiling() -> None:
    """Chatterbox stops at 1000 speech tokens (~40s) and truncates the rest.

    A 900-character summary -- what ``clamp_sentences`` allows on the longer
    path -- wants ~47 seconds at the pace the model actually reads, so speaking
    it in one call loses the ending. Every chunk must stay under the budget.
    """
    text = " ".join(f"Esta e a frase numero {i} do resumo mais longo." for i in range(20))
    assert len(text) > 900
    chunks = tts.split_for_synthesis(text)
    assert len(chunks) > 1
    assert all(len(c) <= tts._CHUNK_MAX_CHARS for c in chunks)
    # Nothing may be dropped on the way through.
    assert " ".join(chunks) == text


def test_a_single_overlong_sentence_is_split_rather_than_truncated() -> None:
    """No sentence boundary is not a reason to ship a cut-off summary."""
    text = "palavra " * 120
    chunks = tts.split_for_synthesis(text)
    assert all(len(c) <= tts._CHUNK_MAX_CHARS for c in chunks)
    assert "".join(c.replace(" ", "") for c in chunks) == text.replace(" ", "")


def test_voice_parameters_are_tunable_without_a_redeploy() -> None:
    with patch.dict(os.environ, {"OMNIGENT_TTS_TEMPO": "0.9"}):
        assert tts._tunable("OMNIGENT_TTS_TEMPO", tts.SPEECH_TEMPO, 0.5, 1.5) == 0.9
    # Out of range is clamped, and nonsense keeps the default rather than raising.
    with patch.dict(os.environ, {"OMNIGENT_TTS_TEMPO": "9"}):
        assert tts._tunable("OMNIGENT_TTS_TEMPO", tts.SPEECH_TEMPO, 0.5, 1.5) == 1.5
    with patch.dict(os.environ, {"OMNIGENT_TTS_TEMPO": "devagar"}):
        assert tts._tunable("OMNIGENT_TTS_TEMPO", tts.SPEECH_TEMPO, 0.5, 1.5) == tts.SPEECH_TEMPO


@pytest.mark.asyncio
async def test_synthesis_uses_the_models_own_voice() -> None:
    """The stock voice, not a cloned-and-pitched reference of our own.

    The reference used to be the model's output pitched down two semitones and
    fed back in as a cloning prompt, which put phase-vocoder artefacts into
    every summary. Nothing may pass an ``audio_prompt_path`` now.
    """
    import torch

    seen: list[dict[str, Any]] = []

    class _Recorder:
        sr = 24000

        def generate(self, text: str, **kwargs: Any) -> Any:
            seen.append(kwargs)
            return torch.zeros(1, 2400)

    def _fake_save(buffer: Any, *a: Any, **k: Any) -> None:
        buffer.write(b"RIFFfake")

    class _FakeTorchaudio:
        save = staticmethod(_fake_save)

    with patch.object(tts, "_load_model", return_value=_Recorder()):
        with patch.dict("sys.modules", {"torchaudio": _FakeTorchaudio}):
            got = await tts.synthesize_summary("Resumo curto.")
    assert got is not None and got.data == b"RIFFfake"

    assert seen and all("audio_prompt_path" not in kw for kw in seen)


def test_a_rambling_chunk_is_rerolled_not_shipped() -> None:
    """Sampling sometimes runs past the text, repeating until Chatterbox stops it.

    Heard as an echo: the chunk says its line, then keeps going. The text says
    how long it should take, so an implausible length is regenerated rather
    than shipped.
    """
    import torch

    lengths = iter([52.0, 11.0])  # a runaway draw, then a good one

    class _Flaky:
        sr = 24000

        def generate(self, text: str, **kwargs: Any) -> Any:
            return torch.zeros(1, int(self.sr * next(lengths)))

    got = tts._generate_chunk(_Flaky(), "x" * 221, "pt", {})
    assert got.shape[-1] / 24000 == pytest.approx(11.0)


def test_a_plausible_chunk_is_kept_on_the_first_try() -> None:
    """The reroll must not cost a second generation on every normal chunk."""
    import torch

    calls = 0

    class _Good:
        sr = 24000

        def generate(self, text: str, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            return torch.zeros(1, int(self.sr * 11.0))

    tts._generate_chunk(_Good(), "x" * 221, "pt", {})
    assert calls == 1


def test_a_chunk_that_never_settles_still_ships_the_shortest_take() -> None:
    """A silent summary is worse than an overlong one, so attempts are bounded."""
    import torch

    seen = [40.0, 38.0, 44.0]
    it = iter(seen)

    class _Hopeless:
        sr = 24000

        def generate(self, text: str, **kwargs: Any) -> Any:
            return torch.zeros(1, int(self.sr * next(it)))

    got = tts._generate_chunk(_Hopeless(), "x" * 221, "pt", {})
    assert got.shape[-1] / 24000 == pytest.approx(min(seen))


def test_speech_is_compressed_before_it_is_shipped() -> None:
    """An uncompressed summary is megabytes, and a slow link stalls on it.

    A stalled fetch surfaces as a media error, which the player cannot tell
    apart from a recording that is genuinely gone -- so it falls back to the
    browser voice on top of audio that is still arriving.
    """
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * 48_000)  # two seconds
    raw = buf.getvalue()

    out = tts._encode_mp3(raw)
    assert out is not None
    assert len(out) < len(raw) / 3


def test_an_unencodable_payload_still_ships_as_wav() -> None:
    """No encoder, or a refused payload, must not cost the reader their audio."""
    assert tts._encode_mp3(b"not actually a wav") is None


@pytest.mark.asyncio
async def test_the_stored_name_and_mime_follow_the_bytes() -> None:
    """A file named .mp3 holding WAV would not play; the three travel together."""
    import torch

    class _Model:
        sr = 24000

        def generate(self, text: str, **kwargs: Any) -> Any:
            return torch.zeros(1, 2400)

    def _fake_save(buffer: Any, *a: Any, **k: Any) -> None:
        buffer.write(b"RIFFfake")

    class _FakeTorchaudio:
        save = staticmethod(_fake_save)

    with patch.object(tts, "_load_model", return_value=_Model()):
        with patch.dict("sys.modules", {"torchaudio": _FakeTorchaudio}):
            with patch.object(tts, "_encode_mp3", return_value=b"ID3fake"):
                got = await tts.synthesize_summary("Resumo curto.")
                assert got == tts.SummaryAudio(b"ID3fake", "resumo.mp3", "audio/mpeg")
            with patch.object(tts, "_encode_mp3", return_value=None):
                got = await tts.synthesize_summary("Resumo curto.")
                assert got == tts.SummaryAudio(b"RIFFfake", "resumo.wav", "audio/wav")


@pytest.mark.asyncio
async def test_a_multi_message_turn_is_summarized_whole() -> None:
    """A native turn is one item per assistant message, and the idle edge only
    carries the last one. Summarizing that alone drops the answer and keeps the
    closing status -- observed as a summary that never mentioned the question
    it was asked."""
    from omnigent.server.routes._sessions.helpers import _native_turn_text

    class _Data:
        def __init__(self, role: str, texts: list[str], agent: str = "claude-native-ui") -> None:
            self.role = role
            self.agent = agent
            self.content = [{"type": "output_text", "text": t} for t in texts]

    class _Item:
        def __init__(self, response_id: str, data: Any) -> None:
            self.response_id = response_id
            self.data = data

    class _Page:
        # Listed newest-first, the way `order="desc"` returns them.
        data = [
            _Item("resp_1", _Data("assistant", ["resumo"], agent="spoken_summary")),
            _Item("resp_1", _Data("assistant", ["closing status"])),
            _Item("resp_2", _Data("assistant", ["another turn entirely"])),
            _Item("resp_1", _Data("assistant", ["the finding"])),
            _Item("resp_1", _Data("user", ["the question"])),
            _Item("resp_1", _Data("assistant", ["the answer"])),
        ]

    class _Store:
        def list_items(self, *a: Any, **k: Any) -> Any:
            return _Page()

    got = await _native_turn_text(_Store(), "conv_1", "resp_1", "closing status")
    # Chronological, this turn only, no user text, and not the summary carrier.
    assert got == "the answer\n\nthe finding\n\nclosing status"


@pytest.mark.asyncio
async def test_an_unreadable_store_still_summarizes_the_last_message() -> None:
    """Losing the earlier detail beats losing the summary."""
    from omnigent.server.routes._sessions.helpers import _native_turn_text

    class _Broken:
        def list_items(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("db gone")

    assert await _native_turn_text(_Broken(), "conv_1", "resp_1", "last only") == "last only"
    assert await _native_turn_text(None, "conv_1", "resp_1", "last only") == "last only"


def test_an_artifact_watch_is_not_pending_work() -> None:
    """Publishing an artifact leaves a live-update monitor "running" for the
    rest of the session. Counted as work, it made the rewriter open a finished
    answer with "I'm still running through everything in the background"."""
    from omnigent.server.routes._sessions.helpers import _pending_work_count
    from omnigent.server.schemas import BackgroundTaskInfo

    watch = BackgroundTaskInfo(
        id="s96q71cme",
        type="monitor",
        status="running",
        description=(
            "live updates for artifact https://claude.ai/code/artifact/x (auto-armed on publish)"
        ),
    )
    shell = BackgroundTaskInfo(id="b1", type="shell", status="running", description="train.py")

    assert _pending_work_count(1, [watch]) == 0
    # A real shell alongside it is still work.
    assert _pending_work_count(2, [watch, shell]) == 1
    # A monitor the model armed to wait on something real still counts.
    waiting = BackgroundTaskInfo(
        id="m2", type="monitor", status="running", description="until training done"
    )
    assert _pending_work_count(1, [waiting]) == 1


def test_running_jobs_are_named_for_the_summary() -> None:
    """Naming the job lets the summary say what is unfinished, and nothing else."""
    from omnigent.server.routes._sessions.helpers import _pending_work_labels
    from omnigent.server.schemas import BackgroundTaskInfo

    watch = BackgroundTaskInfo(
        type="monitor", status="running", description="live updates for artifact https://x"
    )
    described = BackgroundTaskInfo(type="shell", description="Run the full test suite")
    bare = BackgroundTaskInfo(type="shell", command="python   train.py\n --steps 60M")

    assert _pending_work_labels(1, [watch]) == []
    assert _pending_work_labels(2, [watch, described]) == ["Run the full test suite"]
    # A command stands in for a missing description, whitespace collapsed.
    assert _pending_work_labels(1, [bare]) == ["python train.py --steps 60M"]
    # Jobs the tally counts but the detail cannot name are still reported.
    assert _pending_work_labels(3, [described]) == [
        "Run the full test suite",
        "2 more background jobs with no description",
    ]
    assert _pending_work_labels(0, [described]) == []


def test_the_prompt_names_running_work_instead_of_voiding_the_reply() -> None:
    from omnigent.server.spoken_summary import build_spoken_summary_instructions

    with patch("omnigent.server.spoken_summary.load_voice_profile", return_value=None):
        running = build_spoken_summary_instructions("en-US", pending_work=["python train.py"])
        idle = build_spoken_summary_instructions("en-US")
    assert "python train.py" in running
    assert "do not call the whole reply a progress note" in running
    # The old rule opened every such summary with "still running".
    assert "Say so in the opening words" not in running
    assert "never say that something is" in idle


def test_a_tally_without_detail_is_trusted() -> None:
    """An older runner sends only the count; nothing can be excluded then."""
    from omnigent.server.routes._sessions.helpers import _pending_work_count

    assert _pending_work_count(2, None) == 2
    assert _pending_work_count(None, None) == 0
    assert _pending_work_count(0, None) == 0


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

    import torch

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
            return torch.zeros(1, 2400)

    def _fake_save(buffer: Any, *a: Any, **k: Any) -> None:
        buffer.write(b"RIFFfake")

    class _FakeTorchaudio:
        save = staticmethod(_fake_save)

    with patch.object(tts, "_load_model", return_value=_Sequential()):
        with patch.dict("sys.modules", {"torchaudio": _FakeTorchaudio}):
            results = await _asyncio.gather(
                *(tts.synthesize_summary(f"resumo numero {i}") for i in range(4))
            )

    assert overlapped is False
    assert all(r is not None and r.data == b"RIFFfake" for r in results)


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


@pytest.mark.asyncio
async def test_live_voice_sessions_skip_synthesis():
    """A session on the live voice never plays the recording, so none is made.

    The live voice speaks the summary text itself. Synthesizing a file it will
    never open spends tens of seconds of GPU per turn for nothing.
    """
    from omnigent.server.routes._sessions.helpers import _reads_through_live_voice

    class _Conv:
        def __init__(self, labels):
            self.labels = labels

    class _Store:
        def __init__(self, labels):
            self._conv = _Conv(labels)

        def get_conversation(self, _session_id):
            return self._conv

    assert await _reads_through_live_voice(_Store({"voice_backend": "live"}), "conv_a") is True
    assert await _reads_through_live_voice(_Store({"voice_backend": "local"}), "conv_a") is False
    assert await _reads_through_live_voice(_Store({}), "conv_a") is False
    assert await _reads_through_live_voice(None, "conv_a") is False


@pytest.mark.asyncio
async def test_unreadable_label_still_synthesizes():
    """Fail towards the recording: the opposite mistake is silence."""
    from omnigent.server.routes._sessions.helpers import _reads_through_live_voice

    class _Broken:
        def get_conversation(self, _session_id):
            raise RuntimeError("store is down")

    assert await _reads_through_live_voice(_Broken(), "conv_a") is False
