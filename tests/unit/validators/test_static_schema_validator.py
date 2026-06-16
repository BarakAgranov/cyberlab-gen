"""Tests for the static schema validator (``validation.md §6.4``, ADR 0022).

Covers the Task-6 exit criteria for static schema validation:
- a structurally-valid AttackSpec with all references resolving → passes;
- an unknown facet → ``UNKNOWN_FACET`` finding;
- an unknown thesis type → ``UNKNOWN_THESIS_TYPE`` finding (closed catalog, ADR 0016);
- external-source references (``advisory.source``, ``cve.source_of_record``) are NOT
  gate-checked against the tool registry (ADR 0055/0058 — they are a publisher label and a
  framework-authored verifier id, not vocabularies the spec must resolve into);
- the ``spec_kind`` discriminator is enforced;
- the validator never mutates the spec.

The orchestration test (``test_orchestrator.py``) asserts that a static-schema failure
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
from cyberlab_gen.schemas.catalogs import ProvisioningMechanismsCatalog
from cyberlab_gen.schemas.enums import (
    CitationKind,
    ExtractionOutcome,
    ProvenanceSource,
    ProvisioningMechanism,
    ReproducibilityTier,
    SpecKind,
)
from cyberlab_gen.schemas.provenance import CitationBlock, ProvenanceString
from cyberlab_gen.validators.static_schema_validator import (
    PendingProposals,
    StaticSchemaCode,
    StaticSchemaValidator,
)

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


def test_unknown_facet_with_pending_proposal_provisionally_passes() -> None:
    # The Wiz-run case: the facet isn't in the registry yet, but the Extractor
    # proposed it this run. Provisional resolution lets it pass so the proposal
    # survives to the acceptance point (ADR 0044), rather than halting static schema validation.
    spec = _spec(facets=["target:aws_codebuild"])
    pending = PendingProposals(facets=frozenset({"target:aws_codebuild"}))
    result = _validator().validate(spec, pending=pending)
    assert result.passed
    assert result.findings == []


def test_unknown_facet_without_matching_pending_still_fails() -> None:
    spec = _spec(facets=["target:aws_codebuild"])
    pending = PendingProposals(facets=frozenset({"target:some_other_facet"}))
    result = _validator().validate(spec, pending=pending)
    assert not result.passed
    assert any(f.code is StaticSchemaCode.UNKNOWN_FACET for f in result.findings)


def test_unknown_thesis_type_with_pending_proposal_provisionally_passes() -> None:
    spec = _spec(thesis_type="ci_cd_compromise")
    pending = PendingProposals(thesis_types=frozenset({"ci_cd_compromise"}))
    result = _validator().validate(spec, pending=pending)
    assert result.passed


def test_bogus_cve_source_of_record_is_not_checked_at_the_structural_gate() -> None:
    # ADR 0055/0058: cve.source_of_record is a verifying-tool id authored by enrichment
    # (post-gate, always a registered id by construction); the Extractor leaves it None. It
    # must NOT be validated against the tool registry at the pre-enrichment structural gate,
    # so a future Extractor-emitted value can't hard-fail the way advisory.source did.
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
    assert result.passed
    assert not any(f.code is StaticSchemaCode.UNKNOWN_EXTERNAL_SOURCE for f in result.findings)


def test_advisory_source_is_not_checked_at_the_structural_gate() -> None:
    # ADR 0055/0058: advisory.source is a publisher provenance label (e.g. 'aws'), not a
    # queryable tool id — it can never resolve in the ['nvd'] external_data_sources registry
    # and was the lone unconvergeable ship-blocker. It is no longer gate-checked.
    external = ExternalRefsBlock(
        advisories=[
            AdvisoryReference(
                advisory_id="ADV-1",
                source="aws",  # type: ignore[arg-type]
                description=_pstr("an advisory"),
            )
        ]
    )
    result = _validator().validate(_spec(external=external))
    assert result.passed
    assert not any(f.code is StaticSchemaCode.UNKNOWN_EXTERNAL_SOURCE for f in result.findings)


def test_advisory_aws_and_bogus_cve_source_both_ship() -> None:
    # Contract pin (ADR 0058): the two formerly-blocking external-source findings are gone — a
    # spec whose only external-source 'problems' are a publisher-label advisory.source and a
    # not-yet-enriched cve.source_of_record now passes the structural gate.
    external = ExternalRefsBlock(
        cves=[
            CveReference(
                cve_id="CVE-2024-0001",
                description=_pstr("a vuln"),
                source_of_record="not_a_real_source",  # type: ignore[arg-type]
            )
        ],
        advisories=[
            AdvisoryReference(
                advisory_id="ADV-1",
                source="aws",  # type: ignore[arg-type]
                description=_pstr("an advisory"),
            )
        ],
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


def test_catalog_drift_flags_enum_value_absent_from_its_catalog() -> None:
    """A provisioning_mechanism the enum admits but the bundled catalog omits → CATALOG_DRIFT.

    Guards against catalog/enum drift (validation.md §6.4). Fails if the membership check is
    removed. The catalogs are injectable, so a deliberately-drifted catalog (omitting TERRAFORM,
    the mechanism the fixture step declares) exercises the path without editing a bundled file.
    """
    validator = StaticSchemaValidator(
        registries=load_merged_registries(),
        provisioning_mechanisms=ProvisioningMechanismsCatalog(entries=[]),
    )
    result = validator.validate(_spec(facets=["target:aws"]))
    assert not result.passed
    drift = [f for f in result.findings if f.code is StaticSchemaCode.CATALOG_DRIFT]
    assert any("chain.chain_steps[0].provisioning_mechanism" in f.location for f in drift)


def test_schema_invalid_flags_a_smuggled_out_of_range_value() -> None:
    """A value that bypassed construction (a user edit / refinement re-run) and violates a field
    constraint is caught by the re-validation pass as SCHEMA_INVALID (validation.md §6.4)."""
    spec = _spec()
    # model_copy does not re-validate, so this smuggles a completeness_score above its le=1.0 bound.
    bad_meta = spec.extraction_metadata.model_copy(update={"completeness_score": 5.0})
    smuggled = spec.model_copy(update={"extraction_metadata": bad_meta})
    result = _validator().validate(smuggled)
    assert not result.passed
    assert any(f.code is StaticSchemaCode.SCHEMA_INVALID for f in result.findings)
    assert any("completeness_score" in f.location for f in result.findings)


def test_wrong_spec_kind_is_rejected() -> None:
    """A spec carrying the wrong spec_kind (smuggled past construction) does not pass the gate.

    The ``Literal[ATTACK_SPEC]`` discriminator makes the schema re-validation the actual enforcer;
    SPEC_KIND_MISMATCH is defense-in-depth behind it (it cannot be reached through validate(),
    because a wrong spec_kind fails the schema check first). Either way the spec is rejected.
    """
    smuggled = _spec().model_copy(update={"spec_kind": SpecKind.LAB_MANIFEST})
    result = _validator().validate(smuggled)
    assert not result.passed
    assert any("spec_kind" in f.location for f in result.findings)
