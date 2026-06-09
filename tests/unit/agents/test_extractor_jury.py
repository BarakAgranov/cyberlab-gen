"""Tests for the Extractor-Jury (``agents.md §5.5``, ``pipeline.md §3.2.3``, ADR 0021).

Covers the Task 5 exit criteria for the Jury (as amended by ADR 0051/0060):
- approve / revise / reject each fire on constructed AttackSpecs (the jury LLM
  returns each verdict via the mock; the framework reads it);
- the JuryVerdict validator enforces verdict<->feedback consistency;
- the jury CONSUMES the orchestrator-owned grounding findings set and does not
  re-derive it (the mechanical provenance/trace checks now live in the
  GroundingValidator — see tests/unit/validators/test_grounding_validator.py).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from cyberlab_gen.agents.extractor_jury import (
    ExtractorJury,
    JuryFieldFeedback,
    JuryScores,
    JuryVerdict,
    Verdict,
)
from cyberlab_gen.providers import (
    AgentLabel,
    CapabilityHint,
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
from cyberlab_gen.schemas.provenance import (
    CitationBlock,
    ProvenanceString,
)

_HASH = "a" * 64


# --- builders --------------------------------------------------------------


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


def _spec() -> AttackSpec:
    return AttackSpec(
        spec_version=1,
        source=_source(),
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
                    techniques=ChainStepTechniques(mitre=["T1078"]),  # type: ignore[list-item]
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


# --- JuryVerdict schema ----------------------------------------------------


def _scores(v: float = 0.9) -> JuryScores:
    return JuryScores(fidelity=v, completeness=v, provenance_correctness=v, structural_validity=v)


def test_approve_verdict_rejects_feedback() -> None:
    with pytest.raises(ValidationError, match="approve"):
        JuryVerdict(
            verdict=Verdict.APPROVE,
            scores=_scores(),
            feedback=[JuryFieldFeedback(field_path="x", problem="y")],
            retry_recommended=False,
            rationale="ok",
        )


def test_revise_requires_one_to_three_feedback() -> None:
    with pytest.raises(ValidationError, match="revise"):
        JuryVerdict(
            verdict=Verdict.REVISE,
            scores=_scores(0.6),
            feedback=[],
            retry_recommended=True,
            rationale="r",
        )
    # four feedback items is too many for a revise (that is systematic => reject).
    with pytest.raises(ValidationError, match="revise"):
        JuryVerdict(
            verdict=Verdict.REVISE,
            scores=_scores(0.6),
            feedback=[JuryFieldFeedback(field_path=f"f{i}", problem="p") for i in range(4)],
            retry_recommended=True,
            rationale="r",
        )


def test_reject_requires_feedback() -> None:
    with pytest.raises(ValidationError, match="reject"):
        JuryVerdict(
            verdict=Verdict.REJECT,
            scores=_scores(0.2),
            feedback=[],
            retry_recommended=False,
            rationale="r",
        )


def test_verdict_round_trips_through_yaml() -> None:
    v = JuryVerdict(
        verdict=Verdict.REVISE,
        scores=_scores(0.65),
        feedback=[JuryFieldFeedback(field_path="thesis.summary", problem="overclaims")],
        retry_recommended=True,
        rationale="one citation problem",
    )
    assert JuryVerdict.from_yaml(v.to_yaml()) == v


# --- jury LLM stage (each verdict fires) -----------------------------------


def _rankings() -> ModelRankings:
    return ModelRankings.model_validate(
        {
            "by_capability": {
                CapabilityHint.HIGH_QUALITY_REASONING.value: [
                    {"provider": "anthropic", "model": "model-x"}
                ]
            }
        }
    )


def _jury(provider: MockProvider) -> ExtractorJury:
    return ExtractorJury(
        provider=provider,
        registry=ProviderRegistry(_rankings(), frozenset({"anthropic"})),
        registries=load_merged_registries(),
    )


async def _run_with(verdict: JuryVerdict) -> JuryVerdict:
    provider = MockProvider()
    provider.register(
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
        agent_label=AgentLabel.EXTRACTOR_JURY,
        response=verdict,
    )
    return await _jury(provider).review(spec=_spec(), blog_content="the blog")


async def test_jury_approve_fires() -> None:
    out = await _run_with(
        JuryVerdict(
            verdict=Verdict.APPROVE, scores=_scores(0.9), retry_recommended=False, rationale="clean"
        )
    )
    assert out.verdict is Verdict.APPROVE


async def test_jury_revise_fires() -> None:
    out = await _run_with(
        JuryVerdict(
            verdict=Verdict.REVISE,
            scores=_scores(0.65),
            feedback=[JuryFieldFeedback(field_path="chain.chain_steps[0]", problem="weak cite")],
            retry_recommended=True,
            rationale="one field",
        )
    )
    assert out.verdict is Verdict.REVISE
    assert len(out.feedback) == 1


async def test_jury_reject_fires() -> None:
    out = await _run_with(
        JuryVerdict(
            verdict=Verdict.REJECT,
            scores=_scores(0.2),
            feedback=[JuryFieldFeedback(field_path="chain", problem="systematic hallucination")],
            retry_recommended=False,
            rationale="cascading",
        )
    )
    assert out.verdict is Verdict.REJECT


async def test_jury_consumes_supplied_grounding_findings() -> None:
    # ADR 0051/0060: the jury CONSUMES the orchestrator's grounding findings (it no longer
    # re-derives them). A supplied finding reaches the prompt; the jury still returns its
    # verdict. (The mechanical provenance/trace checks now live in the GroundingValidator —
    # see tests/unit/validators/test_grounding_validator.py.)
    from cyberlab_gen.validators.grounding_validator import GroundingCode, GroundingFinding

    provider = MockProvider()
    provider.register(
        capability=CapabilityHint.HIGH_QUALITY_REASONING,
        agent_label=AgentLabel.EXTRACTOR_JURY,
        response=JuryVerdict(
            verdict=Verdict.APPROVE, scores=_scores(0.9), retry_recommended=False, rationale="ok"
        ),
    )
    finding = GroundingFinding(
        code=GroundingCode.PROVENANCE_STRUCTURE,
        location="thesis.summary",
        detail="external_api field lacks an external_api_response citation",
    )
    out = await _jury(provider).review(
        spec=_spec(), blog_content="the blog", grounding_findings=[finding]
    )
    assert out.verdict is Verdict.APPROVE
