"""User-facing output helpers for the CLI.

Architectural source: ``docs/coding-conventions.md §6.3``. User-visible
errors are clean messages by default; stack traces appear only when
``--debug`` is set on the top-level CLI invocation. ``--debug`` flips
the module-level ``_debug_enabled`` toggle via :func:`set_debug`; the CLI
callback in :mod:`cyberlab_gen.cli.main` is responsible for that wiring.

This module owns the single set of ``print``-equivalent calls the
project tolerates outside of ``cli/`` (ruff rule ``T20`` bans
``print``/``pprint`` everywhere else). All output goes through
``typer.echo`` for stable stdout/stderr separation under
``typer.testing.CliRunner``.

Phase 0 scope: ``print_info`` for the "not yet implemented" verb-stub
messages, ``print_error`` for any future error path. The
"internal traces always written to the run's structured report" half
of §6.3 is deferred until the run-report runner ships (Phase 1+).
"""

import traceback

import typer

_debug_enabled: bool = False


def set_debug(on: bool) -> None:
    """Toggle the module-level debug state.

    Called by the CLI top-level callback when the user passes
    ``--debug``. When ``True``, :func:`print_error` includes the
    traceback of any exception it is handed.
    """
    global _debug_enabled
    _debug_enabled = on


def is_debug() -> bool:
    """Return the current debug state. Test hook; not used by production code."""
    return _debug_enabled


def print_info(msg: str) -> None:
    """Write ``msg`` to stdout with a trailing newline."""
    typer.echo(msg)


def print_cost(msg: str) -> None:
    """Write a live per-call cost line to stderr (the ``--show-cost`` echo).

    Goes to stderr so it never pollutes the stdout the CLI uses for results; the full
    per-call detail is always in the run-log file regardless (ADR 0038).
    """
    typer.echo(msg, err=True)


def print_error(msg: str, exc: BaseException | None = None) -> None:
    """Write ``msg`` to stderr; include the traceback when ``--debug`` is on.

    ``exc`` is the originating exception. When ``_debug_enabled`` is ``False``
    the traceback is suppressed regardless of whether ``exc`` is
    supplied — the user sees the clean message only. When ``_debug_enabled``
    is ``True`` and ``exc`` is not ``None``, the traceback is appended
    to stderr after ``msg``.
    """
    typer.echo(msg, err=True)
    if _debug_enabled and exc is not None:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        typer.echo(tb, err=True)
