"""Shared pytest fixtures for the whole suite."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from cyberlab_gen.logging_setup import reset_logging_for_tests
from cyberlab_gen.tracing_setup import reset_tracing_for_tests

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def isolate_state_root(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Redirect ``Path.home()`` to a per-test tmp dir so NO test writes the real home.

    ``LocalState().root`` (state root + ``runs/``, ``checkpoints/``, ``reports/``,
    ``cache/``) and ``registries.loader.default_overlay_dir()`` (the registry overlay)
    both derive their paths from ``Path.home()``. Patching that single chokepoint
    isolates all of them at once — including for a test that forgets ``--state-dir`` and
    would otherwise build a ``RunStore`` and the overlay writer under the developer's
    real ``~/.cyberlab-gen/``. (A real past leak: ~189 ``example-com-blog`` run dirs and a
    polluted overlay landed there because the old fixture isolated only logs/tracing.)

    Autouse + function-scoped: every present and future test is covered, with per-test
    isolation so overlay/runs state never bleeds between tests. The fake home is a
    dedicated factory-minted dir — a *sibling* of each test's ``tmp_path``, not a child —
    so it never intrudes on a test that inspects its own ``tmp_path`` contents. Returns
    the home path so the guard in ``tests/integration/test_state_isolation.py`` can assert
    against it. This is *test-only* isolation, distinct from production path resolution,
    which uses the ``--state-dir`` flag (ADR 0012/0013), never a ``Path.home`` patch.
    """
    home = tmp_path_factory.mktemp("home")

    def _home(_cls: type[Path]) -> Path:
        return home

    monkeypatch.setattr(Path, "home", classmethod(_home))
    return home


@pytest.fixture(autouse=True)
def isolate_run_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Redirect run logs to a per-test tmp dir and reset logging/tracing between tests.

    Keeps central logging (``cyberlab_gen.logging_setup.setup_logging``, invoked by
    the CLI/eval entry points) from writing into the developer's real log directory
    during the test run, and ensures each test starts from a clean logging state.
    Tracing is forced off (so a developer running a local Phoenix can't make the CLI
    entry-point tests configure a global tracer) and reset between tests.
    """
    monkeypatch.setenv("CYBERLAB_GEN_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("CYBERLAB_GEN_TRACING", "off")
    reset_logging_for_tests()
    reset_tracing_for_tests()
    yield
    reset_logging_for_tests()
    reset_tracing_for_tests()
