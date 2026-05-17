"""Tests for ``AttackSpec`` envelope, ``ExtrasEntry``, and YAML round-trip.

Architectural source: ``schema-details.md`` §4 (the brief cites this as
"§5.1"; the doc itself numbers it §4).
"""

import pytest
from pydantic import ValidationError

from cyberlab_gen.schemas import (
    AttackSpec,
    CitationBlock,
    CitationKind,
    ExtractionOutcome,
    ExtrasEntry,
    ProvenanceSource,
    ProvenanceString,
)

# _Phase0InnerStub is module-private but tests need to construct the
# placeholder type that stands in for inner content blocks.
from cyberlab_gen.schemas.attack_spec import _Phase0InnerStub  # pyright: ignore[reportPrivateUsage]


def _stub() -> _Phase0InnerStub:
    return _Phase0InnerStub()


def _out_of_scope_spec(reason: str | None = None) -> AttackSpec:
    """Minimal OUT_OF_SCOPE AttackSpec for fixture reuse."""
    return AttackSpec(
        spec_version=1,
        source=_stub(),
        extraction_outcome=ExtractionOutcome.OUT_OF_SCOPE,
        extraction_outcome_reason=reason
        or "pure on-prem AD attack with no cloud or supply-chain surface",
        extraction_metadata=_stub(),
    )


def _in_scope_spec() -> AttackSpec:
    """Minimal IN_SCOPE AttackSpec for fixture reuse."""
    return AttackSpec(
        spec_version=1,
        source=_stub(),
        extraction_outcome=ExtractionOutcome.IN_SCOPE,
        thesis=_stub(),
        chain=_stub(),
        extraction_metadata=_stub(),
    )


# --- OUT_OF_SCOPE invariants ----------------------------------------------


def test_out_of_scope_requires_reason() -> None:
    with pytest.raises(ValidationError, match="extraction_outcome_reason must be substantive"):
        AttackSpec(
            spec_version=1,
            source=_stub(),
            extraction_outcome=ExtractionOutcome.OUT_OF_SCOPE,
            extraction_metadata=_stub(),
        )


def test_out_of_scope_reason_must_be_at_least_30_chars() -> None:
    with pytest.raises(ValidationError, match="extraction_outcome_reason must be substantive"):
        AttackSpec(
            spec_version=1,
            source=_stub(),
            extraction_outcome=ExtractionOutcome.OUT_OF_SCOPE,
            extraction_outcome_reason="too short",
            extraction_metadata=_stub(),
        )


def test_out_of_scope_forbids_thesis() -> None:
    with pytest.raises(ValidationError, match="thesis must be None when out_of_scope"):
        AttackSpec(
            spec_version=1,
            source=_stub(),
            extraction_outcome=ExtractionOutcome.OUT_OF_SCOPE,
            extraction_outcome_reason="out-of-scope blog with leftover thesis stub",
            thesis=_stub(),
            extraction_metadata=_stub(),
        )


def test_out_of_scope_forbids_chain() -> None:
    with pytest.raises(ValidationError, match="chain must be None when out_of_scope"):
        AttackSpec(
            spec_version=1,
            source=_stub(),
            extraction_outcome=ExtractionOutcome.OUT_OF_SCOPE,
            extraction_outcome_reason="out-of-scope blog with leftover chain stub",
            chain=_stub(),
            extraction_metadata=_stub(),
        )


def test_out_of_scope_forbids_real_world_incidents() -> None:
    with pytest.raises(ValidationError, match="real_world_incidents must be None"):
        AttackSpec(
            spec_version=1,
            source=_stub(),
            extraction_outcome=ExtractionOutcome.OUT_OF_SCOPE,
            extraction_outcome_reason="out-of-scope blog with leftover incidents block",
            real_world_incidents=_stub(),
            extraction_metadata=_stub(),
        )


def test_out_of_scope_forbids_reproducibility() -> None:
    with pytest.raises(ValidationError, match="reproducibility must be None"):
        AttackSpec(
            spec_version=1,
            source=_stub(),
            extraction_outcome=ExtractionOutcome.OUT_OF_SCOPE,
            extraction_outcome_reason="out-of-scope blog with leftover reproducibility stub",
            reproducibility=_stub(),
            extraction_metadata=_stub(),
        )


def test_out_of_scope_forbids_nonempty_defender_techniques() -> None:
    with pytest.raises(ValidationError, match="defender_techniques must be empty"):
        AttackSpec(
            spec_version=1,
            source=_stub(),
            extraction_outcome=ExtractionOutcome.OUT_OF_SCOPE,
            extraction_outcome_reason="out-of-scope blog with leftover defender techniques",
            defender_techniques=[_stub()],
            extraction_metadata=_stub(),
        )


def test_out_of_scope_forbids_nonempty_defenses() -> None:
    with pytest.raises(ValidationError, match="defenses must be empty"):
        AttackSpec(
            spec_version=1,
            source=_stub(),
            extraction_outcome=ExtractionOutcome.OUT_OF_SCOPE,
            extraction_outcome_reason="out-of-scope blog with leftover defense entry",
            defenses=[_stub()],
            extraction_metadata=_stub(),
        )


# --- IN_SCOPE invariants --------------------------------------------------


def test_in_scope_requires_chain() -> None:
    with pytest.raises(ValidationError, match="chain is required when in_scope"):
        AttackSpec(
            spec_version=1,
            source=_stub(),
            extraction_outcome=ExtractionOutcome.IN_SCOPE,
            thesis=_stub(),
            extraction_metadata=_stub(),
        )


def test_in_scope_requires_thesis() -> None:
    with pytest.raises(ValidationError, match="thesis is required when in_scope"):
        AttackSpec(
            spec_version=1,
            source=_stub(),
            extraction_outcome=ExtractionOutcome.IN_SCOPE,
            chain=_stub(),
            extraction_metadata=_stub(),
        )


def test_in_scope_forbids_extraction_outcome_reason() -> None:
    with pytest.raises(ValidationError, match="extraction_outcome_reason must be None"):
        AttackSpec(
            spec_version=1,
            source=_stub(),
            extraction_outcome=ExtractionOutcome.IN_SCOPE,
            thesis=_stub(),
            chain=_stub(),
            extraction_outcome_reason="leftover reason from a prior out-of-scope run",
            extraction_metadata=_stub(),
        )


# --- Happy paths ----------------------------------------------------------


def test_out_of_scope_minimal_round_trips_through_model_dump() -> None:
    spec = _out_of_scope_spec()
    restored = AttackSpec.model_validate(spec.model_dump())
    assert restored == spec


def test_in_scope_minimal_round_trips_through_model_dump() -> None:
    spec = _in_scope_spec()
    restored = AttackSpec.model_validate(spec.model_dump())
    assert restored == spec


def test_attack_spec_spec_kind_is_pinned() -> None:
    spec = _out_of_scope_spec()
    from cyberlab_gen.schemas import SpecKind

    assert spec.spec_kind is SpecKind.ATTACK_SPEC


def test_attack_spec_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError, match="bogus"):
        AttackSpec.model_validate(
            {
                "spec_version": 1,
                "spec_kind": "AttackSpec",
                "source": {},
                "extraction_outcome": "out_of_scope",
                "extraction_outcome_reason": "out-of-scope reason that is sufficiently long",
                "extraction_metadata": {},
                "bogus": "nope",
            }
        )


# --- YAML round-trip (the brief's headline exit criterion) ----------------


def test_attack_spec_yaml_round_trip_representative() -> None:
    """A representative IN_SCOPE AttackSpec round-trips through YAML.

    Carries a facet, an ExtrasEntry with a Provenance-wrapped description,
    and all stubbed inner blocks. ``to_yaml -> from_yaml`` returns an
    equal instance per ``schema-details.md`` §1.
    """
    extras_description = ProvenanceString(
        value="The blog originally appeared as an internal-only writeup.",
        source=ProvenanceSource.BLOG_EXPLICIT,
        citations=[CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="footnote 1")],
    )
    extras = ExtrasEntry(
        name="historical_context",
        description=extras_description,
        source=ProvenanceSource.BLOG_EXPLICIT,
        citations=[CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="footnote 1")],
    )

    original = AttackSpec(
        spec_version=1,
        source=_stub(),
        extraction_outcome=ExtractionOutcome.IN_SCOPE,
        facets=["target:aws"],
        thesis=_stub(),
        chain=_stub(),
        extraction_metadata=_stub(),
        extras=[extras],
    )

    serialized = original.to_yaml()
    assert "spec_kind: AttackSpec" in serialized
    assert "extraction_outcome: in_scope" in serialized
    assert "- target:aws" in serialized
    assert "historical_context" in serialized

    restored = AttackSpec.from_yaml(serialized)
    assert restored == original


# --- ExtrasEntry ----------------------------------------------------------


def test_extras_entry_constructs() -> None:
    description = ProvenanceString(
        value="some text",
        source=ProvenanceSource.USER_PROVIDED,
    )
    entry = ExtrasEntry(name="note", description=description, source=ProvenanceSource.USER_PROVIDED)
    assert entry.name == "note"


def test_extras_entry_rejects_empty_name() -> None:
    description = ProvenanceString(
        value="some text",
        source=ProvenanceSource.USER_PROVIDED,
    )
    with pytest.raises(ValidationError):
        ExtrasEntry(
            name="",  # type: ignore[arg-type]
            description=description,
            source=ProvenanceSource.USER_PROVIDED,
        )
