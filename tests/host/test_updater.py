"""Tests for the host side of a code update."""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest

from omnigent import code_bundle, code_version
from omnigent.host import updater
from omnigent.host.frames import HostUpdateFrame

_real_systemd_unit = updater.systemd_unit


def _repo(path: Path, files: dict[str, str]) -> Path:
    path.mkdir(parents=True)

    def run(*args: str) -> None:
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)

    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    for name, text in files.items():
        (path / name).write_text(text)
    run("add", "-A")
    run("commit", "-qm", "init")
    return path


@pytest.fixture(autouse=True)
def restarts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bool]]:
    """Record scheduled restarts and hide the ambient unit, so no test restarts a real service."""
    scheduled: list[tuple[str, bool]] = []
    monkeypatch.setattr(updater, "schedule_restart", scheduled.append)
    monkeypatch.setattr(updater, "systemd_unit", lambda cgroup=None: None)
    return scheduled


class _Server:
    """Serves a bundle of ``repo`` and remembers the Authorization header it saw."""

    def __init__(self, repo: Path, tmp: Path, status: int = 200) -> None:
        self.bundle = tmp / "served.tar.gz"
        code_bundle.build(repo, self.bundle)
        self.status = status
        self.auth: list[str | None] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.auth.append(request.headers.get("authorization"))
        if self.status != 200:
            return httpx.Response(self.status)
        return httpx.Response(200, content=self.bundle.read_bytes())


def _ask(force: bool = False) -> HostUpdateFrame:
    return HostUpdateFrame(request_id="r1", bundle_path="/v1/code/bundle", force=force)


async def _update(host: Path, server: _Server, tmp: Path, **kw):
    return await updater.update(
        _ask(kw.pop("force", False)),
        server_url="https://server.example",
        bearer="tok",
        live_runners=kw.pop("live_runners", 0),
        root=host,
        backup_dir=tmp / "backups",
        transport=server.transport,
        **kw,
    )


async def test_update_brings_the_host_to_the_servers_code(tmp_path: Path) -> None:
    server_repo = _repo(tmp_path / "server", {"a.py": "new\n"})
    host = _repo(tmp_path / "host", {"a.py": "old\n"})
    server = _Server(server_repo, tmp_path)

    result = await _update(host, server, tmp_path)

    assert result.status == "ok" and result.restarting is False
    assert "Restart this host" in result.message
    assert (host / "a.py").read_text() == "new\n"
    assert code_version.compute(host).id == code_version.compute(server_repo).id  # type: ignore[union-attr]
    assert server.auth == ["Bearer tok"]


async def test_update_schedules_a_restart_under_systemd(
    tmp_path: Path, restarts: list[tuple[str, bool]]
) -> None:
    server_repo = _repo(tmp_path / "server", {"a.py": "new\n"})
    host = _repo(tmp_path / "host", {"a.py": "old\n"})

    result = await _update(
        host, _Server(server_repo, tmp_path), tmp_path, unit=("h.service", True)
    )

    assert result.status == "ok" and result.restarting is True
    assert restarts == [("h.service", True)]


async def test_update_refuses_while_runners_are_alive_unless_forced(tmp_path: Path) -> None:
    server_repo = _repo(tmp_path / "server", {"a.py": "new\n"})
    host = _repo(tmp_path / "host", {"a.py": "old\n"})
    server = _Server(server_repo, tmp_path)

    busy = await _update(host, server, tmp_path, live_runners=2)
    assert busy.status == "busy" and "2 runner" in busy.message
    assert (host / "a.py").read_text() == "old\n" and server.auth == []

    forced = await _update(host, server, tmp_path, live_runners=2, force=True)
    assert forced.status == "ok" and (host / "a.py").read_text() == "new\n"


async def test_update_is_a_no_op_when_already_running_the_servers_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_repo = _repo(tmp_path / "server", {"a.py": "same\n"})
    host = _repo(tmp_path / "host", {"a.py": "same\n"})
    monkeypatch.setattr(code_version, "loaded", lambda: code_version.compute(host))

    result = await _update(host, _Server(server_repo, tmp_path), tmp_path)

    assert result.status == "current" and result.restarting is False


async def test_update_restarts_when_files_match_but_older_code_is_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_repo = _repo(tmp_path / "server", {"a.py": "same\n"})
    host = _repo(tmp_path / "host", {"a.py": "same\n"})
    stale = code_version.CodeVersion(id="stale", label="stale", root=str(host))
    monkeypatch.setattr(code_version, "loaded", lambda: stale)

    result = await _update(
        host, _Server(server_repo, tmp_path), tmp_path, unit=("h.service", True)
    )

    assert result.status == "ok" and result.restarting is True
    assert "already match" in result.message


async def test_failed_download_leaves_the_host_untouched(tmp_path: Path) -> None:
    server_repo = _repo(tmp_path / "server", {"a.py": "new\n"})
    host = _repo(tmp_path / "host", {"a.py": "old\n"})

    result = await _update(host, _Server(server_repo, tmp_path, status=401), tmp_path)

    assert result.status == "failed" and "download" in result.message
    assert (host / "a.py").read_text() == "old\n"


def test_systemd_unit_is_read_from_the_cgroup(monkeypatch: pytest.MonkeyPatch) -> None:
    user = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/omnigent-host.service\n"
    system = "0::/system.slice/omnigent-host.service\n"

    monkeypatch.delenv("INVOCATION_ID", raising=False)
    assert _real_systemd_unit(user) is None

    monkeypatch.setenv("INVOCATION_ID", "abc")
    assert _real_systemd_unit(user) == ("omnigent-host.service", True)
    assert _real_systemd_unit(system) == ("omnigent-host.service", False)
    assert _real_systemd_unit("0::/user.slice/session-3.scope\n") is None
