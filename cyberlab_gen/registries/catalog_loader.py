"""Loaders for the closed bundled-only catalogs (ADR 0016).

Architectural source: ``registry-details.md §7`` (the closed catalogs);
``schema.md §4.5`` / §4.7 (where the enum-valued fields they back are consumed);
ADR 0016.

These four catalogs (``detection_components``, ``severity_levels``,
``detection_formats``, ``provisioning_mechanisms``) are *not* runtime registries
(``catalogs.py`` docstring): they never participate in the
proposal lifecycle and are deliberately not part of ``MergedRegistries``. They
are read on demand by specific consumers — Validator Layer 1 reads them for
reference resolution / catalog-drift detection (``validation.md §6.4``), Layer 3
reads ``ordinal``, the Generator reads ``validator_support``.

Each loader reads ``registry/<stem>.yaml`` and validates it through its
``catalogs.py`` container model. Like the other bundled loaders, a missing file
is a hard error (broken distribution); a malformed file surfaces as
``RegistryLoadError`` with the offending path chained.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError
from ruamel.yaml import YAML, YAMLError

from cyberlab_gen.errors import RegistryLoadError
from cyberlab_gen.registries.loader import bundled_registry_dir
from cyberlab_gen.schemas.catalogs import (
    DetectionComponentsCatalog,
    DetectionFormatsCatalog,
    ProvisioningMechanismsCatalog,
    SeverityLevelsCatalog,
)

if TYPE_CHECKING:
    from pathlib import Path

    from cyberlab_gen.schemas.base import ArtifactModel


def _read_catalog_yaml(path: Path) -> object:
    """Read a bundled catalog YAML file, normalizing an empty file to ``{}``.

    Mirrors the bundled-registry read discipline (``loader._read_yaml``) without
    reaching into that module's private helper: a read or parse failure surfaces
    as ``RegistryLoadError`` with the offending path chained.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RegistryLoadError(
            f"Failed to read catalog file at {path}: {exc}", path=path, cause=exc
        ) from exc
    yaml = YAML()
    try:
        data = yaml.load(text)
    except YAMLError as exc:
        raise RegistryLoadError(
            f"Failed to parse YAML at {path}: {exc}", path=path, cause=exc
        ) from exc
    return {} if data is None else data


def _load_catalog[C: ArtifactModel](stem: str, model: type[C]) -> C:
    """Load ``registry/<stem>.yaml`` and validate it against ``model``."""
    path: Path = bundled_registry_dir() / f"{stem}.yaml"
    if not path.exists():
        raise RegistryLoadError(f"Bundled catalog file not found: {path}", path=path)
    data = _read_catalog_yaml(path)
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise RegistryLoadError(
            f"Catalog file at {path} failed validation:\n{exc}",
            path=path,
            cause=exc,
        ) from exc


def load_detection_components() -> DetectionComponentsCatalog:
    """Load the bundled ``detection_components`` catalog (``registry-details.md §7.1``)."""
    return _load_catalog("detection_components", DetectionComponentsCatalog)


def load_severity_levels() -> SeverityLevelsCatalog:
    """Load the bundled ``severity_levels`` catalog (``registry-details.md §7.2``)."""
    return _load_catalog("severity_levels", SeverityLevelsCatalog)


def load_detection_formats() -> DetectionFormatsCatalog:
    """Load the bundled ``detection_formats`` catalog (``registry-details.md §7.3``)."""
    return _load_catalog("detection_formats", DetectionFormatsCatalog)


def load_provisioning_mechanisms() -> ProvisioningMechanismsCatalog:
    """Load the bundled ``provisioning_mechanisms`` catalog (``registry-details.md §7.4``)."""
    return _load_catalog("provisioning_mechanisms", ProvisioningMechanismsCatalog)


__all__ = [
    "load_detection_components",
    "load_detection_formats",
    "load_provisioning_mechanisms",
    "load_severity_levels",
]
