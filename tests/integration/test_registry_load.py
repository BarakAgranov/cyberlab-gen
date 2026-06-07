"""Smoke + malformed-fixture tests for the registry loader.

Implements the Phase 0 ``implementation-plan.md §3.4`` check 4 smoke test:
every bundled registry file loads cleanly through its Pydantic meta-schema,
and the loader raises a clear ``RegistryLoadError`` (with file path and
underlying error preserved) on malformed input.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError
from ruamel.yaml import YAMLError

from cyberlab_gen.errors import RegistryLoadError
from cyberlab_gen.registries.loader import (
    REGISTRY_FILE_NAMES,
    bundled_registry_dir,
    load_bundled,
    load_bundled_file,
    load_overlay_file,
)
from cyberlab_gen.schemas.registries import (
    ExecutionContextEntry,
    ExternalDataSourceEntry,
    FacetEntry,
    LabCredentialEntry,
    StaticCatalogEntry,
    ThesisTypeEntry,
    ValueTypeEntry,
)

# --- Smoke: every bundled YAML loads through its meta-schema ---------------


def test_value_types_yaml_loads_through_bundled_schema() -> None:
    path = bundled_registry_dir() / "value_types.yaml"
    loaded = load_bundled_file(path, ValueTypeEntry)
    assert len(loaded.entries) >= 1
    assert loaded.entries[0].name == "aws_credentials"


def test_facets_yaml_loads_through_bundled_schema() -> None:
    path = bundled_registry_dir() / "facets.yaml"
    loaded = load_bundled_file(path, FacetEntry)
    assert len(loaded.entries) >= 1
    assert loaded.entries[0].name == "target:aws"


def test_external_data_sources_yaml_loads_through_bundled_schema() -> None:
    path = bundled_registry_dir() / "external_data_sources.yaml"
    loaded = load_bundled_file(path, ExternalDataSourceEntry)
    assert len(loaded.entries) >= 1
    assert loaded.entries[0].id == "nvd"


def test_static_catalogs_yaml_loads_through_bundled_schema() -> None:
    path = bundled_registry_dir() / "static_catalogs.yaml"
    loaded = load_bundled_file(path, StaticCatalogEntry)
    assert len(loaded.entries) >= 1
    assert loaded.entries[0].id == "aws_iam_catalog"
    # ADR 0007 case: empty path_template is valid.
    assert loaded.entries[0].endpoints[0].path_template == ""


def test_execution_contexts_yaml_loads_through_bundled_schema() -> None:
    path = bundled_registry_dir() / "execution_contexts.yaml"
    loaded = load_bundled_file(path, ExecutionContextEntry)
    assert len(loaded.entries) >= 1
    assert loaded.entries[0].name == "attacker_local"


def test_lab_credentials_yaml_loads_through_bundled_schema() -> None:
    path = bundled_registry_dir() / "lab_credentials.yaml"
    loaded = load_bundled_file(path, LabCredentialEntry)
    assert len(loaded.entries) >= 1
    assert loaded.entries[0].id == "aws_test_access_key"


def test_thesis_types_yaml_loads_through_bundled_schema() -> None:
    # thesis_types became a runtime-proposable registry in ADR 0045 (was a catalog).
    path = bundled_registry_dir() / "thesis_types.yaml"
    loaded = load_bundled_file(path, ThesisTypeEntry)
    assert len(loaded.entries) >= 1
    assert loaded.entries[0].name == "ttp_chain"


def test_load_bundled_yields_complete_layer() -> None:
    """Top-level loader returns seven populated registry files."""
    layer = load_bundled()
    assert len(layer.value_types_file.entries) >= 1
    assert len(layer.facets_file.entries) >= 1
    assert len(layer.external_data_sources_file.entries) >= 1
    assert len(layer.static_catalogs_file.entries) >= 1
    assert len(layer.execution_contexts_file.entries) >= 1
    assert len(layer.lab_credentials_file.entries) >= 1
    assert len(layer.thesis_types_file.entries) >= 1


def test_registry_file_names_covers_seven_canonical_registries() -> None:
    assert set(REGISTRY_FILE_NAMES) == {
        "value_types",
        "facets",
        "external_data_sources",
        "static_catalogs",
        "execution_contexts",
        "lab_credentials",
        "thesis_types",
    }


# --- Structural guarantee: bundled rejects `proposals:` --------------------


def test_bundled_file_with_proposals_block_rejected(tmp_path: Path) -> None:
    """A bundled file accidentally containing `proposals:` fails static schema validation.

    The whole point of the separate ``BundledRegistryFile`` shape
    (``schema-details.md §6.6`` lines 1433-1434).
    """
    path = tmp_path / "value_types.yaml"
    path.write_text(
        """entries:
  - name: aws_credentials
    description: "x"
    schema: {type: object}
    sensitive: true
    platforms: [aws]
proposals:
  aws_credentials:
    proposal_origin: llm_during_extraction
    source_lab: lab-1
    source_blog: https://example.com/blog
    proposed_by_model: claude-opus
    proposed_at: 2026-05-01T00:00:00Z
    reasoning: ok
""",
        encoding="utf-8",
    )
    with pytest.raises(RegistryLoadError) as excinfo:
        load_bundled_file(path, ValueTypeEntry)
    assert str(path) in str(excinfo.value)
    assert "proposals" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, ValidationError)


# --- Malformed-input handling ---------------------------------------------


def test_malformed_yaml_yields_clear_error(tmp_path: Path) -> None:
    """Exit criterion 4: clear error on a deliberately broken fixture."""
    path = tmp_path / "value_types.yaml"
    path.write_text("entries:\n  - name: : not valid yaml\n", encoding="utf-8")
    with pytest.raises(RegistryLoadError) as excinfo:
        load_bundled_file(path, ValueTypeEntry)
    assert str(path) in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, YAMLError)


def test_pydantic_validation_error_yields_clear_error(tmp_path: Path) -> None:
    """A missing required field gets a clear file-path + Pydantic message."""
    path = tmp_path / "facets.yaml"
    # FacetEntry.proposed_by is required (no default).
    path.write_text(
        """entries:
  - name: target:aws
    category: target
    description: "missing proposed_by"
    applies_at_levels: [lab]
""",
        encoding="utf-8",
    )
    with pytest.raises(RegistryLoadError) as excinfo:
        load_bundled_file(path, FacetEntry)
    msg = str(excinfo.value)
    assert str(path) in msg
    assert "proposed_by" in msg
    assert isinstance(excinfo.value.__cause__, ValidationError)


def test_missing_bundled_file_is_hard_error(tmp_path: Path) -> None:
    path = tmp_path / "missing.yaml"
    with pytest.raises(RegistryLoadError) as excinfo:
        load_bundled_file(path, ValueTypeEntry)
    assert str(path) in str(excinfo.value)
    assert "not found" in str(excinfo.value)


def test_missing_overlay_file_yields_empty(tmp_path: Path) -> None:
    path = tmp_path / "missing.yaml"
    loaded = load_overlay_file(path, ValueTypeEntry)
    assert loaded.entries == []
    assert loaded.proposals == {}


def test_empty_yaml_file_treated_as_empty_entries(tmp_path: Path) -> None:
    path = tmp_path / "value_types.yaml"
    path.write_text("", encoding="utf-8")
    loaded = load_bundled_file(path, ValueTypeEntry)
    assert loaded.entries == []


def test_duplicate_entry_keys_rejected(tmp_path: Path) -> None:
    """Two entries sharing an ``ENTRY_KEY_FIELD`` value fail load."""
    path = tmp_path / "facets.yaml"
    path.write_text(
        """entries:
  - name: target:aws
    category: target
    proposed_by: extractor
    description: "first"
    applies_at_levels: [lab]
  - name: target:aws
    category: target
    proposed_by: extractor
    description: "duplicate"
    applies_at_levels: [lab]
""",
        encoding="utf-8",
    )
    with pytest.raises(RegistryLoadError) as excinfo:
        load_bundled_file(path, FacetEntry)
    assert "duplicate" in str(excinfo.value).lower()
    assert "target:aws" in str(excinfo.value)


def test_registry_load_error_carries_path_attribute(tmp_path: Path) -> None:
    path = tmp_path / "missing.yaml"
    try:
        load_bundled_file(path, ValueTypeEntry)
    except RegistryLoadError as exc:
        assert exc.path == path
        assert exc.stage == "registry"
    else:
        pytest.fail("expected RegistryLoadError")
