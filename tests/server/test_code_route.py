"""Tests for the route that serves the server's source bundle."""

from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent import code_bundle, code_version
from omnigent.server.routes import code as code_routes


def _repo(path: Path) -> Path:
    path.mkdir()

    def run(*args: str) -> None:
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)

    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (path / "a.py").write_text("print(1)\n")
    run("add", "-A")
    run("commit", "-qm", "init")
    return path


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path]:
    repo = _repo(tmp_path / "repo")
    monkeypatch.setattr(code_version, "find_root", lambda start=None: repo)
    monkeypatch.setattr(code_routes, "_bundle_dir", lambda: tmp_path / "bundles")
    monkeypatch.setattr(code_routes, "_built", None)
    app = FastAPI()
    app.include_router(code_routes.create_code_router(), prefix="/v1")
    return TestClient(app), repo


def test_bundle_matches_the_tree_and_is_reused_until_it_changes(
    client: tuple[TestClient, Path],
) -> None:
    http, repo = client

    first = http.get("/v1/code/bundle")
    again = http.get("/v1/code/bundle")

    assert first.status_code == 200
    expected = code_version.compute(repo)
    assert expected is not None and first.headers["x-code-id"] == expected.id
    with tarfile.open(fileobj=io.BytesIO(first.content)) as tar:
        assert {m.name for m in tar.getmembers()} == {"a.py", code_bundle.MANIFEST}
    assert again.content == first.content

    (repo / "a.py").write_text("print(2)\n")
    changed = http.get("/v1/code/bundle")
    assert changed.headers["x-code-id"] != first.headers["x-code-id"]
    assert len(list((repo.parent / "bundles").glob("code-*.tar.gz"))) == 1


def test_bundle_is_not_served_outside_a_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(code_version, "find_root", lambda start=None: None)
    app = FastAPI()
    app.include_router(code_routes.create_code_router(), prefix="/v1")

    assert TestClient(app).get("/v1/code/bundle").status_code == 404


def test_update_script_is_served_from_the_checkout(
    client: tuple[TestClient, Path],
) -> None:
    http, repo = client
    assert http.get("/v1/code/update-script").status_code == 404

    (repo / "scripts").mkdir()
    (repo / "scripts/update_host.py").write_text("print('hi')\n")
    served = http.get("/v1/code/update-script")

    assert served.status_code == 200 and served.text == "print('hi')\n"
