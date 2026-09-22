"""Speak a summary in a chosen voice rather than the browser's default engine.

The browser's Web Speech API routes through whatever the host provides — on
Linux that is usually espeak-ng, which is robotic enough that people turn the
feature off. This synthesizes the summary server-side instead and ships the
audio with it, so the transcript carries a voice worth listening to.

Optional by design: the model and its CUDA stack are gigabytes, so the import
is lazy and every failure degrades to no audio. A deployment without the
``tts`` extra installed behaves exactly as it did before.

Three properties of Chatterbox shape everything below, all measured on an L40S
against the summaries this actually produces:

* It speaks faster the more text it is handed. A 190-character line comes out
  at ~16 characters per second, which is a natural Portuguese reading pace; the
  same voice on a 485-character summary comes out at ~20. The model has no
  speed control, so the pace is corrected after synthesis instead.
* It has a hard ceiling of 1000 speech tokens, or ~40 seconds of audio. A
  900-character summary wants ~47 seconds and is silently cut off mid-sentence.
  Long summaries are therefore spoken a chunk at a time.
* Its alignment analyzer is built once per process and pinned to the first
  generation's text length, so later generations of a different length read a
  stale slice — the ``stack expects each tensor to be equal size`` and
  ``expected Tensor ... but got NoneType`` crashes in the logs. Chunking makes
  every generation a different length, so the analyzer is rebuilt per call.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import threading
from typing import Any, NamedTuple

_logger = logging.getLogger("omnigent.server.tts")

#: How expressive the delivery is. Chatterbox's own default; below it the
#: reading flattens out, above it the model starts over-acting.
VOICE_EXAGGERATION: float = 0.5

#: Classifier-free guidance weight. Chatterbox's default is 0.5; 0.3 is the
#: value its own docs suggest when the delivery is clipped, and measured ~10%
#: slower here (21.1 -> 19.0 characters per second on the same summary).
VOICE_CFG_WEIGHT: float = 0.3

#: Sampling temperature. Chatterbox's own default; lower readings come out
#: flatter because the prosody stops varying between sentences.
VOICE_TEMPERATURE: float = 0.8

#: Playback rate applied to the finished audio, pitch preserved.
#:
#: Chatterbox cannot be asked to slow down, and the parameters above only buy
#: ~10%. Stretching the result to 0.85 lands a 485-character summary at 16.2
#: characters per second — the pace it reads a short line at, and the pace a
#: person actually reads at. 1.0 disables the pass entirely.
SPEECH_TEMPO: float = 0.85

#: Longest summary worth synthesizing. Past this the audio outlasts anyone's
#: patience and the generation stops being free. Matches
#: ``spoken_summary.REWRITE_MAX_CHARS``: a rewrite short enough to render is
#: short enough to speak, or it would render with no voice at all.
TTS_MAX_CHARS: int = 2000

#: Characters per generation. Chatterbox stops at 1000 speech tokens (~40s);
#: at the pace it actually reads, 300 characters is ~16 seconds, so no chunk
#: can reach the ceiling and get truncated.
_CHUNK_MAX_CHARS: int = 300

#: Silence inserted between chunks, in seconds, so the seam reads as the pause
#: between two sentences rather than a splice.
_CHUNK_GAP_S: float = 0.18

#: Characters per second this model reads at, measured across these summaries.
#: Used only to judge whether a generation came out a plausible length.
_EXPECTED_CHARS_PER_S: float = 19.0

#: How far past the expected duration a chunk may run before it is retried.
#:
#: Generation is sampled, so a bad draw makes the model ramble past the end of
#: its text -- repeating itself until Chatterbox's own long-tail guard trips,
#: which can take tens of seconds. The same text regenerates cleanly, so the
#: cheapest fix is to notice the implausible length and roll again.
_RUNAWAY_RATIO: float = 1.6

#: Extra attempts allowed per chunk before the least-bad one is accepted.
_MAX_CHUNK_RETRIES: int = 2

#: Bitrate for the delivered audio. 96k mono leaves speech untouched to the ear
#: while still taking a minute of summary from ~2.8MB of WAV to well under 1MB.
_MP3_BITRATE: str = "96k"

#: How long the encoder may take before the WAV is shipped as-is.
_ENCODE_TIMEOUT_S: float = 60.0


class SummaryAudio(NamedTuple):
    """Encoded summary speech, with the metadata the file store needs.

    The format is decided here rather than by the caller: whether the encoder
    is available is a property of this module, and the stored filename and MIME
    type have to agree with the bytes.
    """

    data: bytes
    filename: str
    mime: str


_model: Any = None
_model_failed: bool = False

#: Serializes model load and generation.
#:
#: Chatterbox keeps per-generation alignment state on the model instance, so two
#: concurrent ``generate`` calls on the shared singleton corrupt each other and
#: raise ``stack expects each tensor to be equal size``. The work is GPU-bound
#: and already effectively serialized, so holding a lock across it costs nothing
#: and also stops two callers from loading the model twice.
_synthesis_lock = threading.Lock()


def tts_enabled() -> bool:
    """Whether summary audio should be generated.

    :returns: True unless explicitly disabled.
    """
    return os.environ.get("OMNIGENT_TTS_ENABLED", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _tunable(name: str, default: float, low: float, high: float) -> float:
    """Read a voice parameter from the environment, clamped to a sane range.

    Voice is a matter of taste, and taste is not worth a redeploy. A missing or
    unparseable value keeps the default rather than failing the turn.

    :param name: Environment variable to read.
    :param default: Value to use when unset or unparseable.
    :param low: Lowest accepted value.
    :param high: Highest accepted value.
    :returns: The resolved value.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(low, min(high, float(raw)))
    except ValueError:
        _logger.warning("Ignoring unparseable %s=%r; using %s", name, raw, default)
        return default


#: A money amount: the symbol, the whole part (thousands groups allowed) and
#: two-digit cents after either separator, e.g. ``$1.73``, ``R$ 1.200,50``.
_MONEY_RE = re.compile(r"(R\$|(?:US)?\$)\s?(\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,](\d{2}))?(?!\d)")

#: A count with a scale suffix, e.g. ``637k``, ``2.5M``, ``3B``. Lowercase
#: ``m`` is left alone: in these summaries it is minutes as often as millions.
_SCALED_RE = re.compile(r"(?<![\w.,-])(\d+(?:[.,]\d+)?)([kKMB])\b")


class _NumberWords(NamedTuple):
    """How one language says the numbers :func:`speakable_numbers` rewrites."""

    decimal: str  # decimal separator, "." or ","
    point: str
    group: str  # thousands separator
    dollar: tuple[str, str]  # singular, plural
    real: tuple[str, str]
    cent: tuple[str, str]
    and_: str
    thousand: tuple[str, str]
    million: tuple[str, str]
    billion: tuple[str, str]


_NUMBER_WORDS: dict[str, _NumberWords] = {
    "en": _NumberWords(
        ".",
        "point",
        ",",
        ("dollar", "dollars"),
        ("real", "reais"),
        ("cent", "cents"),
        "and",
        ("thousand", "thousand"),
        ("million", "million"),
        ("billion", "billion"),
    ),
    "pt": _NumberWords(
        ",",
        "vírgula",
        ".",
        ("dólar", "dólares"),
        ("real", "reais"),
        ("centavo", "centavos"),
        "e",
        ("mil", "mil"),
        ("milhão", "milhões"),
        ("bilhão", "bilhões"),
    ),
}


def speakable_numbers(text: str, language: str) -> str:
    """Rewrite decimals, money and large numbers in *text* as they are said.

    Summaries show numbers as digits, which reads well on screen. Both local
    voices say small whole numbers right, but measured on 2026-09-22 Chatterbox
    read "15.9" as "15 to 29", "$1.73" as "$9.99" and "4,000,000" as "4,000",
    and Unmute read "$1.73" as "173 dollars"; the worded forms below came out
    right on both. Versions and identifiers ("gpt-5.6-luna", "v1.2") are kept.

    :param text: The text about to be synthesized.
    :param language: BCP-47 tag, e.g. ``"pt-BR"``; languages without a word
        table here are returned unchanged.
    :returns: The text with those numbers in words, e.g. ``"$1.73"`` ->
        ``"1 dollar and 73 cents"`` and ``"2.5M"`` -> ``"2 point 5 million"``.
    """
    words = _NUMBER_WORDS.get((language or "").split("-")[0].lower())
    if words is None:
        return text

    def say(count: int | str, forms: tuple[str, str]) -> str:
        return f"{count} {forms[0] if str(count) == '1' else forms[1]}"

    def money(match: re.Match[str]) -> str:
        whole = int(re.sub(r"[.,]", "", match.group(2)))
        cents = int(match.group(3) or 0)
        unit = words.real if match.group(1) == "R$" else words.dollar
        parts = [say(whole, unit)] if whole or not cents else []
        if cents:
            parts.append(say(cents, words.cent))
        return f" {words.and_} ".join(parts)

    def scaled(match: re.Match[str]) -> str:
        scale = {"k": words.thousand, "K": words.thousand, "M": words.million}
        return say(match.group(1), scale.get(match.group(2), words.billion))

    group = re.escape(words.group)
    text = _MONEY_RE.sub(money, text)
    text = _SCALED_RE.sub(scaled, text)
    # Round thousands and millions, the only large numbers these summaries
    # quote exactly ("4,000,000 tokens"); other long numbers pass through.
    for zeros, forms in (
        (f"{group}000{group}000", words.million),
        (f"{group}000", words.thousand),
    ):
        exact = re.compile(rf"(?<![\w.,])(\d{{1,3}}){zeros}(?![\w]|[.,]\d)")
        text = exact.sub(lambda m, forms=forms: say(m.group(1), forms), text)
    decimal = re.compile(rf"(?<![\w.,-])(\d+){re.escape(words.decimal)}(\d+)(?![\w-]|[.,]\d)")
    return decimal.sub(rf"\1 {words.point} \2", text)


def split_for_synthesis(text: str, max_chars: int = _CHUNK_MAX_CHARS) -> list[str]:
    """Split *text* into chunks small enough to speak without being truncated.

    Sentences are kept whole and packed together up to *max_chars*, so the
    seams land where a reader would already pause. A single sentence longer
    than the budget is split on clause boundaries, then hard-wrapped, because
    speaking a truncated sentence is worse than splitting one.

    :param text: The text to speak.
    :param max_chars: Longest chunk to emit.
    :returns: Chunks in order; empty when *text* has no content.
    """
    body = (text or "").strip()
    if not body:
        return []

    sentences = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", body) if s.strip()]
    pieces: list[str] = []
    for sentence in sentences:
        if len(sentence) <= max_chars:
            pieces.append(sentence)
            continue
        # Too long to speak in one go: break at clause boundaries first.
        clause = ""
        for part in re.split(r"(?<=[,;:])\s+", sentence):
            if clause and len(clause) + 1 + len(part) > max_chars:
                pieces.append(clause)
                clause = part
            else:
                clause = f"{clause} {part}".strip()
        while len(clause) > max_chars:
            cut = clause.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            pieces.append(clause[:cut].strip())
            clause = clause[cut:].strip()
        if clause:
            pieces.append(clause)

    chunks: list[str] = []
    for piece in pieces:
        if chunks and len(chunks[-1]) + 1 + len(piece) <= max_chars:
            chunks[-1] = f"{chunks[-1]} {piece}"
        else:
            chunks.append(piece)
    return chunks


def _load_model() -> Any:
    """Load the TTS model once, or return ``None`` when unavailable.

    :returns: The model, or ``None`` when the extra is not installed.
    """
    global _model, _model_failed
    if _model is not None or _model_failed:
        return _model
    try:
        import torch
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        _model = ChatterboxMultilingualTTS.from_pretrained(device=device)
        _logger.info("Summary voice loaded on %s", device.type)
    except Exception as exc:  # noqa: BLE001 - absence is a supported state
        _model_failed = True
        _logger.info("Summary voice unavailable (%s); falling back to browser speech", exc)
    return _model


def _reset_alignment_analyzer(model: Any) -> None:
    """Force Chatterbox to rebuild its alignment analyzer on the next generate.

    The analyzer is built behind a ``compiled`` flag that is never cleared, so
    it keeps the *first* generation's text-token slice and attention buffers
    forever. Feeding it a different-length text then reads a stale slice, which
    is what raises ``stack expects each tensor to be equal size``. Clearing the
    flag rebuilds it; the hooks it registers are cleared alongside, since it
    registers three more on every build and never removes the old ones.

    Best-effort by design: this reaches into Chatterbox's internals, so a
    version that reshapes them leaves synthesis working exactly as before.

    :param model: The loaded TTS model.
    """
    try:
        t3 = model.t3
        for layer in t3.tfmr.layers:
            layer.self_attn._forward_hooks.clear()
        t3.compiled = False
    except Exception:  # noqa: BLE001 - the reset is an optimization, not a contract
        _logger.debug("Could not reset the alignment analyzer", exc_info=True)


def _generate_chunk(model: Any, chunk: str, lang: str, kwargs: dict[str, Any]) -> Any:
    """Speak one chunk, rerolling a draw that rambles past its text.

    Sampling occasionally sends the model past the end of the chunk, repeating
    itself until Chatterbox forces an end -- heard as an echo. The text implies
    how long it should take to read, so an implausible length is caught here and
    regenerated; the shortest attempt wins, since the failure is always extra
    audio rather than missing audio.

    :param model: The loaded TTS model.
    :param chunk: Text for this generation.
    :param lang: Language subtag, e.g. ``"pt"``.
    :param kwargs: Generation parameters shared by every chunk.
    :returns: The chosen waveform.
    """
    budget = len(chunk) / _EXPECTED_CHARS_PER_S * _RUNAWAY_RATIO + 1.5
    best: Any = None
    best_s = float("inf")
    for attempt in range(_MAX_CHUNK_RETRIES + 1):
        # Every chunk is a different length, which is exactly the case the
        # analyzer pinned to the first generation gets wrong.
        _reset_alignment_analyzer(model)
        part = model.generate(chunk, language_id=lang, **kwargs).cpu()
        seconds = part.shape[-1] / model.sr
        if seconds < best_s:
            best, best_s = part, seconds
        if seconds <= budget:
            return part
        _logger.info(
            "Summary chunk ran %.1fs against a %.1fs budget (%d chars); rerolling (%d/%d)",
            seconds,
            budget,
            len(chunk),
            attempt + 1,
            _MAX_CHUNK_RETRIES,
        )
    _logger.warning("Summary chunk stayed overlong after %d attempts", _MAX_CHUNK_RETRIES + 1)
    return best


def _encode_mp3(wav_bytes: bytes, tempo: float = 1.0) -> bytes | None:
    """Slow *wav_bytes* to *tempo* and compress it, in one pass.

    Two problems, one tool. An uncompressed summary is several megabytes, and a
    browser fetching that over a slow link stalls mid-playback. And the model
    reads too fast with no way to ask it to slow down, so the pace has to be
    corrected after the fact.

    ``atempo`` is used rather than a phase vocoder: vocoding speech smears the
    transients and lends it a metallic, faintly reverberant quality -- the voice
    stops sounding like a person and starts sounding processed.

    :param wav_bytes: The WAV payload to convert.
    :param tempo: Playback rate; 1.0 leaves the pace alone.
    :returns: MP3 bytes, or ``None`` when ffmpeg is absent or fails.
    """
    filters = [] if abs(tempo - 1.0) < 0.01 else ["-filter:a", f"atempo={tempo:.3f}"]
    try:
        done = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "wav",
                "-i",
                "pipe:0",
                "-ac",
                "1",
                *filters,
                "-b:a",
                _MP3_BITRATE,
                "-f",
                "mp3",
                "pipe:1",
            ],
            input=wav_bytes,
            capture_output=True,
            timeout=_ENCODE_TIMEOUT_S,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _logger.warning("Could not encode summary speech (%s); shipping WAV", exc)
        return None
    return done.stdout or None


def _synthesize_sync(text: str, language: str) -> SummaryAudio | None:
    """Generate speech for *text*, returning WAV bytes.

    :param text: The text to speak.
    :param language: BCP-47 tag; only its language subtag is used.
    :returns: The encoded audio, or ``None`` when synthesis is unavailable.
    """
    with _synthesis_lock:
        model = _load_model()
        if model is None:
            return None
        try:
            import io

            import torch
            import torchaudio as ta

            lang = (language or "pt").split("-")[0].lower()
            kwargs: dict[str, Any] = {
                "exaggeration": _tunable(
                    "OMNIGENT_TTS_EXAGGERATION", VOICE_EXAGGERATION, 0.0, 1.0
                ),
                "cfg_weight": _tunable("OMNIGENT_TTS_CFG_WEIGHT", VOICE_CFG_WEIGHT, 0.0, 1.0),
                "temperature": _tunable("OMNIGENT_TTS_TEMPERATURE", VOICE_TEMPERATURE, 0.1, 1.5),
            }
            chunks = split_for_synthesis(speakable_numbers(text, language))
            if not chunks:
                return None

            gap = torch.zeros(1, int(model.sr * _CHUNK_GAP_S))
            spoken: list[Any] = []
            for chunk in chunks:
                part = _generate_chunk(model, chunk, lang, kwargs)
                if spoken:
                    spoken.append(gap)
                spoken.append(part)

            wav = torch.cat(spoken, dim=-1) if len(spoken) > 1 else spoken[0]

            buffer = io.BytesIO()
            ta.save(buffer, wav, model.sr, format="wav")
            raw = buffer.getvalue()
            tempo = _tunable("OMNIGENT_TTS_TEMPO", SPEECH_TEMPO, 0.5, 1.5)
            if compressed := _encode_mp3(raw, tempo):
                return SummaryAudio(compressed, "resumo.mp3", "audio/mpeg")
            # No encoder: ship the model's own pace rather than vocoding it.
            return SummaryAudio(raw, "resumo.wav", "audio/wav")
        except Exception:  # noqa: BLE001 - a silent summary beats a failed turn
            _logger.warning("Summary speech generation failed", exc_info=True)
            return None


async def synthesize_summary(text: str, *, language: str = "pt-BR") -> SummaryAudio | None:
    """Generate summary audio off the event loop.

    Never raises: any failure returns ``None`` and the reader keeps the written
    summary with the browser engine available as before.

    :param text: The summary text to speak.
    :param language: Target language tag, e.g. ``"pt-BR"``.
    :returns: The encoded audio with its filename and MIME type, or ``None``.
    """
    if not tts_enabled():
        return None
    speech = (text or "").strip()
    if not speech or len(speech) > TTS_MAX_CHARS:
        return None
    return await asyncio.to_thread(_synthesize_sync, speech, language)
