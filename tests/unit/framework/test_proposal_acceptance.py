"""Tests for proposal acceptance → overlay write (Part A, ADR 0044).

The acceptance coordinator is mechanical framework code: it converts in-flight
proposals to overlay entries, stamps a framework audit block, and writes them via
the overlay writer. ``--auto`` batches up to a cap; ``--interactive`` accepts one
at a time. These tests pin the write, the audit metadata, and the cap split.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cyberlab_gen.agents.proposals import ProposedFacet, ProposedValueType
from cyberlab_gen.framework.proposal_acceptance import (
    AcceptanceContext,
    accept_facet,
    auto_accept_to_overlay,
)
from cyberlab_gen.registries.loader import load_overlay_file
from cyberlab_gen.schemas.registries import FacetEntry, ValueTypeEntry

if TYPE_CHECKING:
    from pathlib import Path


def _ctx(overlay_dir: Path) -> AcceptanceContext:
    return AcceptanceContext(
        overlay_dir=overlay_dir,
        source_blog="https://example.com/blog",
        proposed_by_model="claude-opus-4-8",
        proposed_at=datetime(2026, 6, 7, tzinfo=UTC),
        run_id="run-xyz",
    )


def _facet(name: str = "target:aws_codebuild") -> ProposedFacet:
    return ProposedFacet(
        name=name,
        category="target",
        description="AWS CodeBuild target.",
        applies_at_levels=["lab"],
        reasoning="seen in blog",
    )


def _vt(name: str = "k8s_sa_token") -> ProposedValueType:
    return ProposedValueType(
        name=name,
        description="A token.",
        value_schema={"type": "string"},
        sensitive=True,
        reasoning="seen in blog",
    )


def test_accept_facet_writes_overlay_with_human_approval(tmp_path: Path) -> None:
    overlay = tmp_path / "registry-overlay"
    accept_facet(_facet(), _ctx(overlay), approval="human")
    loaded = load_overlay_file(overlay / "facets.yaml", FacetEntry)
    assert [e.name for e in loaded.entries] == ["target:aws_codebuild"]
    audit = loaded.proposals["target:aws_codebuild"]
    assert audit.approval == "human"
    assert audit.proposed_by_model == "claude-opus-4-8"
    assert audit.source_lab is None
    assert audit.reasoning == "seen in blog"


def test_auto_accept_writes_both_vocabularies(tmp_path: Path) -> None:
    overlay = tmp_path / "registry-overlay"
    result = auto_accept_to_overlay(
        value_type_proposals=[_vt()],
        facet_proposals=[_facet()],
        ctx=_ctx(overlay),
        cap=5,
    )
    assert result.deferred == []
    assert len(result.accepted) == 2
    vts = load_overlay_file(overlay / "value_types.yaml", ValueTypeEntry)
    facets = load_overlay_file(overlay / "facets.yaml", FacetEntry)
    assert [e.name for e in vts.entries] == ["k8s_sa_token"]
    assert [e.name for e in facets.entries] == ["target:aws_codebuild"]
    assert vts.proposals["k8s_sa_token"].approval == "auto"
    assert vts.entries[0].proposed_in_run == "run-xyz"


def test_auto_accept_respects_cap(tmp_path: Path) -> None:
    overlay = tmp_path / "registry-overlay"
    result = auto_accept_to_overlay(
        value_type_proposals=[_vt("vt_a"), _vt("vt_b")],
        facet_proposals=[_facet("target:facet_c")],
        ctx=_ctx(overlay),
        cap=2,
    )
    assert len(result.accepted) == 2
    assert len(result.deferred) == 1
    # The deferred (over-cap) proposal is not written.
    facets = load_overlay_file(overlay / "facets.yaml", FacetEntry)
    assert facets.entries == []
