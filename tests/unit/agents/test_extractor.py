"""Tests for the Extractor stage (``agents.md §5.4``, ``pipeline.md §3.2.2``, ADR 0021).

Covers the Task 5 exit criteria for the Extractor:
- produces a schema-valid AttackSpec with provenance on every content field;
- an external_api field with no tool-call trace is rejected (search-before-claim);
- a hallucinated MITRE technique id is rejected and re-prompted, then resolved;
- out-of-scope content sets extraction_outcome.

The MockProvider does not drive the tool-use loop (it returns the registered
response), so the executor's lookup trace is empty under the mock. That is
exactly what exercises the search-before-claim rejection: an external_api field
with no matching lookup. The recovery case uses a message_matcher keyed on the
framework's re-prompt text to return a clean spec on the second attempt.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cyberlab_gen.agents.extractor import Extractor
from cyberlab_gen.agents.extractor_jury.schema import JuryFieldFeedback
from cyberlab_gen.errors import ExtractionError
from cyberlab_gen.framework.refinement import FieldPatch, RefinementPatch
from cyberlab_gen.providers import (
    AgentLabel,
    CapabilityHint,
    Message,
    MockProvider,
    ModelRankings,
    ProviderRegistry,
)
from cyberlab_gen.registries.merge import load_merged_registries
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
from cyberlab_gen.schemas.provenance import (
    CitationBlock,
    Provenance,
    ProvenanceString,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

# --- builders --------------------------------------------------------------

_HASH = "a" * 64
# Technique ids present in the bundled MITRE catalog (registry/mitre_attack_techniques.yaml).
_REAL_TECH = "T1078"
_HALLUCINATED_TECH = "T9999"


def _cite() -> CitationBlock:
    return CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="§1, ¶1")


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
        extractor_version="1.0.0", model="mock-model", completeness_score=0.8, citations_count=2
    )


def _per_step() -> PerStepReproducibility:
    return PerStepReproducibility(
        classification=ReproducibilityTier.FULL, caveats=_pstr("none"), why=_pstr("scriptable")
    )


def _step(tech: str = _REAL_TECH) -> ChainStep:
    return ChainStep(
        id="step-1",  # type: ignore[arg-type]
        step_number=1,
        title="Step 1",
        description=_pstr("do the thing"),
        blog_excerpt="verbatim excerpt",
        techniques=ChainStepTechniques(mitre=[tech]),  # type: ignore[list-item]
        reproducibility=_per_step(),
        provisioning_mechanism=ProvisioningMechanism.TERRAFORM,
    )


def _thesis() -> ThesisBlock:
    return ThesisBlock(
        types=["vulnerability_chain"],  # type: ignore[list-item]
        summary=_pstr("a chain"),
        attacker_objective=_pstr("admin"),
        vulnerability_story=_pstr("misconfig"),
        duration_as_described=_pstr("a week"),
    )


def _spec(
    *,
    tech: str = _REAL_TECH,
    external: ExternalRefsBlock | None = None,
) -> AttackSpec:
    return AttackSpec(
        spec_version=1,
        source=_source(),
        extraction_outcome=ExtractionOutcome.IN_SCOPE,
        thesis=_thesis(),
        chain=ChainBlock(chain_steps=[_step(tech)]),
        external_references=external,
        extraction_metadata=_metadata(),
    )


def _out_of_scope_spec() -> AttackSpec:
    return AttackSpec(
        spec_version=1,
        source=_source(),
        extraction_outcome=ExtractionOutcome.OUT_OF_SCOPE,
        extraction_outcome_reason="pure on-prem attack with no cloud or supply-chain surface here",
        extraction_metadata=_metadata(),
    )


def _external_api_cve() -> ExternalRefsBlock:
    """A CVE whose severity claims source=external_api (needs a matching lookup)."""
    return ExternalRefsBlock(
        cves=[
            CveReference(
                cve_id="CVE-2024-0001",
                description=_pstr("a vuln"),
                severity=Provenance[Severity](
                    value=Severity.HIGH,
                    source=ProvenanceSource.EXTERNAL_API,
                    citations=[
                        CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="§2"),
                        CitationBlock(
                            kind=CitationKind.EXTERNAL_API_RESPONSE, reference="nvd:CVE-2024-0001"
                        ),
                    ],
                ),
            )
        ]
    )


# --- harness ---------------------------------------------------------------


def _rankings() -> ModelRankings:
    return ModelRankings.model_validate(
        {
            "by_capability": {
                CapabilityHint.LONG_CONTEXT_EXTRACTION.value: [
                    {"provider": "anthropic", "model": "model-x"}
                ],
                CapabilityHint.HIGH_QUALITY_REASONING.value: [
                    {"provider": "anthropic", "model": "model-x"}
                ],
            }
        }
    )


def _extractor(provider: MockProvider, **kw: object) -> Extractor:
    registry = ProviderRegistry(_rankings(), frozenset({"anthropic"}))
    return Extractor(
        provider=provider,
        registry=registry,
        registries=load_merged_registries(),
        **kw,  # type: ignore[arg-type]
    )


def _register(provider: MockProvider, spec: AttackSpec, **kw: object) -> None:
    provider.register(
        capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
        agent_label=AgentLabel.EXTRACTOR,
        response=spec,
        **kw,  # type: ignore[arg-type]
    )


# --- tests -----------------------------------------------------------------


async def test_produces_schema_valid_spec_with_provenance() -> None:
    provider = MockProvider()
    _register(provider, _spec())
    result = await _extractor(provider).extract(
        blog_content="a blog about an attack", source_summary="url=..."
    )
    assert isinstance(result.attack_spec, AttackSpec)
    assert result.attack_spec.extraction_outcome is ExtractionOutcome.IN_SCOPE
    # Every content field on the chain step carries provenance.
    step = result.attack_spec.chain.chain_steps[0]  # type: ignore[union-attr]
    assert step.description.source is ProvenanceSource.BLOG_EXPLICIT
    assert result.reprompts == 0


async def test_extractor_requests_a_generous_output_budget() -> None:
    # Regression for the truncated-emit bug (ADR 0032): the Extractor must request a
    # generous max_tokens, not fall back to the provider's 4096 default which truncates
    # a full AttackSpec mid-emit (the alternating extraction_metadata/chain failures).
    # The non-streaming SDK path raises above ~21,333 tokens, so the value sits below it.
    from cyberlab_gen.agents.extractor import DEFAULT_EXTRACTOR_MAX_TOKENS

    provider = MockProvider()
    _register(provider, _spec())
    await _extractor(provider).extract(blog_content="blog", source_summary="url=...")
    assert provider.last_max_tokens == DEFAULT_EXTRACTOR_MAX_TOKENS
    assert DEFAULT_EXTRACTOR_MAX_TOKENS == 16384  # generous (4x the 4096 default)
    assert DEFAULT_EXTRACTOR_MAX_TOKENS <= 21333  # safe on the non-streaming call path


async def test_extractor_output_budget_is_configurable() -> None:
    provider = MockProvider()
    _register(provider, _spec())
    await _extractor(provider, max_output_tokens=12000).extract(
        blog_content="blog", source_summary="url=..."
    )
    assert provider.last_max_tokens == 12000


async def test_out_of_scope_sets_extraction_outcome() -> None:
    provider = MockProvider()
    _register(provider, _out_of_scope_spec())
    result = await _extractor(provider).extract(blog_content="off topic", source_summary="url=...")
    assert result.attack_spec.extraction_outcome is ExtractionOutcome.OUT_OF_SCOPE
    assert result.attack_spec.extraction_outcome_reason is not None


async def test_external_api_field_without_trace_is_rejected() -> None:
    # external_api severity claim + empty lookup trace (mock doesn't drive tools)
    # => search-before-claim rejection on every attempt => ExtractionError.
    provider = MockProvider()
    _register(provider, _spec(external=_external_api_cve()))
    extractor = _extractor(provider, hallucination_retry_attempts=1)
    with pytest.raises(ExtractionError, match="search-before-claim"):
        await extractor.extract(blog_content="blog", source_summary="url=...")


async def test_hallucinated_mitre_id_rejected_and_reprompted() -> None:
    # First attempt: a hallucinated technique. The framework re-prompts with the id
    # flagged; the matcher returns a clean spec once the re-prompt text appears.
    provider = MockProvider()

    def is_reprompt(messages: list[Message]) -> bool:
        return any("FRAMEWORK REJECTION" in m.content for m in messages)

    def is_first(messages: list[Message]) -> bool:
        return not is_reprompt(messages)

    _register(provider, _spec(tech=_HALLUCINATED_TECH), message_matcher=is_first)
    _register(provider, _spec(tech=_REAL_TECH), message_matcher=is_reprompt)

    result = await _extractor(provider).extract(blog_content="blog", source_summary="url=...")
    assert result.reprompts == 1
    assert result.attack_spec.chain.chain_steps[0].techniques.mitre == [_REAL_TECH]  # type: ignore[union-attr]


async def test_hallucinated_mitre_exhausts_budget_raises() -> None:
    provider = MockProvider()
    _register(provider, _spec(tech=_HALLUCINATED_TECH))
    extractor = _extractor(provider, hallucination_retry_attempts=2)
    with pytest.raises(ExtractionError, match="mitre_hallucination"):
        await extractor.extract(blog_content="blog", source_summary="url=...")


def test_negative_retry_budget_rejected() -> None:
    provider = MockProvider()
    with pytest.raises(ValueError, match="hallucination_retry_attempts"):
        _extractor(provider, hallucination_retry_attempts=-1)


async def test_blog_explicit_cve_with_nvd_client_unverified_is_rejected() -> None:
    # A CVE whose description is blog_explicit (a grounded claim) but which NVD
    # has no record of => cve_hallucination when an NVD client is wired.
    class _NvdMiss:
        def lookup_cve(self, cve_id: str) -> None:
            return None

    external = ExternalRefsBlock(
        cves=[CveReference(cve_id="CVE-2024-0002", description=_pstr("claimed real CVE"))]
    )
    provider = MockProvider()
    _register(provider, _spec(external=external))
    extractor = _extractor(provider, nvd_client=_NvdMiss(), hallucination_retry_attempts=0)
    with pytest.raises(ExtractionError, match="cve_hallucination"):
        await extractor.extract(blog_content="blog", source_summary="url=...")


# --- refinement: targeted patch (ADR 0048 A1, ADR 0054) --------------------


def _prov_dump(value: str) -> JsonValue:
    """A fresh valid Provenance[str] sub-tree, as a refinement patch would carry it."""
    return _pstr(value).model_dump(mode="json", by_alias=True)


def _register_patch(provider: MockProvider, patch: RefinementPatch, **kw: object) -> None:
    provider.register(
        capability=CapabilityHint.LONG_CONTEXT_EXTRACTION,
        agent_label=AgentLabel.EXTRACTOR,
        response=patch,
        **kw,  # type: ignore[arg-type]
    )


_FEEDBACK = [
    JuryFieldFeedback(field_path="thesis.summary", problem="too vague", suggested_fix="cite §2")
]


async def test_refine_applies_a_clean_patch_and_leaves_other_fields_untouched() -> None:
    provider = MockProvider()
    _register_patch(
        provider,
        RefinementPatch(
            patches=[
                FieldPatch(field_path="thesis.summary", new_value=_prov_dump("a precise summary"))
            ]
        ),
    )
    prior = _spec()
    result = await _extractor(provider).refine(
        prior_spec=prior, feedback=_FEEDBACK, blog_content="blog", source_summary="url=..."
    )
    assert result.attack_spec.thesis.summary.value == "a precise summary"  # type: ignore[union-attr]
    assert result.reprompts == 0
    # the unflagged step description is byte-identical to the prior (convergence at the stage)
    assert (
        result.attack_spec.chain.chain_steps[0].description.value  # type: ignore[union-attr]
        == prior.chain.chain_steps[0].description.value  # type: ignore[union-attr]
    )


async def test_refine_reprompts_on_an_unapplyable_patch_then_succeeds() -> None:
    provider = MockProvider()
    bad = RefinementPatch(patches=[FieldPatch(field_path="thesis.no_such_field", new_value="x")])
    good = RefinementPatch(
        patches=[FieldPatch(field_path="thesis.summary", new_value=_prov_dump("fixed"))]
    )

    def is_reprompt(messages: list[Message]) -> bool:
        return any("PATCH REJECTED" in m.content for m in messages)

    def is_first(messages: list[Message]) -> bool:
        return not is_reprompt(messages)

    _register_patch(provider, bad, message_matcher=is_first)
    _register_patch(provider, good, message_matcher=is_reprompt)

    result = await _extractor(provider).refine(
        prior_spec=_spec(), feedback=_FEEDBACK, blog_content="blog", source_summary="url=..."
    )
    assert result.reprompts == 1
    assert result.attack_spec.thesis.summary.value == "fixed"  # type: ignore[union-attr]


async def test_refine_exhausts_budget_on_a_persistently_unapplyable_patch() -> None:
    # R1 (inner bound): a patch that can never apply must NOT spin — refine()'s own
    # re-prompt loop is bounded and raises ExtractionError on exhaustion.
    provider = MockProvider()
    _register_patch(
        provider,
        RefinementPatch(patches=[FieldPatch(field_path="thesis.no_such_field", new_value="x")]),
    )
    extractor = _extractor(provider, hallucination_retry_attempts=1)  # 2 attempts total
    with pytest.raises(ExtractionError):
        await extractor.refine(
            prior_spec=_spec(), feedback=_FEEDBACK, blog_content="blog", source_summary="url=..."
        )


async def test_refine_runs_mechanical_checks_whole_spec_and_catches_hallucinated_mitre() -> None:
    # R2: the search-before-claim / MITRE / CVE checks run over the WHOLE patched spec,
    # so a patch that introduces a hallucinated technique in the patched field is caught.
    provider = MockProvider()
    _register_patch(
        provider,
        RefinementPatch(
            patches=[
                FieldPatch(
                    field_path="chain.chain_steps[0].techniques.mitre",
                    new_value=[_HALLUCINATED_TECH],
                )
            ]
        ),
    )
    extractor = _extractor(provider, hallucination_retry_attempts=0)  # 1 attempt
    with pytest.raises(ExtractionError, match="mitre_hallucination"):
        await extractor.refine(
            prior_spec=_spec(), feedback=_FEEDBACK, blog_content="blog", source_summary="url=..."
        )
