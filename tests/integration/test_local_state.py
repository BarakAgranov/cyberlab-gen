"""Integration tests for ``LocalState`` (Phase 0 Task 6).

Each test injects ``root=tmp_path`` to isolate the filesystem; no test
touches the real ``~/.cyberlab-gen/`` directory. The one test that does
not inject ``tmp_path`` (``test_localstate_defaults_to_home_dotdir``)
only reads ``LocalState().root`` as a Path value and does not perform
any I/O.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from cyberlab_gen.state import LocalState, UserConfig


def test_localstate_defaults_to_home_dotdir() -> None:
    """Default ``root`` is ``Path.home() / '.cyberlab-gen'``.

    No I/O — this test verifies the path computation only.
    """
    state = LocalState()
    assert state.root == Path.home() / ".cyberlab-gen"


def test_localstate_root_injectable(tmp_path: Path) -> None:
    """Tests can inject a ``tmp_path`` root for filesystem isolation."""
    state = LocalState(root=tmp_path)
    assert state.root == tmp_path


def test_path_properties(tmp_path: Path) -> None:
    """All six path properties anchor under ``root`` with the expected names."""
    state = LocalState(root=tmp_path)
    assert state.config_path == tmp_path / "config.yaml"
    assert state.cache_dir == tmp_path / "cache"
    assert state.checkpoints_dir == tmp_path / "checkpoints"
    assert state.runs_dir == tmp_path / "runs"
    assert state.reports_dir == tmp_path / "reports"
    assert state.registry_overlay_dir == tmp_path / "registry-overlay"


def test_path_properties_have_no_side_effects(tmp_path: Path) -> None:
    """Accessing a path property does not create the directory."""
    state = LocalState(root=tmp_path)
    _ = state.config_path
    _ = state.cache_dir
    _ = state.checkpoints_dir
    _ = state.runs_dir
    _ = state.reports_dir
    _ = state.registry_overlay_dir
    assert not (tmp_path / "cache").exists()
    assert not (tmp_path / "checkpoints").exists()
    assert not (tmp_path / "runs").exists()
    assert not (tmp_path / "reports").exists()
    assert not (tmp_path / "registry-overlay").exists()
    assert not (tmp_path / "config.yaml").exists()


def test_ensure_methods_create_all_directories(tmp_path: Path) -> None:
    """All six ``ensure_*`` methods create their target directory on a fresh root."""
    root = tmp_path / "fresh"
    state = LocalState(root=root)
    state.ensure_root()
    state.ensure_cache_dir()
    state.ensure_checkpoints_dir()
    state.ensure_runs_dir()
    state.ensure_reports_dir()
    state.ensure_registry_overlay_dir()
    assert root.is_dir()
    assert (root / "cache").is_dir()
    assert (root / "checkpoints").is_dir()
    assert (root / "runs").is_dir()
    assert (root / "reports").is_dir()
    assert (root / "registry-overlay").is_dir()


def test_ensure_methods_are_idempotent(tmp_path: Path) -> None:
    """Calling ``ensure_*`` twice on an existing directory does not raise."""
    state = LocalState(root=tmp_path)
    state.ensure_cache_dir()
    state.ensure_cache_dir()  # second call must not raise
    assert state.cache_dir.is_dir()


def test_load_config_returns_default_when_missing(tmp_path: Path) -> None:
    """When ``config.yaml`` is absent, ``load_config`` returns a default ``UserConfig``."""
    state = LocalState(root=tmp_path)
    assert not state.config_path.exists()
    config = state.load_config()
    assert config == UserConfig()


def test_load_config_accepts_empty_yaml(tmp_path: Path) -> None:
    """An empty ``{}`` ``config.yaml`` round-trips to the default ``UserConfig``."""
    state = LocalState(root=tmp_path)
    state.ensure_root()
    state.config_path.write_text("{}\n", encoding="utf-8")
    config = state.load_config()
    assert config == UserConfig()


def test_load_config_rejects_unknown_field(tmp_path: Path) -> None:
    """``extra='forbid'`` (inherited from ``ArtifactModel``) rejects unknown keys.

    This is the right Phase-0 posture: a user editing ``config.yaml`` with
    stray fields (e.g., ``provider_key: ...``) gets a clean static-schema
    validation error rather than silent ignore. Phase 1+ adds fields as
    they are actually consumed.
    """
    state = LocalState(root=tmp_path)
    state.ensure_root()
    state.config_path.write_text("unknown_field: 1\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        state.load_config()


def test_save_config_round_trip(tmp_path: Path) -> None:
    """``save_config`` writes a file that ``load_config`` reads back equal."""
    state = LocalState(root=tmp_path)
    original = UserConfig()
    state.save_config(original)
    assert state.config_path.exists()
    reloaded = state.load_config()
    assert reloaded == original


def test_save_config_creates_root_if_missing(tmp_path: Path) -> None:
    """``save_config`` creates ``root`` when it does not yet exist."""
    root = tmp_path / "nonexistent"
    state = LocalState(root=root)
    assert not root.exists()
    state.save_config(UserConfig())
    assert root.is_dir()
    assert state.config_path.is_file()


def test_default_overlay_dir_delegates_to_localstate() -> None:
    """``registries.loader.default_overlay_dir`` returns the same path as ``LocalState``.

    Pins the delegation contract: the registry-overlay path has a single
    source of truth (``LocalState``), and the legacy function survives
    only as a thin alias to preserve existing call sites in
    ``load_overlay`` / ``load_merged_registries``.
    """
    from cyberlab_gen.registries.loader import default_overlay_dir

    assert default_overlay_dir() == LocalState().registry_overlay_dir
