"""LocalState — canonical on-disk paths under ``~/.cyberlab-gen/``.

Architectural source: ``docs/architecture.md §2.3`` (the system-diagram
local-state layout: ``config.yaml | cache/ | checkpoints/ | runs/ |
reports/``) and ``§2.2`` (the writable user overlay at
``~/.cyberlab-gen/registry-overlay/``). Per-path usage anchors:
``docs/pipeline.md §3.2.1`` (cache), ``§3.6`` (reports + config),
``§3.7`` (checkpoints + runs).

Phase 0 ships the path resolver, on-demand directory creation, and the
``config.yaml`` load/save round-trip. ``UserConfig`` is an empty
``ArtifactModel`` — Phase 1+ adds fields per the ``pipeline.md §3.6``
config table as the pipeline begins reading them.

Path resolution uses ``Path.home() / ".cyberlab-gen"`` literally on
every platform, matching ``architecture.md §2.3`` and the existing
``registries/loader.py:default_overlay_dir()``. The brief asked for
``platformdirs``; ADR 0012 records the deviation.
"""

from dataclasses import dataclass, field
from pathlib import Path

from cyberlab_gen.schemas.base import ArtifactModel


class UserConfig(ArtifactModel):
    """User-editable config at ``~/.cyberlab-gen/config.yaml``.

    Phase 0: no fields. ``extra='forbid'`` (inherited from
    ``ArtifactModel``) means a user-edited ``config.yaml`` with stray
    keys fails static-schema validation cleanly rather than silently
    dropping. Phase 1+ adds fields per ``pipeline.md §3.6`` (the config
    table around line 500: provider API keys, cost cap, telemetry
    toggle) as the pipeline begins consuming them.
    """


@dataclass(frozen=True)
class LocalState:
    """Canonical on-disk paths under ``~/.cyberlab-gen/``.

    Frozen dataclass: path computation has no mutable state. Inject
    ``root`` for filesystem-isolated tests (``LocalState(root=tmp_path)``).

    The five top-level directories plus ``config.yaml`` mirror
    ``architecture.md §2.3``'s system diagram exactly. Per-run
    subdirectories (``cache/<content-hash>/``, ``runs/<run-id>/``,
    ``checkpoints/<run-id>/``) are caller-managed: callers that know
    the hash or run-id compute ``(state.runs_dir / run_id).mkdir(...)``
    directly. Phase-0 keeps the surface minimal; helpers can land in
    Phase 1+ when the pipeline reveals what shape they should take.
    """

    root: Path = field(default_factory=lambda: Path.home() / ".cyberlab-gen")

    # Path properties — pure computation, no I/O.

    @property
    def config_path(self) -> Path:
        """``<root>/config.yaml`` — user-editable config."""
        return self.root / "config.yaml"

    @property
    def cache_dir(self) -> Path:
        """``<root>/cache/`` — blog ingestion cache (``pipeline.md §3.2.1``)."""
        return self.root / "cache"

    @property
    def checkpoints_dir(self) -> Path:
        """``<root>/checkpoints/`` — pipeline-resume snapshots (``pipeline.md §3.7``)."""
        return self.root / "checkpoints"

    @property
    def runs_dir(self) -> Path:
        """``<root>/runs/`` — per-run working directories (``pipeline.md §3.7``)."""
        return self.root / "runs"

    @property
    def reports_dir(self) -> Path:
        """``<root>/reports/`` — telemetry artifacts (``pipeline.md §3.6``)."""
        return self.root / "reports"

    @property
    def registry_overlay_dir(self) -> Path:
        """``<root>/registry-overlay/`` — user-overlay registries (``architecture.md §2.2``)."""
        return self.root / "registry-overlay"

    # Idempotent directory creation.

    def ensure_root(self) -> None:
        """Create ``<root>/`` if missing. Idempotent."""
        self.root.mkdir(parents=True, exist_ok=True)

    def ensure_cache_dir(self) -> None:
        """Create the cache directory if missing. Idempotent."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def ensure_checkpoints_dir(self) -> None:
        """Create the checkpoints directory if missing. Idempotent."""
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)

    def ensure_runs_dir(self) -> None:
        """Create the runs directory if missing. Idempotent."""
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def ensure_reports_dir(self) -> None:
        """Create the reports directory if missing. Idempotent."""
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def ensure_registry_overlay_dir(self) -> None:
        """Create the registry-overlay directory if missing. Idempotent."""
        self.registry_overlay_dir.mkdir(parents=True, exist_ok=True)

    # Config load/save.

    def load_config(self) -> UserConfig:
        """Load ``config.yaml``. Returns a default ``UserConfig`` when missing.

        Malformed YAML or a config with unknown fields surfaces as
        ``pydantic.ValidationError`` (via ``ArtifactModel.from_yaml`` →
        ``model_validate``). Phase 0 lets the underlying exceptions
        propagate; Phase 1+ may wrap them when the CLI surfaces config
        load errors to users.
        """
        if not self.config_path.exists():
            return UserConfig()
        return UserConfig.from_yaml(self.config_path.read_text(encoding="utf-8"))

    def save_config(self, config: UserConfig) -> None:
        """Write ``config`` to ``config.yaml``. Creates the root directory if missing."""
        self.ensure_root()
        self.config_path.write_text(config.to_yaml(), encoding="utf-8")


__all__ = ["LocalState", "UserConfig"]
