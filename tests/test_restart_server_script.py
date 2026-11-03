"""Tests for the pure parts of the safe server restart script."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "restart_server.py"


def _load():
    spec = importlib.util.spec_from_file_location("restart_server_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


UNIT_TEXT = """# /home/u/.config/systemd/user/omnigent-server.service
[Service]
WorkingDirectory=/srv/omnigent
Environment=OMNIGENT_AUTH_ENABLED=1
Environment=OMNIGENT_FEATURES=usage_page
ExecStart=/srv/omnigent/.venv/bin/omni server --host 127.0.0.1 --port 6767 --agent /a --agent /b
Restart=on-failure
# /home/u/.config/systemd/user/omnigent-server.service.d/local-admin.conf
[Service]
Environment=OMNIGENT_ACCOUNTS_INIT_ADMIN_USERNAME=morys5
"""


def test_command_environment_and_directory_come_from_the_unit_and_its_drop_ins() -> None:
    script = _load()

    command, env, cwd = script.parse_unit(UNIT_TEXT)

    assert command[:3] == ["/srv/omnigent/.venv/bin/omni", "server", "--host"]
    assert command.count("--agent") == 2
    assert env == {
        "OMNIGENT_AUTH_ENABLED": "1",
        "OMNIGENT_FEATURES": "usage_page",
        "OMNIGENT_ACCOUNTS_INIT_ADMIN_USERNAME": "morys5",
    }
    assert cwd == "/srv/omnigent"


def test_the_trial_boot_uses_the_same_command_on_another_port() -> None:
    script = _load()
    command, _, _ = script.parse_unit(UNIT_TEXT)

    trial = script.with_port(command, 6799)

    assert trial[trial.index("--port") + 1] == "6799"
    assert [p for p in trial if p != "6799"] == [p for p in command if p != "6767"]
    assert script.with_port(["omni", "server"], 6799)[-2:] == ["--port", "6799"]


def test_online_hosts_and_the_stored_login_are_read_without_writing(tmp_path: Path) -> None:
    script = _load()
    db = tmp_path / "chat.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE hosts (name TEXT, status INTEGER)")
    con.executemany("INSERT INTO hosts VALUES (?, ?)", [("ubuntu-gpu", 1), ("nexus", 2)])
    con.commit()
    con.close()
    (tmp_path / "auth_tokens.json").write_text(
        json.dumps({"http://127.0.0.1:6767": {"user_id": "morys5", "token": "x"}})
    )

    assert script.online_hosts(db) == {"ubuntu-gpu"}
    assert script.online_hosts(tmp_path / "missing.db") == set()
    assert script.stored_login(tmp_path, 6767) == "morys5"
    assert script.stored_login(tmp_path, 9999) is None


def test_restart_notice_is_replaced_with_readable_public_status(tmp_path: Path) -> None:
    script = _load()
    script.write_restart_notice(tmp_path, "event", "planned")
    path = tmp_path / "server-restart-notice.json"
    assert json.loads(path.read_text())["phase"] == "planned"
    script.write_restart_notice(tmp_path, "event", "complete")
    notice = json.loads(path.read_text())
    assert notice["id"] == "event"
    assert notice["phase"] == "complete"
    assert notice["expires_at"] > 0
    assert path.stat().st_mode & 0o004


def test_restart_announces_before_stopping_and_marks_complete_after_health(
    tmp_path: Path,
    monkeypatch,
) -> None:
    script = _load()
    events = []
    monkeypatch.setattr(
        script, "parse_unit", lambda _: (["omni", "--port", "6767"], {}, str(tmp_path))
    )
    monkeypatch.setattr(script, "online_hosts", lambda _: set())
    monkeypatch.setattr(script, "stored_login", lambda *_: None)
    monkeypatch.setattr(script, "smoke_boot", lambda *_: True)
    monkeypatch.setattr(script, "wait_health", lambda *_: events.append("healthy") or True)
    monkeypatch.setattr(script, "write_restart_notice", lambda _, __, phase: events.append(phase))
    monkeypatch.setattr(script.time, "sleep", lambda _: events.append("wait"))

    def systemctl(*args):
        if args[0] == "restart":
            events.append("restart")
        return subprocess.CompletedProcess(args, 0, stdout="active", stderr="")

    monkeypatch.setattr(script, "systemctl", systemctl)
    monkeypatch.setattr(
        script.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="127.0.0.1:6767", stderr=""),
    )
    assert script.main([]) == 0
    assert events == ["planned", "wait", "restart", "healthy", "complete"]
