"""Tests for the Extractor stage (``agents.md §5.4``, ``pipeline.md §3.2.2``, ADR 0021/0060).

Covers the Task 5 exit criteria for the Extractor, as amended by ADR 0051/0060 (the
Extractor stops self-validating; the orchestrator owns the grounding stack):
- produces a schema-valid AttackSpec with provenance on every content field, in ONE pass;
- it does NOT self-validate grounding — an external_api field with no tool-call trace is
  returned as-is (the orchestrator-owned grounding stack flags it, tested in
  ``tests/unit/validators/test_grounding_validator.py`` and ``test_orchestrator.py``);
- a malformed MITRE id is rejected at construction by the MitreTechniqueId type;
- out-of-scope content sets extraction_outcome;
- ``refine`` keeps only a bounded patch-apply re-prompt loop (R1).

The MockProvider does not drive the tool-use loop (it returns the registered
response), so the executor's lookup trace is empty under the mock.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError as PydanticValidationError

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
# A technique id present in the bundled MITRE seed (registry/mitre_attack_techniques.yaml).
_REAL_TECH = "T1078"
# A real, current ATT&CK id absent from the 8-entry seed (the case ADR 0055 protects):
# well-formed and uncatalogued, so it must pass through unverified, not be rejected.
_UNCATALOGUED_TECH = "T1195"  # Supply Chain Compromise — blog-central, absent from the seed
# Well-formed but not a real ATT&CK id. Without an authoritative adapter the framework cannot
# tell this apart from a real uncatalogued id, so per ADR 0055 P2 it too passes unverified
# (the jury's fidelity review is the grounding backstop until the MITRE adapter is wired).
_UNVERIFIABLE_TECH = "T9999"


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
    # Raised 16384 -> 20000 (investigation 0002): ADR 0032's calibration ("16384 covers a
    # rich spec with margin; a 9-step spec ~12K") was falsified by a ~16K/8-step spec that
    # truncated. 20000 gives the dense tail ~30% headroom; it is a stopgap, NOT the
    # truncation class-fix (streaming is — specs >~20K still truncate).
    assert DEFAULT_EXTRACTOR_MAX_TOKENS == 20000
    assert DEFAULT_EXTRACTOR_MAX_TOKENS <= 21333  # still below the non-streaming SDK wall


def test_prompt_steers_first_pass_proposals_for_unknown_vocabulary() -> None:
    # B-ii (investigation 0002 §6): the Extractor must propose unknown vocabulary on the
    # FIRST pass rather than wait for a structural-validation rejection that forces a
    # costlier full re-extraction (the truncation-prone second emit). All three propose_*
    # tools must be documented — including propose_thesis_type (ADR 0045), which the prompt
    # previously omitted even though 4 of the real run's 13 findings were unknown_thesis_type.
    from cyberlab_gen.agents.prompts import load_prompt

    prompt = load_prompt("extractor").lower()
    assert "propose_value_type" in prompt
    assert "propose_facet" in prompt
    assert "propose_thesis_type" in prompt
    assert "first pass" in prompt  # the proactive steering
    assert "do not wait" in prompt  # ...don't wait for a rejection


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


async def test_extract_is_single_pass_and_does_not_self_validate_grounding() -> None:
    # ADR 0051/0060: the Extractor no longer runs its own search-before-claim loop. An
    # external_api severity claim with an empty lookup trace is returned AS-IS in one pass;
    # flagging it is the orchestrator-owned grounding stack's job, not this stage's.
    provider = MockProvider()
    _register(provider, _spec(external=_external_api_cve()))
    result = await _extractor(provider).extract(blog_content="blog", source_summary="url=...")
    assert result.reprompts == 0  # one pass, no self-validation re-prompt
    assert result.attack_spec.external_references is not None
    assert result.attack_spec.external_references.cves[0].cve_id == "CVE-2024-0001"


async def test_uncatalogued_mitre_id_passes_unverified() -> None:
    # ADR 0055/0058: a real, current ATT&CK id absent from the bundled seed is well-formed
    # but unverifiable here — it must pass THROUGH unverified, never be rejected. The
    # Extractor returns it in one pass; the grounding stack also never flags it.
    provider = MockProvider()
    _register(provider, _spec(tech=_UNCATALOGUED_TECH))
    result = await _extractor(provider).extract(blog_content="blog", source_summary="url=...")
    assert result.reprompts == 0
    assert result.attack_spec.chain.chain_steps[0].techniques.mitre == [_UNCATALOGUED_TECH]  # type: ignore[union-attr]


async def test_unverifiable_mitre_id_passes_unverified() -> None:
    # P2: a well-formed-but-unverifiable id (T9999) is never a hard finding — the Extractor
    # returns it; the grounding stack's MITRE layer is a no-op pass-through (ADR 0058).
    provider = MockProvider()
    _register(provider, _spec(tech=_UNVERIFIABLE_TECH))
    result = await _extractor(provider).extract(blog_content="blog", source_summary="url=...")
    assert result.attack_spec.chain.chain_steps[0].techniques.mitre == [_UNVERIFIABLE_TECH]  # type: ignore[union-attr]


def test_malformed_mitre_id_rejected_at_construction() -> None:
    # Well-formedness is owned by the MitreTechniqueId type (primitives.py) and enforced at
    # AttackSpec construction. A malformed id never reaches any framework check; it fails here.
    with pytest.raises(PydanticValidationError):
        ChainStepTechniques(mitre=["T12"])  # type: ignore[list-item]


def test_negative_max_output_tokens_rejected() -> None:
    provider = MockProvider()
    with pytest.raises(ValueError, match="max_output_tokens"):
        _extractor(provider, max_output_tokens=0)


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
    # patch-apply re-prompt loop is bounded (DEFAULT_PATCH_RETRY_ATTEMPTS) and raises
    # ExtractionError on exhaustion.
    provider = MockProvider()
    _register_patch(
        provider,
        RefinementPatch(patches=[FieldPatch(field_path="thesis.no_such_field", new_value="x")]),
    )
    with pytest.raises(ExtractionError):
        await _extractor(provider).refine(
            prior_spec=_spec(), feedback=_FEEDBACK, blog_content="blog", source_summary="url=..."
        )


async def test_refine_returns_patched_spec_without_self_validating_grounding() -> None:
    # ADR 0051/0060: refine no longer runs the grounding re-check (R2). A patch that
    # introduces an ungrounded external_api field is APPLIED and returned in one pass —
    # the orchestrator's grounding stack re-checks the patched spec on the graph
    # (whole-spec R2 coverage preserved without a hidden Extractor loop; see
    # test_orchestrator.py::test_refine_producing_ungrounded_spec_routes_grounding_retry).
    provider = MockProvider()
    _register_patch(
        provider,
        RefinementPatch(
            patches=[
                FieldPatch(
                    field_path="external_references",
                    new_value=_external_api_cve().model_dump(mode="json", by_alias=True),
                )
            ]
        ),
    )
    result = await _extractor(provider).refine(
        prior_spec=_spec(), feedback=_FEEDBACK, blog_content="blog", source_summary="url=..."
    )
    assert result.reprompts == 0
    assert result.attack_spec.external_references is not None  # the patch applied, no raise
