"""Hidden tests for H1 ("the host died").

The host daemon cleans up exited children that are not its own runners. Each
test puts a real exited child the host does not own (an "orphan") in a
position a long-lived host really reaches, then sweeps through the existing
entry point ``HostProcess._reap_orphans_once`` and checks, through ``/proc``,
that the orphan is gone within a few seconds. A sweep that never returns fails
the test instead of hanging it.

Linux-only: the tests fork real children and read ``/proc``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from omnigent.host.connect import HostProcess, _RunnerHandle
from omnigent.host.identity import HostIdentity
from omnigent.host.runner_zygote import ZygoteManager

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="forks real children and reads /proc (Linux)"
)

_DEADLINE_S = 5.0


def _make_host() -> HostProcess:
    """A host with no zygote and no runners, never connected."""
    host = HostProcess(
        identity=HostIdentity(host_id="host_test_reaper", name="test-box"),
        server_url="http://localhost:8000",
    )
    host._zygote = None
    return host


def _state(pid: int) -> str | None:
    """The ``/proc`` state letter of ``pid`` (``"Z"`` for a zombie), or ``None`` once gone."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return None
    return stat.rsplit(")", 1)[1].split()[0]


def _wait_exited(pid: int) -> None:
    """Wait until ``pid`` has exited and is waiting to be collected (a zombie).

    Reads ``/proc`` so nothing here collects it.
    """
    deadline = time.monotonic() + _DEADLINE_S
    while time.monotonic() < deadline:
        if _state(pid) == "Z":
            return
        time.sleep(0.01)
    pytest.fail(f"child {pid} did not exit in time (state {_state(pid)!r})")


def _fork_orphan() -> int:
    """Fork a child nobody tracks that exits at once, and wait until it is a zombie."""
    pid = os.fork()
    if pid == 0:  # pragma: no cover - the child leg never returns to pytest
        os._exit(0)
    _wait_exited(pid)
    return pid


def _sweep(host: HostProcess) -> None:
    """Run one sweep, failing (instead of hanging) if it does not return."""
    errors: list[BaseException] = []

    def run() -> None:
        try:
            host._reap_orphans_once()
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(_DEADLINE_S)
    if worker.is_alive():
        pytest.fail("a cleanup sweep never returned")
    if errors:
        raise errors[0]


def _sweep_until_gone(host: HostProcess, pid: int) -> None:
    """Sweep the way the host does until ``pid`` is collected, or fail after a few seconds."""
    deadline = time.monotonic() + _DEADLINE_S
    while time.monotonic() < deadline:
        _sweep(host)
        if _state(pid) is None:
            return
        time.sleep(0.05)
    pytest.fail(f"exited child {pid} was never cleaned up (state {_state(pid)!r})")


def test_an_exited_zygote_nobody_polls_does_not_stop_orphan_cleanup() -> None:
    """The runner zygote was disabled, then exited, and nothing asks about it again.

    It exited before the orphan did. Orphans that exit after it must still be
    cleaned up, and the zygote's manager must still see it as not running.
    """
    host = _make_host()
    zygote = ZygoteManager()
    zygote._proc = subprocess.Popen(["sh", "-c", "exit 0"])
    host._zygote = zygote
    host._zygote_disabled = True
    _wait_exited(zygote._proc.pid)

    orphan = _fork_orphan()

    _sweep_until_gone(host, orphan)
    assert not zygote.is_running()


def test_a_collected_runners_pid_reused_by_an_orphan_does_not_stop_orphan_cleanup() -> None:
    """A runner the host still lists already exited and was collected; pids wrap.

    Its ``Popen`` keeps reporting the old pid, and the kernel has since handed
    that number to an unrelated orphan. The orphan must still be cleaned up,
    and the finished runner's recorded exit code must not change.
    """
    host = _make_host()
    orphan = _fork_orphan()
    runner = subprocess.Popen(["sh", "-c", "exit 0"])
    assert runner.wait(timeout=_DEADLINE_S) == 0
    runner.pid = orphan  # the collected runner's number, now an orphan's
    host._runners["runner_done"] = _RunnerHandle(proc=runner, log_path=Path("/dev/null"))

    _sweep_until_gone(host, orphan)
    assert runner.returncode == 0


def test_cleanup_behind_a_stale_runner_pid_keeps_a_crashed_runners_exit_code(
    tmp_path: Path,
) -> None:
    """Orphans get cleaned up, but a runner's crash code still reaches its owner.

    Same stale-pid orphan as above, and behind it a live runner that has just
    crashed with exit code 3 and that its watcher has not polled yet. Once the
    orphan is gone, the runner's own ``poll()`` must still report 3: the host
    reports runner crashes from that code.
    """
    host = _make_host()
    orphan = _fork_orphan()
    finished = subprocess.Popen(["sh", "-c", "exit 0"])
    assert finished.wait(timeout=_DEADLINE_S) == 0
    finished.pid = orphan
    host._runners["runner_done"] = _RunnerHandle(proc=finished, log_path=tmp_path / "a.log")

    crashed = subprocess.Popen(["sh", "-c", "exit 3"])
    host._runners["runner_crashed"] = _RunnerHandle(proc=crashed, log_path=tmp_path / "b.log")
    _wait_exited(crashed.pid)

    _sweep_until_gone(host, orphan)
    assert crashed.poll() == 3, "the crashed runner's exit code was lost"
