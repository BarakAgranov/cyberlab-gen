"""Tests for in-flight proposal → overlay-entry conversion (Part A, ADR 0044).

The Extractor's ``Proposed*`` objects mirror the *content* of a registry entry; at
acceptance the framework converts them to the real entry (stamping
``proposed_by``) and writes them to the overlay. These tests pin that conversion.
"""

from __future__ import annotations

from cyberlab_gen.agents.proposals import ProposedFacet, ProposedValueType
from cyberlab_gen.schemas.registries import FacetEntry, ValueTypeEntry


def test_proposed_facet_to_entry() -> None:
    proposal = ProposedFacet(
        name="target:aws_codebuild",
        category="target",
        description="AWS CodeBuild build environment as the attack target.",
        applies_at_levels=["lab"],
        reasoning="the bundled registry rejected this facet, so I am proposing it",
    )
    entry = proposal.to_entry()
    assert isinstance(entry, FacetEntry)
    assert entry.name == "target:aws_codebuild"
    assert entry.category == "target"
    assert entry.proposed_by == "extractor"
    assert entry.applies_at_levels == ["lab"]
    assert entry.description == "AWS CodeBuild build environment as the attack target."


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
    entry = proposal.to_entry(proposed_in_run="run-123")
    assert isinstance(entry, ValueTypeEntry)
    assert entry.name == "k8s_sa_token"
    assert entry.schema_ == {"type": "string", "pattern": "^eyJ"}
    assert entry.sensitive is True
    assert entry.notes_for_generator == "Preserve the kid header."
    assert entry.platforms == ["kubernetes"]
    assert entry.proposed_by == "extractor"
    assert entry.proposed_in_run == "run-123"
