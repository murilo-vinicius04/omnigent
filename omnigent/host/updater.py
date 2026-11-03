"""Host side of a code update: fetch the server's bundle, apply it, restart to load it."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

from omnigent import code_bundle, code_version
from omnigent.host.frames import HostUpdateFrame, HostUpdateResultFrame

_UV_SYNC_TIMEOUT_S = 900.0
_RESTART_DELAY_S = 3


def systemd_unit(cgroup: str | None = None) -> tuple[str, bool] | None:
    """Return ``(unit name, is_user_unit)`` when this process runs under systemd, else ``None``.

    :param cgroup: Text of ``/proc/self/cgroup``; read from the system when omitted.
    """
    if not os.environ.get("INVOCATION_ID"):
        return None
    if cgroup is None:
        try:
            cgroup = Path("/proc/self/cgroup").read_text()
        except OSError:
            return None
    for line in cgroup.splitlines():
        path = line.split(":", 2)[-1]
        unit = next((p for p in reversed(path.split("/")) if p.endswith(".service")), "")
        if unit and not unit.startswith("user@"):
            return unit, "user@" in path
    return None


def schedule_restart(unit: tuple[str, bool]) -> None:
    """Restart the systemd unit shortly, outside this process, so the reply gets out first.

    :param unit: ``(name, is_user_unit)`` from :func:`systemd_unit`.
    """
    name, user = unit
    command = f"sleep {_RESTART_DELAY_S}; systemctl {'--user ' if user else ''}restart {name}"
    subprocess.Popen(
        ["sh", "-c", command],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _result(frame: HostUpdateFrame, status: str, message: str, *, restarting: bool = False):
    return HostUpdateResultFrame(
        request_id=frame.request_id, status=status, message=message, restarting=restarting
    )


async def _download(url: str, bearer: str | None, dest: Path, transport) -> None:
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    timeout = httpx.Timeout(60.0, read=300.0)
    async with (
        httpx.AsyncClient(timeout=timeout, transport=transport) as client,
        client.stream("GET", url, headers=headers) as resp,
    ):
        resp.raise_for_status()
        with dest.open("wb") as out:
            async for chunk in resp.aiter_bytes():
                out.write(chunk)


async def _uv_sync(root: Path) -> str | None:
    """Run ``uv sync --frozen``; return an error text, or ``None`` on success."""
    uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    if not Path(uv).exists():
        return "dependencies changed and uv was not found"
    proc = await asyncio.create_subprocess_exec(
        uv,
        "sync",
        "--frozen",
        cwd=root,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "VIRTUAL_ENV": str(Path(sys.prefix))},
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=_UV_SYNC_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        return "uv sync timed out"
    if proc.returncode != 0:
        return "uv sync failed: " + out.decode(errors="replace")[-300:]
    return None


async def update(
    frame: HostUpdateFrame,
    *,
    server_url: str,
    bearer: str | None,
    live_runners: int,
    root: Path | None = None,
    backup_dir: Path | None = None,
    unit: tuple[str, bool] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> HostUpdateResultFrame:
    """Bring this checkout to the server's code and say whether a restart was scheduled.

    :param frame: The request.
    :param server_url: Server base URL, e.g. ``"https://omnigent-vm.tail0994ab.ts.net"``.
    :param bearer: Token for the server, or ``None``.
    :param live_runners: Runners alive on this host; they end when it restarts.
    :param root: Checkout to update; defaults to the one this host runs from.
    :param backup_dir: Where replaced files are kept (default: ``update-backups`` in data dir).
    :param unit: systemd unit to restart; defaults to the one this process runs under.
    :param transport: HTTP transport override, for tests.
    :returns: The outcome. When ``restarting`` is true the restart is already scheduled.
    """
    root = root or code_version.find_root()
    if root is None:
        return _result(frame, "failed", "this host does not run from a git checkout")
    if live_runners and not frame.force:
        return _result(
            frame,
            "busy",
            f"{live_runners} runner(s) are alive and a restart would end their sessions",
        )
    unit = unit if unit is not None else systemd_unit()
    data_dir = Path(os.environ.get("OMNIGENT_DATA_DIR") or Path.home() / ".omnigent")
    backup_dir = backup_dir or data_dir / "update-backups"
    work = Path(tempfile.mkdtemp(prefix="omnigent-update-"))
    try:
        bundle = work / "bundle.tar.gz"
        try:
            await _download(server_url.rstrip("/") + frame.bundle_path, bearer, bundle, transport)
        except httpx.HTTPError as exc:
            return _result(frame, "failed", f"could not download the bundle: {exc}")
        try:
            manifest = await asyncio.to_thread(code_bundle.read_manifest, bundle)
            loaded = await asyncio.to_thread(code_version.loaded)
            on_disk = await asyncio.to_thread(code_version.compute, root)
            if on_disk is not None and on_disk.id == manifest.get("id"):
                if loaded is not None and loaded.id == on_disk.id:
                    return _result(frame, "current", "already running the server's code")
                changed = None
            else:
                changed = await asyncio.to_thread(code_bundle.apply, root, bundle, backup_dir)
        except code_bundle.BundleError as exc:
            return _result(frame, "failed", str(exc))
        if changed is not None and changed.dependencies_changed:
            problem = await _uv_sync(root)
            if problem:
                await asyncio.to_thread(code_bundle.restore, root, changed.backup)
                return _result(frame, "failed", f"{problem}; the previous code was restored")
        what = (
            f"updated {changed.written} files, removed {changed.removed}; backup {changed.backup}"
            if changed
            else "files already match; loading them"
        )
        if unit is None:
            return _result(frame, "ok", f"{what}. Restart this host to load the new code.")
        schedule_restart(unit)
        return _result(frame, "ok", f"{what}. Restarting.", restarting=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
