"""Tests for the source-tree identity used to compare hosts with the server."""

from __future__ import annotations

import subprocess
from pathlib import Path

from omnigent import code_version


def _repo(tmp_path: Path) -> Path:
    def run(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (tmp_path / "a.py").write_text("print(1)\n")
    (tmp_path / ".gitignore").write_text("ignored.txt\n")
    run("add", "-A")
    run("commit", "-qm", "init")
    return tmp_path


def test_uncommitted_and_untracked_changes_change_the_id(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    clean = code_version.compute(root)
    assert clean is not None and "+" not in clean.label

    (root / "a.py").write_text("print(2)\n")
    edited = code_version.compute(root)
    (root / "new.py").write_text("x = 1\n")
    added = code_version.compute(root)

    assert edited is not None and added is not None
    assert len({clean.id, edited.id, added.id}) == 3
    assert edited.label.endswith("+1") and added.label.endswith("+2")


def test_ignored_and_host_local_files_do_not_count(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "examples" / "nexus").mkdir(parents=True)
    (root / "examples/nexus/config.yaml").write_text("a: 1\n")
    before = code_version.compute(root)

    (root / "ignored.txt").write_text("noise")
    (root / "examples/nexus/config.yaml").write_text("a: 2\n")
    after = code_version.compute(root)

    assert before is not None and after is not None
    assert before.id == after.id and before.label == after.label


def test_same_content_gives_the_same_id_in_another_checkout(tmp_path: Path) -> None:
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    first = code_version.compute(_repo(tmp_path / "one"))
    second = code_version.compute(_repo(tmp_path / "two"))

    assert first is not None and second is not None
    assert first.id == second.id and first.root != second.root


def test_not_a_checkout_gives_none(tmp_path: Path) -> None:
    assert code_version.compute(tmp_path) is None


def _v(id_: str, root: str = "/srv/omnigent") -> code_version.CodeVersion:
    return code_version.CodeVersion(id=id_, label=id_[:4], root=root)


def test_update_state_cases() -> None:
    loaded, disk = _v("aaaa"), _v("bbbb")
    state = code_version.update_state

    assert state(None, loaded=loaded, disk=disk) is None
    assert state({"id": "x", "root": None}, loaded=None, disk=None) is None
    assert state({"id": None, "label": None, "root": None}, loaded=loaded, disk=disk) == "unknown"
    assert state({"id": "aaaa", "root": "/h"}, loaded=loaded, disk=disk) == "current"
    assert state({"id": "bbbb", "root": "/h"}, loaded=loaded, disk=disk) == "current"
    assert state({"id": "cccc", "root": "/h"}, loaded=loaded, disk=disk) == "outdated"
    # Same checkout as the server: it loads edits when restarted, so it is never behind.
    assert state({"id": "cccc", "root": "/srv/omnigent"}, loaded=loaded, disk=disk) == "current"
