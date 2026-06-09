"""Tests for ``Provenance[T]`` and ``CitationBlock``.

Architectural source: ``schema-details.md`` §3.
"""

import pytest
from pydantic import ValidationError

from cyberlab_gen.schemas import (
    CitationBlock,
    CitationKind,
    ConfidenceSource,
    Provenance,
    ProvenanceSource,
    ProvenanceString,
    ProvenanceStringList,
)


def _citation(kind: CitationKind = CitationKind.BLOG_PASSAGE) -> CitationBlock:
    return CitationBlock(kind=kind, reference="§3, ¶2")


# --- CitationBlock ---------------------------------------------------------


def test_citation_block_constructs() -> None:
    block = CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="§3, ¶2")
    assert block.location is None


def test_citation_block_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError) as exc:
        CitationBlock.model_validate({"kind": "blog_passage", "reference": "§3", "bogus": "nope"})
    assert "bogus" in str(exc.value)


def test_citation_block_rejects_empty_reference() -> None:
    with pytest.raises(ValidationError):
        CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="")  # type: ignore[arg-type]


# --- Provenance: source-rules required-when --------------------------------


def test_llm_inference_requires_confidence() -> None:
    with pytest.raises(ValidationError, match="confidence is required"):
        Provenance[str](
            value="x",
            source=ProvenanceSource.LLM_INFERENCE,
            citations=[_citation()],
        )


def test_llm_inference_confidence_requires_confidence_source() -> None:
    with pytest.raises(ValidationError, match="confidence_source is required"):
        Provenance[str](
            value="x",
            source=ProvenanceSource.LLM_INFERENCE,
            confidence=0.5,
            citations=[_citation()],
        )


def test_unknown_from_blog_requires_reason() -> None:
    with pytest.raises(ValidationError, match="reason is required"):
        Provenance[str](value="x", source=ProvenanceSource.UNKNOWN_FROM_BLOG)


def test_blog_explicit_requires_citations() -> None:
    with pytest.raises(ValidationError, match="citations are required"):
        Provenance[str](value="x", source=ProvenanceSource.BLOG_EXPLICIT)


def test_external_api_requires_citations() -> None:
    with pytest.raises(ValidationError, match="citations are required"):
        Provenance[str](value="x", source=ProvenanceSource.EXTERNAL_API)


# --- Provenance: confidence/confidence_source pairing ---------------------


def test_confidence_without_confidence_source_raises() -> None:
    with pytest.raises(ValidationError, match="confidence_source is required"):
        Provenance[str](
            value="x",
            source=ProvenanceSource.BLOG_EXPLICIT,
            citations=[_citation()],
            confidence=0.7,
        )


def test_confidence_source_without_confidence_raises() -> None:
    with pytest.raises(ValidationError, match="confidence_source must be None"):
        Provenance[str](
            value="x",
            source=ProvenanceSource.BLOG_EXPLICIT,
            citations=[_citation()],
            confidence_source=ConfidenceSource.FRAMEWORK_COMPUTED,
        )


# --- Provenance: UNKNOWN_FROM_BLOG negative invariants --------------------


def test_unknown_from_blog_forbids_citations() -> None:
    with pytest.raises(ValidationError, match="citations must be empty"):
        Provenance[str](
            value="x",
            source=ProvenanceSource.UNKNOWN_FROM_BLOG,
            reason="blog only describes outcomes",
            citations=[_citation()],
        )


# --- Provenance: confidence is LLM_INFERENCE-only -------------------------


def test_blog_explicit_forbids_confidence() -> None:
    """Confidence is exclusive to LLM_INFERENCE. Per ADR 0005."""
    with pytest.raises(
        ValidationError, match="confidence is only valid when source is llm_inference"
    ):
        Provenance[str](
            value="x",
            source=ProvenanceSource.BLOG_EXPLICIT,
            citations=[_citation()],
            confidence=0.5,
            confidence_source=ConfidenceSource.FRAMEWORK_COMPUTED,
        )


def test_external_api_forbids_confidence() -> None:
    """EXTERNAL_API in v1 is exact-match enrichment only. Per ADR 0005."""
    with pytest.raises(
        ValidationError, match="confidence is only valid when source is llm_inference"
    ):
        Provenance[str](
            value="CVE-2024-1234",
            source=ProvenanceSource.EXTERNAL_API,
            citations=[_citation(CitationKind.EXTERNAL_API_RESPONSE)],
            confidence=0.95,
            confidence_source=ConfidenceSource.FRAMEWORK_COMPUTED,
        )


def test_unknown_from_blog_forbids_confidence() -> None:
    """UNKNOWN_FROM_BLOG has no value to be confident about. Per ADR 0005."""
    with pytest.raises(
        ValidationError, match="confidence is only valid when source is llm_inference"
    ):
        Provenance[str](
            value="",
            source=ProvenanceSource.UNKNOWN_FROM_BLOG,
            reason="blog only describes outcomes",
            confidence=0.5,
            confidence_source=ConfidenceSource.MODEL_SELF_REPORTED,
        )


def test_user_provided_forbids_confidence() -> None:
    """USER_PROVIDED carries no probability. Per ADR 0005."""
    with pytest.raises(
        ValidationError, match="confidence is only valid when source is llm_inference"
    ):
        Provenance[str](
            value="user-pick",
            source=ProvenanceSource.USER_PROVIDED,
            confidence=0.7,
            confidence_source=ConfidenceSource.MODEL_SELF_REPORTED,
        )


# --- Provenance: discrepancy-record invariants ----------------------------


def test_discrepancy_true_requires_overridden_blog_value() -> None:
    with pytest.raises(ValidationError, match="overridden_blog_value is required"):
        Provenance[str](
            value="api-value",
            source=ProvenanceSource.EXTERNAL_API,
            citations=[_citation(CitationKind.EXTERNAL_API_RESPONSE)],
            discrepancy_with_blog=True,
            discrepancy_classification="material",
        )


def test_discrepancy_true_requires_classification() -> None:
    with pytest.raises(ValidationError, match="discrepancy_classification is required"):
        Provenance[str](
            value="api-value",
            source=ProvenanceSource.EXTERNAL_API,
            citations=[_citation(CitationKind.EXTERNAL_API_RESPONSE)],
            discrepancy_with_blog=True,
            overridden_blog_value="blog-value",
        )


def test_discrepancy_requires_external_api_source() -> None:
    with pytest.raises(ValidationError, match="only valid when source is external_api"):
        Provenance[str](
            value="x",
            source=ProvenanceSource.BLOG_EXPLICIT,
            citations=[_citation()],
            discrepancy_with_blog=True,
            overridden_blog_value="blog-value",
            discrepancy_classification="material",
        )


def test_discrepancy_false_forbids_overridden_blog_value() -> None:
    with pytest.raises(ValidationError, match="overridden_blog_value must be None"):
        Provenance[str](
            value="x",
            source=ProvenanceSource.BLOG_EXPLICIT,
            citations=[_citation()],
            overridden_blog_value="leftover",
        )


def test_discrepancy_false_forbids_classification() -> None:
    with pytest.raises(ValidationError, match="discrepancy_classification must be None"):
        Provenance[str](
            value="x",
            source=ProvenanceSource.BLOG_EXPLICIT,
            citations=[_citation()],
            discrepancy_classification="material",
        )


# --- Provenance: happy paths per source value -----------------------------


def test_blog_explicit_happy_path() -> None:
    p = Provenance[str](
        value="t1.084",
        source=ProvenanceSource.BLOG_EXPLICIT,
        citations=[_citation()],
    )
    assert p.source is ProvenanceSource.BLOG_EXPLICIT


def test_external_api_happy_path() -> None:
    p = Provenance[str](
        value="CVE-2024-1234",
        source=ProvenanceSource.EXTERNAL_API,
        citations=[_citation(CitationKind.EXTERNAL_API_RESPONSE)],
    )
    assert p.value == "CVE-2024-1234"


def test_llm_inference_happy_path() -> None:
    p = Provenance[str](
        value="lateral-movement",
        source=ProvenanceSource.LLM_INFERENCE,
        confidence=0.8,
        confidence_source=ConfidenceSource.FRAMEWORK_COMPUTED,
        citations=[_citation(CitationKind.LLM_REASONING_TRACE)],
    )
    assert p.confidence == 0.8


def test_unknown_from_blog_happy_path() -> None:
    p = Provenance[str](
        value="",
        source=ProvenanceSource.UNKNOWN_FROM_BLOG,
        reason="blog only describes outcomes, not technique",
    )
    assert p.reason


def test_user_provided_happy_path() -> None:
    p = Provenance[str](value="user-pick", source=ProvenanceSource.USER_PROVIDED)
    assert p.source is ProvenanceSource.USER_PROVIDED


def test_discrepancy_record_happy_path() -> None:
    p = Provenance[str](
        value="api-authoritative",
        source=ProvenanceSource.EXTERNAL_API,
        citations=[
            _citation(CitationKind.BLOG_PASSAGE),
            _citation(CitationKind.EXTERNAL_API_RESPONSE),
        ],
        discrepancy_with_blog=True,
        overridden_blog_value="blog-original",
        discrepancy_classification="material",
    )
    assert p.discrepancy_with_blog is True
    assert p.overridden_blog_value == "blog-original"


# --- Provenance: generics + round-trip ------------------------------------


def test_provenance_string_alias_round_trip() -> None:
    original = ProvenanceString(
        value="hello",
        source=ProvenanceSource.BLOG_EXPLICIT,
        citations=[_citation()],
    )
    restored = ProvenanceString.model_validate(original.model_dump())
    assert restored == original


def test_provenance_string_list_alias_round_trip() -> None:
    original = ProvenanceStringList(
        value=["a", "b"],
        source=ProvenanceSource.BLOG_EXPLICIT,
        citations=[_citation()],
    )
    restored = ProvenanceStringList.model_validate(original.model_dump())
    assert restored == original
    assert restored.value == ["a", "b"]


def test_provenance_confidence_bounds_enforced() -> None:
    with pytest.raises(ValidationError):
        Provenance[str](
            value="x",
            source=ProvenanceSource.LLM_INFERENCE,
            confidence=1.5,
            confidence_source=ConfidenceSource.FRAMEWORK_COMPUTED,
            citations=[_citation()],
        )


# --- framework_enriched (ADR 0052 / 0061) ----------------------------------


def _api_citation() -> CitationBlock:
    return CitationBlock(kind=CitationKind.EXTERNAL_API_RESPONSE, reference="nvd:CVE-2024-0001")


def test_framework_enriched_defaults_false() -> None:
    prov = Provenance[str](
        value="x", source=ProvenanceSource.BLOG_EXPLICIT, citations=[_citation()]
    )
    assert prov.framework_enriched is False


def test_framework_enriched_external_api_is_valid_and_round_trips() -> None:
    prov = Provenance[str](
        value="x",
        source=ProvenanceSource.EXTERNAL_API,
        citations=[_citation(), _api_citation()],
        framework_enriched=True,
    )
    assert prov.framework_enriched is True
    restored = Provenance[str].model_validate(prov.model_dump())
    assert restored == prov
    assert restored.framework_enriched is True


def test_framework_enriched_requires_external_api_source() -> None:
    # framework_enriched marks the framework's own authoritative external_api call; it is only
    # meaningful on source=external_api (ADR 0052 / 0061). A blog_explicit field must not carry it.
    with pytest.raises(ValidationError, match="framework_enriched"):
        Provenance[str](
            value="x",
            source=ProvenanceSource.BLOG_EXPLICIT,
            citations=[_citation()],
            framework_enriched=True,
        )


def test_framework_enriched_does_not_let_external_api_carry_confidence() -> None:
    # ADR 0005 is unperturbed: confidence stays exclusive to llm_inference even with the
    # new boolean set.
    with pytest.raises(ValidationError, match="confidence"):
        Provenance[str](
            value="x",
            source=ProvenanceSource.EXTERNAL_API,
            citations=[_citation(), _api_citation()],
            framework_enriched=True,
            confidence=0.9,
            confidence_source=ConfidenceSource.FRAMEWORK_COMPUTED,
        )
