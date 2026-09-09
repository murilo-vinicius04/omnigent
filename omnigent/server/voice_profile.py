"""A persistent voice profile for the rewrite layer.

The rewrite is the only voice the reader actually hears, so its register
matters more than anything else about it. Left to its own devices a model
writes like documentation — correct, and nothing you would want read to you
every turn.

This loads a plain-text profile the reader owns and edits: how to address them,
how casual to be, words to use or avoid, and examples of how they themselves
write. It is appended to the rewrite instructions, so editing the file changes
the voice on the next turn with no restart.

Deliberately a file rather than a setting: it is prose, it wants examples, and
the reader should be able to open it and rewrite it wholesale.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

_logger = logging.getLogger("omnigent.server.voice_profile")

#: Filename of the profile inside the Omnigent config home.
VOICE_PROFILE_FILENAME: str = "voice.md"

#: Cap on the profile text folded into every rewrite prompt. Generous for
#: prose and examples, bounded so an accidental paste cannot dominate the call.
VOICE_PROFILE_MAX_CHARS: int = 4000

#: What ships on first use. Casual on purpose — the formal register is the
#: failure mode this file exists to correct.
DEFAULT_VOICE_PROFILE: str = """\
# Voice

How to talk to me. Edit freely — this file is read fresh on every turn.

- Talk like a colleague I actually know, not a support desk. Contractions, plain
  words, no throat-clearing.
- Get to the point in the first sentence. No "I have completed the task of".
- It's fine to be blunt. If something broke, say it broke.
- Don't hedge or pad. Don't apologise unless something actually went wrong.
- Never open with a compliment about my question.
- Skip the sign-offs and the "let me know if you need anything else".

## Words

- Prefer: "achei", "quebrou", "consertei", "tá funcionando", "faltou"
- Avoid: "prezado", "solicitação", "efetuado", "conforme mencionado",
  "gostaria de informar"

## How I write (mirror this register)

Add lines here in your own words and the rewrite will follow your register.
"""


def voice_profile_path() -> Path:
    """Return the effective path of the voice profile.

    :returns: ``$OMNIGENT_CONFIG_HOME/voice.md`` when set, else
        ``~/.omnigent/voice.md``.
    """
    if config_home := os.environ.get("OMNIGENT_CONFIG_HOME"):
        return Path(config_home) / VOICE_PROFILE_FILENAME
    return Path.home() / ".omnigent" / VOICE_PROFILE_FILENAME


def ensure_voice_profile() -> Path:
    """Create the profile with its default contents when absent.

    :returns: The profile path, whether or not it was just created.
    """
    path = voice_profile_path()
    try:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(DEFAULT_VOICE_PROFILE, encoding="utf-8")
    except OSError as exc:
        _logger.warning("Could not create voice profile at %s: %s", path, exc)
    return path


def load_voice_profile() -> str | None:
    """Read the reader's voice profile, if any.

    Never raises: an unreadable profile degrades to the model's default voice
    rather than failing the rewrite.

    :returns: The profile text, truncated to the cap, or ``None`` when absent
        or empty.
    """
    path = voice_profile_path()
    try:
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        _logger.warning("Could not read voice profile at %s: %s", path, exc)
        return None
    if not text:
        return None
    return text[:VOICE_PROFILE_MAX_CHARS]


#: How many of the reader's own messages to fold in as register examples.
VOICE_PROFILE_SAMPLE_COUNT: int = 12

#: Marker beneath which sampled examples are written. Everything above it is
#: the reader's own prose and is never touched.
VOICE_PROFILE_SAMPLES_HEADING: str = "## How I write (mirror this register)"


def render_voice_samples(messages: list[str]) -> str:
    """Render the reader's own messages as register examples.

    Their own words are the most accurate description of how they want to be
    spoken to, and cost nothing to collect.

    :param messages: The reader's recent message texts, newest first.
    :returns: The examples block, or an empty string when there is nothing
        worth showing.
    """
    picked: list[str] = []
    seen: set[str] = set()
    for raw in messages:
        text = " ".join(raw.split())
        # Long messages are usually pasted content, not the reader's own voice.
        if not (12 <= len(text) <= 240):
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        picked.append(text)
        if len(picked) >= VOICE_PROFILE_SAMPLE_COUNT:
            break
    if not picked:
        return ""
    lines = "\n".join(f"- {t}" for t in picked)
    return f"{VOICE_PROFILE_SAMPLES_HEADING}\n\n{lines}\n"


def write_voice_samples(messages: list[str]) -> Path:
    """Replace the examples section of the profile, preserving the prose above it.

    :param messages: The reader's recent message texts, newest first.
    :returns: The profile path.
    """
    path = ensure_voice_profile()
    block = render_voice_samples(messages)
    if not block:
        return path
    try:
        current = path.read_text(encoding="utf-8")
    except OSError as exc:
        _logger.warning("Could not read voice profile at %s: %s", path, exc)
        return path
    head = current.split(VOICE_PROFILE_SAMPLES_HEADING)[0].rstrip()
    try:
        path.write_text(f"{head}\n\n{block}", encoding="utf-8")
    except OSError as exc:
        _logger.warning("Could not write voice profile at %s: %s", path, exc)
    return path
