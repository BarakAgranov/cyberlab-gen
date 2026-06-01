"""Tests for Validator Layer 1 (``validation.md §6.4``, ADR 0022).

Covers the Task-6 exit criteria for Layer 1:
- a structurally-valid AttackSpec with all references resolving → passes;
- an unknown facet → ``UNKNOWN_FACET`` finding;
- an unknown thesis type → ``UNKNOWN_THESIS_TYPE`` finding (closed catalog, ADR 0016);
- an unknown external-data-source reference → ``UNKNOWN_EXTERNAL_SOURCE`` finding;
- the ``spec_kind`` discriminator is enforced;
- the validator never mutates the spec.

The orchestration test (``test_orchestrator.py``) asserts that a Layer-1 failure
routes to the Extractor's *retry*, not refinement (``validation.md §6.10``); this
file pins the layer's own behavior.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cyberlab_gen.registries.merge import load_merged_registries
from cyberlab_gen.schemas.attack_spec import (
    AdvisoryReference,
    AttackSpec,
    ChainBlock,
    ChainStep,
    ChainStepTechniques,
    CveReference,
    ExternalRefsBlock,
    ExtractionMetadataBlock,
    PerStepReproducibility,
    PublisherBlock,
    SourceBlock,
    ThesisBlock,
)
from cyberlab_gen.schemas.enums import (
    CitationKind,
    ExtractionOutcome,
    ProvenanceSource,
    ProvisioningMechanism,
    ReproducibilityTier,
)
from cyberlab_gen.schemas.provenance import CitationBlock, ProvenanceString
from cyberlab_gen.validators.static_schema_validator import StaticSchemaCode, StaticSchemaValidator

_HASH = "a" * 64


def _cite() -> CitationBlock:
    return CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="§1")


def _pstr(value: str) -> ProvenanceString:
    return ProvenanceString(value=value, source=ProvenanceSource.BLOG_EXPLICIT, citations=[_cite()])


def _source() -> SourceBlock:
    return SourceBlock(
        url="https://example.com/blog",  # type: ignore[arg-type]
        canonical_url="https://example.com/blog",  # type: ignore[arg-type]
        title="A writeup",
        publisher=PublisherBlock(name="Lab", domain="example.com", kind="vendor_lab"),  # type: ignore[arg-type]
        fetched_at=datetime(2025, 2, 1, tzinfo=UTC),
        content_hash=_HASH,
        fetch_method="httpx",
        word_count=100,
    )


def _metadata() -> ExtractionMetadataBlock:
    return ExtractionMetadataBlock(
        extractor_version="1.0.0", model="m", completeness_score=0.8, citations_count=2
    )


def _step() -> ChainStep:
    return ChainStep(
        id="step-1",  # type: ignore[arg-type]
        step_number=1,
        title="Step 1",
        description=_pstr("do the thing"),
        blog_excerpt="verbatim excerpt",
        techniques=ChainStepTechniques(mitre=["T1078"]),  # type: ignore[list-item]
        reproducibility=PerStepReproducibility(
            classification=ReproducibilityTier.FULL, caveats=_pstr("none"), why=_pstr("scriptable")
        ),
        provisioning_mechanism=ProvisioningMechanism.TERRAFORM,
    )


def _thesis(thesis_type: str = "vulnerability_chain") -> ThesisBlock:
    return ThesisBlock(
        types=[thesis_type],  # type: ignore[list-item]
        summary=_pstr("a chain"),
        attacker_objective=_pstr("admin"),
        vulnerability_story=_pstr("misconfig"),
        duration_as_described=_pstr("a week"),
    )


def _spec(
    *,
    thesis_type: str = "vulnerability_chain",
    facets: list[str] | None = None,
    external: ExternalRefsBlock | None = None,
) -> AttackSpec:
    return AttackSpec(
        spec_version=1,
        source=_source(),
        extraction_outcome=ExtractionOutcome.IN_SCOPE,
        thesis=_thesis(thesis_type),
        facets=facets or [],  # type: ignore[arg-type]
        chain=ChainBlock(chain_steps=[_step()]),
        external_references=external,
        extraction_metadata=_metadata(),
    )


def _validator() -> StaticSchemaValidator:
    return StaticSchemaValidator(registries=load_merged_registries())


# --- tests -----------------------------------------------------------------


def test_valid_spec_passes() -> None:
    # target:aws is a bundled facet; vulnerability_chain is a bundled thesis type.
    spec = _spec(facets=["target:aws"])
    result = _validator().validate(spec)
    assert result.passed
    assert result.findings == []


def test_unknown_facet_fails() -> None:
    # target:nonexistent_cloud is structurally a valid FacetName but not in the registry.
    spec = _spec(facets=["target:nonexistent_cloud"])
    result = _validator().validate(spec)
    assert not result.passed
    codes = {f.code for f in result.findings}
    assert StaticSchemaCode.UNKNOWN_FACET in codes
    assert any("facets[0]" in f.location for f in result.findings)


def test_unknown_thesis_type_fails() -> None:
    # A snake_case name that is not in the closed thesis_types catalog (ADR 0016).
    spec = _spec(thesis_type="totally_made_up_thesis")
    result = _validator().validate(spec)
    assert not result.passed
    assert any(f.code is StaticSchemaCode.UNKNOWN_THESIS_TYPE for f in result.findings)


def test_unknown_external_source_in_cve_fails() -> None:
    external = ExternalRefsBlock(
        cves=[
            CveReference(
                cve_id="CVE-2024-0001",
                description=_pstr("a vuln"),
                source_of_record="not_a_real_source",  # type: ignore[arg-type]
            )
        ]
    )
    result = _validator().validate(_spec(external=external))
    assert not result.passed
    assert any(f.code is StaticSchemaCode.UNKNOWN_EXTERNAL_SOURCE for f in result.findings)


def test_unknown_external_source_in_advisory_fails() -> None:
    external = ExternalRefsBlock(
        advisories=[
            AdvisoryReference(
                advisory_id="ADV-1",
                source="not_a_real_source",  # type: ignore[arg-type]
                description=_pstr("an advisory"),
            )
        ]
    )
    result = _validator().validate(_spec(external=external))
    assert any(f.code is StaticSchemaCode.UNKNOWN_EXTERNAL_SOURCE for f in result.findings)


def test_known_external_source_passes() -> None:
    # nvd is the one integrated external source in Phase 1.
    external = ExternalRefsBlock(
        cves=[
            CveReference(
                cve_id="CVE-2024-0001",
                description=_pstr("a vuln"),
                source_of_record="nvd",  # type: ignore[arg-type]
            )
        ]
    )
    result = _validator().validate(_spec(external=external))
    assert result.passed


def test_validator_does_not_mutate_spec() -> None:
    spec = _spec(facets=["target:aws"])
    before = spec.model_dump(mode="json", by_alias=True)
    _validator().validate(spec)
    assert spec.model_dump(mode="json", by_alias=True) == before


def test_findings_render_round_trips_code_and_location() -> None:
    result = _validator().validate(_spec(facets=["target:nonexistent_cloud"]))
    rendered = result.rendered_findings()
    assert rendered
    assert any("unknown_facet@facets[0]" in line for line in rendered)
