"""Tests for ACP executor timeout configuration."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from omnigent.inner.acp_executor import (
    _MUSE_TIMEOUT_ENV,
    _MUSE_TIMEOUT_SECONDS,
    _PROMPT_TIMEOUT_SECONDS,
    AcpAgentConfig,
    AcpExecutor,
    _is_muse_backend,
)

_TIMEOUT_ENV = "HARNESS_ACP_PROMPT_TIMEOUT_S"
_PRINT_TIMEOUT = (
    "from omnigent.inner.acp_executor import _PROMPT_TIMEOUT_SECONDS; "
    "print(_PROMPT_TIMEOUT_SECONDS)"
)
_PRINT_MUSE_TIMEOUT = (
    "from omnigent.inner.acp_executor import _MUSE_TIMEOUT_SECONDS; print(_MUSE_TIMEOUT_SECONDS)"
)


def _subprocess_env(value: str | None, *, var: str = _TIMEOUT_ENV) -> dict[str, str]:
    env = os.environ.copy()
    if value is None:
        env.pop(var, None)
    else:
        env[var] = value
    return env


def _executor(command: str = "some-agent --acp", name: str = "Some agent") -> AcpExecutor:
    return AcpExecutor(AcpAgentConfig(command=command, name=name))


def test_prompt_timeout_defaults_and_override() -> None:
    assert (
        subprocess.check_output(
            [sys.executable, "-c", _PRINT_TIMEOUT],
            env=_subprocess_env(None),
            text=True,
        ).strip()
        == "1800.0"
    )
    assert (
        subprocess.check_output(
            [sys.executable, "-c", _PRINT_TIMEOUT],
            env=_subprocess_env("7200"),
            text=True,
        ).strip()
        == "7200.0"
    )


def test_prompt_timeout_malformed_value_fails_loud() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _PRINT_TIMEOUT],
        env=_subprocess_env("not-a-number"),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert _TIMEOUT_ENV in result.stderr.strip().splitlines()[-1]


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_prompt_timeout_rejects_non_positive_or_non_finite_values(value: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", _PRINT_TIMEOUT],
        env=_subprocess_env(value),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert _TIMEOUT_ENV in result.stderr.strip().splitlines()[-1]


def test_muse_timeout_defaults_and_override() -> None:
    assert (
        subprocess.check_output(
            [sys.executable, "-c", _PRINT_MUSE_TIMEOUT],
            env=_subprocess_env(None, var=_MUSE_TIMEOUT_ENV),
            text=True,
        ).strip()
        == "600.0"
    )
    assert (
        subprocess.check_output(
            [sys.executable, "-c", _PRINT_MUSE_TIMEOUT],
            env=_subprocess_env("120", var=_MUSE_TIMEOUT_ENV),
            text=True,
        ).strip()
        == "120.0"
    )


@pytest.mark.parametrize("value", ["0", "-5", "nan", "inf", "not-a-number"])
def test_muse_timeout_malformed_or_non_positive_value_fails_loud(value: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", _PRINT_TIMEOUT],
        env=_subprocess_env(value, var=_MUSE_TIMEOUT_ENV),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert _MUSE_TIMEOUT_ENV in result.stderr.strip().splitlines()[-1]


@pytest.mark.parametrize(
    ("command", "name", "expected"),
    [
        ("muse-code-acp", "Muse Code", True),
        ("npx @bex-co/muse-code-acp", "acp", True),
        ("some-agent --acp", "Muse Code", True),
        ("some-agent --acp", "Some agent", False),
        ("gemini --experimental-acp", "Gemini CLI", False),
    ],
)
def test_is_muse_backend_matches_adapter_and_label(
    command: str, name: str, expected: bool
) -> None:
    assert _is_muse_backend(command, name) is expected


def test_idle_timeout_muse_backend_gets_larger_budget() -> None:
    assert _MUSE_TIMEOUT_SECONDS > 0
    assert _PROMPT_TIMEOUT_SECONDS > 0
    assert _executor("muse-code-acp", "Muse Code")._idle_timeout_seconds() == max(
        _PROMPT_TIMEOUT_SECONDS, _MUSE_TIMEOUT_SECONDS
    )


def test_idle_timeout_generic_agent_uses_generic_budget() -> None:
    assert (
        _executor("gemini --experimental-acp", "Gemini CLI")._idle_timeout_seconds()
        == _PROMPT_TIMEOUT_SECONDS
    )


def test_idle_timeout_generic_override_does_not_shrink_muse_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raising the generic budget past the Muse one lifts Muse turns too."""
    import omnigent.inner.acp_executor as acp_executor_module

    monkeypatch.setattr(
        acp_executor_module, "_PROMPT_TIMEOUT_SECONDS", _MUSE_TIMEOUT_SECONDS + 100.0
    )
    assert (
        _executor("muse-code-acp", "Muse Code")._idle_timeout_seconds()
        == _MUSE_TIMEOUT_SECONDS + 100.0
    )
