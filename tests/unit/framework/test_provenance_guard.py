"""framework_enriched and material_discrepancies are framework-only-authored (schema.md
§4.9; enrichment is the sole writer). The framework neutralizes any LLM-authored values on
the Extractor output before it processes the spec (ADR 0082).

Without this, an LLM (a hallucination, or a prompt-injection from blog content) that
self-stamps framework_enriched bypasses two safety mechanisms: enrichment skips an
already-framework_enriched field as a no-op, and the grounding stack's search-before-claim
check EXEMPTS framework_enriched fields (grounding_validator.py:187-192). These tests fail
if the neutralization stops resetting either field.
"""

import importlib

from cyberlab_gen.framework.provenance_guard import neutralize_framework_owned_provenance
from cyberlab_gen.schemas.attack_spec import (
    AttackSpec,
    CveReference,
    ExternalRefsBlock,
    MaterialDiscrepancy,
    ReproducibilityBlock,
)
from cyberlab_gen.schemas.base import ArtifactModel
from cyberlab_gen.schemas.enums import CitationKind, ProvenanceSource, ReproducibilityLabLevel
from cyberlab_gen.schemas.provenance import (
    CitationBlock,
    Provenance,
    ProvenanceFloat,
    ProvenanceString,
)
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


def test_neutralize_resets_the_full_discrepancy_record() -> None:
    # The whole API-override discrepancy record is framework-owned (ADR 0087). A poisoned
    # Provenance carrying all four owned fields must come back fully reset — and consistently
    # (discrepancy_with_blog=False with both companions None), so re-validation's coupling holds.
    poisoned = ProvenanceFloat(
        value=9.8,
        source=ProvenanceSource.EXTERNAL_API,
        citations=[CitationBlock(kind=CitationKind.EXTERNAL_API_RESPONSE, reference="NVD")],
        framework_enriched=True,
        discrepancy_with_blog=True,
        overridden_blog_value=5.0,
        discrepancy_classification="material",
    )
    spec = make_spec().model_copy(
        update={
            "external_references": ExternalRefsBlock(
                cves=[
                    CveReference(
                        cve_id="CVE-2025-0001",  # type: ignore[arg-type]
                        description=_api_string("a CVE"),
                        cvss_score=poisoned,
                    )
                ]
            )
        }
    )
    cleaned = neutralize_framework_owned_provenance(spec)
    refs = cleaned.external_references
    assert refs is not None
    score = refs.cves[0].cvss_score
    assert score is not None
    assert score.framework_enriched is False
    assert score.discrepancy_with_blog is False
    assert score.overridden_blog_value is None
    assert score.discrepancy_classification is None


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


def test_neutralize_nulls_llm_authored_cve_source_of_record() -> None:
    # source_of_record is framework-set only after a SUCCESSFUL enrichment lookup (enrichment.py);
    # an Extractor-authored value would otherwise survive on every skipped lookup as a forged claim
    # that an authoritative source backs a CVE the lab never queried (ADR 0085 / #7).
    spec = make_spec().model_copy(
        update={
            "external_references": ExternalRefsBlock(
                cves=[
                    CveReference(
                        cve_id="CVE-2025-0001",  # type: ignore[arg-type]
                        description=_api_string("an LLM-claimed CVE"),
                        source_of_record="nvd",  # type: ignore[arg-type]  # LLM-forged
                    )
                ]
            )
        }
    )
    refs = spec.external_references
    assert refs is not None and refs.cves[0].source_of_record == "nvd"  # poisoned
    cleaned = neutralize_framework_owned_provenance(spec)
    cleaned_refs = cleaned.external_references
    assert cleaned_refs is not None
    assert cleaned_refs.cves[0].source_of_record is None


def test_neutralize_nulls_llm_authored_lab_reproducibility() -> None:
    # The lab-level reproducibility block is framework-DERIVED from the per-step tiers
    # (architecture.md §0.7), never authored upfront; an LLM-authored derivation_trace would be a
    # fabricated framework audit trail (ADR 0085 / #7).
    spec = make_spec().model_copy(
        update={
            "reproducibility": ReproducibilityBlock(
                classification_lab_level=ReproducibilityLabLevel.FULL,
                overall_assessment=_api_string("fully reproducible"),
                derivation_trace=["an LLM-fabricated derivation trace"],
            )
        }
    )
    assert spec.reproducibility is not None  # poisoned
    cleaned = neutralize_framework_owned_provenance(spec)
    assert cleaned.reproducibility is None


def test_neutralize_is_a_noop_on_a_clean_spec() -> None:
    """A spec with no LLM-authored framework fields round-trips to an equal instance."""
    spec = make_spec()
    assert neutralize_framework_owned_provenance(spec) == spec


def _all_artifact_model_subclasses() -> set[type[ArtifactModel]]:
    """Every imported ``ArtifactModel`` subclass, walked recursively."""
    seen: set[type[ArtifactModel]] = set()
    stack: list[type[ArtifactModel]] = list(ArtifactModel.__subclasses__())
    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        stack.extend(cls.__subclasses__())
    return seen


def test_framework_enriched_marker_set_is_unique_to_provenance() -> None:
    # provenance_guard._scrub_node identifies a serialized Provenance by the key set
    # {source, citations, framework_enriched} and resets those fields. If a future
    # non-Provenance model acquired that exact field set, the guard would silently scrub it.
    # Pin the discriminator so that can never happen unnoticed (review #3).
    for mod in ("attack_spec", "manifest", "registries", "catalogs", "provenance"):
        importlib.import_module(f"cyberlab_gen.schemas.{mod}")
    markers = {"source", "citations", "framework_enriched"}
    offenders = sorted(
        cls.__name__
        for cls in _all_artifact_model_subclasses()
        if markers <= set(cls.model_fields) and not issubclass(cls, Provenance)
    )
    assert offenders == [], (
        f"non-Provenance ArtifactModel subclasses carry the provenance marker set {markers}: "
        f"{offenders} — provenance_guard would silently scrub them; give them a distinct shape "
        "or extend the guard deliberately"
    )
