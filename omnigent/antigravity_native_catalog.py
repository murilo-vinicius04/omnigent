"""The pre-launch model catalog for native Antigravity (agy) sessions.

Every other native harness can answer "which models could this run?" before a
session exists — codex and claude from their own probed catalogs, pi from the
provider `omni setup` configured. Antigravity could not: its catalog was
reachable only over the connect-RPC port of a RUNNING agy
(:func:`omnigent.antigravity_native_rpc.get_available_models`), which is no use
to a picker that has not launched anything yet. So the host answered
"model options are unsupported for harness 'antigravity-native'", and every
surface that asks — the new-session picker, and the per-sub-agent model row for
a head retargeted onto agy — had nothing to show.

``agy models`` answers the same question from the CLI, printing one
tab-separated ``id<TAB>display name`` per line. That is the probe here.

Answers are cached in the shared :mod:`omnigent.model_catalog_store`, keyed by
the agy binary's identity, so an upgraded agy misses rather than serving the
previous release's list.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from omnigent import model_catalog_store

_logger = logging.getLogger(__name__)

#: Canonical harness name this catalog is stored under.
HARNESS = "antigravity-native"

#: How long ``agy models`` may take. It is a network call (the CLI prints
#: "Fetching available models..." first), so this is generous compared with a
#: local listing — but bounded, because a picker is waiting on it.
_PROBE_TIMEOUT_S = 30.0


def parse_agy_models(stdout: str) -> list[dict[str, Any]]:
    """Parse ``agy models`` output into catalog rows.

    The command prints a human progress line before the table, and one
    ``id<TAB>display name`` pair per model after it. Anything without a tab is
    not a row — that is the whole discriminator, so a future banner or trailing
    hint is ignored rather than parsed as a model named after itself.

    :param stdout: Captured stdout, e.g.
        ``"Fetching available models...\\ngemini-3.8-flash-low\\tGemini 3.8 Flash (Low)\\n"``.
    :returns: Rows shaped like every other harness's — ``id``, ``model`` and
        ``displayName`` — in the order agy listed them, which is its own
        preference order. Empty when nothing parsed.
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in stdout.splitlines():
        model_id, tab, display = line.partition("\t")
        model_id, display = model_id.strip(), display.strip()
        if not tab or not model_id or model_id in seen:
            continue
        seen.add(model_id)
        rows.append({"id": model_id, "model": model_id, "displayName": display or model_id})
    return rows


async def _probe_agy_models(agy_path: str) -> list[dict[str, Any]] | None:
    """Run ``agy models`` once and parse it.

    :param agy_path: Absolute path to the agy executable.
    :returns: Parsed rows, or ``None`` when the command failed, timed out, or
        printed nothing a row could be read from.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            agy_path,
            "models",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        _logger.warning("could not run %r to list Antigravity models", agy_path, exc_info=True)
        return None
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_PROBE_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        # Reap it: an unawaited killed child stays a zombie for the life of the
        # host process, and this probe runs on every picker open.
        await proc.wait()
        _logger.warning("`agy models` did not answer within %.0fs", _PROBE_TIMEOUT_S)
        return None
    if proc.returncode != 0:
        _logger.warning(
            "`agy models` exited %s: %s",
            proc.returncode,
            stderr.decode("utf-8", "replace")[:200],
        )
        return None
    rows = parse_agy_models(stdout.decode("utf-8", "replace"))
    # An empty parse is a failure, not an empty catalog: agy always lists
    # something when it answers at all, so nothing parsed means the output
    # shape changed. Returning [] would persist that as the truth.
    return rows or None


async def antigravity_launch_catalog(
    *, agy_path: str | None = None
) -> list[dict[str, Any]] | None:
    """The agy model catalog for a pre-launch picker: store, then probe.

    A stored entry is served even when stale — the picker gets an answer now,
    and the next miss after the binary changes re-probes. A failed probe leaves
    any stored rows in place rather than replacing them with nothing.

    :param agy_path: Optional agy executable override. When omitted, resolved
        the way a launch resolves it.
    :returns: Catalog rows, or ``None`` when no catalog could be obtained —
        agy missing, the command failing, or its output unreadable.
    """
    if agy_path is None:
        from omnigent.antigravity_native_launch import agy_binary_path

        try:
            agy_path = await asyncio.to_thread(agy_binary_path)
        except RuntimeError:
            # agy is not installed on this host. Not an error worth a warning:
            # the picker simply offers no models for a harness this machine
            # cannot run, which the harness badge already says out loud.
            return None

    fingerprint = model_catalog_store.fingerprint_of(model_catalog_store.binary_identity(agy_path))
    stored = model_catalog_store.read_catalog(HARNESS, fingerprint)
    if stored:
        return stored

    rows = await _probe_agy_models(agy_path)
    if rows is None:
        return None
    model_catalog_store.write_catalog(HARNESS, fingerprint, rows)
    return rows
