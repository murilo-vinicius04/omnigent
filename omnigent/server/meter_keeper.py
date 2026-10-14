"""Keep the plan meters current, Gemini's included, while the server runs.

Gemini's quota is read from a running ``agy``: its local language server
answers ``RetrieveUserQuotaSummary``. With no agy up, the reading ages until
the hub stops giving Gemini work. This keeps one idle agy alive -- it is never
sent a prompt, so it spends no model quota -- and reads every plan meter on an
interval, so the hub decides on current numbers even when no page is open.

``OMNIGENT_METER_KEEPER=0`` turns it off.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import pathlib
import shutil
from collections.abc import Awaitable, Callable
from typing import Final

from omnigent.antigravity_native_rpc import companion_csrf_token

_logger = logging.getLogger(__name__)

#: How often every plan meter is read.
INTERVAL_S: Final[float] = 300.0

#: A fresh agy needs a moment before its language server answers.
WARM_UP_S: Final[float] = 5.0

#: How long a stopping agy gets before it is killed.
STOP_GRACE_S: Final[float] = 5.0

#: Where the idle agy runs: an empty folder of its own.
WORKDIR: Final[pathlib.Path] = pathlib.Path.home() / ".omnigent" / "companion" / "meter-agy"


def enabled() -> bool:
    """On unless ``OMNIGENT_METER_KEEPER=0``, and only where agy is installed."""
    if os.environ.get("OMNIGENT_METER_KEEPER", "").strip() == "0":
        return False
    return shutil.which(_binary()) is not None


def _binary() -> str:
    return os.environ.get("OMNIGENT_DISCUSSION_AGY_BIN", "").strip() or "agy"


def _model() -> str:
    from omnigent.server.discussion import _model as companion_model

    return companion_model()


async def start_idle_agy() -> asyncio.subprocess.Process | None:
    """One agy waiting on stdin, with the token the quota reader knows; ``None`` if it fails."""
    binary = shutil.which(_binary())
    if binary is None:
        return None
    token = companion_csrf_token()
    WORKDIR.mkdir(parents=True, exist_ok=True)
    try:
        return await asyncio.create_subprocess_exec(
            binary,
            "--print=",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--model",
            _model(),
            "--disable-slash-commands",
            "--csrf_token",
            token,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=str(WORKDIR),
            env={**os.environ, "ANTIGRAVITY_CSRF_TOKEN": token},
        )
    except OSError:
        _logger.warning("meter keeper: could not start agy", exc_info=True)
        return None


async def read_meters() -> None:
    """Read every provider's plan meter; the readings are cached on disk."""
    from omnigent.server.routes.plan_limits import collect_plan_limits

    await collect_plan_limits()


async def stop(process: asyncio.subprocess.Process | None) -> None:
    """Stop the idle agy: close its stdin, then terminate, then kill."""
    if process is None or process.returncode is not None:
        return
    if process.stdin is not None:
        with contextlib.suppress(Exception):
            process.stdin.close()
    with contextlib.suppress(ProcessLookupError):
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), STOP_GRACE_S)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()


async def keep_meters(
    *,
    interval_s: float = INTERVAL_S,
    warm_up_s: float = WARM_UP_S,
    start: Callable[[], Awaitable[asyncio.subprocess.Process | None]] = start_idle_agy,
    read: Callable[[], Awaitable[None]] = read_meters,
) -> None:
    """Keep one idle agy up and read the meters every *interval_s*, until cancelled.

    An agy that exits is started again on the next round, so a crash costs
    one interval at most.
    """
    process: asyncio.subprocess.Process | None = None
    try:
        while True:
            if process is None or process.returncode is not None:
                if process is not None:
                    _logger.info(
                        "meter keeper: agy exited (%s); starting another", process.returncode
                    )
                process = await start()
                if process is not None:
                    await asyncio.sleep(warm_up_s)
            try:
                await read()
            except Exception:  # noqa: BLE001 - a failed read waits for the next round
                _logger.info("meter keeper: meters not read", exc_info=True)
            await asyncio.sleep(interval_s)
    finally:
        await stop(process)
