"""Exercise the live-hook adapter without a real agent or host."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from scripts import duet_hook_proxy


def test_sidecar_forwards_policy_delivers_once_and_restores_after_crash(tmp_path: Path) -> None:
    pending = [{"id": "n1", "created_at": time.time(), "text": "Peer finding"}]
    acks: list[str] = []

    class Relay(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            assert self.headers["Authorization"] == "Bearer upstream-token"
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/hook/claude/evaluate-policy":
                result = {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": "existing policy warning",
                    }
                }
            else:
                args = data["arguments"]
                if args["op"] == "peek":
                    value = {"notes": list(pending)}
                else:
                    acks.extend(args["ids"])
                    pending.clear()
                    value = {"ok": True}
                result = {"content": [{"type": "text", "text": json.dumps(value)}]}
            raw = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Relay)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    upstream = f"http://127.0.0.1:{server.server_port}"
    (tmp_path / "tool_relay.json").write_text(
        json.dumps({"url": upstream, "token": "upstream-token"})
    )
    original = f"OMNIGENT_RELAY_URL='{upstream}'\nOMNIGENT_RELAY_TOKEN='upstream-token'\n".encode()
    env = tmp_path / "tool_relay.env"
    env.write_bytes(original)
    process = subprocess.Popen(
        [sys.executable, duet_hook_proxy.__file__, "--bridge-dir", str(tmp_path)]
    )
    try:
        for _ in range(100):
            if env.read_bytes() != original:
                break
            time.sleep(0.02)
        fields = dict(line.split("=", 1) for line in env.read_text().splitlines())
        url, token = (
            fields[key].strip("'") for key in ("OMNIGENT_RELAY_URL", "OMNIGENT_RELAY_TOKEN")
        )
        assert url != upstream
        req = urllib.request.Request(
            url + "/hook/claude/evaluate-policy",
            data=json.dumps(
                {
                    "hook_event_name": "PostToolUse",
                    "tool_name": "Bash",
                }
            ).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            output = json.load(resp)
        context = output["hookSpecificOutput"]["additionalContext"]
        assert "existing policy warning" in context and "Peer finding" in context
        for _ in range(100):
            if acks:
                break
            time.sleep(0.01)
        assert acks == ["n1"]
        process.kill()
        process.wait(timeout=3)
        for _ in range(100):
            if env.read_bytes() == original:
                break
            time.sleep(0.02)
        assert env.read_bytes() == original
        assert os.stat(env).st_mode & 0o777 == 0o600
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)
        server.shutdown()
        server.server_close()


def test_invalid_hook_output_leaves_notes_queued() -> None:
    assert (
        duet_hook_proxy._with_notes(b"[]", [{"id": "x", "text": "hello", "created_at": 1}]) is None
    )


def test_restore_does_not_overwrite_replaced_relay(tmp_path: Path) -> None:
    env = tmp_path / "tool_relay.env"
    env.write_bytes(b"new runner relay")
    duet_hook_proxy._restore(env, b"our adapter", b"old relay")
    assert env.read_bytes() == b"new runner relay"
