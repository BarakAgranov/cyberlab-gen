"""Tests for the Phase-1 orchestration state machine (``pipeline.md §3.1``, ADR 0023).

Covers the Task-6 exit criteria:

- a Layer-1-invalid AttackSpec routes to the Extractor's *retry*, **not** the
  refinement coordinator (``validation.md §6.10``) — asserted by the call path:
  the Extractor is re-run on the same input feedback-kind=structural_retry, and
  the Jury is *never* invoked while Layer 1 is red;
- a Jury ``revise`` triggers a bounded re-run that stops at the refinement cap and
  ships the last AttackSpec with ``low_jury_confidence=true`` and the unresolved
  feedback in the outcome (``pipeline.md §3.2.3`` (b));
- a Jury ``reject`` halts (``JuryRejectionError``);
- the full Ingestion→…→enrichment graph runs end-to-end on a fixture and produces
  an enriched, validated AttackSpec.

The fakes implement the narrow ``extract`` / ``review`` surfaces the orchestrator
depends on, recording how they were called so the retry-vs-refinement *path* is
asserted directly (not just the terminal state).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cyberlab_gen.agents.extractor.extractor import ExtractionResult
from cyberlab_gen.agents.extractor_jury.schema import (
    JuryFieldFeedback,
    JuryScores,
    JuryVerdict,
    Verdict,
)
from cyberlab_gen.errors import ValidationError
from cyberlab_gen.framework.orchestrator import (
    JuryRejectionError,
    PipelineState,
    PipelineStatus,
    build_pipeline,
    reject_interactive_when_headless,
    run_pipeline,
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
from cyberlab_gen.schemas.ingestion import IngestionResult
from cyberlab_gen.schemas.provenance import CitationBlock, ProvenanceString
from cyberlab_gen.validators.static_schema_validator import StaticSchemaValidator

if TYPE_CHECKING:
    from pathlib import Path

    from cyberlab_gen.agents.extractor.tools import ExternalLookupRecord

_HASH = "a" * 64


# --- builders --------------------------------------------------------------


def _cite() -> CitationBlock:
    return CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="§1")


def _pstr(value: str) -> ProvenanceString:
    return ProvenanceString(value=value, source=ProvenanceSource.BLOG_EXPLICIT, citations=[_cite()])


def _spec(*, facets: list[str] | None = None) -> AttackSpec:
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
        facets=facets or [],  # type: ignore[arg-type]
        chain=ChainBlock(
            chain_steps=[
                ChainStep(
                    id="step-1",  # type: ignore[arg-type]
                    step_number=1,
                    title="Step 1",
                    description=_pstr("do the thing"),
                    blog_excerpt="verbatim",
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


def _result(spec: AttackSpec) -> ExtractionResult:
    return ExtractionResult(
        attack_spec=spec, value_type_proposals=[], facet_proposals=[], lookups=[]
    )


def _verdict(verdict: Verdict, *, feedback: list[JuryFieldFeedback] | None = None) -> JuryVerdict:
    return JuryVerdict(
        verdict=verdict,
        scores=JuryScores(
            fidelity=0.9, completeness=0.9, provenance_correctness=0.9, structural_validity=0.9
        ),
        feedback=feedback or [],
        retry_recommended=verdict is not Verdict.APPROVE,
        rationale="because",
    )


def _ingestion() -> IngestionResult:
    return IngestionResult(
        url="https://example.com/blog",  # type: ignore[arg-type]
        canonical_url="https://example.com/blog",  # type: ignore[arg-type]
        content_hash=_HASH,
        fetched_at=datetime(2025, 2, 1, tzinfo=UTC),
        fetch_method="http_get",
        word_count=100,
        publisher_domain="example.com",
        cached_path="/tmp/cache/x",
    )


# --- fakes recording the call path -----------------------------------------


class _FakeExtractor:
    """Records every extract() call and returns scripted specs in sequence."""

    def __init__(self, specs: list[AttackSpec]) -> None:
        self._specs = specs
        self.calls: list[str] = []  # the source_summary each call received

    async def extract(self, *, blog_content: str, source_summary: str) -> ExtractionResult:
        del blog_content
        self.calls.append(source_summary)
        # return the next scripted spec; repeat the last one once exhausted
        idx = min(len(self.calls) - 1, len(self._specs) - 1)
        return _result(self._specs[idx])


class _FakeJury:
    """Records every review() call and returns scripted verdicts in sequence."""

    def __init__(self, verdicts: list[JuryVerdict]) -> None:
        self._verdicts = verdicts
        self.calls = 0

    async def review(
        self,
        *,
        spec: AttackSpec,
        blog_content: str,
        lookups: list[ExternalLookupRecord] | None = None,
    ) -> JuryVerdict:
        del spec, blog_content, lookups
        idx = min(self.calls, len(self._verdicts) - 1)
        self.calls += 1
        return self._verdicts[idx]


def _validator() -> StaticSchemaValidator:
    return StaticSchemaValidator(registries=load_merged_registries())


# --- tests -----------------------------------------------------------------


async def test_clean_approve_runs_end_to_end_and_enriches() -> None:
    ext = _FakeExtractor([_spec(facets=["target:aws"])])
    jury = _FakeJury([_verdict(Verdict.APPROVE)])
    run = build_pipeline(extractor=ext, validator=_validator(), jury=jury)
    state = await run(PipelineState(blog_content="blog", source_summary="url=..."))

    assert state.status is PipelineStatus.SHIPPED
    assert state.enrichment is not None  # enrichment ran
    assert state.spec is not None
    assert state.structural_attempts == 1
    assert state.refinement_iterations == 0
    assert jury.calls == 1


async def test_run_pipeline_returns_outcome_for_clean_approve() -> None:
    ext = _FakeExtractor([_spec(facets=["target:aws"])])
    jury = _FakeJury([_verdict(Verdict.APPROVE)])
    outcome = await run_pipeline(
        ingestion=_ingestion(),
        blog_content="blog",
        extractor=ext,  # type: ignore[arg-type]
        validator=_validator(),
        jury=jury,  # type: ignore[arg-type]
    )
    assert outcome.status is PipelineStatus.SHIPPED
    assert outcome.low_jury_confidence is False
    assert isinstance(outcome.spec, AttackSpec)
    # the Ingestion metadata reached the Extractor's source_summary (the §3.3 contract)
    assert any("example.com" in c for c in ext.calls)


async def test_layer1_failure_routes_to_retry_not_refinement() -> None:
    # The Extractor keeps producing a Layer-1-invalid spec (unknown facet). The
    # orchestrator must re-run the EXTRACTOR (retry), never call the Jury
    # (refinement), and finally HALT with ValidationError on retry exhaustion.
    bad = _spec(facets=["target:bogus_unknown_cloud"])
    ext = _FakeExtractor([bad])
    jury = _FakeJury([_verdict(Verdict.APPROVE)])  # must never be consulted
    run = build_pipeline(
        extractor=ext, validator=_validator(), jury=jury, structural_retry_attempts=3
    )
    state = await run(PipelineState(blog_content="blog", source_summary="url=..."))

    assert state.status is PipelineStatus.HALTED_VALIDATION
    # retry path: the Extractor was re-run up to the budget...
    assert len(ext.calls) == 3
    # ...and the re-runs carried STRUCTURAL feedback (not refinement)...
    assert "STRUCTURAL VALIDATION FAILURE" in ext.calls[1]
    assert "STRUCTURAL VALIDATION FAILURE" in ext.calls[2]
    # ...and the Jury (the refinement gate) was never invoked.
    assert jury.calls == 0


async def test_layer1_retry_then_recovers() -> None:
    # First spec is Layer-1-invalid; the retry produces a valid one → proceeds.
    bad = _spec(facets=["target:bogus_unknown_cloud"])
    good = _spec(facets=["target:aws"])
    ext = _FakeExtractor([bad, good])
    jury = _FakeJury([_verdict(Verdict.APPROVE)])
    run = build_pipeline(extractor=ext, validator=_validator(), jury=jury)
    state = await run(PipelineState(blog_content="blog", source_summary="url=..."))

    assert state.status is PipelineStatus.SHIPPED
    assert state.structural_attempts == 2
    assert jury.calls == 1


async def test_layer1_exhaustion_raises_validation_error_via_run_pipeline() -> None:
    bad = _spec(facets=["target:bogus_unknown_cloud"])
    ext = _FakeExtractor([bad])
    jury = _FakeJury([_verdict(Verdict.APPROVE)])
    with pytest.raises(ValidationError) as exc_info:
        await run_pipeline(
            ingestion=_ingestion(),
            blog_content="blog",
            extractor=ext,  # type: ignore[arg-type]
            validator=_validator(),
            jury=jury,  # type: ignore[arg-type]
        )
    # the unresolved Layer-1 findings ride along for the run report
    assert exc_info.value.findings
    assert any("unknown_facet" in f for f in exc_info.value.findings)
    assert not isinstance(exc_info.value, JuryRejectionError)


async def test_jury_revise_bounded_then_ships_low_confidence() -> None:
    # The Jury always returns revise; the spec is always Layer-1-valid. The
    # refinement coordinator re-runs the Extractor up to the cap, then ships the
    # last spec with low_jury_confidence and the unresolved feedback.
    good = _spec(facets=["target:aws"])
    ext = _FakeExtractor([good])
    revise = _verdict(
        Verdict.REVISE,
        feedback=[JuryFieldFeedback(field_path="thesis.summary", problem="too vague")],
    )
    jury = _FakeJury([revise])
    run = build_pipeline(extractor=ext, validator=_validator(), jury=jury, refinement_cap=3)
    state = await run(PipelineState(blog_content="blog", source_summary="url=..."))

    assert state.status is PipelineStatus.SHIPPED_LOW_CONFIDENCE
    assert state.enrichment is not None  # still ships → enrichment ran
    # 1 initial extract + 3 refinement re-runs = 4 extract calls
    assert len(ext.calls) == 4
    assert state.refinement_iterations == 3
    # the refinement re-runs carried JURY (refinement) feedback, not structural
    assert "JURY REVISION REQUESTED" in ext.calls[1]
    assert state.unresolved_feedback
    assert any("thesis.summary" in item for item in state.unresolved_feedback)


async def test_jury_revise_then_approve_ships_clean() -> None:
    good = _spec(facets=["target:aws"])
    ext = _FakeExtractor([good])
    revise = _verdict(
        Verdict.REVISE,
        feedback=[JuryFieldFeedback(field_path="thesis.summary", problem="x")],
    )
    jury = _FakeJury([revise, _verdict(Verdict.APPROVE)])
    run = build_pipeline(extractor=ext, validator=_validator(), jury=jury)
    state = await run(PipelineState(blog_content="blog", source_summary="url=..."))

    assert state.status is PipelineStatus.SHIPPED
    assert state.refinement_iterations == 1
    assert jury.calls == 2


async def test_jury_reject_halts() -> None:
    good = _spec(facets=["target:aws"])
    ext = _FakeExtractor([good])
    reject = _verdict(
        Verdict.REJECT,
        feedback=[JuryFieldFeedback(field_path="chain.chain_steps[0]", problem="hallucinated")],
    )
    jury = _FakeJury([reject])
    with pytest.raises(JuryRejectionError):
        await run_pipeline(
            ingestion=_ingestion(),
            blog_content="blog",
            extractor=ext,  # type: ignore[arg-type]
            validator=_validator(),
            jury=jury,  # type: ignore[arg-type]
        )


async def test_low_confidence_outcome_carries_unresolved_feedback() -> None:
    good = _spec(facets=["target:aws"])
    ext = _FakeExtractor([good])
    revise = _verdict(
        Verdict.REVISE,
        feedback=[JuryFieldFeedback(field_path="thesis.summary", problem="vague")],
    )
    jury = _FakeJury([revise])
    outcome = await run_pipeline(
        ingestion=_ingestion(),
        blog_content="blog",
        extractor=ext,  # type: ignore[arg-type]
        validator=_validator(),
        jury=jury,  # type: ignore[arg-type]
        refinement_cap=2,
    )
    assert outcome.low_jury_confidence is True
    assert outcome.status is PipelineStatus.SHIPPED_LOW_CONFIDENCE
    assert outcome.unresolved_feedback
    assert outcome.refinement_iterations == 2


# --- headless guard --------------------------------------------------------


def test_headless_rejects_interactive() -> None:
    with pytest.raises(ValueError, match="--auto"):
        reject_interactive_when_headless(interactive=True, stdin_is_tty=False)


def test_interactive_with_tty_is_allowed() -> None:
    reject_interactive_when_headless(interactive=True, stdin_is_tty=True)


def test_auto_in_headless_is_allowed() -> None:
    reject_interactive_when_headless(interactive=False, stdin_is_tty=False)


# --- guards ----------------------------------------------------------------


def test_build_rejects_zero_structural_attempts() -> None:
    ext = _FakeExtractor([_spec()])
    jury = _FakeJury([_verdict(Verdict.APPROVE)])
    with pytest.raises(ValueError, match="structural_retry_attempts"):
        build_pipeline(
            extractor=ext, validator=_validator(), jury=jury, structural_retry_attempts=0
        )


def test_build_rejects_negative_refinement_cap() -> None:
    ext = _FakeExtractor([_spec()])
    jury = _FakeJury([_verdict(Verdict.APPROVE)])
    with pytest.raises(ValueError, match="refinement_cap"):
        build_pipeline(extractor=ext, validator=_validator(), jury=jury, refinement_cap=-1)


# --- checkpointer / resume (ADR 0040) --------------------------------------


class _CrashOnceJury:
    """Raises on its first review (a mid-node crash), then returns scripted verdicts."""

    def __init__(self, verdicts: list[JuryVerdict]) -> None:
        self._verdicts = verdicts
        self.calls = 0

    async def review(
        self,
        *,
        spec: AttackSpec,
        blog_content: str,
        lookups: list[ExternalLookupRecord] | None = None,
    ) -> JuryVerdict:
        del spec, blog_content, lookups
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("boom in jury")
        return self._verdicts[min(self.calls - 2, len(self._verdicts) - 1)]


async def test_checkpointer_survives_midnode_crash_and_resumes(tmp_path: Path) -> None:
    # A persistent sqlite checkpointer must let a run that crashed mid-pipeline resume
    # from the last completed node — extract + validate are NOT re-run. This run also
    # proves PipelineState (with its Pydantic-model fields) round-trips through the
    # checkpointer's serializer (it is persisted to sqlite, then restored).
    from cyberlab_gen.framework.checkpointing import open_sqlite_checkpointer

    db = tmp_path / "cp.sqlite"
    ext = _FakeExtractor([_spec(facets=["target:aws"])])
    jury = _CrashOnceJury([_verdict(Verdict.APPROVE)])
    initial = PipelineState(blog_content="blog", source_summary="url=...")

    # Run 1: the jury node raises after extract + validate have completed (checkpointed).
    async with open_sqlite_checkpointer(db) as saver:
        run = build_pipeline(extractor=ext, validator=_validator(), jury=jury, checkpointer=saver)
        with pytest.raises(RuntimeError, match="boom"):
            await run(initial, thread_id="run-A")
    assert ext.calls == ["url=..."]  # extractor ran exactly once before the crash

    # Run 2: same thread, fresh saver over the same db file (a new process would do
    # this). Resume with input=None → LangGraph picks up from the last checkpoint;
    # extract is NOT re-run, the jury retries.
    async with open_sqlite_checkpointer(db) as saver:
        run = build_pipeline(extractor=ext, validator=_validator(), jury=jury, checkpointer=saver)
        state = await run(None, thread_id="run-A")

    assert state.status is PipelineStatus.SHIPPED
    assert state.enrichment is not None  # the full state was restored + finished
    assert len(ext.calls) == 1  # extractor was NOT re-run on resume — proof of resume
    assert jury.calls == 2  # jury crashed once, then approved on the resumed super-step


async def test_no_checkpointer_keeps_prior_behaviour() -> None:
    # The default (no checkpointer) path is unchanged: run takes no thread and ships.
    ext = _FakeExtractor([_spec(facets=["target:aws"])])
    jury = _FakeJury([_verdict(Verdict.APPROVE)])
    run = build_pipeline(extractor=ext, validator=_validator(), jury=jury)
    state = await run(PipelineState(blog_content="blog", source_summary="url=..."))
    assert state.status is PipelineStatus.SHIPPED
