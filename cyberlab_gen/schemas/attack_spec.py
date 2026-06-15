"""AttackSpec envelope and inner content blocks.

Architectural source: ``schema-details.md`` §4 (the brief's "§5.1" cite points
at the same content under a different number; flagged in the phase-0 execution
log). Top-level semantics: ``schema.md`` §4.8.

Phase 1 (Task 1) discharges the Phase-0 ``# TODO(phase-1)`` stubs: every inner
content block named in ``schema-details.md`` §4 now has its real Pydantic shape.
Per ADR 0004 every model here extends ``ArtifactModel`` (``extra="forbid"``)
rather than the bare ``BaseModel + ConfigDict`` the doc shows inline.

Provenance discipline (``schema.md`` §4.9): every *content* field is wrapped in
``Provenance[T]`` so its source/citations travel with the value. *Structural*
fields (ids, paths, type references, enums-as-discriminators) are bare.

Emergent lab class (``architecture.md`` §0.7): the per-step ``reproducibility``
tier is authored by the Extractor and carried unchanged downstream; there is no
upfront lab-class field. The lab-level ``ReproducibilityBlock`` is *derived*,
not authored.
"""

import re
from datetime import datetime
from typing import ClassVar, Literal, Self

from pydantic import Field, field_validator, model_validator

from cyberlab_gen.schemas.base import ArtifactModel
from cyberlab_gen.schemas.enums import (
    DefenderTechniqueKind,
    DefenseApplicability,
    DetectionComponent,
    DetectionFormat,
    ExtractionOutcome,
    IncidentStatus,
    ProvenanceSource,
    ProvisioningMechanism,
    PublisherKind,
    ReproducibilityLabLevel,
    ReproducibilityTier,
    Severity,
    SpecKind,
)
from cyberlab_gen.schemas.envelope import SpecEnvelope
from cyberlab_gen.schemas.primitives import (
    CveId,
    ExternalDataSourceId,
    FacetName,
    HttpUrl,
    KebabId,
    MitreTacticId,
    MitreTechniqueId,
    NonEmptyString,
    SemVer,
    Sha256Hex,
    ThesisType,
    TradecraftName,
)
from cyberlab_gen.schemas.provenance import (
    CitationBlock,
    Provenance,
    ProvenanceFloat,
    ProvenanceString,
)

# The technique/CVE id primitives now live in ``primitives.py`` (so the registry
# meta-schemas can reference ``MitreTechniqueId`` too); re-bound here for the
# blocks below and for callers that import them from this module.


# --- §4.1 SourceBlock ------------------------------------------------------


class PublisherBlock(ArtifactModel):
    """Publisher of the source blog. schema.md §4.8."""

    name: NonEmptyString
    domain: NonEmptyString
    kind: PublisherKind


class SourceBlock(ArtifactModel):
    """Provenance of the source blog. schema.md §4.8."""

    url: HttpUrl
    canonical_url: HttpUrl
    title: NonEmptyString
    publisher: PublisherBlock
    authors: list[NonEmptyString] = Field(default_factory=list[NonEmptyString])
    published_at: datetime | None = None  # None when blog has no date
    fetched_at: datetime
    content_hash: Sha256Hex
    fetch_method: str  # e.g., "httpx", "playwright"; informational
    word_count: int = Field(ge=0)


# --- §4.2 ThesisBlock ------------------------------------------------------


class ThesisBlock(ArtifactModel):
    """The attack's central thesis. schema.md §4.8.

    ``types`` is multi-valued and open-set (``ThesisType`` is registry-validated
    at static schema validation, ADR 0016).
    """

    types: list[ThesisType] = Field(min_length=1)
    summary: ProvenanceString
    attacker_objective: ProvenanceString
    vulnerability_story: ProvenanceString  # may be empty for TTP-chain blogs
    duration_as_described: ProvenanceString


# --- §4.4 DetectionBlock (used inside chain steps) -------------------------


class DetectionFormatEntry(ArtifactModel):
    """A single detection-content file reference. schema.md §4.7."""

    format: DetectionFormat
    # relative path inside the generated lab; e.g., "detection/phase_1/sigma.yml"
    path: NonEmptyString


class DetectionBlock(ArtifactModel):
    """A detection opportunity for a chain step. schema.md §4.7.

    ``severity`` is ``Provenance[Severity]`` (a content judgment), not a bare
    enum -- per the §7 cross-reference table.
    """

    component: DetectionComponent
    severity: Provenance[Severity]
    description: ProvenanceString
    soc_view: ProvenanceString
    remediation: ProvenanceString
    formats: list[DetectionFormatEntry] = Field(default_factory=list[DetectionFormatEntry])


# --- §4.3 ChainBlock / ChainStep -------------------------------------------


class TradecraftNote(ArtifactModel):
    """A tradecraft technique not captured by a MITRE id. schema.md §4.7."""

    # soft convention enforced by the extractor prompt; may be absent
    name: TradecraftName | None = None
    description: ProvenanceString
    evades_what: ProvenanceString | None = None


class ChainStepTechniques(ArtifactModel):
    """MITRE + tradecraft technique references for a chain step. schema.md §4.7."""

    mitre: list[MitreTechniqueId] = Field(default_factory=list[MitreTechniqueId])
    tradecraft: list[TradecraftNote] = Field(default_factory=list[TradecraftNote])


class PerStepReproducibility(ArtifactModel):
    """Per-step reproducibility tier (authored, carried unchanged). schema.md §4.8.

    Per ``architecture.md`` §0.7 this is the emergent-lab-class signal: the lab
    class is the sum of these per-step decisions, never a pre-classification.
    """

    classification: ReproducibilityTier
    caveats: ProvenanceString
    why: ProvenanceString


class ChainStep(ArtifactModel):
    """A single step in the attack chain. schema.md §4.8.

    ``blog_excerpt`` is the verbatim passage(s); it is fed downstream as the
    ``chain_step_excerpts`` the Planner and juries verify against.
    """

    id: KebabId
    step_number: int | str  # int or sub-numbered "1.1"; validated below
    title: NonEmptyString
    description: ProvenanceString
    blog_excerpt: NonEmptyString
    techniques: ChainStepTechniques
    preconditions: list[ProvenanceString] = Field(default_factory=list[ProvenanceString])
    postconditions: list[ProvenanceString] = Field(default_factory=list[ProvenanceString])
    detections: list[DetectionBlock] = Field(default_factory=list[DetectionBlock])
    reproducibility: PerStepReproducibility
    depends_on: list[KebabId] = Field(default_factory=list[KebabId])
    provisioning_mechanism: ProvisioningMechanism

    @field_validator("step_number")
    @classmethod
    def _validate_step_number(cls, v: int | str) -> int | str:
        if isinstance(v, int) and v < 1:
            raise ValueError("integer step_number must be >= 1")
        if isinstance(v, str) and not re.match(r"^\d+(\.\d+)+$", v):
            raise ValueError("string step_number must match N.N(.N)* (e.g., '1.2')")
        return v


def _step_number_key(sn: int | str) -> tuple[int, ...]:
    """Sort key for a step_number: numeric tuple on the dotted components."""
    if isinstance(sn, int):
        return (sn,)
    return tuple(int(part) for part in sn.split("."))


class AlternativePath(ArtifactModel):
    """An alternative attack path. schema.md §4.8. Captured in v1, generated v1.5+."""

    id: KebabId
    name: NonEmptyString
    description: ProvenanceString
    chain_steps: list[ChainStep] = Field(min_length=1)
    shares_steps_with_canonical: list[KebabId] = Field(default_factory=list[KebabId])
    reproducibility_summary: ReproducibilityTier


class ChainBlock(ArtifactModel):
    """The canonical attack chain plus any alternative paths. schema.md §4.8."""

    chain_steps: list[ChainStep] = Field(min_length=1)
    alternative_paths: list[AlternativePath] = Field(default_factory=list[AlternativePath])

    @model_validator(mode="after")
    def _step_numbers_monotonic(self) -> Self:
        """List-level invariant: chain_steps' step_numbers must be non-decreasing.

        The per-field validator on ``ChainStep.step_number`` checks syntax. This
        list-level validator catches out-of-order lists like [1, 3, 2, '2.1'].
        Sub-numbered steps (e.g., '2.1') sort under their parent via the
        numeric-tuple key, so '2.1' may immediately follow '2'.
        """
        keys = [_step_number_key(step.step_number) for step in self.chain_steps]
        for i in range(1, len(keys)):
            if keys[i] < keys[i - 1]:
                raise ValueError(
                    f"chain_steps step_number must be non-decreasing; "
                    f"step {i} ({self.chain_steps[i].step_number}) "
                    f"precedes step {i - 1} ({self.chain_steps[i - 1].step_number})"
                )
        return self


# --- §4.5 ExternalRefsBlock / RealWorldIncidentsBlock ----------------------


class CveReference(ArtifactModel):
    """A CVE reference. schema.md §4.8.

    ``source_of_record`` is set by the framework after pre-Planner enrichment
    (Task 4); the Extractor leaves it None.
    """

    cve_id: CveId
    description: ProvenanceString
    cvss_score: ProvenanceFloat | None = None
    severity: Provenance[Severity] | None = None
    source_of_record: ExternalDataSourceId | None = None


class RelatedBlogReference(ArtifactModel):
    """A related-blog cross-reference. schema.md §4.8."""

    url: HttpUrl
    title: ProvenanceString
    relationship: ProvenanceString


class AdvisoryReference(ArtifactModel):
    """A vendor/advisory reference. schema.md §4.8."""

    advisory_id: NonEmptyString
    source: ExternalDataSourceId
    url: HttpUrl | None = None
    description: ProvenanceString


class MitreTechniqueReference(ArtifactModel):
    """A standalone MITRE technique reference. schema.md §4.8."""

    technique_id: MitreTechniqueId
    name: ProvenanceString
    tactic_ids: list[MitreTacticId] = Field(default_factory=list[MitreTacticId])


class ExternalRefsBlock(ArtifactModel):
    """External references discovered for the attack. schema.md §4.8."""

    cves: list[CveReference] = Field(default_factory=list[CveReference])
    related_blogs: list[RelatedBlogReference] = Field(default_factory=list[RelatedBlogReference])
    advisories: list[AdvisoryReference] = Field(default_factory=list[AdvisoryReference])
    mitre_techniques: list[MitreTechniqueReference] = Field(
        default_factory=list[MitreTechniqueReference]
    )


class RealWorldIncident(ArtifactModel):
    """A single documented real-world incident. schema.md §4.8."""

    incident_id: KebabId
    name: NonEmptyString
    description: ProvenanceString
    affected_organizations: list[NonEmptyString] = Field(default_factory=list[NonEmptyString])
    # threat actor; None when unattributed
    attribution: NonEmptyString | None = None
    date_range: ProvenanceString


class RealWorldIncidentsBlock(ArtifactModel):
    """Tri-state record of real-world exploitation. schema.md §4.8."""

    status: IncidentStatus
    # required when status != UNKNOWN
    evidence_source: ProvenanceString | None = None
    incidents: list[RealWorldIncident] = Field(default_factory=list[RealWorldIncident])

    @model_validator(mode="after")
    def _status_rules(self) -> Self:
        if self.status is not IncidentStatus.UNKNOWN and self.evidence_source is None:
            raise ValueError("evidence_source required when status != unknown")
        if self.status is IncidentStatus.INCIDENTS_DOCUMENTED and not self.incidents:
            raise ValueError("incidents required when status == incidents_documented")
        if self.status is not IncidentStatus.INCIDENTS_DOCUMENTED and self.incidents:
            raise ValueError("incidents must be empty unless status == incidents_documented")
        return self


# --- §4.6 DefenderTechniqueBlock / DefenseBlock ----------------------------


class DefenderTechniqueBlock(ArtifactModel):
    """A defender-side technique observed in the blog. schema.md §4.8."""

    id: KebabId
    name: NonEmptyString
    description: ProvenanceString
    technique_kind: DefenderTechniqueKind
    applies_to_chain_steps: list[KebabId] = Field(default_factory=list[KebabId])


class DefenseBlock(ArtifactModel):
    """A defense / mitigation. schema.md §4.8."""

    id: KebabId
    description: ProvenanceString
    applicability: DefenseApplicability
    addresses_chain_steps: list[KebabId] = Field(default_factory=list[KebabId])
    detection_path: NonEmptyString | None = None
    detection_format: DetectionFormat | None = None

    @model_validator(mode="after")
    def _detection_paired(self) -> Self:
        if (self.detection_path is None) != (self.detection_format is None):
            raise ValueError("detection_path and detection_format must be both set or both unset")
        return self


# --- §4.7 ReproducibilityBlock (lab-level, derived) ------------------------


class ReproducibilityBlock(ArtifactModel):
    """Lab-level reproducibility. Derived from per-step values. schema.md §4.8.

    Per ``architecture.md`` §0.7 this block is *derived* by the framework from
    the per-step ``PerStepReproducibility`` tiers, never authored upfront.
    ``derivation_trace`` records how the lab-level classification was reached.
    """

    classification_lab_level: ReproducibilityLabLevel
    caveats: list[NonEmptyString] = Field(default_factory=list[NonEmptyString])
    overall_assessment: ProvenanceString
    derivation_trace: list[NonEmptyString] = Field(default_factory=list[NonEmptyString])


# --- §4.8 GapEntry / ExtractionMetadataBlock / ExtrasEntry -----------------


class GapEntry(ArtifactModel):
    """A top-level gap the Extractor could not fill. pipeline.md §3.2.2."""

    # JSONPath-like; e.g., "chain.chain_steps[2].postconditions"
    field_path: NonEmptyString
    reason: NonEmptyString
    severity: Severity


class MaterialDiscrepancy(ArtifactModel):
    """A material blog-vs-authoritative discrepancy. ADR 0017.

    Populated by pre-Planner enrichment (Task 4), never by an agent
    (``schema.md`` §4.9 framework-only authorship). This is a top-level *index*
    into the spec; the authoritative record lives in the target field's own
    ``Provenance`` (``discrepancy_with_blog`` / ``overridden_blog_value`` /
    ``discrepancy_classification``). Declared now, per the Task 1 brief.
    """

    # JSONPath-like locator, same convention as GapEntry.field_path
    field_path: NonEmptyString
    summary: NonEmptyString
    blog_value: NonEmptyString
    authoritative_value: NonEmptyString
    source_of_record: ExternalDataSourceId


class ExtractionMetadataBlock(ArtifactModel):
    """Run metadata for the extraction (``schema.md §4.8``).

    Authorship is mixed and must not be confused (``architecture.md §1.5``):

    - ``model`` is **framework-stamped** from the billed cost ledger (ADR 0065) — never the LLM's
      self-report.
    - ``completeness_score``, ``unknown_fields``, ``citations_count``, ``notes_for_planner`` are
      **LLM self-reports**: the model's own assessment of its run, *not* framework-computed facts and
      *not* ship gates (ADR 0070). The substantive completeness gate is the Extractor-Jury's
      0.7-floored ``completeness`` rubric dimension (``agents.md §5.5``), never this number.
    """

    extractor_version: SemVer
    model: NonEmptyString  # framework-stamped from the billed ledger (ADR 0065); not LLM-authored
    completeness_score: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "LLM self-report (non-authoritative): the Extractor's own completeness estimate — not a "
            "framework-computed fact and not a ship gate (ADR 0070). The eval harness records it "
            "alongside its independently-computed structural_completeness; the framework never "
            "stamps, gates, or consumes it."
        ),
    )
    unknown_fields: list[NonEmptyString] = Field(default_factory=list[NonEmptyString])
    citations_count: int = Field(ge=0)
    notes_for_planner: NonEmptyString | None = None


class ExtrasEntry(ArtifactModel):
    """A single escape-hatch entry on an AttackSpec.

    Carries its own ``source`` and ``citations`` so unstructured content still
    travels with provenance metadata. ``schema.md`` §4.10.
    """

    name: NonEmptyString
    description: ProvenanceString
    source: ProvenanceSource
    citations: list[CitationBlock] = Field(default_factory=list[CitationBlock])


# --- The envelope ----------------------------------------------------------

#: The schema version the framework writes for every AttackSpec it produces — **per-kind**, distinct
#: from ``CURRENT_MANIFEST_VERSION`` (ADR 0080 amends ADR 0069's single constant, because the two
#: artifacts evolve independently). The framework *stamps* this value (the LLM never authors it —
#: ``architecture.md §1.5``); on load, a spec whose ``spec_version`` differs is **refused, never
#: migrated** (``architecture.md §0.6``). Bump only with a coordinated load-gate update.
CURRENT_ATTACK_SPEC_VERSION = 1


class AttackSpec(SpecEnvelope):
    """The structured artifact produced by the Extractor.

    ``extraction_outcome`` is the top-level discriminator: IN_SCOPE specs must
    carry ``thesis`` and ``chain``; OUT_OF_SCOPE specs must carry a substantive
    ``extraction_outcome_reason`` and may not carry any of the content blocks
    (so refinement re-runs that flip scope can't leak stale planning data).
    ``schema.md`` §4.8.

    ``spec_version`` is inherited from ``SpecEnvelope`` and framework-stamped to
    ``CURRENT_ATTACK_SPEC_VERSION`` at the ship/persist seam (ADR 0069/0080), the same
    discipline as the model-provenance family (ADR 0065); the LLM never authors it.
    The floor (``ge=1``) keeps a hand-built spec from being version 0; the equality
    gate lives in the load path. ``source`` stays top-level here (ADR 0080).
    """

    CURRENT_VERSION: ClassVar[int] = CURRENT_ATTACK_SPEC_VERSION

    spec_kind: Literal[SpecKind.ATTACK_SPEC] = SpecKind.ATTACK_SPEC

    source: SourceBlock

    extraction_outcome: ExtractionOutcome
    # Required when extraction_outcome == OUT_OF_SCOPE; must be None when
    # IN_SCOPE. Length floor (>=30) enforced in _scope_consistency.
    extraction_outcome_reason: NonEmptyString | None = None

    thesis: ThesisBlock | None = None
    facets: list[FacetName] = Field(default_factory=list[FacetName])
    external_references: ExternalRefsBlock | None = None
    real_world_incidents: RealWorldIncidentsBlock | None = None
    chain: ChainBlock | None = None
    defender_techniques: list[DefenderTechniqueBlock] = Field(
        default_factory=list[DefenderTechniqueBlock]
    )
    defenses: list[DefenseBlock] = Field(default_factory=list[DefenseBlock])
    reproducibility: ReproducibilityBlock | None = None
    gaps: list[GapEntry] = Field(default_factory=list[GapEntry])
    # Populated by pre-Planner enrichment (Task 4); declared now per ADR 0017.
    material_discrepancies: list[MaterialDiscrepancy] = Field(
        default_factory=list[MaterialDiscrepancy]
    )
    extraction_metadata: ExtractionMetadataBlock

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
    "CURRENT_ATTACK_SPEC_VERSION",
    "AdvisoryReference",
    "AlternativePath",
    "AttackSpec",
    "ChainBlock",
    "ChainStep",
    "ChainStepTechniques",
    "CveReference",
    "DefenderTechniqueBlock",
    "DefenseBlock",
    "DetectionBlock",
    "DetectionFormatEntry",
    "ExternalRefsBlock",
    "ExtractionMetadataBlock",
    "ExtrasEntry",
    "GapEntry",
    "MaterialDiscrepancy",
    "MitreTechniqueReference",
    "PerStepReproducibility",
    "PublisherBlock",
    "RealWorldIncident",
    "RealWorldIncidentsBlock",
    "RelatedBlogReference",
    "ReproducibilityBlock",
    "SourceBlock",
    "ThesisBlock",
    "TradecraftNote",
]
