"""Guard: the test suite must never write into the developer's real ``~/.cyberlab-gen/``.

The ``isolate_state_root`` autouse fixture (``tests/conftest.py``) redirects
``Path.home()`` to a per-test tmp directory and returns it. Both ``LocalState().root``
and ``registries.loader.default_overlay_dir()`` derive their paths from ``Path.home()``,
so that single redirect isolates the state root, the runs dir, AND the registry
overlay — even for a test that forgets ``--state-dir``.

These tests are the regression guard for a real past leak: ~189 ``example-com-blog``
run directories and a polluted registry overlay (``target:fastly`` / ``s3_bucket_arn``
with ``proposed_by_model: m`` fixture values) landed in the developer's real home
because the previous ``isolate_run_logs`` fixture isolated only logs/tracing. If the
net is removed or defeated, these fail.
"""

from __future__ import annotations

from pathlib import Path

from cyberlab_gen.registries.loader import default_overlay_dir
from cyberlab_gen.state import LocalState


def test_home_is_redirected_off_the_real_home(isolate_state_root: Path) -> None:
    """``Path.home()`` resolves to the fixture's tmp dir, not the developer's real home."""
    assert Path.home() == isolate_state_root


def test_local_state_paths_are_isolated(isolate_state_root: Path) -> None:
    """``LocalState`` (root, runs, overlay) anchors under the redirected home."""
    home = isolate_state_root
    state = LocalState()
    assert state.root == home / ".cyberlab-gen"
    assert state.runs_dir == home / ".cyberlab-gen" / "runs"
    assert state.registry_overlay_dir == home / ".cyberlab-gen" / "registry-overlay"
    # Strongest form: nothing LocalState derives can escape the redirected home.
    assert state.root.is_relative_to(home)


def test_default_overlay_dir_is_isolated(isolate_state_root: Path) -> None:
    """The registry-overlay path used by un-flagged runs is isolated too.

    ``default_overlay_dir()`` is the fallback an ``extract`` invocation with no
    ``--state-dir`` resolves to (``cli/extract.py``); it must not point at the real
    home, or auto-accepted proposals would pollute the shared overlay.
    """
    overlay = default_overlay_dir()
    assert overlay == isolate_state_root / ".cyberlab-gen" / "registry-overlay"
    assert overlay.is_relative_to(isolate_state_root)
