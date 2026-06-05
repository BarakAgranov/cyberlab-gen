"""Shared pytest fixtures for the whole suite."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cyberlab_gen.logging_setup import reset_logging_for_tests

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def isolate_run_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Redirect run logs to a per-test tmp dir and reset logging between tests.

    Keeps central logging (``cyberlab_gen.logging_setup.setup_logging``, invoked by
    the CLI/eval entry points) from writing into the developer's real log directory
    during the test run, and ensures each test starts from a clean logging state.
    """
    monkeypatch.setenv("CYBERLAB_GEN_LOG_DIR", str(tmp_path / "logs"))
    reset_logging_for_tests()
    yield
    reset_logging_for_tests()
