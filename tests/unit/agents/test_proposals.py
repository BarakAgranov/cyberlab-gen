"""Tests for in-flight proposal → overlay-entry conversion (Part A, ADR 0044).

The Extractor's ``Proposed*`` objects mirror the *content* of a registry entry; at
acceptance the framework converts them to the real entry (stamping
``proposed_by``) and writes them to the overlay. These tests pin that conversion.
"""

from __future__ import annotations

from cyberlab_gen.agents.proposals import (
    EXTRACTOR_FACET_CATEGORIES,
    PLANNER_FACET_CATEGORIES,
    ProposedFacet,
    ProposedThesisType,
    ProposedValueType,
)
from cyberlab_gen.schemas.registries import FacetEntry, ThesisTypeEntry, ValueTypeEntry


def test_proposed_facet_to_entry() -> None:
    proposal = ProposedFacet(
        name="target:aws_codebuild",
        category="target",
        description="AWS CodeBuild build environment as the attack target.",
        applies_at_levels=["lab"],
        reasoning="the bundled registry rejected this facet, so I am proposing it",
    )
    # proposed_by is stamped by the framework at the accept boundary (Task 7), not hardcoded.
    entry = proposal.to_entry(proposed_by="extractor")
    assert isinstance(entry, FacetEntry)
    assert entry.name == "target:aws_codebuild"
    assert entry.category == "target"
    assert entry.proposed_by == "extractor"
    assert entry.applies_at_levels == ["lab"]
    assert entry.description == "AWS CodeBuild build environment as the attack target."


def test_proposed_facet_admits_runtime_category_and_stamps_planner() -> None:
    # Task 7: the Planner proposes runtime:* facets — the in-flight model must admit `runtime`
    # (it was Literal['target','lab_class_signal'] before), and the framework stamps proposed_by.
    proposal = ProposedFacet(
        name="runtime:codebuild_project",
        category="runtime",
        description="A CodeBuild project provisioned for the lab runtime.",
        applies_at_levels=["lab"],
        reasoning="the planned lab needs a runtime facet not in the bundled registry",
    )
    entry = proposal.to_entry(proposed_by="planner")
    assert entry.category == "runtime"
    assert entry.proposed_by == "planner"


def test_facet_proposal_authorities_are_disjoint_per_agent() -> None:
    # Authority is a per-agent input, not a hardcoded literal (Task 7 no-discretion clause).
    assert set(EXTRACTOR_FACET_CATEGORIES) == {"target", "lab_class_signal"}
    assert set(PLANNER_FACET_CATEGORIES) == {"runtime", "lab_class_signal"}


def test_proposed_value_type_to_entry() -> None:
    proposal = ProposedValueType(
        name="k8s_sa_token",
        description="Kubernetes service account JWT token.",
        value_schema={"type": "string", "pattern": "^eyJ"},
        sensitive=True,
        notes_for_generator="Preserve the kid header.",
        platforms=["kubernetes"],
        reasoning="Blog harvests JWT tokens from /var/run/secrets.",
    )
    entry = proposal.to_entry(proposed_by="extractor", proposed_in_run="run-123")
    assert isinstance(entry, ValueTypeEntry)
    assert entry.name == "k8s_sa_token"
    assert entry.schema_ == {"type": "string", "pattern": "^eyJ"}
    assert entry.sensitive is True
    assert entry.notes_for_generator == "Preserve the kid header."
    assert entry.platforms == ["kubernetes"]
    assert entry.proposed_by == "extractor"
    assert entry.proposed_in_run == "run-123"


def test_proposed_thesis_type_to_entry() -> None:
    proposal = ProposedThesisType(
        name="ci_cd_compromise",
        description="Compromise of a CI/CD build pipeline.",
        reasoning="the bundled registry has no thesis type for CI/CD compromise",
    )
    entry = proposal.to_entry(proposed_by="extractor", proposed_in_run="run-456")
    assert isinstance(entry, ThesisTypeEntry)
    assert entry.name == "ci_cd_compromise"
    assert entry.proposed_by == "extractor"
    assert entry.proposed_in_run == "run-456"
