"""Process-runtime helpers shared by the CLI and the eval harness (ADR 0039).

Currently: a SIGTERM→``KeyboardInterrupt`` guard so a terminate signal unwinds
through the run-store ``finally`` blocks (a partial run is persisted) instead of
killing the process without saving anything.
"""

from __future__ import annotations

import signal
from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator
    from types import FrameType


@contextmanager
def persisting_signal_guard() -> Generator[None]:
    """Make SIGTERM raise ``KeyboardInterrupt`` for the duration of the block.

    SIGINT (Ctrl-C) already raises ``KeyboardInterrupt``, which unwinds through the
    run-store ``finally`` (ADR 0039) so a partial run is saved. SIGTERM normally
    terminates the process *without* unwinding — we install a handler that raises
    ``KeyboardInterrupt`` instead, giving SIGTERM the same persist-then-exit path.

    Best-effort: on a platform or non-main thread where the handler can't be
    installed (``signal`` raises ``ValueError``/``OSError``), this is a no-op and
    SIGINT still works. The previous handler is restored on exit so repeated
    invocations in one process (the test runner) don't leak handler state.
    """

    def _raise_interrupt(_signum: int, _frame: FrameType | None) -> None:
        raise KeyboardInterrupt

    installed = False
    previous = None
    try:
        previous = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, _raise_interrupt)
        installed = True
    except (ValueError, OSError, AttributeError):
        installed = False
    try:
        yield
    finally:
        if installed and previous is not None:
            with suppress(ValueError, OSError):
                signal.signal(signal.SIGTERM, previous)


__all__ = ["persisting_signal_guard"]
