"""Tests for central logging configuration (ADR 0037)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from cyberlab_gen.logging_setup import (
    default_log_dir,
    run_log_path,
    setup_logging,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_setup_creates_dir_and_file_and_returns_path(tmp_path: Path) -> None:
    log_dir = tmp_path / "made" / "by" / "code"
    assert not log_dir.exists()
    path = setup_logging(log_dir=log_dir)
    assert path.parent == log_dir
    assert log_dir.is_dir()
    assert path.exists()
    assert run_log_path() == path


def test_file_handler_captures_info_and_below(tmp_path: Path) -> None:
    path = setup_logging(log_dir=tmp_path / "logs")
    logger = logging.getLogger("cyberlab_gen.test.sample")
    logger.info("stage %s started", "extract")
    logger.debug("verbose %d", 7)
    for handler in logging.getLogger().handlers:
        handler.flush()
    contents = path.read_text(encoding="utf-8")
    assert "stage extract started" in contents
    assert "verbose 7" in contents  # file handler is DEBUG


def test_idempotent_does_not_stack_handlers(tmp_path: Path) -> None:
    first = setup_logging(log_dir=tmp_path / "logs")
    before = len(logging.getLogger().handlers)
    second = setup_logging(log_dir=tmp_path / "other")
    after = len(logging.getLogger().handlers)
    assert first == second  # same run-log file; second call is a no-op for the path
    assert before == after  # no duplicate handlers


def test_debug_raises_console_level_but_keeps_file_at_debug(tmp_path: Path) -> None:
    setup_logging(log_dir=tmp_path / "logs", debug=False)
    console = _console_handler()
    assert console.level == logging.WARNING
    # A later --debug call re-applies the console level without re-adding handlers.
    setup_logging(log_dir=tmp_path / "logs", debug=True)
    assert _console_handler().level == logging.DEBUG
    assert _file_handler().level == logging.DEBUG


def test_env_override_redirects_default_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "env-logs"
    monkeypatch.setenv("CYBERLAB_GEN_LOG_DIR", str(target))
    assert default_log_dir() == target
    path = setup_logging()
    assert path.parent == target


def test_run_id_is_woven_into_filename(tmp_path: Path) -> None:
    path = setup_logging(log_dir=tmp_path / "logs", run_id="eval")
    assert path.name.endswith("-eval.log")


def _console_handler() -> logging.Handler:
    for handler in logging.getLogger().handlers:
        if getattr(handler, "_cyberlab_console", False):
            return handler
    raise AssertionError("console handler not found")


def _file_handler() -> logging.Handler:
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.FileHandler):
            return handler
    raise AssertionError("file handler not found")
