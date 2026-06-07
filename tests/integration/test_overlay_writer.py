"""Overlay-writer tests: the propose→accept→overlay write (Part A, ADR 0044).

The writer is mechanical framework code: given a typed registry entry and a
framework-stamped ``ProposalAuditBlock`` it appends/replaces the entry in the
overlay file and records the audit block, atomically. These tests pin the
round-trip (write → load), merge-on-existing, same-key replacement, and that no
temp file is left behind.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cyberlab_gen.registries.loader import load_overlay_file
from cyberlab_gen.registries.overlay_writer import write_overlay_entry
from cyberlab_gen.schemas.registries import FacetEntry, ProposalAuditBlock

if TYPE_CHECKING:
    from pathlib import Path


def _audit(approval: str = "auto", reasoning: str = "new facet seen in blog") -> ProposalAuditBlock:
    return ProposalAuditBlock(
        proposal_origin="llm_during_extraction",
        source_blog="https://example.com/blog",  # type: ignore[arg-type]
        proposed_by_model="claude-opus-4-8",
        proposed_at=datetime(2026, 6, 7, tzinfo=UTC),
        reasoning=reasoning,  # type: ignore[arg-type]
        approval=approval,  # type: ignore[arg-type]
    )


def _facet(
    name: str = "target:aws_codebuild", description: str = "AWS CodeBuild target"
) -> FacetEntry:
    return FacetEntry(
        name=name,  # type: ignore[arg-type]
        category="target",
        proposed_by="extractor",
        description=description,  # type: ignore[arg-type]
        applies_at_levels=["lab"],
    )


def test_write_creates_overlay_dir_and_file(tmp_path: Path) -> None:
    overlay = tmp_path / "registry-overlay"
    assert not overlay.exists()
    write_overlay_entry(
        overlay_dir=overlay,
        registry_filename="facets",
        entry_type=FacetEntry,
        entry=_facet(),
        audit=_audit(),
    )
    loaded = load_overlay_file(overlay / "facets.yaml", FacetEntry)
    assert [e.name for e in loaded.entries] == ["target:aws_codebuild"]
    assert "target:aws_codebuild" in loaded.proposals
    assert loaded.proposals["target:aws_codebuild"].approval == "auto"
    assert loaded.proposals["target:aws_codebuild"].source_lab is None


def test_write_appends_new_key_to_existing_file(tmp_path: Path) -> None:
    overlay = tmp_path / "registry-overlay"
    write_overlay_entry(
        overlay_dir=overlay,
        registry_filename="facets",
        entry_type=FacetEntry,
        entry=_facet("target:aws_codebuild"),
        audit=_audit(),
    )
    write_overlay_entry(
        overlay_dir=overlay,
        registry_filename="facets",
        entry_type=FacetEntry,
        entry=_facet("target:github_ci_integration", "GitHub CI integration target"),
        audit=_audit(),
    )
    loaded = load_overlay_file(overlay / "facets.yaml", FacetEntry)
    assert {e.name for e in loaded.entries} == {
        "target:aws_codebuild",
        "target:github_ci_integration",
    }
    assert set(loaded.proposals) == {"target:aws_codebuild", "target:github_ci_integration"}


def test_write_replaces_entry_with_same_key(tmp_path: Path) -> None:
    overlay = tmp_path / "registry-overlay"
    write_overlay_entry(
        overlay_dir=overlay,
        registry_filename="facets",
        entry_type=FacetEntry,
        entry=_facet(description="first description"),
        audit=_audit(approval="auto"),
    )
    write_overlay_entry(
        overlay_dir=overlay,
        registry_filename="facets",
        entry_type=FacetEntry,
        entry=_facet(description="corrected description"),
        audit=_audit(approval="human"),
    )
    loaded = load_overlay_file(overlay / "facets.yaml", FacetEntry)
    assert len(loaded.entries) == 1
    assert loaded.entries[0].description == "corrected description"
    assert loaded.proposals["target:aws_codebuild"].approval == "human"


def test_write_leaves_no_temp_file(tmp_path: Path) -> None:
    overlay = tmp_path / "registry-overlay"
    write_overlay_entry(
        overlay_dir=overlay,
        registry_filename="facets",
        entry_type=FacetEntry,
        entry=_facet(),
        audit=_audit(),
    )
    leftovers = [p.name for p in overlay.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []
