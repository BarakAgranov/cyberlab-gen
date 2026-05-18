"""State subpackage — local on-disk state under ``~/.cyberlab-gen/``.

Manages ``config.yaml``, the blog ingestion cache, pipeline-resume
checkpoints, per-run working directories, telemetry reports, and the
registry-overlay directory. Path resolution is hardcoded to
``Path.home() / ".cyberlab-gen"`` on every platform, matching
``docs/architecture.md §2.3`` literally; ADR 0012 records why
``platformdirs`` was not used despite the brief's instruction.

Cross-subpackage imports go through this re-export surface; intra-subpackage
modules import from each sibling directly.
"""

from cyberlab_gen.state.local_state import LocalState, UserConfig

__all__ = ["LocalState", "UserConfig"]
