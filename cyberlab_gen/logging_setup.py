"""Central logging configuration (ADR 0037).

The library follows ``coding-conventions.md §6.2``: every module owns a
``logging.getLogger(__name__)`` and uses lazy ``%``-format; it never configures
handlers. Handler/level/destination policy is installed exactly once, here, at a
process entry point (the CLI callback and the eval runner's ``main``).

A run always produces a readable, persisted log file so the run can be understood
after the fact (operational-foundation principle: no spend without a complete,
readable account). The file is written to a code-created directory the user never
has to set up by hand; console output stays terse (curated user messages are
``cli.output``'s job, not the logger's).

Levels (``§6.2`` taxonomy): DEBUG verbose internal state; INFO stage transitions
and per-call metadata; WARNING recoverable issues; ERROR stage failures; CRITICAL
unrecoverable runtime errors.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from platformdirs import user_log_dir

_APP_NAME = "cyberlab-gen"

#: Override the default run-log directory. The user (or the test suite) can point
#: logs anywhere without a code change; unset ⇒ the platform-standard location.
_LOG_DIR_ENV = "CYBERLAB_GEN_LOG_DIR"

#: Guard so repeated ``setup_logging`` calls (CLI callback + eval main, or tests)
#: do not stack duplicate handlers. The first call wins; later calls only adjust
#: the console level (so a late ``--debug`` still takes effect).
_configured = False
_run_log_path: Path | None = None

_FILE_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_CONSOLE_FORMAT = "%(levelname)-8s %(name)s: %(message)s"


def default_log_dir() -> Path:
    """The default run-log directory.

    ``$CYBERLAB_GEN_LOG_DIR`` when set, else the platform-standard per-user log
    directory (e.g. ``%LOCALAPPDATA%\\cyberlab-gen\\Logs`` on Windows,
    ``~/.local/state/cyberlab-gen/log`` on Linux). Not created here.
    """
    override = (os.environ.get(_LOG_DIR_ENV) or "").strip()
    if override:
        return Path(override)
    return Path(user_log_dir(_APP_NAME, appauthor=False))


def run_log_path() -> Path | None:
    """The file the current process's run log is being written to, if configured."""
    return _run_log_path


def setup_logging(
    *,
    debug: bool = False,
    log_dir: Path | None = None,
    run_id: str | None = None,
) -> Path:
    """Install the root logging configuration and return the run-log file path.

    Idempotent: the first call installs a console handler (``WARNING``, or ``DEBUG``
    when ``debug``) and a file handler (always ``DEBUG`` — full detail to disk).
    The log directory is created if missing. Subsequent calls only re-apply the
    console level so a later ``--debug`` is honoured without duplicating handlers.

    Args:
        debug: raise the *console* level to DEBUG (the file is always DEBUG).
        log_dir: directory for the run-log file; defaults to :func:`default_log_dir`.
        run_id: optional tag woven into the filename for correlation.
    """
    global _configured, _run_log_path

    console_level = logging.DEBUG if debug else logging.WARNING

    if _configured:
        for handler in logging.getLogger().handlers:
            if getattr(handler, "_cyberlab_console", False):
                handler.setLevel(console_level)
        assert _run_log_path is not None
        return _run_log_path

    target_dir = log_dir if log_dir is not None else default_log_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"-{run_id}" if run_id else ""
    log_path = target_dir / f"run-{stamp}{suffix}.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # let handlers filter; the file wants everything

    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
    console._cyberlab_console = True  # type: ignore[attr-defined]  # tag for level re-apply
    root.addHandler(console)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(_FILE_FORMAT))
    root.addHandler(file_handler)

    _configured = True
    _run_log_path = log_path
    logging.getLogger(__name__).info("run log: %s", log_path)
    return log_path


def reset_logging_for_tests() -> None:
    """Tear down the configured handlers so a test can reconfigure cleanly."""
    global _configured, _run_log_path
    root = logging.getLogger()
    for handler in list(root.handlers):
        handler.close()
        root.removeHandler(handler)
    _configured = False
    _run_log_path = None
