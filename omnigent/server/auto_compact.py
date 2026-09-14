"""Write the context down, then compact.

Claude Code already compacts on its own near the end of the window (~967K of
1M by default; ``/autocompact`` or ``CLAUDE_CODE_AUTO_COMPACT_WINDOW`` move
it). What it does not do is ask for the context to be written down first --
and that is the whole point of compacting early. A summary keeps what the
summarizer thought mattered; a file in the repo keeps what the next session
actually needs.

So this watches the context share Omnigent already records per turn
(``omnigent.last_context_tokens`` / ``omnigent.last_context_window``) and,
when a turn ends past the threshold, asks for the write-up. The compaction
itself is triggered when *that* turn ends, so the notes are written before
anything is dropped.

Nothing here talks to a session: the caller owns delivery, so the decision
stays testable on its own.
"""

from __future__ import annotations

import os
import time
from typing import Final, Literal

#: Share of the context window at which the write-up is asked for. Deliberately
#: far below Claude Code's own trigger: the point is to compact while there is
#: still room to think about what to keep.
DEFAULT_THRESHOLD_PCT: Final[int] = 60

#: Per-session override, so one session can compact earlier or later.
THRESHOLD_LABEL: Final[str] = "omnigent.autocompact_pct"

#: Kill switch. Unset means on.
ENABLED_ENV: Final[str] = "OMNIGENT_AUTO_COMPACT"

#: A threshold outside this range is a mistake, not a preference: under 10%
#: compacts constantly, over 95% is past Claude Code's own trigger.
MIN_THRESHOLD_PCT: Final[int] = 10
MAX_THRESHOLD_PCT: Final[int] = 95

#: How long before the same session may be asked again. A compaction that
#: fails leaves the context just as full, and without this the next turn end
#: would ask again, and the one after that.
RETRY_AFTER_S: Final[float] = 600.0

_TOKENS_LABEL: Final[str] = "omnigent.last_context_tokens"
_WINDOW_LABEL: Final[str] = "omnigent.last_context_window"

#: Where the UI reads what compaction is doing right now. Without it a
#: compaction is invisible: the work happens inside a turn, and the context
#: percentage beside it only changes when the NEXT turn ends -- so the reader
#: sees the old number and concludes nothing happened.
STATE_LABEL: Final[str] = "omnigent.autocompact_state"

#: The write-up was asked for; the session is answering it now.
STATE_WRITING_NOTES: Final[str] = "writing-notes"

#: Compacted, but the percentage beside it is still the pre-compaction
#: measurement until the next turn ends.
STATE_COMPACTED: Final[str] = "compacted"

#: What the reader's session is asked to write before it loses the detail.
DOCUMENTATION_PROMPT: Final[str] = (
    "[Omnigent] This session is at {pct}% of its context window, so it will be "
    "compacted as soon as you finish this turn. Before that, write down what a "
    "fresh session would need: what we are doing and why, what is decided, "
    "what is done and what is still open, the files, commands and identifiers "
    "that matter, and anything measured that would be expensive to find again. "
    "Put it where it belongs -- the notes or docs in this repo -- rather than "
    "only in your reply, and keep it short enough to read at a glance."
)

#: Sessions that have been asked for the write-up and whose next turn end
#: should compact, with the moment they were asked.
_ASKED: dict[str, float] = {}

#: When each session was last compacted. A compaction that freed nothing (it
#: failed, or the window was already mostly one long turn) leaves the share
#: above the threshold, and without this every following turn would ask again.
_LAST_COMPACTED: dict[str, float] = {}

Step = Literal["write-notes", "compact"]


def enabled() -> bool:
    """Whether auto-compaction is switched on. Unset means on."""
    return os.environ.get(ENABLED_ENV, "").strip().lower() not in {"0", "false", "no", "off"}


def threshold_pct(labels: dict[str, str] | None) -> int:
    """Return the share of the window at which to compact, for one session.

    :param labels: The session's labels, which may carry an override.
    :returns: A percentage between :data:`MIN_THRESHOLD_PCT` and
        :data:`MAX_THRESHOLD_PCT`.
    """
    raw = (labels or {}).get(THRESHOLD_LABEL, "").strip()
    try:
        wanted = int(float(raw))
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD_PCT
    return max(MIN_THRESHOLD_PCT, min(MAX_THRESHOLD_PCT, wanted))


def context_share_pct(labels: dict[str, str] | None) -> float | None:
    """Return how full the context window is, as a percentage.

    :param labels: The session's labels, carrying the last turn's tokens and
        window size.
    :returns: The percentage, or ``None`` when the session has not reported
        both numbers yet.
    """
    values = labels or {}
    try:
        tokens = float(values.get(_TOKENS_LABEL, ""))
        window = float(values.get(_WINDOW_LABEL, ""))
    except (TypeError, ValueError):
        return None
    if tokens <= 0 or window <= 0:
        return None
    return tokens / window * 100.0


def next_step(
    session_id: str, labels: dict[str, str] | None, *, now: float | None = None
) -> Step | None:
    """Decide what this turn's end should do about compaction.

    Two turns, deliberately: the write-up is asked for first, and only the
    turn that answers it is compacted. Compacting in one step would drop the
    detail the notes are made of.

    :param session_id: Session/conversation id, e.g. ``"conv_abc123"``.
    :param labels: The session's labels.
    :param now: Clock seam for tests.
    :returns: ``"write-notes"`` to ask for the write-up, ``"compact"`` to
        compact now, or ``None`` to do nothing.
    """
    moment = time.monotonic() if now is None else now
    if session_id in _ASKED:
        del _ASKED[session_id]
        _LAST_COMPACTED[session_id] = moment
        return "compact"
    if not enabled():
        return None
    if moment - _LAST_COMPACTED.get(session_id, float("-inf")) < RETRY_AFTER_S:
        return None
    share = context_share_pct(labels)
    if share is None or share < threshold_pct(labels):
        return None
    _ASKED[session_id] = moment
    return "write-notes"


def state_for(step: Step | None) -> str:
    """Return what the UI should say about compaction after this turn end.

    :param step: The step this turn end is taking, from :func:`next_step`.
    :returns: A value for :data:`STATE_LABEL`; ``""`` clears the label.
    """
    if step == "write-notes":
        return STATE_WRITING_NOTES
    if step == "compact":
        return STATE_COMPACTED
    return ""


def forget(session_id: str) -> None:
    """Drop any pending state for a session (it went away, or compaction failed).

    :param session_id: Session/conversation id.
    :returns: None.
    """
    _ASKED.pop(session_id, None)
