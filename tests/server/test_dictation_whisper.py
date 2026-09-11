"""Whisper dictation engine: vocabulary, availability, and take mechanics.

The model and the voice-activity detector are stood in for, so these run
without a GPU or model weights. They cover the engine's own logic -- what it
feeds the VAD, what it hands Whisper, when text comes out -- not recognition
accuracy, which only real speech can measure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest

from omnigent.server import dictation, dictation_whisper
from omnigent.server.dictation_whisper import _WhisperStream, load_vocab_prompt


def test_the_engine_is_selectable_by_name() -> None:
    assert dictation_whisper.ENGINE_WHISPER in dictation._ENGINE_REGISTRY


# ── vocabulary ───────────────────────────────────────────────────────────


def test_a_missing_vocabulary_file_is_created_with_a_starter_list(tmp_path: Path) -> None:
    path = tmp_path / "vocab.txt"
    prompt = load_vocab_prompt(path)
    assert path.exists()
    assert prompt is not None and "Omnigent" in prompt


def test_comments_commas_and_duplicates_are_handled(tmp_path: Path) -> None:
    path = tmp_path / "vocab.txt"
    path.write_text("# a comment\nOmnigent\nReLIC, PhysX\n\nOmnigent\n  Isaac Sim  \n")
    assert load_vocab_prompt(path) == "Omnigent, ReLIC, PhysX, Isaac Sim."


def test_a_file_of_only_comments_means_no_prompt(tmp_path: Path) -> None:
    path = tmp_path / "vocab.txt"
    path.write_text("# nothing yet\n\n")
    assert load_vocab_prompt(path) is None


def test_a_long_vocabulary_is_cut_at_a_whole_term(tmp_path: Path) -> None:
    """Whisper's prompt budget is small; a term cut in half would bias it
    toward a word that does not exist."""
    path = tmp_path / "vocab.txt"
    terms = [f"term{i:04d}" for i in range(400)]
    path.write_text("\n".join(terms))
    prompt = load_vocab_prompt(path)
    assert prompt is not None
    assert len(prompt) <= dictation_whisper._VOCAB_MAX_CHARS + 1
    assert all(part.strip(" .") in terms for part in prompt.split(","))


def test_an_unreadable_vocabulary_means_dictation_without_biasing(tmp_path: Path) -> None:
    unreadable = tmp_path / "is-a-directory"
    unreadable.mkdir()
    assert load_vocab_prompt(unreadable) is None


# ── availability ─────────────────────────────────────────────────────────


def test_unavailable_without_the_vad_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(dictation_whisper.VAD_DIR_ENV, str(tmp_path))
    with patch("importlib.util.find_spec", return_value=object()):
        assert dictation_whisper.whisper_available() == (False, dictation.REASON_MODELS_MISSING)


def test_unavailable_without_the_packages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "silero_vad.onnx").write_bytes(b"x")
    monkeypatch.setenv(dictation_whisper.VAD_DIR_ENV, str(tmp_path))
    with patch("importlib.util.find_spec", return_value=None):
        assert dictation_whisper.whisper_available() == (
            False,
            dictation.REASON_EXTRA_NOT_INSTALLED,
        )


def test_available_with_packages_and_vad_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "silero_vad.onnx").write_bytes(b"x")
    monkeypatch.setenv(dictation_whisper.VAD_DIR_ENV, str(tmp_path))
    with patch("importlib.util.find_spec", return_value=object()):
        assert dictation_whisper.whisper_available() == (True, None)


# ── one take ─────────────────────────────────────────────────────────────


class _Segment:
    def __init__(self, samples: list[float]) -> None:
        # The real sherpa VAD hands back a plain list, not an ndarray.
        self.samples = samples


class _FakeVad:
    """Closes an utterance whenever told to; records what it was fed."""

    def __init__(self) -> None:
        self.fed: list[int] = []
        self.queue: list[_Segment] = []
        self.flushed = False

    def accept_waveform(self, samples: Any) -> None:
        self.fed.append(len(samples))

    def empty(self) -> bool:
        return not self.queue

    @property
    def front(self) -> _Segment:
        return self.queue[0]

    def pop(self) -> None:
        self.queue.pop(0)

    def flush(self) -> None:
        self.flushed = True
        self.queue.append(_Segment([0.0] * 1600))


class _FakeEngine:
    def __init__(self, text: str = "hello there", texts: list[str] | None = None) -> None:
        self.text = text
        self.texts = texts
        self.seen: list[tuple[Any, str | None]] = []

    def transcribe(self, samples: Any, prompt: str | None) -> str:
        self.seen.append((samples, prompt))
        if self.texts:
            return self.texts.pop(0)
        return self.text


def _stream(engine: _FakeEngine, vad: _FakeVad, prompt: str | None = "Omnigent.") -> Any:
    stream = _WhisperStream.__new__(_WhisperStream)
    stream._engine = engine
    stream._prompt = prompt
    stream._vad = vad
    stream._pending = np.zeros(0, dtype=np.float32)
    stream._done = False
    stream._held = None
    stream._last_audio = None
    stream._last_text = ""
    return stream


def _pcm(n_samples: int) -> bytes:
    return (np.ones(n_samples, dtype=np.int16) * 1000).tobytes()


def test_whisper_is_handed_an_array_not_the_vads_list() -> None:
    """faster-whisper treats anything that is not an ndarray as a file path
    and fails to open it -- caught on the first real run."""
    engine, vad = _FakeEngine(), _FakeVad()
    stream = _stream(engine, vad)
    vad.queue.append(_Segment([0.1] * 32000))  # 2 s: long enough to stand alone
    update = stream.feed_pcm16(_pcm(512))
    assert update.finalized == "hello there"
    samples, prompt = engine.seen[0]
    assert isinstance(samples, np.ndarray) and samples.dtype == np.float32
    assert prompt == "Omnigent."


def test_a_scrap_is_never_transcribed_on_its_own() -> None:
    """Half a second with no context makes Whisper guess: the end of a sentence
    comes back as "Thank you.". A scrap rides along with the sentence before it,
    and the words already shown are cut off the front."""
    engine = _FakeEngine(texts=["the first sentence", "the first sentence and the rest"])
    vad = _FakeVad()
    stream = _stream(engine, vad)

    vad.queue.append(_Segment([0.1] * 32000))  # 2 s sentence
    assert stream.feed_pcm16(_pcm(512)).finalized == "the first sentence"

    vad.queue.append(_Segment([0.1] * 8000))  # 0.5 s scrap
    assert stream.feed_pcm16(_pcm(512)).finalized == "and the rest"
    # It was transcribed together with the sentence, not alone.
    assert len(engine.seen[1][0]) > 32000


def test_a_scrap_that_opens_a_take_waits_for_what_follows() -> None:
    """With nothing before it, a scrap has to borrow context from the next
    piece instead."""
    engine = _FakeEngine("scrap and sentence")
    vad = _FakeVad()
    stream = _stream(engine, vad)

    vad.queue.append(_Segment([0.1] * 8000))
    assert stream.feed_pcm16(_pcm(512)).finalized is None
    assert not engine.seen  # nothing transcribed yet

    vad.queue.append(_Segment([0.1] * 32000))
    assert stream.feed_pcm16(_pcm(512)).finalized == "scrap and sentence"
    assert len(engine.seen) == 1


def test_a_tail_whisper_rehears_differently_falls_back_to_the_scrap_alone() -> None:
    """When the re-transcription does not start with what was shown, the words
    cannot be split apart safely, so the scrap is transcribed by itself."""
    engine = _FakeEngine(texts=["first sentence", "something else entirely", "just the scrap"])
    vad = _FakeVad()
    stream = _stream(engine, vad)
    vad.queue.append(_Segment([0.1] * 32000))
    stream.feed_pcm16(_pcm(512))
    vad.queue.append(_Segment([0.1] * 8000))
    assert stream.feed_pcm16(_pcm(512)).finalized == "just the scrap"


@pytest.mark.parametrize(
    ("combined", "shown", "expected"),
    [
        ("the first sentence and the rest", "the first sentence", "and the rest"),
        ("The first sentence. And the rest.", "the first sentence", "And the rest."),
        ("the first sentence", "the first sentence", ""),
        ("something else entirely", "the first sentence", None),
        ("the first", "the first sentence and more", None),
        ("anything", "", None),
    ],
)
def test_cutting_the_words_already_shown(combined: str, shown: str, expected: str | None) -> None:
    from omnigent.server.dictation_whisper import _strip_shown_prefix

    assert _strip_shown_prefix(combined, shown) == expected


def test_the_vad_is_fed_whole_windows_and_the_rest_is_carried() -> None:
    """Browser frames are not a multiple of the VAD window; nothing may be
    dropped or fed in odd-sized pieces."""
    vad = _FakeVad()
    stream = _stream(_FakeEngine(), vad)
    stream.feed_pcm16(_pcm(1600))  # 100 ms, the worklet's cadence
    stream.feed_pcm16(_pcm(1600))
    assert all(n == dictation_whisper._VAD_WINDOW for n in vad.fed)
    assert sum(vad.fed) + len(stream._pending) == 3200


def test_no_text_while_the_speaker_is_still_talking() -> None:
    stream = _stream(_FakeEngine(), _FakeVad())
    update = stream.feed_pcm16(_pcm(1600))
    assert update.finalized is None
    assert update.partial == ""


def test_finish_flushes_speech_that_never_reached_a_pause() -> None:
    """Stopping mid-sentence must not lose the sentence."""
    engine, vad = _FakeEngine("last words"), _FakeVad()
    stream = _stream(engine, vad)
    stream.feed_pcm16(_pcm(1600))
    assert stream.finish() == "last words"
    assert vad.flushed
    # A second stop, or a close after it, returns nothing and does not rerun.
    assert stream.finish() == ""
    stream.close()
    assert len(engine.seen) == 1


def test_an_odd_trailing_byte_does_not_break_the_take() -> None:
    stream = _stream(_FakeEngine(), _FakeVad())
    assert stream.feed_pcm16(b"\x01").finalized is None
    assert stream.feed_pcm16(_pcm(512) + b"\x01").finalized is None
