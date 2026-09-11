"""Whisper dictation engine: accurate, vocabulary-aware, utterance at a time.

Selected with ``OMNIGENT_DICTATION_ENGINE=whisper``. The streaming sherpa
transducer that ships as the default decodes word by word, but it mishears
the reader's own vocabulary -- product names, libraries, people -- and has no
way to be told about it. Whisper does: an ``initial_prompt`` listing the terms
makes the model far more likely to spell them the reader's way. The file of
terms is read fresh on every take, so adding a word needs no restart.

The cost is liveness. Whisper transcribes a finished stretch of audio, not a
stream, so this engine segments the take with a voice-activity detector and
emits each utterance as ``finalized`` when the speaker pauses. There are no
word-by-word partials.

Configuration
-------------

=========================================  =============================================
Env var                                    Default
=========================================  =============================================
``OMNIGENT_DICTATION_WHISPER_MODEL``       ``large-v3-turbo`` (downloaded on first use)
``OMNIGENT_DICTATION_WHISPER_LANGUAGE``    empty -- detect per utterance
``OMNIGENT_DICTATION_VAD_DIR``             ``~/.omnigent/models/dictation/vad``
``OMNIGENT_DICTATION_VOCAB``               ``~/.omnigent/dictation-vocab.txt``
=========================================  =============================================

The VAD dir must hold ``silero_vad.onnx`` (see ``scripts/fetch-dictation-models.sh``).
The model runs on the GPU when CTranslate2 can see one, else on the CPU.
"""

from __future__ import annotations

import ctypes
import importlib.util
import logging
import os
import threading
from pathlib import Path
from typing import Any

from omnigent.server.dictation import (
    REASON_EXTRA_NOT_INSTALLED,
    REASON_MODELS_MISSING,
    SAMPLE_RATE,
    DictationUpdate,
    register_engine,
)

_logger = logging.getLogger(__name__)

ENGINE_WHISPER = "whisper"
WHISPER_MODEL_ENV = "OMNIGENT_DICTATION_WHISPER_MODEL"
WHISPER_LANGUAGE_ENV = "OMNIGENT_DICTATION_WHISPER_LANGUAGE"
VAD_DIR_ENV = "OMNIGENT_DICTATION_VAD_DIR"
VOCAB_ENV = "OMNIGENT_DICTATION_VOCAB"

_DEFAULT_MODEL = "large-v3-turbo"
_VAD_FILE = "silero_vad.onnx"

#: Samples per VAD step. Silero is trained on 512-sample windows at 16 kHz.
_VAD_WINDOW = 512

#: Pause that ends an utterance. Long enough to survive a thinking pause
#: mid-sentence, short enough that text lands soon after the speaker stops.
_VAD_MIN_SILENCE_S = 1.0

#: Shortest audio Whisper is given on its own. Alone, a half-second scrap has
#: no context and Whisper guesses: the end of a sentence comes back as
#: "Thank you.", "Is it allowed?" as "Loud.". Shorter pieces ride along with
#: their neighbour instead.
_MIN_ALONE_S = 1.0

#: Silence placed between pieces joined for context.
_JOIN_GAP_S = 0.25

#: Shortest sound treated as speech; filters clicks and keyboard taps.
_VAD_MIN_SPEECH_S = 0.15

#: How sure the detector must be before calling something speech. Silero's own
#: default (0.5) drops quiet word endings outright -- a trailing "isn't" never
#: reaches Whisper and the word is simply missing. Lower catches those; the
#: noise it lets through lands as a short piece, which rides along with its
#: neighbour rather than being transcribed on its own.
_VAD_THRESHOLD = 0.35

#: Whisper reads 30-second windows; cut a monologue before it overruns one.
_VAD_MAX_SPEECH_S = 28.0

#: Whisper's prompt budget is 224 tokens. Terms past this are dropped rather
#: than letting the prompt silently truncate mid-word.
_VOCAB_MAX_CHARS = 700

#: A segment Whisper itself thinks is silence is dropped: on a noisy gap it
#: otherwise invents stock phrases ("Thank you.").
_NO_SPEECH_DROP = 0.8

#: Written the first time the file is missing, so there is something to edit.
DEFAULT_VOCAB = """\
# Words dictation should spell your way: product names, libraries, people.
# One per line, or comma-separated. Lines starting with # are ignored.
# Read fresh on every dictation, so edits apply to the next take.
Omnigent
Gemini
Claude
Chatterbox
"""


def _vad_path() -> Path:
    default = Path.home() / ".omnigent" / "models" / "dictation" / "vad"
    return Path(os.environ.get(VAD_DIR_ENV) or default).expanduser() / _VAD_FILE


def vocab_path() -> Path:
    """Return the dictation vocabulary file's path."""
    default = Path.home() / ".omnigent" / "dictation-vocab.txt"
    return Path(os.environ.get(VOCAB_ENV) or default).expanduser()


def load_vocab_prompt(path: Path | None = None) -> str | None:
    """Build Whisper's ``initial_prompt`` from the vocabulary file.

    Creates the file with a starter list when it is missing. Never raises: an
    unreadable file means dictation without biasing, not no dictation.

    :param path: File to read; defaults to :func:`vocab_path`.
    :returns: ``"Omnigent, Gemini, …."``, or ``None`` when there are no terms.
    """
    target = path or vocab_path()
    try:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(DEFAULT_VOCAB, encoding="utf-8")
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        _logger.warning("Could not read dictation vocabulary at %s: %s", target, exc)
        return None
    terms: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        terms.extend(t.strip() for t in line.split(",") if t.strip())
    if not terms:
        return None
    prompt = ""
    for term in dict.fromkeys(terms):  # de-duplicate, keep order
        candidate = f"{prompt}, {term}" if prompt else term
        if len(candidate) > _VOCAB_MAX_CHARS:
            break
        prompt = candidate
    return f"{prompt}."


def _preload_cuda_libraries() -> None:
    """Load PyTorch's CUDA libraries into the process for CTranslate2.

    CTranslate2 dlopens ``libcublas.so.12`` and ``libcudnn.so.9`` by name and
    fails when they are not on the linker path. PyTorch's wheels already carry
    matching copies; loading them with ``RTLD_GLOBAL`` first lets CTranslate2
    find them without an ``LD_LIBRARY_PATH`` in the service environment.
    Best-effort: without them the engine falls back to the CPU.
    """
    wanted = {
        "nvidia.cublas": ("libcublasLt.so.12", "libcublas.so.12"),
        "nvidia.cudnn": ("libcudnn.so.9",),
    }
    for package, names in wanted.items():
        spec = importlib.util.find_spec(package)
        if spec is None or not spec.submodule_search_locations:
            continue
        lib_dir = Path(next(iter(spec.submodule_search_locations))) / "lib"
        for name in names:
            try:
                ctypes.CDLL(str(lib_dir / name), mode=ctypes.RTLD_GLOBAL)
            except OSError:
                _logger.debug("Could not preload %s from %s", name, lib_dir)


def whisper_available() -> tuple[bool, str | None]:
    """Availability probe for the Whisper engine (loads nothing)."""
    for module in ("faster_whisper", "sherpa_onnx"):
        if importlib.util.find_spec(module) is None:
            return False, REASON_EXTRA_NOT_INSTALLED
    if not _vad_path().is_file():
        return False, REASON_MODELS_MISSING
    return True, None


def _words(text: str) -> list[str]:
    return [w for w in "".join(c.lower() if c.isalnum() else " " for c in text).split() if w]


def _strip_shown_prefix(combined: str, shown: str) -> str | None:
    """Return *combined* minus the words already shown, or ``None`` if unsure.

    :param combined: Transcript of the previous piece plus the tail.
    :param shown: Text already emitted for the previous piece.
    :returns: The tail's words in *combined*'s own spelling, ``""`` when
        nothing follows, or ``None`` when *combined* does not start with
        (nearly all of) *shown*.
    """
    shown_words = _words(shown)
    tokens = combined.split()
    matched, i = 0, 0
    for token in tokens:
        norm = _words(token)
        if not norm:
            i += 1
            continue
        if matched < len(shown_words) and norm[0] == shown_words[matched]:
            matched += 1
            i += 1
        else:
            break
    if not shown_words or matched < max(1, int(len(shown_words) * 0.8)):
        return None
    return " ".join(tokens[i:]).strip()


class WhisperDictationEngine:
    """Shared Whisper model plus a voice-activity detector per take."""

    def __init__(self) -> None:
        """Load the model eagerly; construction is slow (seconds).

        :raises RuntimeError: If the model cannot be loaded.
        """
        _preload_cuda_libraries()
        import ctranslate2  # type: ignore[import-not-found]
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]

        on_gpu = ctranslate2.get_cuda_device_count() > 0
        name = os.environ.get(WHISPER_MODEL_ENV, "").strip() or _DEFAULT_MODEL
        _logger.info("Loading Whisper dictation model %s on %s", name, "cuda" if on_gpu else "cpu")
        self._model = WhisperModel(
            name,
            device="cuda" if on_gpu else "cpu",
            compute_type="float16" if on_gpu else "int8",
        )
        self._language = os.environ.get(WHISPER_LANGUAGE_ENV, "").strip() or None
        # One transcription at a time: the model is shared, and two takes
        # decoding at once would only contend for the same GPU.
        self._lock = threading.Lock()

    def transcribe(self, samples: Any, prompt: str | None) -> str:
        """Transcribe one utterance of 16 kHz float32 audio.

        :param samples: Mono float32 samples in ``[-1, 1]``.
        :param prompt: Vocabulary prompt, or ``None``.
        :returns: The display-ready text, possibly empty.
        """
        with self._lock:
            segments, _ = self._model.transcribe(
                samples,
                language=self._language,
                initial_prompt=prompt,
                beam_size=5,
                vad_filter=False,  # the take is already segmented
                condition_on_previous_text=False,
                without_timestamps=True,
            )
            kept = [s.text.strip() for s in segments if s.no_speech_prob < _NO_SPEECH_DROP]
        return " ".join(t for t in kept if t)

    def create_stream(self) -> _WhisperStream:
        """Open a take with its own VAD and a fresh read of the vocabulary."""
        return _WhisperStream(self, load_vocab_prompt())


class _WhisperStream:
    """One take (see :class:`omnigent.server.dictation.DictationStreamHandle`)."""

    def __init__(self, engine: WhisperDictationEngine, prompt: str | None) -> None:
        import numpy as np  # type: ignore[import-not-found]
        import sherpa_onnx  # type: ignore[import-not-found]

        self._engine = engine
        self._prompt = prompt
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(_vad_path())
        config.silero_vad.min_silence_duration = _VAD_MIN_SILENCE_S
        config.silero_vad.min_speech_duration = _VAD_MIN_SPEECH_S
        config.silero_vad.threshold = _VAD_THRESHOLD
        config.silero_vad.max_speech_duration = _VAD_MAX_SPEECH_S
        config.silero_vad.window_size = _VAD_WINDOW
        config.sample_rate = SAMPLE_RATE
        config.num_threads = 1
        self._vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=60)
        self._pending = np.zeros(0, dtype=np.float32)
        self._done = False
        # A short piece waiting for context, and the last piece shown, which
        # lends its context to a short piece that follows it.
        self._held: Any = None
        self._last_audio: Any = None
        self._last_text = ""

    def _join(self, first: Any, second: Any) -> Any:
        import numpy as np

        gap = np.zeros(int(_JOIN_GAP_S * SAMPLE_RATE), dtype=np.float32)
        return np.concatenate([first, gap, second])

    def _drain(self) -> str | None:
        """Transcribe every utterance the VAD has closed, oldest first."""
        import numpy as np

        texts: list[str] = []
        while not self._vad.empty():
            # The VAD hands back a Python list; Whisper treats anything that
            # is not an ndarray as a file to open.
            samples = np.asarray(self._vad.front.samples, dtype=np.float32)
            self._vad.pop()
            if self._held is not None:
                samples, self._held = self._join(self._held, samples), None
            if len(samples) < _MIN_ALONE_S * SAMPLE_RATE:
                if self._last_audio is None:
                    # Nothing before it to lean on yet: wait for what follows.
                    self._held = samples
                    continue
                # A scrap after a pause usually finishes the previous thought.
                text = self._transcribe_tail(samples)
                if text:
                    texts.append(text)
                    joined = self._join(self._last_audio, samples)
                    self._remember(joined, f"{self._last_text} {text}")
                continue
            text = self._engine.transcribe(samples, self._prompt)
            if text:
                texts.append(text)
                self._remember(samples, text)
        return " ".join(texts) or None

    def _remember(self, audio: Any, text: str) -> None:
        """Keep the latest shown piece as context, capped to one Whisper window."""
        limit = int(_VAD_MAX_SPEECH_S * SAMPLE_RATE)
        if len(audio) > limit:
            # Too long to replay whole; the next full sentence resets context.
            self._last_audio, self._last_text = None, ""
            return
        self._last_audio, self._last_text = audio, text.strip()

    def _transcribe_tail(self, tail: Any) -> str:
        """Transcribe a short last piece with the previous one as context.

        Whisper hears the previous piece again, and the words already shown
        for it are cut from the front of the result. When the result does not
        start with them, the tail is transcribed alone as a last resort.
        """
        if self._last_audio is None:
            return self._engine.transcribe(tail, self._prompt)
        combined = self._engine.transcribe(self._join(self._last_audio, tail), self._prompt)
        rest = _strip_shown_prefix(combined, self._last_text)
        return rest if rest is not None else self._engine.transcribe(tail, self._prompt)

    def feed_pcm16(self, data: bytes) -> DictationUpdate:
        """Buffer a PCM chunk; emit any utterance a pause just closed."""
        import numpy as np

        usable = len(data) - (len(data) % 2)
        if usable <= 0 or self._done:
            return DictationUpdate(partial="")
        chunk = np.frombuffer(data[:usable], dtype=np.int16).astype(np.float32) / 32768.0
        self._pending = np.concatenate([self._pending, chunk])
        whole = len(self._pending) - len(self._pending) % _VAD_WINDOW
        for start in range(0, whole, _VAD_WINDOW):
            self._vad.accept_waveform(self._pending[start : start + _VAD_WINDOW])
        self._pending = self._pending[whole:]
        return DictationUpdate(partial="", finalized=self._drain())

    def finish(self) -> str:
        """Close the take: flush the VAD and transcribe what is left."""
        if self._done:
            return ""
        self._done = True
        if len(self._pending):
            self._vad.accept_waveform(self._pending)
        self._vad.flush()
        parts = [self._drain() or ""]
        if self._held is not None:
            parts.append(self._transcribe_tail(self._held))
            self._held = None
        return " ".join(p for p in parts if p)

    def close(self) -> None:
        """Nothing external to release; the VAD frees with the handle."""
        self._done = True


register_engine(ENGINE_WHISPER, WhisperDictationEngine, available=whisper_available)
