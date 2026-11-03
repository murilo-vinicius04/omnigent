#!/usr/bin/env python3
"""Bridge duet notes into an already-running Claude policy hook.

Run on the runner's machine as its user, with --bridge-dir pointing at the
session's existing bridge directory. The runner and Claude remain untouched.
The file is intentionally stdlib-only so a host need not update its checkout.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import signal
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import suppress
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def _post(url: str, token: str, value: dict[str, Any], timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        data=json.dumps(value).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _relay_call(url: str, token: str, op: str, ids: list[str] | None = None) -> dict[str, Any]:
    args: dict[str, Any] = {"op": op, "_omnigent_hook_transport": True}
    if ids is not None:
        args["ids"] = ids
    status, raw = _post(
        url + "/tool",
        token,
        {
            "name": "sys_duet_inbox",
            "arguments": args,
        },
        timeout=1.0,
    )
    if status != 200:
        return {}
    envelope = json.loads(raw)
    if envelope.get("isError"):
        return {}
    value = json.loads(envelope["content"][0]["text"])
    return value if isinstance(value, dict) else {}


def _with_notes(raw: bytes, notes: list[dict[str, Any]]) -> bytes | None:
    try:
        output = json.loads(raw) if raw else {}
        if not isinstance(output, dict):
            return None
        detail = output.get("hookSpecificOutput", {})
        if (
            not isinstance(detail, dict)
            or detail.get("hookEventName", "PostToolUse") != "PostToolUse"
        ):
            return None
        old = detail.get("additionalContext", "")
        if not isinstance(old, str):
            return None
        blocks = []
        for note in notes:
            stamp = datetime.fromtimestamp(float(note["created_at"]), timezone.utc)
            blocks.append(f"Received note created {stamp:%Y-%m-%d %H:%M:%S UTC}:\n{note['text']}")
        detail["hookEventName"] = "PostToolUse"
        detail["additionalContext"] = "\n\n".join(filter(None, [old, *blocks]))
        output["hookSpecificOutput"] = detail
        return json.dumps(output, ensure_ascii=False).encode()
    except (ValueError, TypeError, KeyError, OverflowError, OSError):
        return None


def make_handler(upstream: str, upstream_token: str, token: str) -> type[BaseHTTPRequestHandler]:
    """Forward policy unchanged and append notes only to successful post-tool hooks."""
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            if self.path != "/hook/claude/evaluate-policy" or not hmac.compare_digest(
                self.headers.get("Authorization", ""), f"Bearer {token}"
            ):
                self.send_error(403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 16 * 1024 * 1024:
                    raise ValueError("invalid length")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("invalid payload")
                status, raw = _post(upstream + self.path, upstream_token, payload, timeout=86410)
            except (OSError, ValueError, urllib.error.URLError):
                self.send_error(502)
                return
            ids: list[str] = []
            acquired = (
                status == 200
                and payload.get("hook_event_name") == "PostToolUse"
                and lock.acquire(False)
            )
            try:
                if acquired:
                    try:
                        batch = _relay_call(upstream, upstream_token, "peek").get("notes", [])
                        if isinstance(batch, list):
                            batch = [
                                n
                                for n in batch[:5]
                                if isinstance(n, dict)
                                and isinstance(n.get("id"), str)
                                and isinstance(n.get("text"), str)
                            ]
                            result = _with_notes(raw, batch) if batch else None
                            if result is not None:
                                raw, ids = result, [n["id"] for n in batch]
                    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
                        pass
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                if raw:
                    self.wfile.write(raw)
                    self.wfile.flush()
                if ids:
                    with suppress(OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
                        _relay_call(upstream, upstream_token, "ack", ids)
            finally:
                if acquired:
                    lock.release()

    return Handler


def _atomic_write(path: Path, content: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=".duet-relay-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _restore(path: Path, installed: bytes, original: bytes) -> None:
    try:
        if path.read_bytes() == installed:
            _atomic_write(path, original)
    except OSError:
        pass


def run(bridge_dir: Path) -> None:
    info = json.loads((bridge_dir / "tool_relay.json").read_text())
    upstream, upstream_token = info["url"], info["token"]
    parsed = urlsplit(upstream)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
        raise ValueError("relay is not a loopback HTTP endpoint")
    env = bridge_dir / "tool_relay.env"
    original = env.read_bytes()
    expected = (
        f"OMNIGENT_RELAY_URL='{upstream}'\nOMNIGENT_RELAY_TOKEN='{upstream_token}'\n"
    ).encode()
    if original != expected:
        raise ValueError("relay env changed or is already wrapped")
    token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(upstream, upstream_token, token))
    installed = (
        f"OMNIGENT_RELAY_URL='http://127.0.0.1:{server.server_port}'\n"
        f"OMNIGENT_RELAY_TOKEN='{token}'\n"
    ).encode()
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(write_fd)
        os.close(server.fileno())
        try:
            os.read(read_fd, 1)  # EOF when the proxy exits, even after SIGKILL.
            _restore(env, installed, original)
        finally:
            os._exit(0)
    os.close(read_fd)
    try:
        if env.read_bytes() != original:
            raise RuntimeError("relay changed during setup")
        _atomic_write(env, installed)

        def watch_relay_replacement() -> None:
            while True:
                time.sleep(1)
                try:
                    if env.read_bytes() == installed:
                        continue
                except OSError:
                    pass
                server.shutdown()
                return

        threading.Thread(target=watch_relay_replacement, daemon=True).start()

        def stop(_signum: int, _frame: object) -> None:
            threading.Thread(target=server.shutdown, daemon=True).start()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        _restore(env, installed, original)
        os.close(write_fd)
        os.waitpid(child, 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bridge-dir", type=Path, required=True)
    run(parser.parse_args().bridge_dir)
