"""Integration tests for the bundled + overlay merge.

Pins overlay-wins semantics (``schema.md §4.11``), accessor coverage
(``schema-details.md §6.6``), the orphan-proposal Layer-1 guarantee,
``MergedRegistries`` immutability, and the ``lab_credential_patterns``
filter behavior.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from cyberlab_gen.errors import RegistryLoadError
from cyberlab_gen.registries.merge import (
    MergedRegistries,
    load_merged_registries,
)


def _write_overlay_facets(overlay_dir: Path, body: str) -> None:
    overlay_dir.mkdir(parents=True, exist_ok=True)
    (overlay_dir / "facets.yaml").write_text(body, encoding="utf-8")


def _write_overlay_external_sources(overlay_dir: Path, body: str) -> None:
    overlay_dir.mkdir(parents=True, exist_ok=True)
    (overlay_dir / "external_data_sources.yaml").write_text(body, encoding="utf-8")


def _write_overlay_value_types(overlay_dir: Path, body: str) -> None:
    overlay_dir.mkdir(parents=True, exist_ok=True)
    (overlay_dir / "value_types.yaml").write_text(body, encoding="utf-8")


# --- Baseline: no overlay --------------------------------------------------


def test_load_merged_registries_with_no_overlay_yields_bundled_only(
    tmp_path: Path,
) -> None:
    """A pristine install (no overlay dir) returns just the bundled seeds."""
    merged = load_merged_registries(overlay_dir=tmp_path / "missing")
    assert merged.value_type("aws_credentials") is not None
    assert merged.facet("target:aws") is not None
    assert merged.external_source("nvd") is not None
    assert merged.static_catalog("aws_iam_catalog") is not None
    assert merged.execution_context("attacker_local") is not None
    assert len(merged.lab_credential_patterns()) == 1


# --- Overlay-wins ----------------------------------------------------------


def test_overlay_wins_on_name_collision_for_facets(tmp_path: Path) -> None:
    _write_overlay_facets(
        tmp_path,
        """entries:
  - name: target:aws
    category: target
    proposed_by: extractor
    description: "OVERRIDDEN BY OVERLAY"
    applies_at_levels: [lab]
""",
    )
    merged = load_merged_registries(overlay_dir=tmp_path)
    entry = merged.facet("target:aws")
    assert entry is not None
    assert entry.description == "OVERRIDDEN BY OVERLAY"


def test_overlay_wins_on_id_collision_for_external_sources(tmp_path: Path) -> None:
    _write_overlay_external_sources(
        tmp_path,
        """entries:
  - id: nvd
    name: "Overridden NVD"
    description: "Replaced via overlay."
    base_url: "https://example.com/api"
    auth_type: none
    rate_limit:
      without_key: "n/a"
    endpoints: []
    cache:
      ttl: P1D
      scope: per-key
""",
    )
    merged = load_merged_registries(overlay_dir=tmp_path)
    entry = merged.external_source("nvd")
    assert entry is not None
    assert entry.name == "Overridden NVD"


# --- Overlay introduces new entries ----------------------------------------


def test_overlay_only_entry_appears_in_merged(tmp_path: Path) -> None:
    _write_overlay_facets(
        tmp_path,
        """entries:
  - name: target:cloudflare
    category: target
    proposed_by: extractor
    description: "Attack targets Cloudflare."
    applies_at_levels: [lab]
""",
    )
    merged = load_merged_registries(overlay_dir=tmp_path)
    # Bundled entry still present.
    assert merged.facet("target:aws") is not None
    # New overlay-only entry visible.
    entry = merged.facet("target:cloudflare")
    assert entry is not None
    assert entry.description == "Attack targets Cloudflare."


# --- Proposal validation ---------------------------------------------------


def test_overlay_proposals_block_for_real_entry_accepted(tmp_path: Path) -> None:
    """An overlay file with a `proposals:` key matching an entry loads cleanly."""
    _write_overlay_value_types(
        tmp_path,
        """entries:
  - name: my_proposed_type
    description: "Proposed at runtime."
    schema:
      type: object
    sensitive: false
    platforms: []
    proposed_by: extractor
    proposed_in_run: run-123
proposals:
  my_proposed_type:
    proposal_origin: llm_during_extraction
    source_lab: lab-1
    source_blog: "https://example.com/blog"
    proposed_by_model: claude-opus
    proposed_at: 2026-05-01T00:00:00Z
    reasoning: "Encountered an unknown credential pair in the blog."
    approval: auto
""",
    )
    merged = load_merged_registries(overlay_dir=tmp_path)
    entry = merged.value_type("my_proposed_type")
    assert entry is not None
    assert entry.proposed_by == "extractor"


def test_overlay_proposals_block_orphan_rejected(tmp_path: Path) -> None:
    """Orphan proposals (no matching entry) fail Layer 1."""
    _write_overlay_value_types(
        tmp_path,
        """entries: []
proposals:
  ghost_entry:
    proposal_origin: llm_during_extraction
    source_lab: lab-1
    source_blog: "https://example.com/blog"
    proposed_by_model: claude-opus
    proposed_at: 2026-05-01T00:00:00Z
    reasoning: "no matching entry"
    approval: auto
""",
    )
    with pytest.raises(RegistryLoadError) as excinfo:
        load_merged_registries(overlay_dir=tmp_path)
    assert isinstance(excinfo.value.__cause__, ValidationError)
    assert "ghost_entry" in str(excinfo.value)


# --- lab_credential_patterns filter ----------------------------------------


def test_lab_credential_patterns_no_filter_returns_all() -> None:
    merged = load_merged_registries(overlay_dir=Path("/nonexistent-path-on-disk"))
    patterns = merged.lab_credential_patterns()
    assert len(patterns) == 1
    assert patterns[0].id == "aws_test_access_key"


def test_lab_credential_patterns_filter_by_platform() -> None:
    merged = load_merged_registries(overlay_dir=Path("/nonexistent-path-on-disk"))
    aws_patterns = merged.lab_credential_patterns(platform="aws")
    assert len(aws_patterns) == 1
    assert aws_patterns[0].platform == "aws"

    none_patterns = merged.lab_credential_patterns(platform="azure")
    assert none_patterns == []


# --- Immutability ----------------------------------------------------------


def test_merged_registries_is_frozen() -> None:
    merged = load_merged_registries(overlay_dir=Path("/nonexistent-path-on-disk"))
    with pytest.raises(ValidationError):
        merged.value_types = merged.value_types  # type: ignore[misc]


# --- Accessor None-return semantics ----------------------------------------


def test_accessors_return_none_for_missing_key() -> None:
    merged = load_merged_registries(overlay_dir=Path("/nonexistent-path-on-disk"))
    assert merged.value_type("does_not_exist") is None
    assert merged.facet("target:does_not_exist") is None
    assert merged.external_source("does_not_exist") is None
    assert merged.static_catalog("does_not_exist") is None
    assert merged.execution_context("does_not_exist") is None


# --- Merge preserves bundled-first ordering --------------------------------


def test_merged_registry_ordering_bundled_then_overlay_new_keys(tmp_path: Path) -> None:
    """Insertion order is bundled-first; overlay-only entries append."""
    _write_overlay_facets(
        tmp_path,
        """entries:
  - name: target:cloudflare
    category: target
    proposed_by: extractor
    description: "Cloudflare."
    applies_at_levels: [lab]
""",
    )
    merged = load_merged_registries(overlay_dir=tmp_path)
    names = [e.name for e in merged.facets.entries]
    assert names == ["target:aws", "target:cloudflare"]


def test_merged_registries_construct_directly_is_valid() -> None:
    """``MergedRegistries`` accepts construction from already-built registries."""
    from cyberlab_gen.schemas.registries import (
        ExecutionContextsRegistry,
        ExternalDataSourcesRegistry,
        FacetsRegistry,
        LabCredentialsRegistry,
        StaticCatalogsRegistry,
        ValueTypesRegistry,
    )

    merged = MergedRegistries(
        value_types=ValueTypesRegistry(),
        facets=FacetsRegistry(),
        external_data_sources=ExternalDataSourcesRegistry(),
        static_catalogs=StaticCatalogsRegistry(),
        execution_contexts=ExecutionContextsRegistry(),
        lab_credentials=LabCredentialsRegistry(),
    )
    assert merged.value_type("anything") is None
    assert merged.lab_credential_patterns() == []
