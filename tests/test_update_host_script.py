"""Tests for the standalone script that syncs a host too old to update itself."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

from omnigent import code_bundle, code_version

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update_host.py"


def _load():
    spec = importlib.util.spec_from_file_location("update_host_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _repo(path: Path, files: dict[str, str]) -> Path:
    path.mkdir()

    def run(*args: str) -> None:
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)

    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    for name, text in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    run("add", "-A")
    run("commit", "-qm", "init")
    return path


def test_script_applies_a_bundle_to_a_checkout_using_only_the_standard_library(
    tmp_path: Path,
) -> None:
    repo_root = Path(code_bundle.__file__).resolve().parents[1]
    modules = {
        f"omnigent/{name}": (repo_root / "omnigent" / name).read_text()
        for name in ("code_version.py", "code_bundle.py")
    }
    server = _repo(tmp_path / "server", {**modules, "a.py": "new\n"})
    host = _repo(tmp_path / "host", {"a.py": "old\n", "gone.py": "x\n"})
    bundle = tmp_path / "b.tar.gz"
    code_bundle.build(server, bundle)

    done = subprocess.run(
        [
            sys.executable,
            "-I",
            str(SCRIPT),
            "--server",
            "https://x",
            "--root",
            str(host),
            "--bundle",
            str(bundle),
        ],
        capture_output=True,
        text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        check=False,
    )

    assert done.returncode == 0, done.stderr
    assert (host / "a.py").read_text() == "new\n" and not (host / "gone.py").exists()
    assert code_version.compute(host).id == code_version.compute(server).id  # type: ignore[union-attr]
    assert "Restart the host" in done.stdout


def test_checkout_is_read_from_the_unit_and_token_from_the_saved_login(tmp_path: Path) -> None:
    script = _load()
    unit = "WorkingDirectory=/srv/a\nEnvironment=OMNIGENT_CHECKOUT=/home/u/omnigent\n"
    assert script.checkout_from_unit(unit) == Path("/home/u/omnigent")
    assert script.checkout_from_unit("WorkingDirectory=/srv/a\n") == Path("/srv/a")
    assert script.checkout_from_unit("") is None

    (tmp_path / "auth_tokens.json").write_text('{"https://s": {"token": "abc"}}')
    assert script.stored_token("https://s/", tmp_path) == "abc"
    assert script.stored_token("https://other", tmp_path) is None
