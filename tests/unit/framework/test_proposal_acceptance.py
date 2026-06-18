"""Tests for the generic proposal accept path → overlay write (ADR 0044, generalized in ADR 0099).

The acceptance coordinator is mechanical framework code: it converts any in-flight ``Proposal`` to
its overlay entry, stamps a framework audit block (with the framework-supplied ``proposed_by`` /
``proposal_origin``), mechanically dedups against the merged registry + the in-flight batch, and
writes via the overlay writer. ``auto_accept_to_overlay`` batches up to a cap. These tests pin the
write, the stamp, the dedup, the cap split, and the post-write snapshot reload (the invalidation seam).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cyberlab_gen.agents.proposals import ProposedFacet, ProposedThesisType, ProposedValueType
from cyberlab_gen.framework.proposal_acceptance import (
    AcceptanceContext,
    accept_proposal,
    accept_proposals,
    auto_accept_to_overlay,
)
from cyberlab_gen.registries.loader import load_overlay_file
from cyberlab_gen.registries.merge import (
    MergedRegistries,
    load_merged_registries,
    reload_merged_registries,
)
from cyberlab_gen.schemas.registries import (
    ExecutionContextsRegistry,
    ExternalDataSourcesRegistry,
    FacetEntry,
    FacetsRegistry,
    LabCredentialsRegistry,
    StaticCatalogsRegistry,
    ThesisTypeEntry,
    ThesisTypesRegistry,
    ValueTypeEntry,
    ValueTypesRegistry,
)

if TYPE_CHECKING:
    from pathlib import Path


def _ctx(
    overlay_dir: Path,
    *,
    proposed_by: str = "extractor",
    proposal_origin: str = "llm_during_extraction",
) -> AcceptanceContext:
    return AcceptanceContext(
        overlay_dir=overlay_dir,
        source_blog="https://example.com/blog",
        proposed_by_model="claude-opus-4-8",
        proposed_at=datetime(2026, 6, 7, tzinfo=UTC),
        run_id="run-xyz",
        proposed_by=proposed_by,  # type: ignore[arg-type]
        proposal_origin=proposal_origin,  # type: ignore[arg-type]
    )


def _facet(name: str = "target:aws_codebuild", *, category: str = "target") -> ProposedFacet:
    return ProposedFacet(
        name=name,
        category=category,  # type: ignore[arg-type]
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


def _thesis(name: str = "ci_cd_compromise") -> ProposedThesisType:
    return ProposedThesisType(
        name=name, description="Compromise of a CI/CD build pipeline.", reasoning="seen in blog"
    )


def _registries_with(*, facet: str | None = None) -> MergedRegistries:
    """A minimal merged registry, optionally seeding one bundled facet for the dedup tests."""
    facet_entries = (
        [
            FacetEntry(
                name=facet,  # type: ignore[arg-type]
                category="target",
                proposed_by="maintainer",
                description="already bundled",
                applies_at_levels=["lab"],
            )
        ]
        if facet is not None
        else []
    )
    return MergedRegistries(
        value_types=ValueTypesRegistry(),
        facets=FacetsRegistry(entries=facet_entries),
        external_data_sources=ExternalDataSourcesRegistry(),
        static_catalogs=StaticCatalogsRegistry(),
        execution_contexts=ExecutionContextsRegistry(),
        lab_credentials=LabCredentialsRegistry(),
        thesis_types=ThesisTypesRegistry(),
    )


# --- the single generic accept path ----------------------------------------


def test_accept_proposal_writes_facet_overlay_stamped_from_context(tmp_path: Path) -> None:
    overlay = tmp_path / "registry-overlay"
    accept_proposal(_facet(), _ctx(overlay), approval="human")
    loaded = load_overlay_file(overlay / "facets.yaml", FacetEntry)
    assert [e.name for e in loaded.entries] == ["target:aws_codebuild"]
    assert loaded.entries[0].proposed_by == "extractor"  # stamped from ctx, not hardcoded
    audit = loaded.proposals["target:aws_codebuild"]
    assert audit.approval == "human"
    assert audit.proposal_origin == "llm_during_extraction"
    assert audit.proposed_by_model == "claude-opus-4-8"
    assert audit.source_lab is None
    assert audit.reasoning == "seen in blog"


def test_accept_proposal_stamps_planner_origin_when_context_says_planner(tmp_path: Path) -> None:
    # Task 7 / ADR 0099: the same generic path stamps proposed_by=planner +
    # proposal_origin=llm_during_planning purely from the framework-supplied context.
    overlay = tmp_path / "registry-overlay"
    accept_proposal(
        _facet("runtime:codebuild_project", category="runtime"),
        _ctx(overlay, proposed_by="planner", proposal_origin="llm_during_planning"),
        approval="auto",
    )
    loaded = load_overlay_file(overlay / "facets.yaml", FacetEntry)
    assert loaded.entries[0].proposed_by == "planner"
    assert loaded.proposals["runtime:codebuild_project"].proposal_origin == "llm_during_planning"


def test_accept_proposal_writes_value_type_with_run_id(tmp_path: Path) -> None:
    overlay = tmp_path / "registry-overlay"
    accept_proposal(_vt(), _ctx(overlay), approval="auto")
    loaded = load_overlay_file(overlay / "value_types.yaml", ValueTypeEntry)
    assert loaded.entries[0].proposed_in_run == "run-xyz"
    assert loaded.entries[0].proposed_by == "extractor"


def test_accept_proposal_writes_thesis_type(tmp_path: Path) -> None:
    overlay = tmp_path / "registry-overlay"
    accept_proposal(_thesis(), _ctx(overlay), approval="auto")
    loaded = load_overlay_file(overlay / "thesis_types.yaml", ThesisTypeEntry)
    assert [e.name for e in loaded.entries] == ["ci_cd_compromise"]
    assert loaded.entries[0].proposed_by == "extractor"


# --- the batch path: cap + dedup --------------------------------------------


def test_accept_proposals_writes_all_three_vocabularies(tmp_path: Path) -> None:
    overlay = tmp_path / "registry-overlay"
    result = accept_proposals([_vt(), _facet(), _thesis()], _ctx(overlay), approval="auto", cap=5)
    assert result.deferred == []
    assert result.skipped == []
    assert len(result.accepted) == 3
    assert [
        e.name for e in load_overlay_file(overlay / "value_types.yaml", ValueTypeEntry).entries
    ] == ["k8s_sa_token"]
    assert [e.name for e in load_overlay_file(overlay / "facets.yaml", FacetEntry).entries] == [
        "target:aws_codebuild"
    ]
    assert [
        e.name for e in load_overlay_file(overlay / "thesis_types.yaml", ThesisTypeEntry).entries
    ] == ["ci_cd_compromise"]


def test_accept_proposals_respects_cap(tmp_path: Path) -> None:
    overlay = tmp_path / "registry-overlay"
    result = accept_proposals(
        [_vt("vt_a"), _vt("vt_b"), _facet("target:c")], _ctx(overlay), approval="auto", cap=2
    )
    assert len(result.accepted) == 2
    assert len(result.deferred) == 1
    assert (
        load_overlay_file(overlay / "facets.yaml", FacetEntry).entries == []
    )  # over-cap not written


def test_accept_proposals_skips_a_proposal_already_in_the_merged_registry(tmp_path: Path) -> None:
    # ADR 0099: a proposal colliding with a bundled entry is mechanically rejected (not written) —
    # it would otherwise silently shadow the bundled entry (overlay-wins). The Planner is a second
    # proposer, so this matters.
    overlay = tmp_path / "registry-overlay"
    result = accept_proposals(
        [_facet("target:already_bundled")],
        _ctx(overlay),
        approval="auto",
        registries=_registries_with(facet="target:already_bundled"),
    )
    assert result.accepted == []
    assert len(result.skipped) == 1
    assert not (overlay / "facets.yaml").exists()  # nothing written


def test_accept_proposals_skips_intra_batch_duplicate(tmp_path: Path) -> None:
    # Two same-named proposals in one batch: the first writes, the second is a skip (the in-flight
    # stale-snapshot case, handled with a running key-set — ADR 0099).
    overlay = tmp_path / "registry-overlay"
    result = accept_proposals(
        [_facet("target:dup"), _facet("target:dup")], _ctx(overlay), approval="auto"
    )
    assert len(result.accepted) == 1
    assert len(result.skipped) == 1
    assert [e.name for e in load_overlay_file(overlay / "facets.yaml", FacetEntry).entries] == [
        "target:dup"
    ]


def test_accept_proposals_different_registries_same_name_do_not_collide(tmp_path: Path) -> None:
    # A facet and a value_type may share a name — different keyspaces, no collision.
    overlay = tmp_path / "registry-overlay"
    result = accept_proposals(
        [_vt("shared_name"), _facet("target:shared_name")], _ctx(overlay), approval="auto"
    )
    assert len(result.accepted) == 2
    assert result.skipped == []


# --- the order-preserving wrapper the CLI uses ------------------------------


def test_auto_accept_to_overlay_preserves_order_and_cap(tmp_path: Path) -> None:
    overlay = tmp_path / "registry-overlay"
    result = auto_accept_to_overlay(
        value_type_proposals=[_vt()],
        facet_proposals=[_facet()],
        thesis_type_proposals=[],
        ctx=_ctx(overlay),
        cap=5,
    )
    assert result.deferred == []
    assert len(result.accepted) == 2
    vts = load_overlay_file(overlay / "value_types.yaml", ValueTypeEntry)
    assert vts.proposals["k8s_sa_token"].approval == "auto"
    assert vts.entries[0].proposed_in_run == "run-xyz"


# --- the snapshot-invalidation seam -----------------------------------------


def test_reload_merged_registries_sees_a_just_accepted_facet(tmp_path: Path) -> None:
    # The stale-snapshot fix (seams §2, ADR 0099): after an overlay write the in-process snapshot is
    # stale; reload_merged_registries(overlay_dir) re-reads it so a second proposer sees the entry.
    overlay = tmp_path / "registry-overlay"
    before = load_merged_registries(overlay)
    assert before.facet("runtime:codebuild_project") is None
    accept_proposal(
        _facet("runtime:codebuild_project", category="runtime"),
        _ctx(overlay, proposed_by="planner", proposal_origin="llm_during_planning"),
        approval="auto",
    )
    after = reload_merged_registries(overlay)
    assert after.facet("runtime:codebuild_project") is not None
