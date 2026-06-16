"""framework_enriched and material_discrepancies are framework-only-authored (schema.md
§4.9; enrichment is the sole writer). The framework neutralizes any LLM-authored values on
the Extractor output before it processes the spec (ADR 0082).

Without this, an LLM (a hallucination, or a prompt-injection from blog content) that
self-stamps framework_enriched bypasses two safety mechanisms: enrichment skips an
already-framework_enriched field as a no-op, and the grounding stack's search-before-claim
check EXEMPTS framework_enriched fields (grounding_validator.py:187-192). These tests fail
if the neutralization stops resetting either field.
"""

from cyberlab_gen.framework.provenance_guard import neutralize_framework_owned_provenance
from cyberlab_gen.schemas.attack_spec import (
    AttackSpec,
    CveReference,
    ExternalRefsBlock,
    MaterialDiscrepancy,
)
from cyberlab_gen.schemas.enums import CitationKind, ProvenanceSource
from cyberlab_gen.schemas.provenance import CitationBlock, ProvenanceFloat, ProvenanceString
from tests.unit.framework.pipeline_fakes import make_spec


def _api_string(value: str) -> ProvenanceString:
    return ProvenanceString(
        value=value,
        source=ProvenanceSource.BLOG_EXPLICIT,
        citations=[CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="§1")],
    )


def _poisoned_external_api_float(value: float) -> ProvenanceFloat:
    """An external_api provenance the LLM has illegitimately self-stamped framework_enriched.

    Only the framework's enrichment pass may set framework_enriched; this is structurally
    valid (passes Layer 1), which is exactly why a downstream mechanical reset is required.
    """
    return ProvenanceFloat(
        value=value,
        source=ProvenanceSource.EXTERNAL_API,
        citations=[CitationBlock(kind=CitationKind.EXTERNAL_API_RESPONSE, reference="NVD")],
        framework_enriched=True,
    )


def _spec_with_poisoned_cve() -> AttackSpec:
    return make_spec().model_copy(
        update={
            "external_references": ExternalRefsBlock(
                cves=[
                    CveReference(
                        cve_id="CVE-2025-0001",  # type: ignore[arg-type]
                        description=_api_string("an LLM-claimed CVE"),
                        cvss_score=_poisoned_external_api_float(9.8),
                    )
                ]
            )
        }
    )


def test_neutralize_resets_llm_authored_framework_enriched() -> None:
    spec = _spec_with_poisoned_cve()
    refs = spec.external_references
    assert refs is not None
    score = refs.cves[0].cvss_score
    assert score is not None
    assert score.framework_enriched is True  # poisoned
    cleaned = neutralize_framework_owned_provenance(spec)
    cleaned_refs = cleaned.external_references
    assert cleaned_refs is not None
    cleaned_score = cleaned_refs.cves[0].cvss_score
    assert cleaned_score is not None
    assert cleaned_score.framework_enriched is False
    # The source is preserved — only the framework-only flag is reset, so the field now faces
    # the grounding search-before-claim check instead of being exempt from it.
    assert cleaned_score.source is ProvenanceSource.EXTERNAL_API


def test_neutralize_clears_llm_authored_material_discrepancies() -> None:
    spec = make_spec().model_copy(
        update={
            "material_discrepancies": [
                MaterialDiscrepancy(
                    field_path="chain.chain_steps[0].description",
                    summary="an LLM-invented discrepancy",
                    blog_value="x",
                    authoritative_value="y",
                    source_of_record="nvd",  # type: ignore[arg-type]
                )
            ]
        }
    )
    assert len(spec.material_discrepancies) == 1  # poisoned
    cleaned = neutralize_framework_owned_provenance(spec)
    assert cleaned.material_discrepancies == []


def test_neutralize_is_a_noop_on_a_clean_spec() -> None:
    """A spec with no LLM-authored framework fields round-trips to an equal instance."""
    spec = make_spec()
    assert neutralize_framework_owned_provenance(spec) == spec
