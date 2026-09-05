"""The pre-launch ``agy models`` catalog.

Covers the parse (the only place agy's output shape is interpreted) and the
store-then-probe contract, including the two failure modes that must NOT be
persisted as an answer: a non-zero exit and output nothing parses from.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from omnigent import model_catalog_store
from omnigent.antigravity_native_catalog import (
    HARNESS,
    antigravity_launch_catalog,
    parse_agy_models,
)

_AGY_OUTPUT = (
    "Fetching available models...\n"
    "gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n"
    "gemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n"
    "claude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)\n"
)


def test_parse_keeps_agys_order_and_ignores_the_progress_line() -> None:
    """Rows are the tab-separated lines, in the order agy printed them.

    The order is agy's own preference order, so re-sorting would quietly
    change which model a picker shows first.
    """
    rows = parse_agy_models(_AGY_OUTPUT)
    assert [row["id"] for row in rows] == [
        "gemini-3.8-flash-high",
        "gemini-3.8-flash-low",
        "claude-sonnet-4-6",
    ]
    assert rows[0] == {
        "id": "gemini-3.8-flash-high",
        "model": "gemini-3.8-flash-high",
        "displayName": "Gemini 3.8 Flash (High)",
    }


def test_parse_ignores_anything_without_a_tab() -> None:
    """A banner or trailing hint is not a model named after itself."""
    assert parse_agy_models("Fetching available models...\n\nSee https://example\n") == []


def test_parse_drops_a_repeated_id() -> None:
    """One row per id, first spelling wins."""
    rows = parse_agy_models("a\tFirst\na\tSecond\n")
    assert [row["displayName"] for row in rows] == ["First"]


def _fake_agy(tmp_path: Path, *, stdout: str = "", exit_code: int = 0) -> str:
    """Write a stand-in ``agy`` that prints *stdout* and exits *exit_code*.

    A real executable rather than a patched subprocess call, so the probe's
    own argv, decoding and exit-code handling are exercised.

    :param tmp_path: Pytest temporary directory.
    :param stdout: What the fake prints.
    :param exit_code: Its exit status.
    :returns: Path to the executable.
    """
    script = tmp_path / "agy"
    script.write_text(
        "#!/usr/bin/env python3\nimport sys\n"
        f"sys.stdout.write({stdout!r})\nsys.exit({exit_code})\n"
    )
    script.chmod(0o755)
    return str(script)


@pytest.fixture(autouse=True)
def _isolated_catalog_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the shared catalog store at a scratch dir.

    :param tmp_path: Pytest temporary directory.
    :param monkeypatch: Pytest monkeypatch fixture.
    :returns: None.
    """
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path / "data"))


def test_probe_persists_what_agy_listed(tmp_path: Path) -> None:
    """A miss probes once and stores the answer for the next reader."""
    agy = _fake_agy(tmp_path, stdout=_AGY_OUTPUT)
    rows = asyncio.run(antigravity_launch_catalog(agy_path=agy))
    assert rows is not None
    assert [row["id"] for row in rows][:1] == ["gemini-3.8-flash-high"]

    fingerprint = model_catalog_store.fingerprint_of(model_catalog_store.binary_identity(agy))
    assert model_catalog_store.read_catalog(HARNESS, fingerprint) == rows


def test_stored_rows_are_served_without_probing(tmp_path: Path) -> None:
    """A hit does not run agy — the fake would fail the call if it did."""
    agy = _fake_agy(tmp_path, stdout=_AGY_OUTPUT)
    fingerprint = model_catalog_store.fingerprint_of(model_catalog_store.binary_identity(agy))
    stored: list[dict[str, Any]] = [{"id": "stored", "model": "stored", "displayName": "Stored"}]
    model_catalog_store.write_catalog(HARNESS, fingerprint, stored)

    assert asyncio.run(antigravity_launch_catalog(agy_path=agy)) == stored


def test_a_failed_listing_answers_none_and_stores_nothing(tmp_path: Path) -> None:
    """A non-zero exit is not an empty catalog.

    Persisting ``[]`` would teach every later reader that agy offers no
    models, and only a binary change would clear it.
    """
    agy = _fake_agy(tmp_path, stdout="boom\n", exit_code=2)
    assert asyncio.run(antigravity_launch_catalog(agy_path=agy)) is None

    fingerprint = model_catalog_store.fingerprint_of(model_catalog_store.binary_identity(agy))
    assert model_catalog_store.read_catalog(HARNESS, fingerprint) is None


def test_unparseable_output_answers_none(tmp_path: Path) -> None:
    """agy answering in a shape with no rows is a failure, not an empty list."""
    agy = _fake_agy(tmp_path, stdout="Fetching available models...\n")
    assert asyncio.run(antigravity_launch_catalog(agy_path=agy)) is None


def test_missing_agy_answers_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A host without agy offers no models rather than raising.

    The picker already badges a harness this machine cannot run; a raise here
    would fail the whole model-options request instead.
    """
    from omnigent import antigravity_native_launch

    def _no_agy() -> str:
        raise RuntimeError("agy CLI not found on PATH")

    monkeypatch.setattr(antigravity_native_launch, "agy_binary_path", _no_agy)
    assert asyncio.run(antigravity_launch_catalog()) is None
