"""The companion's read-only commands: what passes, what never does, and the sandbox."""

from __future__ import annotations

import asyncio
import shutil

import pytest

from omnigent.server import companion_commands as cc
from omnigent.server.companion_commands import CommandRefused, scrub, validate

needs_bwrap = pytest.mark.skipif(shutil.which("bwrap") is None, reason="bwrap not installed")


@pytest.mark.parametrize(
    "command",
    [
        "df -h /",
        "du -sh /tmp | sort -h | tail -3",
        "git -C /tmp log --oneline -5",
        "git status --short",
        "git branch -a",
        "git stash list",
        "ps aux | grep python | head",
        "systemctl --user status some.service",
        "journalctl --user -n 20",
        "docker ps -a --format '{{.Names}}'",
        "docker system df",
        "find . -maxdepth 2 -name '*.py'",
        "sed -n '1,20p' notes.txt",
        "grep -rn 'def run' .",
        "nvidia-smi --query-gpu=name --format=csv",
        "tail -n 50 server.log",
    ],
)
def test_reading_commands_pass(command, tmp_path):
    assert validate(command, cwd=tmp_path, home=tmp_path)


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf build",
        "touch x",
        "mv a b",
        "python -c 'print(1)'",
        "bash -c ls",
        "sh -c ls",
        "env ls",
        "xargs rm",
        "kill 1234",
        "ls > out.txt",
        "ls; rm x",
        "ls && rm x",
        "echo $(id)",
        "echo `id`",
        "echo $HOME",
        "ls | | wc",
        "/bin/rm x",
        "find . -delete",
        "find . -exec rm {} ;",
        "git -c core.pager=sh status",
        "git push",
        "git commit -m x",
        "git checkout main",
        "git reset --hard",
        "git branch -D old",
        "git branch newbranch",
        "git stash pop",
        "git tag -d v1",
        "git config user.name x",
        "tail -f server.log",
        "sort -o out.txt in.txt",
        "sed -i 's/a/b/' f",
        "sed -n 'e id' f",
        "docker rm abc",
        "docker run alpine",
        "docker exec -it abc sh",
        "docker stats",
        "systemctl --user stop some.service",
        "systemctl restart x",
        "journalctl --vacuum-time=1d",
        "nvidia-smi -pm 1",
        "rg --pre cat foo",
        "ps aux | git log",
        "cat 'unbalanced",
    ],
)
def test_anything_that_writes_or_launches_is_refused(command, tmp_path):
    with pytest.raises(CommandRefused):
        validate(command, cwd=tmp_path, home=tmp_path)


def test_quoted_metacharacters_are_only_text(tmp_path):
    # Inside quotes these are a grep pattern, and the word is re-quoted
    # before the shell sees it, so nothing expands.
    stages = validate("grep -E '$(id);`x` > y' f", cwd=tmp_path, home=tmp_path)
    assert stages == [["grep", "-E", "$(id);`x` > y", "f"]]


def test_tilde_and_unquoted_globs_expand_like_a_shell(tmp_path):
    (tmp_path / "a.log").write_text("x")
    (tmp_path / "b.log").write_text("y")
    assert validate("ls ~/", cwd=tmp_path, home=tmp_path) == [["ls", f"{tmp_path}/"]]
    assert validate("cat *.log", cwd=tmp_path, home=tmp_path) == [["cat", "a.log", "b.log"]]
    # Quoted, the pattern is the program's, not the shell's.
    assert validate("find . -name '*.log'", cwd=tmp_path, home=tmp_path) == [
        ["find", ".", "-name", "*.log"]
    ]


def test_a_glob_cannot_smuggle_an_option_into_find(tmp_path):
    (tmp_path / "-delete").write_text("")
    with pytest.raises(CommandRefused):
        validate("find *", cwd=tmp_path, home=tmp_path)


def test_scrub_hides_credentials():
    text = (
        "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwx\n"
        "token: ghp_abcdefghijklmnopqrstuvwxyz0123\n"
        "url https://bob:hunter2pass@github.com/x.git\n"
        "Authorization: Bearer abc.def.ghi\n"
        "google AIzaSyA1234567890abcdefghijklmnopqrstu\n"
        "nexus 123 python serve.py --api-key flagvalue123 --port 80\n"
        "plain words stay\n"
    )
    out = scrub(text)
    for secret in (
        "sk-proj-abc",
        "ghp_abc",
        "hunter2pass",
        "abc.def.ghi",
        "AIzaSyA123",
        "flagvalue",
    ):
        assert secret not in out
    assert "plain words stay" in out


def test_long_output_keeps_head_and_tail():
    text = "".join(f"line {i}\n" for i in range(5000))
    capped = cc._cap(text, limit=1000)
    assert capped.startswith("line 0")
    assert "line 4999" in capped
    assert "characters cut" in capped
    assert len(capped) < 1200


async def test_missing_sandbox_refuses_rather_than_runs_unconfined(tmp_path, monkeypatch):
    monkeypatch.setattr(cc.shutil, "which", lambda _name: None)
    with pytest.raises(CommandRefused):
        await cc.run("ls", cwd=tmp_path, home=tmp_path)


@needs_bwrap
async def test_the_sandbox_is_read_only_even_past_the_allowlist(tmp_path):
    # The second guard on its own: a stage that skipped validation still
    # cannot write, because the filesystem is mounted read-only.
    target = tmp_path / "written"
    argv = cc.sandbox_argv([["touch", str(target)]], cwd=tmp_path, home=tmp_path)
    process = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await process.communicate()
    assert process.returncode != 0
    assert not target.exists()
    assert b"Read-only" in out


@needs_bwrap
async def test_credential_files_are_hidden(tmp_path):
    secret = tmp_path / ".codex" / "auth.json"
    secret.parent.mkdir()
    secret.write_text('{"token": "very-secret-value"}')
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "id_ed25519").write_text("PRIVATE")
    result = await cc.run("cat .codex/auth.json", cwd=tmp_path, home=tmp_path)
    assert "very-secret-value" not in result.output
    listing = await cc.run("ls .ssh", cwd=tmp_path, home=tmp_path)
    assert "id_ed25519" not in listing.output


@needs_bwrap
async def test_a_reading_command_runs_and_reports(tmp_path):
    (tmp_path / "notes.txt").write_text("one\ntwo\nthree\n")
    result = await cc.run("wc -l notes.txt", cwd=tmp_path, home=tmp_path)
    assert result.exit_code == 0
    assert result.output.split()[0] == "3"
    assert result.for_model().startswith("$ wc -l notes.txt\n[exit 0,")


@needs_bwrap
async def test_no_network_inside_the_sandbox(tmp_path):
    # /proc/net belongs to the sandbox's own network namespace: loopback only.
    result = await cc.run("cat /proc/net/dev", cwd=tmp_path, home=tmp_path)
    interfaces = [line.split(":")[0].strip() for line in result.output.splitlines() if ":" in line]
    assert interfaces == ["lo"]
