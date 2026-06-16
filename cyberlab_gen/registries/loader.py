"""File-by-file YAML loading for registries (bundled + overlay layers).

Bundled files validate through ``BundledRegistryFile[E]``; overlay files
through ``OverlayRegistryFile[E]``. The two are deliberately distinct
shapes (``schema-details.md §6.6`` lines 1433-1434): a bundled file
accidentally containing a ``proposals:`` block fails static schema validation via
``extra='forbid'`` on the bundled shape, while overlay files require
that block when they carry proposed entries. Sharing one shape would
erase that structural guarantee.

Single-file loaders are generic over the entry type and dispatch on
source directory — there is no "load any registry file" entry point.
Top-level loaders (``load_bundled``, ``load_overlay``) iterate the seven
canonical registries and collect them into a ``LoadedRegistryLayer`` for
the merge step (``cyberlab_gen/registries/merge.py``).

Path resolution:
- Bundled: resolved relative to the package source tree. Phase 0 has no
  installed-wheel story (ADR 0010 records the deferral).
- Overlay: ``~/.cyberlab-gen/registry-overlay/`` as the Phase-0 default;
  every overlay loader accepts a path override so tests and Task 6's
  ``LocalState`` can pin it elsewhere.

Error handling: every failure surfaces as ``RegistryLoadError`` with
the offending path and the underlying ``YAMLError`` /
``pydantic.ValidationError`` chained as ``__cause__``. Missing bundled
files are a hard error (broken distribution); missing overlay files
are silent (empty ``OverlayRegistryFile``).
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ValidationError
from ruamel.yaml import YAML, YAMLError

import cyberlab_gen
from cyberlab_gen.errors import RegistryLoadError
from cyberlab_gen.schemas.registries import (
    BundledRegistryFile,
    ExecutionContextEntry,
    ExternalDataSourceEntry,
    FacetEntry,
    LabCredentialEntry,
    MitreTechniqueCatalog,
    OverlayRegistryFile,
    StaticCatalogEntry,
    StaticCatalogsRegistry,
    ThesisTypeEntry,
    ValueTypeEntry,
)

logger = logging.getLogger(__name__)

REGISTRY_FILE_NAMES: Final[Sequence[str]] = (
    "value_types",
    "facets",
    "external_data_sources",
    "static_catalogs",
    "execution_contexts",
    "lab_credentials",
    "thesis_types",
)


@dataclass(frozen=True, slots=True)
class LoadedRegistryLayer:
    """One layer (bundled or overlay) — all seven files loaded but unmerged.

    Bundled and overlay layers share this container because the merge
    step only cares about each file's ``entries`` list. The concrete
    file-shape distinction (bundled rejects ``proposals``, overlay
    accepts it) was already enforced when each file was validated.
    """

    value_types_file: BundledRegistryFile[ValueTypeEntry] | OverlayRegistryFile[ValueTypeEntry]
    facets_file: BundledRegistryFile[FacetEntry] | OverlayRegistryFile[FacetEntry]
    external_data_sources_file: (
        BundledRegistryFile[ExternalDataSourceEntry] | OverlayRegistryFile[ExternalDataSourceEntry]
    )
    static_catalogs_file: (
        BundledRegistryFile[StaticCatalogEntry] | OverlayRegistryFile[StaticCatalogEntry]
    )
    execution_contexts_file: (
        BundledRegistryFile[ExecutionContextEntry] | OverlayRegistryFile[ExecutionContextEntry]
    )
    lab_credentials_file: (
        BundledRegistryFile[LabCredentialEntry] | OverlayRegistryFile[LabCredentialEntry]
    )
    thesis_types_file: BundledRegistryFile[ThesisTypeEntry] | OverlayRegistryFile[ThesisTypeEntry]


def bundled_registry_dir() -> Path:
    """Resolve the bundled registry directory under the source tree.

    Phase 0 has no installed-wheel layout; the package ships with the
    bundled ``registry/`` directory as a sibling of ``cyberlab_gen/``.
    ADR 0010 records the wheel-packaging deferral.
    """
    return Path(cyberlab_gen.__file__).resolve().parent.parent / "registry"


def default_overlay_dir() -> Path:
    """Default overlay directory: ``~/.cyberlab-gen/registry-overlay/``.

    Thin alias delegating to :class:`~cyberlab_gen.state.LocalState`,
    which owns the canonical path resolver as of Phase 0 Task 6. Kept
    as a function for call-site stability — ``load_overlay`` and
    ``load_merged_registries`` use it as a default-argument fallback.
    """
    from cyberlab_gen.state import LocalState

    return LocalState().registry_overlay_dir


def _read_yaml(path: Path) -> object:
    """Read a YAML file and return the parsed object.

    Empty files (``ruamel.yaml`` returns ``None``) are normalized to an
    empty mapping so the caller's ``model_validate`` sees a valid
    ``{entries: [], ...}``-shaped default rather than a ``None``.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RegistryLoadError(
            f"Failed to read registry file at {path}: {exc}",
            path=path,
            cause=exc,
        ) from exc
    yaml = YAML()
    try:
        data = yaml.load(text)
    except YAMLError as exc:
        raise RegistryLoadError(
            f"Failed to parse YAML at {path}: {exc}",
            path=path,
            cause=exc,
        ) from exc
    return {} if data is None else data


def _check_unique_keys[E: BaseModel](entries: list[E], entry_type: type[E], path: Path) -> None:
    """Reject duplicate ``ENTRY_KEY_FIELD`` values within a single file.

    Pydantic validates each entry's shape; cross-entry uniqueness is a
    registry-level constraint enforced here. (Plan note: under
    unconstrained scope a ``@model_validator`` on the file shapes would
    be cleaner — see the Task 4 execution-log entry for the refactor
    debt.)
    """
    # ``ENTRY_KEY_FIELD`` lives on each concrete entry subclass; the static
    # type here is ``BaseModel``, so ``getattr`` is the right read tool
    # (matching the pattern in ``cyberlab_gen/schemas/registries.py``).
    key_field: str = getattr(entry_type, "ENTRY_KEY_FIELD")  # noqa: B009
    keys: list[object] = [getattr(e, key_field) for e in entries]
    seen: set[object] = set()
    duplicates: list[object] = []
    for k in keys:
        if k in seen and k not in duplicates:
            duplicates.append(k)
        seen.add(k)
    if duplicates:
        raise RegistryLoadError(
            f"Registry file at {path} contains duplicate entry keys ({key_field}): {duplicates}",
            path=path,
        )


def load_static_catalogs() -> StaticCatalogsRegistry:
    """Load and validate the bundled ``static_catalogs`` registry.

    Has the same ``{entries: [...]}`` container shape as the other first-class
    registries but is validated directly against its ``StaticCatalogsRegistry``
    model (it is bundled-only; ``schema.md §4.14``).
    """
    return StaticCatalogsRegistry.model_validate(
        _read_yaml(bundled_registry_dir() / "static_catalogs.yaml")
    )


def load_mitre_techniques() -> MitreTechniqueCatalog:
    """Load and validate the bundled MITRE ATT&CK technique seed.

    Read locally (``registry-details.md §5.1``), never live-fetched. A Phase-1 *stopgap*
    seed of **external-authority** data — not a project-owned closed catalog like the
    ADR-0016 enums, and not a live mirror. It enriches the technique ids it happens to
    carry, but is **not** a membership gate: a well-formed uncatalogued id is left
    unverified, never rejected (ADR 0055/0058). Not part of ``MergedRegistries`` (read on
    demand). Wiring a real MITRE adapter (``lookup_by_id`` / ``lookup_by_description``) is
    LATER work (findings doc 0001 §5).
    """
    return MitreTechniqueCatalog.model_validate(
        _read_yaml(bundled_registry_dir() / "mitre_attack_techniques.yaml")
    )


def load_bundled_file[E: BaseModel](path: Path, entry_type: type[E]) -> BundledRegistryFile[E]:
    """Load a bundled registry YAML and validate as ``BundledRegistryFile[E]``.

    Bundled files carry only ``entries:``. A ``proposals:`` block is
    rejected by the inherited ``extra='forbid'`` (the static schema validation
    structural guarantee). Missing files are a hard error.
    """
    if not path.exists():
        raise RegistryLoadError(
            f"Bundled registry file not found: {path}",
            path=path,
        )
    data = _read_yaml(path)
    try:
        loaded = BundledRegistryFile[entry_type].model_validate(data)
    except ValidationError as exc:
        raise RegistryLoadError(
            f"Registry file at {path} failed validation:\n{exc}",
            path=path,
            cause=exc,
        ) from exc
    _check_unique_keys(loaded.entries, entry_type, path)
    return loaded


def load_overlay_file[E: BaseModel](path: Path, entry_type: type[E]) -> OverlayRegistryFile[E]:
    """Load an overlay registry YAML and validate as ``OverlayRegistryFile[E]``.

    Overlay files carry ``entries:`` plus an optional ``proposals:``
    audit block; the ``_proposal_keys_match_entries`` validator on the
    shape rejects orphan proposals. Missing files yield an empty
    ``OverlayRegistryFile`` — a fresh install has no overlay.
    """
    if not path.exists():
        logger.debug("overlay file %s missing; using empty entries", path)
        return OverlayRegistryFile[entry_type]()
    data = _read_yaml(path)
    try:
        loaded = OverlayRegistryFile[entry_type].model_validate(data)
    except ValidationError as exc:
        raise RegistryLoadError(
            f"Registry file at {path} failed validation:\n{exc}",
            path=path,
            cause=exc,
        ) from exc
    _check_unique_keys(loaded.entries, entry_type, path)
    return loaded


def load_bundled() -> LoadedRegistryLayer:
    """Load every bundled registry file. Missing files are a hard error."""
    base = bundled_registry_dir()
    return LoadedRegistryLayer(
        value_types_file=load_bundled_file(base / "value_types.yaml", ValueTypeEntry),
        facets_file=load_bundled_file(base / "facets.yaml", FacetEntry),
        external_data_sources_file=load_bundled_file(
            base / "external_data_sources.yaml", ExternalDataSourceEntry
        ),
        static_catalogs_file=load_bundled_file(base / "static_catalogs.yaml", StaticCatalogEntry),
        execution_contexts_file=load_bundled_file(
            base / "execution_contexts.yaml", ExecutionContextEntry
        ),
        lab_credentials_file=load_bundled_file(base / "lab_credentials.yaml", LabCredentialEntry),
        thesis_types_file=load_bundled_file(base / "thesis_types.yaml", ThesisTypeEntry),
    )


def load_overlay(overlay_dir: Path | None = None) -> LoadedRegistryLayer:
    """Load every overlay registry file. Missing files are silent (empty entries)."""
    base = overlay_dir if overlay_dir is not None else default_overlay_dir()
    return LoadedRegistryLayer(
        value_types_file=load_overlay_file(base / "value_types.yaml", ValueTypeEntry),
        facets_file=load_overlay_file(base / "facets.yaml", FacetEntry),
        external_data_sources_file=load_overlay_file(
            base / "external_data_sources.yaml", ExternalDataSourceEntry
        ),
        static_catalogs_file=load_overlay_file(base / "static_catalogs.yaml", StaticCatalogEntry),
        execution_contexts_file=load_overlay_file(
            base / "execution_contexts.yaml", ExecutionContextEntry
        ),
        lab_credentials_file=load_overlay_file(base / "lab_credentials.yaml", LabCredentialEntry),
        thesis_types_file=load_overlay_file(base / "thesis_types.yaml", ThesisTypeEntry),
    )
