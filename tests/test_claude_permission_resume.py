"""Permission changes retain native conversation identity and terminal isolation."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from omnigent import claude_native_bridge as bridge
from omnigent.inner.terminal import TerminalInstance
from omnigent.server.routes._sessions.helpers import _merge_claude_permission_launch_args

NATIVE_ID = "f0f39aa1-5d4d-4d31-b97a-50ea056bb380"


def test_resume_args_preserve_context_and_current_configuration() -> None:
    original = [
        "--resume=old",
        "--fork-session",
        "--session-id",
        "old",
        "--dangerously-skip-permissions",
        "--permission-mode=auto",
        "--model",
        "old-model",
        "--effort",
        "low",
        "--settings",
        "/private/settings.json",
        "--plugin-dir",
        "/agent/skills",
    ]
    args = bridge.permission_mode_resume_args(
        original,
        session_id=NATIVE_ID,
        mode="bypassPermissions",
        model="current-model",
        effort="high",
    )
    assert args == [
        "--settings",
        "/private/settings.json",
        "--plugin-dir",
        "/agent/skills",
        "--allow-dangerously-skip-permissions",
        "--model",
        "current-model",
        "--effort",
        "high",
        "--resume",
        NATIVE_ID,
        "--permission-mode",
        "bypassPermissions",
    ]
    assert original[0] == "--resume=old"


@pytest.mark.parametrize("status", ["busy", "waiting", None])
def test_resume_leaves_a_running_or_unidentified_turn_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str | None
) -> None:
    settings = _prepare_resume_files(tmp_path, monkeypatch, status)
    before = settings.read_bytes()
    with pytest.raises(bridge.ClaudePermissionModeRestartBlocked, match="finish or stop"):
        bridge.prepare_permission_mode_resume(tmp_path, mode="bypassPermissions", pane_pid=123)
    assert settings.read_bytes() == before


def _prepare_resume_files(root: Path, monkeypatch: pytest.MonkeyPatch, status: str | None) -> Path:
    monkeypatch.setattr(bridge, "_BRIDGE_ROOT", root)
    monkeypatch.setattr(bridge, "_TRUSTED_PARENT", root.parent)
    transcript = root / f"{NATIVE_ID}.jsonl"
    transcript.write_text('{"type":"user","message":{"content":"remember this"}}\n')
    (root / "state.json").write_text(
        json.dumps(
            {
                "claude_session_id": NATIVE_ID,
                "transcript_path": str(transcript),
            }
        )
    )
    settings = root / "claude-settings.json"
    settings.write_text(
        json.dumps({"hooks": {"SessionStart": []}, "permissions": {"deny": ["x"]}})
    )
    monkeypatch.setattr(
        "omnigent.claude_native_status_file.resolve_status_file",
        lambda **_kw: root / "status.json",
    )
    monkeypatch.setattr(
        "omnigent.claude_native_status_file.read_session_status",
        lambda _p: SimpleNamespace(raw_status=status) if status else None,
    )
    return settings


def test_resume_uses_existing_transcript_and_keeps_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _prepare_resume_files(tmp_path, monkeypatch, "idle")
    transcript = tmp_path / f"{NATIVE_ID}.jsonl"
    before = transcript.read_bytes()
    assert (
        bridge.prepare_permission_mode_resume(tmp_path, mode="bypassPermissions", pane_pid=123)
        == NATIVE_ID
    )
    saved = json.loads(settings.read_text())
    assert saved["permissions"] == {"deny": ["x"], "defaultMode": "bypassPermissions"}
    assert saved["hooks"] == {"SessionStart": []}
    assert saved["skipDangerousModePermissionPrompt"] is True
    assert transcript.read_bytes() == before


def test_resume_requires_a_saved_transcript(tmp_path: Path) -> None:
    with pytest.raises(
        bridge.ClaudePermissionModeRestartBlocked, match="no saved native transcript"
    ):
        bridge.prepare_permission_mode_resume(tmp_path, mode="bypassPermissions")


def test_explicit_switch_survives_restart_and_can_disable_a_bypass_launch() -> None:
    assert _merge_claude_permission_launch_args(None, "bypassPermissions", explicit=True) == [
        "--permission-mode",
        "bypassPermissions",
    ]
    assert _merge_claude_permission_launch_args(
        ["--dangerously-skip-permissions", "--model", "opus"], "default", explicit=True
    ) == ["--model", "opus", "--permission-mode", "default"]
    assert _merge_claude_permission_launch_args(None, "auto") is None


async def test_respawn_reuses_the_sandbox_launcher_cwd_and_tmux_server(tmp_path: Path) -> None:
    terminal = TerminalInstance(
        name="claude",
        session_key="main",
        socket_path=tmp_path / "tmux.sock",
        private_dir=tmp_path,
        running=True,
        launch_cwd="/workspace with spaces",
    )
    terminal._launch_executable = "/private/sandbox-launcher"
    tmux = AsyncMock()
    terminal._tmux = tmux
    args = ["--resume", NATIVE_ID, "--permission-mode", "bypassPermissions"]
    await terminal.respawn(args=args)
    command = tmux.call_args.args
    assert command[:6] == ("respawn-pane", "-k", "-t", "main", "-c", "/workspace with spaces")
    assert shlex.split(command[6]) == ["/private/sandbox-launcher", *args]
    assert terminal.args == args
    assert terminal.running


async def test_respawn_does_not_invent_a_launch_environment(tmp_path: Path) -> None:
    terminal = TerminalInstance(
        name="claude", session_key="main", socket_path=tmp_path / "tmux.sock", private_dir=tmp_path
    )
    terminal._tmux = AsyncMock()
    with pytest.raises(RuntimeError, match="original launch context"):
        await terminal.respawn(args=["--permission-mode", "bypassPermissions"])
    terminal._tmux.assert_not_called()
