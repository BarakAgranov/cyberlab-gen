"""Tests for the Phase-1 orchestration state machine (``pipeline.md §3.1``, ADR 0023).

Covers the Task-6 exit criteria:

- a static-schema-invalid AttackSpec routes to the Extractor's *retry*, **not** the
  refinement coordinator (``validation.md §6.10``) — asserted by the call path:
  the Extractor is re-run on the same input feedback-kind=structural_retry, and
  the Jury is *never* invoked while static schema validation is red;
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

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError as PydanticValidationError

from cyberlab_gen.agents.extractor_jury.schema import JuryFieldFeedback, Verdict
from cyberlab_gen.errors import ValidationError
from cyberlab_gen.framework.orchestrator import (
    FeedbackKind,
    JuryRejectionError,
    PipelineState,
    PipelineStatus,
    RefinementFeedback,
    build_pipeline,
    reject_interactive_when_headless,
    run_pipeline,
)
from cyberlab_gen.schemas.attack_spec import AttackSpec
from cyberlab_gen.validators.static_schema_validator import (
    StaticSchemaCode,
    StaticSchemaFinding,
)
from tests.unit.framework.pipeline_fakes import (
    CrashOnceJury,
    FakeExtractor,
    FakeJury,
    make_ingestion,
    make_spec,
    make_validator,
    make_verdict,
)

if TYPE_CHECKING:
    from pathlib import Path

# The pipeline test doubles + builders live in a shared, typed module; alias them to the
# private names this module's existing tests already use so the call sites stay unchanged.
_CrashOnceJury = CrashOnceJury
_FakeExtractor = FakeExtractor
_FakeJury = FakeJury
_ingestion = make_ingestion
_spec = make_spec
_validator = make_validator
_verdict = make_verdict


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
    assert not ext.refine_calls  # a clean first-run approve never patches


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


async def test_static_schema_failure_routes_to_retry_not_refinement() -> None:
    # The Extractor keeps producing a static-schema-invalid spec (unknown facet). The
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
    # ...via FULL extraction (structural retry), never the targeted-patch path (refine)...
    assert not ext.refine_calls
    # ...and the re-runs carried STRUCTURAL feedback (not refinement)...
    assert "STRUCTURAL VALIDATION FAILURE" in ext.calls[1]
    assert "STRUCTURAL VALIDATION FAILURE" in ext.calls[2]
    # ...and the Jury (the refinement gate) was never invoked.
    assert jury.calls == 0


async def test_static_schema_retry_then_recovers() -> None:
    # First spec is static-schema-invalid; the retry produces a valid one → proceeds.
    bad = _spec(facets=["target:bogus_unknown_cloud"])
    good = _spec(facets=["target:aws"])
    ext = _FakeExtractor([bad, good])
    jury = _FakeJury([_verdict(Verdict.APPROVE)])
    run = build_pipeline(extractor=ext, validator=_validator(), jury=jury)
    state = await run(PipelineState(blog_content="blog", source_summary="url=..."))

    assert state.status is PipelineStatus.SHIPPED
    assert state.structural_attempts == 2
    assert jury.calls == 1


async def test_static_schema_exhaustion_raises_validation_error_via_run_pipeline() -> None:
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
    # the unresolved static-schema findings ride along for the run report
    assert exc_info.value.findings
    assert any("unknown_facet" in f for f in exc_info.value.findings)
    assert not isinstance(exc_info.value, JuryRejectionError)


async def test_jury_revise_bounded_then_ships_low_confidence() -> None:
    # The Jury always returns revise; the patched spec is always static-schema-valid. The
    # refinement coordinator re-runs the Extractor via TARGETED PATCH (refine) up to the cap,
    # then ships the last spec with low_jury_confidence and the unresolved feedback. R1: the
    # flagged field never gets satisfied, so termination comes from the cap, never a spin.
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
    # 1 initial full extract; the 3 refinement iterations are targeted PATCHES, not extracts
    assert len(ext.calls) == 1
    assert len(ext.refine_calls) == 3
    assert state.refinement_iterations == 3
    # the patch path received the structured jury feedback (field path intact)
    assert ext.refine_calls[0][1][0].field_path == "thesis.summary"
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
    # the single re-run was a targeted patch, not a second full extract
    assert len(ext.calls) == 1
    assert len(ext.refine_calls) == 1


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


# --- A2: RefinementFeedback carries typed *contents*, not strings (ADR 0048) ---
#
# The cross-stage feedback object must retain the STRUCTURED findings — the jury's
# JuryFieldFeedback (incl. suggested_fix, previously discarded) and the static-schema
# StaticSchemaFinding — and render to prompt text only at the prompt boundary. These
# tests pin: (1) structured round-trip incl. suggested_fix, (2) render() output,
# (3) the kind↔payload invariant, and (4) that the structure (e.g. suggested_fix)
# survives the producer→Extractor boundary rather than being flattened away.


def test_refinement_feedback_round_trips_jury_feedback_with_suggested_fix() -> None:
    fb = RefinementFeedback(
        kind=FeedbackKind.REFINEMENT,
        jury_feedback=[
            JuryFieldFeedback(
                field_path="thesis.summary", problem="too vague", suggested_fix="quote §2 verbatim"
            )
        ],
    )
    restored = RefinementFeedback.model_validate(fb.model_dump())
    assert restored == fb
    assert isinstance(restored.jury_feedback[0], JuryFieldFeedback)
    # the suggested_fix is retained in the structured form (it used to be dropped)
    assert restored.jury_feedback[0].suggested_fix == "quote §2 verbatim"


def test_refinement_feedback_round_trips_static_findings() -> None:
    fb = RefinementFeedback(
        kind=FeedbackKind.STRUCTURAL_RETRY,
        static_findings=[
            StaticSchemaFinding(
                code=StaticSchemaCode.UNKNOWN_FACET, location="facets[0]", detail="no such facet"
            )
        ],
    )
    restored = RefinementFeedback.model_validate(fb.model_dump())
    assert restored == fb
    assert isinstance(restored.static_findings[0], StaticSchemaFinding)
    assert restored.static_findings[0].code is StaticSchemaCode.UNKNOWN_FACET


def test_refinement_render_structural_contains_header_and_finding_render() -> None:
    fb = RefinementFeedback(
        kind=FeedbackKind.STRUCTURAL_RETRY,
        static_findings=[
            StaticSchemaFinding(
                code=StaticSchemaCode.UNKNOWN_FACET, location="facets[0]", detail="no such facet"
            )
        ],
    )
    rendered = fb.render()
    assert "STRUCTURAL VALIDATION FAILURE" in rendered
    # the structured finding's own one-line render reaches the prompt verbatim
    assert "unknown_facet@facets[0]: no such facet" in rendered


def test_refinement_render_refinement_includes_field_path_problem_and_suggested_fix() -> None:
    fb = RefinementFeedback(
        kind=FeedbackKind.REFINEMENT,
        jury_feedback=[
            JuryFieldFeedback(
                field_path="thesis.summary",
                problem="too vague",
                suggested_fix="quote the RCE paragraph",
            )
        ],
    )
    rendered = fb.render()
    assert "JURY REVISION REQUESTED" in rendered
    assert "thesis.summary: too vague" in rendered
    assert "quote the RCE paragraph" in rendered


def test_refinement_render_omits_suggested_fix_clause_when_absent() -> None:
    fb = RefinementFeedback(
        kind=FeedbackKind.REFINEMENT,
        jury_feedback=[JuryFieldFeedback(field_path="thesis.summary", problem="too vague")],
    )
    rendered = fb.render()
    assert "thesis.summary: too vague" in rendered
    assert "suggested fix" not in rendered


def test_refinement_feedback_rejects_refinement_carrying_static_findings() -> None:
    with pytest.raises(PydanticValidationError):
        RefinementFeedback(
            kind=FeedbackKind.REFINEMENT,
            static_findings=[
                StaticSchemaFinding(
                    code=StaticSchemaCode.UNKNOWN_FACET, location="facets[0]", detail="x"
                )
            ],
        )


def test_refinement_feedback_rejects_structural_carrying_jury_feedback() -> None:
    with pytest.raises(PydanticValidationError):
        RefinementFeedback(
            kind=FeedbackKind.STRUCTURAL_RETRY,
            jury_feedback=[JuryFieldFeedback(field_path="thesis.summary", problem="x")],
        )


def test_refinement_feedback_rejects_empty_matching_payload() -> None:
    # a structural retry with no findings, and a refinement with no feedback, are both
    # meaningless — the matching payload must be non-empty.
    with pytest.raises(PydanticValidationError):
        RefinementFeedback(kind=FeedbackKind.STRUCTURAL_RETRY)
    with pytest.raises(PydanticValidationError):
        RefinementFeedback(kind=FeedbackKind.REFINEMENT)


async def test_jury_revise_routes_structured_feedback_to_refine() -> None:
    # A1: a jury revise routes to the targeted-patch path (refine), which receives the
    # structured JuryFieldFeedback directly — incl. suggested_fix — rather than a stringified
    # addendum folded into a full re-extract prompt. (Replaces the A2-era prompt-render check,
    # which pinned the now-superseded full-re-extraction behaviour.)
    good = _spec(facets=["target:aws"])
    ext = _FakeExtractor([good])
    revise = _verdict(
        Verdict.REVISE,
        feedback=[
            JuryFieldFeedback(
                field_path="thesis.summary",
                problem="too vague",
                suggested_fix="quote the blog's privilege-escalation paragraph",
            )
        ],
    )
    jury = _FakeJury([revise, _verdict(Verdict.APPROVE)])
    run = build_pipeline(extractor=ext, validator=_validator(), jury=jury)
    await run(PipelineState(blog_content="blog", source_summary="url=..."))

    # the re-run was a patch, not a full extract; refine got the prior spec + typed feedback
    assert len(ext.calls) == 1
    assert len(ext.refine_calls) == 1
    prior, fb = ext.refine_calls[0]
    assert prior.facets == ["target:aws"]
    assert fb[0].field_path == "thesis.summary"
    assert fb[0].suggested_fix == "quote the blog's privilege-escalation paragraph"


async def test_unresolved_feedback_preserves_suggested_fix() -> None:
    # On cap exhaustion the run-report `unresolved_feedback` is rendered from the
    # structured feedback, so suggested_fix survives into the report too.
    good = _spec(facets=["target:aws"])
    ext = _FakeExtractor([good])
    revise = _verdict(
        Verdict.REVISE,
        feedback=[
            JuryFieldFeedback(
                field_path="thesis.summary", problem="too vague", suggested_fix="add a citation"
            )
        ],
    )
    jury = _FakeJury([revise])
    run = build_pipeline(extractor=ext, validator=_validator(), jury=jury, refinement_cap=1)
    state = await run(PipelineState(blog_content="blog", source_summary="url=..."))

    assert state.status is PipelineStatus.SHIPPED_LOW_CONFIDENCE
    assert any("thesis.summary" in item for item in state.unresolved_feedback)
    assert any("add a citation" in item for item in state.unresolved_feedback)
