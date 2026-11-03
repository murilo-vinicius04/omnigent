#!/usr/bin/env python3
"""Restart the Omnigent server only after proving the new code starts, then prove it came back.

Standard library only. Steps, each of which stops the run if it fails:

1. Boot the server's exact command on a spare port with an empty database and its own data
   directory (so it cannot touch the real login, hub state or scheduled tasks) and wait for
   ``/health``. A broken edit in the shared worktree is caught here, before anything goes down.
2. ``systemctl --user restart`` the real unit.
3. Wait for ``/health`` on the real port, then check the stored local login, the Tailscale
   proxy and that the hosts that were online are online again.

Run it from anywhere:  python3 scripts/restart_server.py [--skip-smoke]
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

UNIT = "omnigent-server.service"
SMOKE_PORT = 6799
HEALTH_TIMEOUT_S = 120.0
NOTICE_LEAD_S = 30
NOTICE_LIFETIME_S = 900


def write_restart_notice(web_dir: Path, notice_id: str, phase: str) -> None:
    """Publish a non-secret notice atomically for open web clients to poll."""
    path = web_dir / "server-restart-notice.json"
    now = time.time()
    payload = {"id": notice_id, "phase": phase, "expires_at": now + NOTICE_LIFETIME_S}
    with tempfile.NamedTemporaryFile(mode="w", dir=web_dir, delete=False, encoding="utf-8") as f:
        try:
            json.dump(payload, f)
            f.flush()
            os.fchmod(f.fileno(), 0o644)
            os.replace(f.name, path)
        finally:
            Path(f.name).unlink(missing_ok=True)


def systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    """Run ``systemctl --user`` with the runtime dir set, so it works from any shell."""
    env = {**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"}
    return subprocess.run(
        ["systemctl", "--user", *args], capture_output=True, text=True, env=env, check=False
    )


def parse_unit(text: str) -> tuple[list[str], dict[str, str], str]:
    """Return ``(command, environment, working directory)`` from ``systemctl cat`` output."""
    command: list[str] = []
    env: dict[str, str] = {}
    cwd = str(Path.home())
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("ExecStart="):
            command = shlex.split(line.removeprefix("ExecStart="))
        elif line.startswith("Environment="):
            for item in shlex.split(line.removeprefix("Environment=")):
                key, _, value = item.partition("=")
                env[key] = value
        elif line.startswith("WorkingDirectory="):
            cwd = line.removeprefix("WorkingDirectory=")
    return command, env, cwd


def with_port(command: list[str], port: int) -> list[str]:
    """The same command listening on another port."""
    out = list(command)
    for i, part in enumerate(out):
        if part == "--port" and i + 1 < len(out):
            out[i + 1] = str(port)
            return out
    return [*out, "--port", str(port)]


def health(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def wait_health(port: int, timeout_s: float, proc: subprocess.Popen[bytes] | None = None) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if health(port):
            return True
        if proc is not None and proc.poll() is not None:
            return False
        time.sleep(1.0)
    return False


def smoke_boot(command: list[str], env: dict[str, str], cwd: str) -> bool:
    """Start the server on a spare port with a throwaway data dir; True if it answers /health."""
    scratch = Path(tempfile.mkdtemp(prefix="omnigent-smoke-"))
    log = scratch / "boot.log"
    proc: subprocess.Popen[bytes] | None = None
    try:
        with log.open("wb") as out:
            proc = subprocess.Popen(
                with_port(command, SMOKE_PORT),
                cwd=cwd,
                env={**os.environ, **env, "OMNIGENT_DATA_DIR": str(scratch)},
                stdout=out,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
            ok = wait_health(SMOKE_PORT, HEALTH_TIMEOUT_S, proc)
        if not ok:
            print("  smoke boot failed; last lines of its output:")
            print("    " + "\n    ".join(log.read_text(errors="replace").splitlines()[-15:]))
        return ok
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(scratch, ignore_errors=True)


def online_hosts(db: Path) -> set[str]:
    """Names of hosts the database says are online (status 1), read-only."""
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        try:
            return {r[0] for r in con.execute("SELECT name FROM hosts WHERE status = 1")}
        finally:
            con.close()
    except sqlite3.Error:
        return set()


def stored_login(data_dir: Path, port: int) -> str | None:
    try:
        entry = json.loads((data_dir / "auth_tokens.json").read_text())[f"http://127.0.0.1:{port}"]
        return entry.get("user_id")
    except (OSError, ValueError, KeyError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-smoke", action="store_true", help="restart without the trial boot")
    parser.add_argument(
        "--smoke-only", action="store_true", help="only try booting the new code; restart nothing"
    )
    args = parser.parse_args(argv)

    cat = systemctl("cat", UNIT)
    if cat.returncode != 0:
        sys.exit(f"Cannot read {UNIT}: {cat.stderr.strip()}")
    command, env, cwd = parse_unit(cat.stdout)
    if not command:
        sys.exit("No ExecStart found in the unit.")
    port = int(command[command.index("--port") + 1]) if "--port" in command else 6767
    data_dir = Path(os.environ.get("OMNIGENT_DATA_DIR") or Path.home() / ".omnigent")
    expected_user = env.get("OMNIGENT_ACCOUNTS_INIT_ADMIN_USERNAME")
    hosts_before = online_hosts(data_dir / "chat.db")

    if not args.skip_smoke:
        print("1/3 trial boot of the new code on a spare port ...")
        if not smoke_boot(command, env, cwd):
            sys.exit("The new code does not start. The running server was NOT touched.")
        print("  ok")
    if args.smoke_only:
        print("Trial boot passed; nothing was restarted.")
        return 0
    web_dir = Path(env.get("OMNIGENT_WEB_UI_DIST") or Path(cwd) / "omnigent/server/static/web-ui")
    notice_id = uuid.uuid4().hex
    try:
        write_restart_notice(web_dir, notice_id, "planned")
    except OSError as exc:
        sys.exit(f"Could not publish the restart notice; the server was NOT touched: {exc}")
    print(f"  Restart notice published; waiting {NOTICE_LEAD_S}s for open pages ...", flush=True)
    time.sleep(NOTICE_LEAD_S)
    print("2/3 restarting the server ...")
    done = systemctl("restart", UNIT)
    if done.returncode != 0:
        write_restart_notice(web_dir, notice_id, "failed")
        sys.exit(f"systemctl restart failed: {done.stderr.strip()}")
    print(f"3/3 waiting for it on port {port} ...")
    if not wait_health(port, HEALTH_TIMEOUT_S):
        write_restart_notice(web_dir, notice_id, "failed")
        tail = subprocess.run(
            ["journalctl", "--user", "-u", UNIT, "-n", "20", "--no-pager"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        sys.exit(f"The server did not answer /health in time. The unit keeps retrying.\n{tail}")
    write_restart_notice(web_dir, notice_id, "complete")
    problems: list[str] = []
    login = stored_login(data_dir, port)
    if expected_user and login != expected_user:
        problems.append(f"stored local login is {login!r}, expected {expected_user!r}")
    serve = subprocess.run(
        ["tailscale", "serve", "status"], capture_output=True, text=True, check=False
    ).stdout
    if f"127.0.0.1:{port}" not in serve:
        problems.append(f"tailscale serve does not point at 127.0.0.1:{port}")
    deadline = time.monotonic() + 90
    missing = hosts_before
    while missing and time.monotonic() < deadline:
        missing = hosts_before - online_hosts(data_dir / "chat.db")
        if missing:
            time.sleep(3)
    if missing:
        problems.append("hosts not back online: " + ", ".join(sorted(missing)))
    state = systemctl("is-active", UNIT).stdout.strip()
    print(
        f"  server {state}; stored login {login!r}; hosts online: {sorted(hosts_before - missing)}"
    )
    if problems:
        sys.exit("Server is up but needs attention:\n  - " + "\n  - ".join(problems))
    print("Server is back and everything checks out.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
