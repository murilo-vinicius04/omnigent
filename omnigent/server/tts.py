"""Speak a summary in a chosen voice rather than the browser's default engine.

The browser's Web Speech API routes through whatever the host provides — on
Linux that is usually espeak-ng, which is robotic enough that people turn the
feature off. This synthesizes the summary server-side instead and ships the
audio with it, so the transcript carries a voice worth listening to.

Optional by design: the model and its CUDA stack are gigabytes, so the import
is lazy and every failure degrades to no audio. A deployment without the
``tts`` extra installed behaves exactly as it did before.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

_logger = logging.getLogger("omnigent.server.tts")

#: Semitones to lower the model's stock voice by. Its default sits high enough
#: to read as shrill over a small speaker; this lands it in a normal register.
VOICE_PITCH_SEMITONES: float = -2.0

#: Text spoken once to capture the reference the voice is cloned from. Its
#: content is irrelevant — only the timbre is reused.
_REFERENCE_TEXT: str = "Terminei o ajuste e rodei os testes. Passou tudo, pode conferir."

#: Longest summary worth synthesizing. Past this the audio outlasts anyone's
#: patience and the generation stops being free.
TTS_MAX_CHARS: int = 1200

_model: Any = None
_model_failed: bool = False


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


def voice_reference_path() -> Path:
    """Return the cached voice reference clip's path."""
    home = os.environ.get("OMNIGENT_CONFIG_HOME")
    base = Path(home) if home else Path.home() / ".omnigent"
    return base / "voice-reference.wav"


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


def _ensure_voice_reference(model: Any) -> Path | None:
    """Create the voice reference clip if it is not cached yet.

    The reference is the model's own voice pitched down, so the deployment
    needs no shipped audio and the result is reproducible from the model alone.

    :param model: The loaded TTS model.
    :returns: Path to the reference clip, or ``None`` on failure.
    """
    path = voice_reference_path()
    if path.exists():
        return path
    try:
        import torchaudio as ta
        from torchaudio.transforms import PitchShift

        wav = model.generate(_REFERENCE_TEXT, language_id="pt")
        shifted = PitchShift(model.sr, int(VOICE_PITCH_SEMITONES))(wav.cpu())
        path.parent.mkdir(parents=True, exist_ok=True)
        ta.save(str(path), shifted, model.sr)
        _logger.info("Captured summary voice reference at %s", path)
        return path
    except Exception:  # noqa: BLE001
        _logger.warning("Could not capture the voice reference", exc_info=True)
        return None


def _synthesize_sync(text: str, language: str) -> bytes | None:
    """Generate speech for *text*, returning WAV bytes.

    :param text: The text to speak.
    :param language: BCP-47 tag; only its language subtag is used.
    :returns: WAV bytes, or ``None`` when synthesis is unavailable.
    """
    model = _load_model()
    if model is None:
        return None
    reference = _ensure_voice_reference(model)
    try:
        import io

        import torchaudio as ta

        lang = (language or "pt").split("-")[0].lower()
        kwargs: dict[str, Any] = {"exaggeration": 0.4, "cfg_weight": 0.6, "temperature": 0.7}
        if reference is not None:
            kwargs["audio_prompt_path"] = str(reference)
        wav = model.generate(text, language_id=lang, **kwargs)
        buffer = io.BytesIO()
        ta.save(buffer, wav.cpu(), model.sr, format="wav")
        return buffer.getvalue()
    except Exception:  # noqa: BLE001 - a silent summary beats a failed turn
        _logger.warning("Summary speech generation failed", exc_info=True)
        return None


async def synthesize_summary(text: str, *, language: str = "pt-BR") -> bytes | None:
    """Generate summary audio off the event loop.

    Never raises: any failure returns ``None`` and the reader keeps the written
    summary with the browser engine available as before.

    :param text: The summary text to speak.
    :param language: Target language tag, e.g. ``"pt-BR"``.
    :returns: WAV bytes, or ``None``.
    """
    if not tts_enabled():
        return None
    speech = (text or "").strip()
    if not speech or len(speech) > TTS_MAX_CHARS:
        return None
    return await asyncio.to_thread(_synthesize_sync, speech, language)
