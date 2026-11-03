"""Tests for packing a checkout and applying the pack to another one."""

from __future__ import annotations

import io
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

from omnigent import code_bundle, code_version


def _repo(path: Path, files: dict[str, str]) -> Path:
    path.mkdir(parents=True)

    def run(*args: str) -> None:
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)

    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (path / ".gitignore").write_text("cache/\n")
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)
    run("add", "-A")
    run("commit", "-qm", "init")
    return path


def test_apply_makes_the_host_identical_and_keeps_local_files(tmp_path: Path) -> None:
    server = _repo(
        tmp_path / "server", {"a.py": "new\n", "pkg/b.py": "b\n", "run.sh": "#!/bin/sh\n"}
    )
    os.chmod(server / "run.sh", 0o755)
    os.symlink("a.py", server / "link.py")
    (server / "extra.py").write_text("uncommitted\n")
    host = _repo(tmp_path / "host", {"a.py": "old\n", "gone.py": "x\n", "pkg/old.py": "y\n"})
    (host / "examples" / "nexus").mkdir(parents=True)
    (host / "examples/nexus/config.yaml").write_text("mine: true\n")
    (host / "cache").mkdir()
    (host / "cache/keep.bin").write_text("ignored")

    bundle = tmp_path / "b.tar.gz"
    built = code_bundle.build(server, bundle)
    done = code_bundle.apply(host, bundle, tmp_path / "backups")

    assert done.version.id == built.id == code_version.compute(server).id  # type: ignore[union-attr]
    assert (host / "a.py").read_text() == "new\n" and not (host / "gone.py").exists()
    assert not (host / "pkg/old.py").exists() and (host / "pkg/b.py").exists()
    assert (host / "link.py").is_symlink() and os.readlink(host / "link.py") == "a.py"
    assert os.access(host / "run.sh", os.X_OK)
    assert (host / "examples/nexus/config.yaml").read_text() == "mine: true\n"
    assert (host / "cache/keep.bin").read_text() == "ignored"
    assert done.removed == 2 and done.written >= 4 and done.backup.exists()
    assert code_bundle.apply(host, bundle, tmp_path / "backups").written == 0


def test_a_corrupt_file_rolls_everything_back(tmp_path: Path) -> None:
    server = _repo(tmp_path / "server", {"a.py": "new\n", "b.py": "new\n"})
    host = _repo(tmp_path / "host", {"a.py": "old\n", "b.py": "old\n", "gone.py": "keep me\n"})
    before = code_version.compute(host)
    bundle = tmp_path / "b.tar.gz"
    code_bundle.build(server, bundle)

    # Same manifest, but b.py's content no longer matches its checksum.
    broken = tmp_path / "broken.tar.gz"
    with tarfile.open(bundle) as src, tarfile.open(broken, "w:gz") as dst:
        for member in src.getmembers():
            data = src.extractfile(member)
            body = data.read() if data else b""
            if member.name == "b.py":
                body = b"tampered\n"
            member.size = len(body)
            dst.addfile(member, io.BytesIO(body))

    with pytest.raises(code_bundle.BundleError, match="checksum"):
        code_bundle.apply(host, broken, tmp_path / "backups")

    assert code_version.compute(host) == before
    assert (host / "gone.py").read_text() == "keep me\n"


def test_dependency_change_is_reported(tmp_path: Path) -> None:
    server = _repo(tmp_path / "server", {"pyproject.toml": "v2\n", "a.py": "a\n"})
    host = _repo(tmp_path / "host", {"pyproject.toml": "v1\n", "a.py": "a\n"})
    bundle = tmp_path / "b.tar.gz"
    code_bundle.build(server, bundle)

    assert code_bundle.apply(host, bundle, tmp_path / "bk").dependencies_changed is True


def test_a_bundle_cannot_write_outside_the_checkout(tmp_path: Path) -> None:
    host = _repo(tmp_path / "host", {"a.py": "a\n"})
    evil = tmp_path / "evil.tar.gz"
    manifest = json.dumps({"id": "x", "files": {"../escape.py": "0" * 40}}).encode()
    with tarfile.open(evil, "w:gz") as tar:
        info = tarfile.TarInfo(code_bundle.MANIFEST)
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))

    with pytest.raises(code_bundle.BundleError, match="outside"):
        code_bundle.apply(host, evil, tmp_path / "bk")
    assert not (tmp_path / "escape.py").exists()
