"""The plan meters are read on a timer, with one idle agy kept up for Gemini's."""

from __future__ import annotations

import asyncio
import contextlib

from omnigent.server import meter_keeper


class _Agy:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.stdin = None
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


async def test_one_agy_is_kept_up_and_the_meters_read_each_round() -> None:
    started: list[_Agy] = []
    reads: list[int] = []

    async def start():
        started.append(_Agy())
        return started[-1]

    async def read() -> None:
        reads.append(len(started))
        if len(reads) == 2:
            started[0].returncode = 1  # it died between rounds
        if len(reads) == 4:
            raise RuntimeError("Claude's usage API is down")  # a failed read waits

    task = asyncio.create_task(
        meter_keeper.keep_meters(interval_s=0.01, warm_up_s=0, start=start, read=read)
    )
    while len(reads) < 5:
        await asyncio.sleep(0.005)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    # Started once, again once after it died, never more.
    assert len(started) == 2 and reads[:3] == [1, 1, 2]
    # Stopping the server stops the agy.
    assert started[-1].terminated


def test_it_is_off_when_asked(monkeypatch) -> None:
    monkeypatch.setenv("OMNIGENT_METER_KEEPER", "0")
    assert not meter_keeper.enabled()
