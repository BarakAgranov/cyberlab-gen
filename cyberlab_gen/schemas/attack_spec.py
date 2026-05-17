"""AttackSpec envelope -- Phase 0 subset.

Architectural source: ``schema-details.md`` §4 (the brief's "§5.1" cite
points at the same content under a different number; flagged in the
phase-0 execution log). Top-level semantics: ``schema.md`` §4.8.

Phase 0 ships:

- The envelope scalars / enums in their final shape.
- ``ExtrasEntry`` -- the escape hatch surface (``schema.md`` §4.10), real
  rather than stubbed because it is the provenance-aware tail of the
  envelope, not an "inner content block" in the §4 sense.
- A single ``_Phase0InnerStub`` placeholder for every content block
  (chain, thesis, real_world_incidents, etc.) that ``schema-details.md``
  §4 will pin in Phase 1. Each stubbed field carries a ``# TODO(phase-1)``
  comment naming the section that fills it in.
- ``_scope_consistency`` -- the IN_SCOPE / OUT_OF_SCOPE invariants from
  §4 (the validator is real even though the inner blocks are stubs).
"""

from typing import Literal, Self

from pydantic import Field, model_validator

from cyberlab_gen.schemas.base import ArtifactModel, InternalModel
from cyberlab_gen.schemas.enums import (
    ExtractionOutcome,
    ProvenanceSource,
    SpecKind,
)
from cyberlab_gen.schemas.primitives import FacetName, NonEmptyString
from cyberlab_gen.schemas.provenance import CitationBlock, ProvenanceString


class _Phase0InnerStub(InternalModel):
    """Placeholder for an AttackSpec inner content block.

    Phase 1 replaces each ``_Phase0InnerStub``-typed field on ``AttackSpec``
    with its real Pydantic shape from ``schema-details.md`` §4. ``extra=
    "ignore"`` (inherited from ``InternalModel``) lets test fixtures and
    Phase 0 callers attach illustrative fields without committing to a
    Phase 1 shape; those extras are silently dropped on round-trip, which
    is the right behavior for a placeholder.
    """


class ExtrasEntry(ArtifactModel):
    """A single escape-hatch entry on an AttackSpec.

    Carries its own ``source`` and ``citations`` so unstructured content
    still travels with provenance metadata. ``schema.md`` §4.10.
    """

    name: NonEmptyString
    description: ProvenanceString
    source: ProvenanceSource
    citations: list[CitationBlock] = Field(default_factory=list[CitationBlock])


class AttackSpec(ArtifactModel):
    """The structured artifact produced by the Extractor.

    ``extraction_outcome`` is the top-level discriminator: IN_SCOPE specs
    must carry ``thesis`` and ``chain``; OUT_OF_SCOPE specs must carry a
    substantive ``extraction_outcome_reason`` and may not carry any of the
    content blocks (so refinement re-runs that flip scope can't leak
    stale planning data). ``schema.md`` §4.8.
    """

    spec_version: int = Field(ge=1)
    spec_kind: Literal[SpecKind.ATTACK_SPEC] = SpecKind.ATTACK_SPEC

    # TODO(phase-1: schema-details.md §4.1): real SourceBlock.
    source: _Phase0InnerStub

    extraction_outcome: ExtractionOutcome
    # Required when extraction_outcome == OUT_OF_SCOPE; must be None when
    # IN_SCOPE. Length floor (>=30) enforced in _scope_consistency.
    extraction_outcome_reason: NonEmptyString | None = None

    # TODO(phase-1: schema-details.md §4.2): real ThesisBlock.
    thesis: _Phase0InnerStub | None = None
    facets: list[FacetName] = Field(default_factory=list[FacetName])
    # TODO(phase-1: schema-details.md §4.3): real ExternalRefsBlock.
    external_references: _Phase0InnerStub | None = None
    # TODO(phase-1: schema-details.md §4.4): real RealWorldIncidentsBlock.
    real_world_incidents: _Phase0InnerStub | None = None
    # TODO(phase-1: schema-details.md §4.5): real ChainBlock.
    chain: _Phase0InnerStub | None = None
    # TODO(phase-1: schema-details.md §4.6): real DefenderTechniqueBlock.
    defender_techniques: list[_Phase0InnerStub] = Field(default_factory=list[_Phase0InnerStub])
    # TODO(phase-1: schema-details.md §4.6): real DefenseBlock.
    defenses: list[_Phase0InnerStub] = Field(default_factory=list[_Phase0InnerStub])
    # TODO(phase-1: schema-details.md §4.7): real ReproducibilityBlock.
    reproducibility: _Phase0InnerStub | None = None
    # TODO(phase-1: schema-details.md §4.8): real GapEntry.
    gaps: list[_Phase0InnerStub] = Field(default_factory=list[_Phase0InnerStub])
    # TODO(phase-1: schema-details.md §4.9): real ExtractionMetadataBlock.
    extraction_metadata: _Phase0InnerStub

    extras: list[ExtrasEntry] = Field(default_factory=list[ExtrasEntry])

    @model_validator(mode="after")
    def _scope_consistency(self) -> Self:
        if self.extraction_outcome is ExtractionOutcome.OUT_OF_SCOPE:
            if not self.extraction_outcome_reason or len(self.extraction_outcome_reason) < 30:
                raise ValueError(
                    "extraction_outcome_reason must be substantive (>=30 chars) "
                    "when extraction_outcome is out_of_scope"
                )
            # Negative invariants -- out_of_scope specs must not carry stale
            # planning data left over from a refinement re-run.
            if self.thesis is not None:
                raise ValueError("thesis must be None when out_of_scope")
            if self.chain is not None:
                raise ValueError("chain must be None when out_of_scope")
            if self.real_world_incidents is not None:
                raise ValueError("real_world_incidents must be None when out_of_scope")
            if self.reproducibility is not None:
                raise ValueError("reproducibility must be None when out_of_scope")
            if self.defender_techniques:
                raise ValueError("defender_techniques must be empty when out_of_scope")
            if self.defenses:
                raise ValueError("defenses must be empty when out_of_scope")
        else:  # IN_SCOPE
            if self.chain is None:
                raise ValueError("chain is required when in_scope")
            if self.thesis is None:
                raise ValueError("thesis is required when in_scope")
            if self.extraction_outcome_reason is not None:
                raise ValueError(
                    "extraction_outcome_reason must be None when in_scope "
                    "(the reason field describes why a blog was rejected)"
                )
        return self


__all__ = [
    "AttackSpec",
    "ExtrasEntry",
]
