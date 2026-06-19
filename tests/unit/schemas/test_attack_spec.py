"""Tests for ``AttackSpec`` envelope, inner content blocks, and YAML round-trip.

Architectural source: ``schema-details.md`` §4 (the brief cites this as
"§5.1"; the doc itself numbers it §4). Phase 1 Task 1 replaced the Phase-0
``_Phase0InnerStub`` placeholders with the real inner blocks, so these tests
construct each block from its canonical shape, exercise its validators, confirm
it rejects unknown fields, and round-trip a full representative AttackSpec
through YAML to an equal instance.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from cyberlab_gen.schemas import (
    AttackSpec,
    ChainBlock,
    ChainStep,
    ChainStepTechniques,
    CitationBlock,
    CitationKind,
    DefenseApplicability,
    DefenseBlock,
    DetectionBlock,
    DetectionComponent,
    ExtractionMetadataBlock,
    ExtractionOutcome,
    ExtrasEntry,
    GapEntry,
    IncidentStatus,
    MaterialDiscrepancy,
    PerStepReproducibility,
    Provenance,
    ProvenanceSource,
    ProvenanceString,
    ProvisioningMechanism,
    PublisherBlock,
    RealWorldIncidentsBlock,
    ReproducibilityBlock,
    ReproducibilityLabLevel,
    ReproducibilityTier,
    Severity,
    SourceBlock,
    SpecKind,
    ThesisBlock,
)

# --- small typed-value helpers --------------------------------------------

_HASH = "a" * 64


def _cite() -> CitationBlock:
    return CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="§1, ¶1")


def _pstr(value: str) -> ProvenanceString:
    return ProvenanceString(
        value=value,
        source=ProvenanceSource.BLOG_EXPLICIT,
        citations=[_cite()],
    )


def _source() -> SourceBlock:
    return SourceBlock(
        url="https://example.com/blog",  # type: ignore[arg-type]
        canonical_url="https://example.com/blog",  # type: ignore[arg-type]
        title="A representative attack writeup",
        publisher=PublisherBlock(
            name="Example Labs",
            domain="example.com",
            kind="vendor_lab",  # type: ignore[arg-type]
        ),
        authors=["A. Researcher"],
        published_at=datetime(2025, 1, 1, tzinfo=UTC),
        fetched_at=datetime(2025, 2, 1, tzinfo=UTC),
        content_hash=_HASH,
        fetch_method="httpx",
        word_count=1234,
    )


def _metadata() -> ExtractionMetadataBlock:
    return ExtractionMetadataBlock(
        extractor_version="1.0.0",
        model="mock-model",
        completeness_score=0.8,
        unknown_fields=[],
        citations_count=3,
    )


def test_completeness_score_is_explicitly_non_authoritative() -> None:
    """``completeness_score`` is an LLM self-report, marked non-authoritative — it must never be
    mistaken for a framework-computed fact or a ship gate (ADR 0070). Pinned on the field's
    description so the marker can't be silently dropped.
    """
    desc = (ExtractionMetadataBlock.model_fields["completeness_score"].description or "").lower()
    assert "self-report" in desc
    assert "non-authoritative" in desc


def _per_step(tier: ReproducibilityTier = ReproducibilityTier.FULL) -> PerStepReproducibility:
    return PerStepReproducibility(
        classification=tier,
        caveats=_pstr("no caveats"),
        why=_pstr("fully scriptable"),
    )


def _step(num: int | str, step_id: str) -> ChainStep:
    return ChainStep(
        id=step_id,  # type: ignore[arg-type]
        step_number=num,
        title=f"Step {num}",
        description=_pstr(f"do thing {num}"),
        blog_excerpt=f"verbatim excerpt for step {num}",
        techniques=ChainStepTechniques(mitre=["T1059", "T1059.001"]),
        reproducibility=_per_step(),
        provisioning_mechanism=ProvisioningMechanism.TERRAFORM,
    )


def _chain(*steps: ChainStep) -> ChainBlock:
    return ChainBlock(chain_steps=list(steps))


def _thesis() -> ThesisBlock:
    return ThesisBlock(
        types=["vulnerability_chain"],  # type: ignore[list-item]
        summary=_pstr("a chained exploit"),
        attacker_objective=_pstr("gain admin"),
        vulnerability_story=_pstr("misconfig leads to escalation"),
        duration_as_described=_pstr("two weeks"),
    )


def _out_of_scope_spec() -> AttackSpec:
    return AttackSpec(
        spec_version=1,
        source=_source(),
        extraction_outcome=ExtractionOutcome.OUT_OF_SCOPE,
        extraction_outcome_reason="pure on-prem AD attack with no cloud or supply-chain surface",
        extraction_metadata=_metadata(),
    )


def _in_scope_spec() -> AttackSpec:
    return AttackSpec(
        spec_version=1,
        source=_source(),
        extraction_outcome=ExtractionOutcome.IN_SCOPE,
        thesis=_thesis(),
        chain=_chain(_step(1, "step-1"), _step(2, "step-2")),
        extraction_metadata=_metadata(),
    )


# --- SourceBlock / PublisherBlock -----------------------------------------


def test_source_block_constructs_and_round_trips() -> None:
    src = _source()
    assert SourceBlock.model_validate(src.model_dump()) == src


def test_source_block_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError, match="bogus"):
        SourceBlock.model_validate({**_source().model_dump(), "bogus": 1})


def test_publisher_block_kind_must_be_known() -> None:
    with pytest.raises(ValidationError):
        PublisherBlock(name="x", domain="x.com", kind="not_a_kind")  # type: ignore[arg-type]


# --- ThesisBlock -----------------------------------------------------------


def test_thesis_requires_at_least_one_type() -> None:
    with pytest.raises(ValidationError):
        ThesisBlock(
            types=[],
            summary=_pstr("s"),
            attacker_objective=_pstr("o"),
            vulnerability_story=_pstr("v"),
            duration_as_described=_pstr("d"),
        )


def test_thesis_round_trips() -> None:
    t = _thesis()
    assert ThesisBlock.model_validate(t.model_dump()) == t


def test_thesis_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError, match="bogus"):
        ThesisBlock.model_validate({**_thesis().model_dump(), "bogus": 1})


# --- ChainStep / ChainBlock ------------------------------------------------


def test_chain_step_round_trips() -> None:
    s = _step(1, "step-1")
    assert ChainStep.model_validate(s.model_dump()) == s


def test_chain_step_rejects_integer_step_number_below_one() -> None:
    with pytest.raises(ValidationError, match="integer step_number must be >= 1"):
        _step(0, "step-0")


def test_chain_step_rejects_malformed_string_step_number() -> None:
    with pytest.raises(ValidationError, match="string step_number must match"):
        _step("1", "step-1")  # bare integer-as-string is not N.N(.N)*


def test_chain_step_accepts_sub_numbered_step() -> None:
    s = _step("1.2", "step-1-2")
    assert s.step_number == "1.2"


def test_chain_step_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError, match="bogus"):
        ChainStep.model_validate({**_step(1, "step-1").model_dump(), "bogus": 1})


def test_chain_step_rejects_bad_mitre_id() -> None:
    with pytest.raises(ValidationError):
        ChainStepTechniques(mitre=["not-a-technique"])


def test_chain_block_enforces_monotonic_step_numbers() -> None:
    with pytest.raises(ValidationError, match="non-decreasing"):
        _chain(_step(2, "step-2"), _step(1, "step-1"))


def test_chain_block_allows_sub_numbered_after_parent() -> None:
    block = _chain(_step(2, "step-2"), _step("2.1", "step-2-1"))
    assert [s.step_number for s in block.chain_steps] == [2, "2.1"]


def test_chain_block_requires_at_least_one_step() -> None:
    with pytest.raises(ValidationError):
        ChainBlock(chain_steps=[])


# --- DetectionBlock --------------------------------------------------------


def test_detection_block_severity_is_provenance_wrapped() -> None:
    det = DetectionBlock(
        component=DetectionComponent.CDR,
        severity=Provenance[Severity](
            value=Severity.HIGH,
            source=ProvenanceSource.BLOG_EXPLICIT,
            citations=[_cite()],
        ),
        description=_pstr("alert fires"),
        soc_view=_pstr("triage"),
        remediation=_pstr("rotate"),
    )
    assert det.severity.value is Severity.HIGH
    assert DetectionBlock.model_validate(det.model_dump()) == det


# --- RealWorldIncidentsBlock (tri-state) -----------------------------------


def test_incidents_unknown_needs_no_evidence() -> None:
    block = RealWorldIncidentsBlock(status=IncidentStatus.UNKNOWN)
    assert block.evidence_source is None


def test_incidents_non_unknown_requires_evidence_source() -> None:
    with pytest.raises(ValidationError, match="evidence_source required"):
        RealWorldIncidentsBlock(status=IncidentStatus.NONE_OBSERVED)


def test_incidents_documented_requires_incidents() -> None:
    with pytest.raises(ValidationError, match="incidents required"):
        RealWorldIncidentsBlock(
            status=IncidentStatus.INCIDENTS_DOCUMENTED,
            evidence_source=_pstr("threat intel report"),
        )


def test_incidents_must_be_empty_unless_documented() -> None:
    from cyberlab_gen.schemas import RealWorldIncident

    incident = RealWorldIncident(
        incident_id="incident-1",
        name="Big Breach",
        description=_pstr("a breach"),
        date_range=_pstr("2024"),
    )
    with pytest.raises(ValidationError, match="incidents must be empty"):
        RealWorldIncidentsBlock(
            status=IncidentStatus.NONE_OBSERVED,
            evidence_source=_pstr("none observed"),
            incidents=[incident],
        )


# --- DefenseBlock pairing --------------------------------------------------


def test_defense_detection_path_and_format_paired() -> None:
    with pytest.raises(ValidationError, match="both set or both unset"):
        DefenseBlock(
            id="defense-1",
            description=_pstr("patch it"),
            applicability=DefenseApplicability.CUSTOMER_ACTIONABLE,
            detection_path="detection/d.yml",
        )


def test_defense_with_neither_detection_field_is_valid() -> None:
    d = DefenseBlock(
        id="defense-1",
        description=_pstr("patch it"),
        applicability=DefenseApplicability.CUSTOMER_ACTIONABLE,
    )
    assert DefenseBlock.model_validate(d.model_dump()) == d


# --- ReproducibilityBlock (derived) ----------------------------------------


def test_reproducibility_block_round_trips() -> None:
    block = ReproducibilityBlock(
        classification_lab_level=ReproducibilityLabLevel.MIXED,
        caveats=["one step is demonstration-only"],
        overall_assessment=_pstr("mostly reproducible"),
        derivation_trace=["step-1: full", "step-2: demonstration_only"],
    )
    assert ReproducibilityBlock.model_validate(block.model_dump()) == block


def test_reproducibility_block_overall_assessment_is_optional() -> None:
    # ADR 0088: the framework derives {classification, caveats, derivation_trace} and leaves
    # the prose overall_assessment None (no honest framework ProvenanceSource per §4.9).
    block = ReproducibilityBlock(
        classification_lab_level=ReproducibilityLabLevel.FULL,
        caveats=["all steps are full"],
        derivation_trace=["step-1: full", "lab-level classification: full"],
    )
    assert block.overall_assessment is None
    assert ReproducibilityBlock.from_yaml(block.to_yaml()) == block


# --- GapEntry / MaterialDiscrepancy ----------------------------------------


def test_gap_entry_round_trips() -> None:
    gap = GapEntry(
        field_path="chain.chain_steps[1].postconditions",
        reason="blog did not state the postcondition",
        severity=Severity.LOW,
    )
    assert GapEntry.model_validate(gap.model_dump()) == gap


def test_material_discrepancy_round_trips() -> None:
    md = MaterialDiscrepancy(
        field_path="external_references.cves[0].cvss_score",
        summary="blog said 7.5, NVD says 9.8",
        blog_value="7.5",
        authoritative_value="9.8",
        source_of_record="nvd",  # type: ignore[arg-type]
    )
    assert MaterialDiscrepancy.model_validate(md.model_dump()) == md


# --- OUT_OF_SCOPE invariants ----------------------------------------------


def test_out_of_scope_requires_reason() -> None:
    with pytest.raises(ValidationError, match="extraction_outcome_reason must be substantive"):
        AttackSpec(
            spec_version=1,
            source=_source(),
            extraction_outcome=ExtractionOutcome.OUT_OF_SCOPE,
            extraction_metadata=_metadata(),
        )


def test_out_of_scope_reason_must_be_at_least_30_chars() -> None:
    with pytest.raises(ValidationError, match="extraction_outcome_reason must be substantive"):
        AttackSpec(
            spec_version=1,
            source=_source(),
            extraction_outcome=ExtractionOutcome.OUT_OF_SCOPE,
            extraction_outcome_reason="too short",
            extraction_metadata=_metadata(),
        )


def test_out_of_scope_forbids_thesis() -> None:
    with pytest.raises(ValidationError, match="thesis must be None when out_of_scope"):
        AttackSpec(
            spec_version=1,
            source=_source(),
            extraction_outcome=ExtractionOutcome.OUT_OF_SCOPE,
            extraction_outcome_reason="out-of-scope blog with leftover thesis block",
            thesis=_thesis(),
            extraction_metadata=_metadata(),
        )


def test_out_of_scope_forbids_chain() -> None:
    with pytest.raises(ValidationError, match="chain must be None when out_of_scope"):
        AttackSpec(
            spec_version=1,
            source=_source(),
            extraction_outcome=ExtractionOutcome.OUT_OF_SCOPE,
            extraction_outcome_reason="out-of-scope blog with leftover chain block",
            chain=_chain(_step(1, "step-1")),
            extraction_metadata=_metadata(),
        )


def test_out_of_scope_forbids_real_world_incidents() -> None:
    with pytest.raises(ValidationError, match="real_world_incidents must be None"):
        AttackSpec(
            spec_version=1,
            source=_source(),
            extraction_outcome=ExtractionOutcome.OUT_OF_SCOPE,
            extraction_outcome_reason="out-of-scope blog with leftover incidents block",
            real_world_incidents=RealWorldIncidentsBlock(status=IncidentStatus.UNKNOWN),
            extraction_metadata=_metadata(),
        )


def test_out_of_scope_forbids_reproducibility() -> None:
    with pytest.raises(ValidationError, match="reproducibility must be None"):
        AttackSpec(
            spec_version=1,
            source=_source(),
            extraction_outcome=ExtractionOutcome.OUT_OF_SCOPE,
            extraction_outcome_reason="out-of-scope blog with leftover reproducibility block",
            reproducibility=ReproducibilityBlock(
                classification_lab_level=ReproducibilityLabLevel.FULL,
                overall_assessment=_pstr("x"),
            ),
            extraction_metadata=_metadata(),
        )


def test_out_of_scope_forbids_nonempty_defenses() -> None:
    with pytest.raises(ValidationError, match="defenses must be empty"):
        AttackSpec(
            spec_version=1,
            source=_source(),
            extraction_outcome=ExtractionOutcome.OUT_OF_SCOPE,
            extraction_outcome_reason="out-of-scope blog with leftover defense entry",
            defenses=[
                DefenseBlock(
                    id="defense-1",
                    description=_pstr("patch"),
                    applicability=DefenseApplicability.CUSTOMER_ACTIONABLE,
                )
            ],
            extraction_metadata=_metadata(),
        )


# --- IN_SCOPE invariants --------------------------------------------------


def test_in_scope_requires_chain() -> None:
    with pytest.raises(ValidationError, match="chain is required when in_scope"):
        AttackSpec(
            spec_version=1,
            source=_source(),
            extraction_outcome=ExtractionOutcome.IN_SCOPE,
            thesis=_thesis(),
            extraction_metadata=_metadata(),
        )


def test_in_scope_requires_thesis() -> None:
    with pytest.raises(ValidationError, match="thesis is required when in_scope"):
        AttackSpec(
            spec_version=1,
            source=_source(),
            extraction_outcome=ExtractionOutcome.IN_SCOPE,
            chain=_chain(_step(1, "step-1")),
            extraction_metadata=_metadata(),
        )


def test_in_scope_forbids_extraction_outcome_reason() -> None:
    with pytest.raises(ValidationError, match="extraction_outcome_reason must be None"):
        AttackSpec(
            spec_version=1,
            source=_source(),
            extraction_outcome=ExtractionOutcome.IN_SCOPE,
            thesis=_thesis(),
            chain=_chain(_step(1, "step-1")),
            extraction_outcome_reason="leftover reason from a prior out-of-scope run",
            extraction_metadata=_metadata(),
        )


# --- Happy paths + spec_kind ----------------------------------------------


def test_out_of_scope_minimal_round_trips_through_model_dump() -> None:
    spec = _out_of_scope_spec()
    assert AttackSpec.model_validate(spec.model_dump()) == spec


def test_in_scope_minimal_round_trips_through_model_dump() -> None:
    spec = _in_scope_spec()
    assert AttackSpec.model_validate(spec.model_dump()) == spec


def test_attack_spec_spec_kind_is_pinned() -> None:
    assert _out_of_scope_spec().spec_kind is SpecKind.ATTACK_SPEC


def test_attack_spec_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError, match="bogus"):
        AttackSpec.model_validate({**_in_scope_spec().model_dump(), "bogus": "nope"})


# --- Full representative AttackSpec, YAML round-trip -----------------------


def test_representative_attack_spec_yaml_round_trips_to_equal_instance() -> None:
    """The brief's headline exit criterion.

    A full representative IN_SCOPE AttackSpec (chain of three steps, thesis,
    detections, defenses, incidents, derived reproducibility, gaps, a material
    discrepancy, and provenance on every content field) round-trips Python ->
    YAML -> Python to an equal instance.
    """
    extras = ExtrasEntry(
        name="historical_context",
        description=_pstr("originally an internal writeup"),
        source=ProvenanceSource.BLOG_EXPLICIT,
        citations=[_cite()],
    )

    detection = DetectionBlock(
        component=DetectionComponent.CDR,
        severity=Provenance[Severity](
            value=Severity.HIGH,
            source=ProvenanceSource.BLOG_EXPLICIT,
            citations=[_cite()],
        ),
        description=_pstr("alert fires on token misuse"),
        soc_view=_pstr("p1 triage"),
        remediation=_pstr("revoke and rotate"),
    )
    step2 = _step(2, "step-2")
    step2 = step2.model_copy(update={"detections": [detection]})

    original = AttackSpec(
        spec_version=1,
        source=_source(),
        extraction_outcome=ExtractionOutcome.IN_SCOPE,
        facets=["target:aws"],  # type: ignore[list-item]
        thesis=_thesis(),
        real_world_incidents=RealWorldIncidentsBlock(status=IncidentStatus.UNKNOWN),
        chain=_chain(_step(1, "step-1"), step2, _step(3, "step-3")),
        defenses=[
            DefenseBlock(
                id="defense-1",
                description=_pstr("apply least privilege"),
                applicability=DefenseApplicability.CUSTOMER_ACTIONABLE,
            )
        ],
        reproducibility=ReproducibilityBlock(
            classification_lab_level=ReproducibilityLabLevel.FULL,
            overall_assessment=_pstr("fully reproducible in a sandbox"),
            derivation_trace=["all steps full"],
        ),
        gaps=[
            GapEntry(
                field_path="chain.chain_steps[2].postconditions",
                reason="blog did not state the postcondition",
                severity=Severity.LOW,
            )
        ],
        material_discrepancies=[
            MaterialDiscrepancy(
                field_path="external_references.cves[0].cvss_score",
                summary="blog said 7.5, NVD says 9.8",
                blog_value="7.5",
                authoritative_value="9.8",
                source_of_record="nvd",  # type: ignore[arg-type]
            )
        ],
        extraction_metadata=_metadata(),
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
    entry = ExtrasEntry(
        name="note",
        description=ProvenanceString(value="some text", source=ProvenanceSource.USER_PROVIDED),
        source=ProvenanceSource.USER_PROVIDED,
    )
    assert entry.name == "note"


def test_extras_entry_rejects_empty_name() -> None:
    with pytest.raises(ValidationError):
        ExtrasEntry(
            name="",  # type: ignore[arg-type]
            description=ProvenanceString(value="x", source=ProvenanceSource.USER_PROVIDED),
            source=ProvenanceSource.USER_PROVIDED,
        )


def test_advisory_reference_source_accepts_publisher_label_and_round_trips() -> None:
    # ADR 0077 / 0101: AdvisoryReference.source is a PublisherLabel (a publisher provenance
    # label, e.g. 'aws') retyped off ExternalDataSourceId so it is not misread as a tool id.
    # Structurally still a SnakeName: a publisher label round-trips, a non-snake value is rejected.
    from cyberlab_gen.schemas.attack_spec import AdvisoryReference

    adv = AdvisoryReference(
        advisory_id="ADV-1",
        source="aws",  # type: ignore[arg-type]
        description=_pstr("an advisory"),
    )
    assert adv.source == "aws"
    restored = AdvisoryReference.model_validate(adv.model_dump())
    assert restored == adv
    with pytest.raises(ValidationError):
        AdvisoryReference(
            advisory_id="ADV-2",
            source="Not A Snake",  # type: ignore[arg-type]
            description=_pstr("bad"),
        )
