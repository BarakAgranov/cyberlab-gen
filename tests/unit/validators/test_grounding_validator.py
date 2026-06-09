"""Tests for the orchestrator-owned grounding mechanical-validator stack (ADR 0060).

The grounding stack is the relocation of the Extractor's former internal
``_run_checks`` loop (search-before-claim / MITRE / CVE) and the jury's former
``verify_provenance`` (provenance-structure + the de-duplicated trace check) into
ONE orchestrator-owned validator producing ONE findings set (``validation.md
§6.10.2``, ADR 0051). These tests pin:

- the provenance-structure layer (per-source-kind well-formedness);
- the search-before-claim trace layer (the single, de-duplicated trace check);
- the MITRE pass-through (post-ADR-0058: a well-formed-but-uncatalogued id is
  UNVERIFIED, never a finding — no seed-membership hard-gate);
- which finding codes are retry-triggering (``needs_retry``) vs informational
  jury grounding.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cyberlab_gen.agents.extractor.tools import ExternalLookupRecord
from cyberlab_gen.schemas.attack_spec import (
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
    Severity,
)
from cyberlab_gen.schemas.provenance import CitationBlock, Provenance, ProvenanceString
from cyberlab_gen.validators.grounding_validator import (
    GroundingCode,
    GroundingValidator,
)

_HASH = "a" * 64


def _cite() -> CitationBlock:
    return CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="§1")


def _pstr(value: str) -> ProvenanceString:
    return ProvenanceString(value=value, source=ProvenanceSource.BLOG_EXPLICIT, citations=[_cite()])


def _spec(*, mitre: list[str] | None = None) -> AttackSpec:
    return AttackSpec(
        spec_version=1,
        source=SourceBlock(
            url="https://example.com/blog",  # type: ignore[arg-type]
            canonical_url="https://example.com/blog",  # type: ignore[arg-type]
            title="A writeup",
            publisher=PublisherBlock(name="Lab", domain="example.com", kind="vendor_lab"),  # type: ignore[arg-type]
            fetched_at=datetime(2025, 2, 1, tzinfo=UTC),
            content_hash=_HASH,
            fetch_method="httpx",
            word_count=100,
        ),
        extraction_outcome=ExtractionOutcome.IN_SCOPE,
        thesis=ThesisBlock(
            types=["vulnerability_chain"],  # type: ignore[list-item]
            summary=_pstr("a chain"),
            attacker_objective=_pstr("admin"),
            vulnerability_story=_pstr("misconfig"),
            duration_as_described=_pstr("a week"),
        ),
        chain=ChainBlock(
            chain_steps=[
                ChainStep(
                    id="step-1",  # type: ignore[arg-type]
                    step_number=1,
                    title="Step 1",
                    description=_pstr("do thing"),
                    blog_excerpt="excerpt",
                    techniques=ChainStepTechniques(mitre=mitre or ["T1078"]),  # type: ignore[arg-type]
                    reproducibility=PerStepReproducibility(
                        classification=ReproducibilityTier.FULL,
                        caveats=_pstr("none"),
                        why=_pstr("scriptable"),
                    ),
                    provisioning_mechanism=ProvisioningMechanism.TERRAFORM,
                )
            ]
        ),
        extraction_metadata=ExtractionMetadataBlock(
            extractor_version="1.0.0", model="m", completeness_score=0.8, citations_count=2
        ),
    )


def _api_cve_severity(*, with_api_citation: bool, framework_enriched: bool = False) -> AttackSpec:
    """A spec whose CVE severity claims ``source=external_api`` (optionally w/ api citation)."""
    spec = _spec()
    citations = [CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="§2")]
    if with_api_citation:
        citations.append(
            CitationBlock(kind=CitationKind.EXTERNAL_API_RESPONSE, reference="nvd:CVE-2024-0001")
        )
    spec.external_references = ExternalRefsBlock(
        cves=[
            CveReference(
                cve_id="CVE-2024-0001",  # type: ignore[arg-type]
                description=_pstr("v"),
                severity=Provenance[Severity](
                    value=Severity.HIGH,
                    source=ProvenanceSource.EXTERNAL_API,
                    citations=citations,
                    framework_enriched=framework_enriched,
                ),
            )
        ]
    )
    return spec


# --- provenance-structure layer --------------------------------------------


def test_clean_blog_explicit_spec_has_no_findings() -> None:
    assert GroundingValidator().validate(_spec(), lookups=[]).findings == []


def test_external_api_missing_api_citation_is_a_structure_finding() -> None:
    spec = _api_cve_severity(with_api_citation=False)
    result = GroundingValidator().validate(spec, lookups=[])
    structure = [f for f in result.findings if f.code is GroundingCode.PROVENANCE_STRUCTURE]
    assert any("external_api_response citation" in f.detail for f in structure)


# --- search-before-claim trace layer (the de-duplicated check) -------------


def test_external_api_without_matching_trace_is_search_before_claim() -> None:
    spec = _api_cve_severity(with_api_citation=True)
    result = GroundingValidator().validate(spec, lookups=[])
    assert any(f.code is GroundingCode.SEARCH_BEFORE_CLAIM for f in result.findings)
    # search-before-claim is a hallucination → it must trigger an orchestrator retry.
    assert result.needs_retry is True


def test_external_api_with_matching_trace_is_not_flagged() -> None:
    spec = _api_cve_severity(with_api_citation=True)
    trace = [
        ExternalLookupRecord(
            source_id="nvd", params={"cve_id": "CVE-2024-0001"}, found=True, detail="ok"
        )
    ]
    result = GroundingValidator().validate(spec, lookups=trace)
    assert not any(f.code is GroundingCode.SEARCH_BEFORE_CLAIM for f in result.findings)


def test_framework_enriched_external_api_is_exempt_from_search_before_claim() -> None:
    # The C1<->A3/B1 interlock (ADR 0052 / 0061): a framework_enriched external_api field is
    # the framework's own NVD call — the API-response citation IS the evidence, so it must be
    # EXEMPT from the agent-trace requirement (its call is not in the agent's lookup trace).
    spec = _api_cve_severity(with_api_citation=True, framework_enriched=True)
    result = GroundingValidator().validate(spec, lookups=[])  # no agent trace at all
    assert not any(f.code is GroundingCode.SEARCH_BEFORE_CLAIM for f in result.findings)
    assert result.needs_retry is False


def test_agent_claimed_external_api_still_held_to_search_before_claim() -> None:
    # The mirror of the exemption: an agent-claimed (framework_enriched=False) external_api
    # field with no trace is STILL flagged — the exemption is precise to framework calls.
    spec = _api_cve_severity(with_api_citation=True, framework_enriched=False)
    result = GroundingValidator().validate(spec, lookups=[])
    assert any(f.code is GroundingCode.SEARCH_BEFORE_CLAIM for f in result.findings)


# --- MITRE pass-through (post-ADR-0058: no seed-membership hard-gate) -------


def test_uncatalogued_mitre_technique_is_not_a_finding() -> None:
    # T1195/T1199 are real, current ATT&CK ids absent from the 8-entry bundled seed.
    # They must pass UNVERIFIED — never a finding (ADR 0055/0058 P2).
    spec = _spec(mitre=["T1195", "T1199"])
    result = GroundingValidator().validate(spec, lookups=[])
    assert result.findings == []
    assert result.needs_retry is False


# --- retry vs informational classification ---------------------------------


def test_structure_only_findings_do_not_trigger_retry() -> None:
    # A provenance-structure problem is informational jury grounding, not a retry trigger:
    # the external_api field lacks an api citation but DOES have a matching trace, so the
    # only finding is PROVENANCE_STRUCTURE — which must not by itself force a re-extract.
    spec = _api_cve_severity(with_api_citation=False)
    trace = [
        ExternalLookupRecord(
            source_id="nvd", params={"cve_id": "CVE-2024-0001"}, found=True, detail="ok"
        )
    ]
    result = GroundingValidator().validate(spec, lookups=trace)
    assert result.findings  # there IS a structure finding
    assert all(f.code is GroundingCode.PROVENANCE_STRUCTURE for f in result.findings)
    assert result.needs_retry is False


# --- CVE-hallucination layer (needs an NVD client) -------------------------


def test_blog_explicit_cve_unresolved_against_nvd_is_a_cve_hallucination() -> None:
    # A grounded (blog_explicit) CVE id NVD has no record of is a hallucination when an NVD
    # client is wired. Retry-triggering. (Relocated from the Extractor's former _check_cves.)
    class _NvdMiss:
        def lookup_cve(self, cve_id: str) -> None:
            del cve_id
            return None

    spec = _spec()
    spec.external_references = ExternalRefsBlock(
        cves=[CveReference(cve_id="CVE-2024-0002", description=_pstr("claimed real CVE"))]  # type: ignore[arg-type]
    )
    result = GroundingValidator(nvd_client=_NvdMiss()).validate(spec, lookups=[])
    assert any(f.code is GroundingCode.CVE_HALLUCINATION for f in result.findings)
    assert result.needs_retry is True


def test_no_nvd_client_skips_cve_check() -> None:
    # No NVD client wired (the Phase-1 default) → the CVE-hallucination check is skipped, not
    # failed (the honest "couldn't check" posture, architecture.md §1.6).
    spec = _spec()
    spec.external_references = ExternalRefsBlock(
        cves=[CveReference(cve_id="CVE-2024-0002", description=_pstr("claimed real CVE"))]  # type: ignore[arg-type]
    )
    result = GroundingValidator(nvd_client=None).validate(spec, lookups=[])
    assert not any(f.code is GroundingCode.CVE_HALLUCINATION for f in result.findings)


# --- provenance structure: the other source kinds --------------------------


def test_llm_inference_with_confidence_and_citation_is_clean() -> None:
    from cyberlab_gen.schemas.enums import ConfidenceSource

    spec = _spec()
    spec.thesis.summary = Provenance[str](  # type: ignore[union-attr]
        value="inferred summary",
        source=ProvenanceSource.LLM_INFERENCE,
        confidence=0.7,
        confidence_source=ConfidenceSource.MODEL_SELF_REPORTED,
        citations=[_cite()],
    )
    assert GroundingValidator().validate(spec, lookups=[]).findings == []


def test_unknown_from_blog_with_reason_is_clean() -> None:
    spec = _spec()
    spec.thesis.duration_as_described = Provenance[str](  # type: ignore[union-attr]
        value="", source=ProvenanceSource.UNKNOWN_FROM_BLOG, reason="requires external research"
    )
    assert GroundingValidator().validate(spec, lookups=[]).findings == []
