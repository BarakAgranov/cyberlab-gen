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

from cyberlab_gen.agents.extractor_jury.jury import DEFAULT_RUBRIC_FLOOR
from cyberlab_gen.agents.extractor_jury.schema import JuryFieldFeedback, JuryScores, Verdict
from cyberlab_gen.errors import ValidationError
from cyberlab_gen.framework.enrichment import EnrichmentConfig, NvdCveData
from cyberlab_gen.framework.orchestrator import (
    GLOBAL_ITERATION_CAP,
    GRAPH_RECURSION_LIMIT,
    FeedbackKind,
    JuryInconsistencyError,
    JuryRejectionError,
    PipelineState,
    PipelineStatus,
    RefinementFeedback,
    build_pipeline,
    reject_interactive_when_headless,
    run_pipeline,
)
from cyberlab_gen.schemas.attack_spec import (
    AttackSpec,
    CveReference,
    ExternalRefsBlock,
)
from cyberlab_gen.schemas.enums import CitationKind, ProvenanceSource, Severity
from cyberlab_gen.schemas.provenance import (
    CitationBlock,
    Provenance,
    ProvenanceFloat,
    ProvenanceString,
)
from cyberlab_gen.validators.grounding_validator import GroundingCode
from cyberlab_gen.validators.static_schema_validator import (
    StaticSchemaCode,
    StaticSchemaFinding,
)
from tests.unit.framework.pipeline_fakes import (
    ChangingBadExtractor,
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
    # The Extractor keeps producing static-schema-invalid specs (each a DIFFERENT unknown
    # facet, so findings change and the no-progress bail (ADR 0057) never fires). The
    # orchestrator must re-run the EXTRACTOR (retry), never call the Jury (refinement), and
    # finally HALT with ValidationError on retry exhaustion (the full budget is used).
    bad1 = _spec(facets=["target:bogus_unknown_one"])
    bad2 = _spec(facets=["target:bogus_unknown_two"])
    bad3 = _spec(facets=["target:bogus_unknown_three"])
    ext = _FakeExtractor([bad1, bad2, bad3])
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


def _subfloor_scores() -> JuryScores:
    """An otherwise-clean score set with one dimension below the default rubric floor."""
    return JuryScores(
        fidelity=0.1, completeness=0.9, provenance_correctness=0.9, structural_validity=0.9
    )


async def test_jury_approve_with_subfloor_score_halts_not_ships() -> None:
    """A self-contradictory ``approve`` (verdict=approve, a rubric dimension below the floor)
    must NOT ship — the framework reads ``verdict.scores`` against the floor and mechanically
    refuses it (ADR 0067, the defense-in-depth backstop of ``architecture.md §1.6``). Before
    the fix this shipped at full confidence because ``jury_node`` routed on ``verdict.verdict``
    alone and never read the scores.
    """
    ext = _FakeExtractor([_spec(facets=["target:aws"])])
    jury = _FakeJury([_verdict(Verdict.APPROVE, scores=_subfloor_scores())])
    run = build_pipeline(extractor=ext, validator=_validator(), jury=jury)
    state = await run(PipelineState(blog_content="blog", source_summary="url=..."))

    assert state.status is PipelineStatus.HALTED_JURY_INCONSISTENT
    assert state.spec is not None  # retained for the run report; just not shipped


async def test_jury_approve_at_floor_still_ships() -> None:
    """The backstop is an inclusive floor, not a strict ceiling: an approve whose lowest
    dimension is exactly at the floor is consistent and still ships (boundary, ADR 0067).
    """
    ext = _FakeExtractor([_spec(facets=["target:aws"])])
    at_floor = JuryScores(
        fidelity=DEFAULT_RUBRIC_FLOOR,
        completeness=0.9,
        provenance_correctness=0.9,
        structural_validity=0.9,
    )
    jury = _FakeJury([_verdict(Verdict.APPROVE, scores=at_floor)])
    run = build_pipeline(extractor=ext, validator=_validator(), jury=jury)
    state = await run(PipelineState(blog_content="blog", source_summary="url=..."))

    assert state.status is PipelineStatus.SHIPPED


async def test_run_pipeline_raises_jury_inconsistency_on_subfloor_approve() -> None:
    """``run_pipeline`` raises ``JuryInconsistencyError`` (a reject-class quality halt) when an
    approve contradicts its own sub-floor scores (ADR 0067).
    """
    ext = _FakeExtractor([_spec(facets=["target:aws"])])
    jury = _FakeJury([_verdict(Verdict.APPROVE, scores=_subfloor_scores())])
    with pytest.raises(JuryInconsistencyError):
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


# --- refinement vs structural-retry budgets are INDEPENDENT ----------
#
# architecture.md's retry/refinement table specifies separate budgets. The orchestrator
# must not charge a jury-revise refinement re-run against the structural-retry counter
# (or vice versa) — else one mechanism silently steals the other's budget.


async def test_refinement_does_not_consume_structural_attempts() -> None:
    # A jury-revise refinement re-runs the Extractor via `refine`, but `structural_attempts`
    # must count only first/structural extracts — never the refinement re-run.
    good = _spec(facets=["target:aws"])
    ext = _FakeExtractor([good])
    revise = _verdict(
        Verdict.REVISE,
        feedback=[JuryFieldFeedback(field_path="thesis.summary", problem="vague")],
    )
    jury = _FakeJury([revise, _verdict(Verdict.APPROVE)])
    run = build_pipeline(extractor=ext, validator=_validator(), jury=jury)
    state = await run(PipelineState(blog_content="blog", source_summary="url=..."))

    assert state.status is PipelineStatus.SHIPPED
    assert state.refinement_iterations == 1
    assert len(ext.refine_calls) == 1
    # only the first extract counted as a structural attempt; the refine did NOT bump it
    assert state.structural_attempts == 1


async def test_structural_budget_intact_after_refinement() -> None:
    # Behavioral: after a refinement re-run, a subsequent static-schema failure still has the
    # FULL structural-retry budget. Here the refine produces a static-schema-INVALID patch;
    # with a SHARED counter the refine would have spent the only structural retry and the run
    # would HALT — with independent counters the structural retry still fires and ships.
    good = _spec(facets=["target:aws"])
    bad = _spec(facets=["target:bogus_unknown_cloud"])
    # extract#1 -> good (passes); jury revise -> refine -> bad (fails static); structural
    # retry -> extract#2 -> good (passes) -> jury approve -> ship.
    ext = _FakeExtractor([good, good], refine_specs=[bad])
    jury = _FakeJury(
        [
            _verdict(
                Verdict.REVISE,
                feedback=[JuryFieldFeedback(field_path="thesis.summary", problem="x")],
            ),
            _verdict(Verdict.APPROVE),
        ]
    )
    run = build_pipeline(
        extractor=ext, validator=_validator(), jury=jury, structural_retry_attempts=2
    )
    state = await run(PipelineState(blog_content="blog", source_summary="url=..."))

    assert state.status is PipelineStatus.SHIPPED
    assert state.refinement_iterations == 1
    assert len(ext.refine_calls) == 1
    # first extract + the structural retry after the bad patch (the refine is not an extract)
    assert len(ext.calls) == 2
    assert state.structural_attempts == 2


# --- global iteration cap + LangGraph recursion_limit backstop ----------
#
# Nothing must bound total pipeline iterations end-to-end except the documented global cap
# (architecture.md §6 "Total iteration cap (default 20)"), with the LangGraph recursion_limit
# as a final graph-level backstop regardless of per-node caps.


async def test_global_iteration_cap_bounds_pathological_loop() -> None:
    # With the per-node structural cap raised far past it, the GLOBAL iteration cap is what
    # bounds total iterations — a pathological loop halts at the global cap, not at the (much
    # larger) per-node budget. The Extractor emits a DIFFERENT invalid spec each time so the
    # no-progress bail (ADR 0057) never fires and the loop reaches the cap. (This also implies
    # recursion_limit > the cap's super-steps, since the clean halt fires instead of a
    # GraphRecursionError.)
    ext = ChangingBadExtractor()
    jury = _FakeJury([_verdict(Verdict.APPROVE)])  # never reached
    run = build_pipeline(
        extractor=ext,
        validator=_validator(),
        jury=jury,
        structural_retry_attempts=100,
        global_iteration_cap=20,
    )
    state = await run(PipelineState(blog_content="blog", source_summary="url=..."))

    assert state.status is PipelineStatus.HALTED_VALIDATION
    assert "iteration cap" in (state.halt_reason or "").lower()
    assert state.total_iterations == 20
    assert len(ext.calls) == 20  # bounded by the global cap, not the 100-attempt structural cap


def test_graph_recursion_limit_is_the_documented_backstop() -> None:
    # The recursion_limit is a fixed multiple of the global cap, sized so the semantic cap
    # (which halts cleanly with a clear reason) always binds first in a legitimate run. The
    # multiplier is 6x after the ADR-0052/0061 reorder added enrich + grounding to the per-iter
    # path (extract -> validate -> enrich -> grounding -> jury = 5 super-steps/iteration).
    assert GRAPH_RECURSION_LIMIT == 6 * GLOBAL_ITERATION_CAP


async def test_recursion_limit_bounds_graph_when_app_caps_disabled() -> None:
    # Final backstop: with BOTH the per-node and global caps effectively disabled, the
    # LangGraph recursion_limit still bounds the graph — a runaway loop raises
    # GraphRecursionError rather than spinning forever.
    from langgraph.errors import GraphRecursionError

    ext = ChangingBadExtractor()  # changing findings -> no-progress bail never fires
    jury = _FakeJury([_verdict(Verdict.APPROVE)])
    run = build_pipeline(
        extractor=ext,
        validator=_validator(),
        jury=jury,
        structural_retry_attempts=10_000,
        global_iteration_cap=10_000,
    )
    with pytest.raises(GraphRecursionError):
        await run(PipelineState(blog_content="blog", source_summary="url=..."))


# --- no-progress early-bail on the structural-retry loop (ADR 0057) ---------
#
# A structural retry that reproduces the IDENTICAL finding set is not converging; halting
# immediately (rather than spending the rest of the ~$3-4 retry budget re-extracting toward
# a finding that can never clear) mirrors the ADR-0032 no-progress bail on the call surface.


async def test_no_progress_structural_retry_bails_early() -> None:
    # The Extractor keeps producing the same failing spec -> identical findings each attempt.
    # The loop must halt after the second identical attempt, NOT grind to the full budget.
    bad = _spec(facets=["target:bogus_unknown_cloud"])
    ext = _FakeExtractor([bad])  # same failing spec every attempt
    jury = _FakeJury([_verdict(Verdict.APPROVE)])  # never reached
    run = build_pipeline(
        extractor=ext, validator=_validator(), jury=jury, structural_retry_attempts=5
    )
    state = await run(PipelineState(blog_content="blog", source_summary="url=..."))

    assert state.status is PipelineStatus.HALTED_VALIDATION
    assert "no progress" in (state.halt_reason or "").lower()
    # bailed after the SECOND identical attempt, not the full budget of 5
    assert len(ext.calls) == 2
    assert jury.calls == 0


async def test_progressing_structural_retry_is_not_bailed() -> None:
    # A run whose findings CHANGE each attempt is making progress and must NOT be bailed — it
    # uses the full structural-retry budget, then halts on exhaustion (not on no-progress).
    bad1 = _spec(facets=["target:bogus_cloud_one"])
    bad2 = _spec(facets=["target:bogus_cloud_two"])
    bad3 = _spec(facets=["target:bogus_cloud_three"])
    ext = _FakeExtractor([bad1, bad2, bad3])  # a different finding each attempt
    jury = _FakeJury([_verdict(Verdict.APPROVE)])
    run = build_pipeline(
        extractor=ext, validator=_validator(), jury=jury, structural_retry_attempts=3
    )
    state = await run(PipelineState(blog_content="blog", source_summary="url=..."))

    assert state.status is PipelineStatus.HALTED_VALIDATION  # exhaustion, not no-progress
    assert "no progress" not in (state.halt_reason or "").lower()
    assert len(ext.calls) == 3  # full budget used (findings kept changing)


# --- A3/B1: one orchestrator-owned grounding stack (ADR 0051/0060) ----------
#
# The mechanical grounding checks (search-before-claim / MITRE / CVE) used to run inside
# the Extractor on its own hidden hallucination_retry budget, and the jury independently
# re-ran the search-before-claim trace check. They are now ONE orchestrator-owned stack:
# the orchestrator routes its findings (retry on a hallucination) and the jury CONSUMES
# them without re-deriving. These tests pin that ownership move.


def _bcite() -> CitationBlock:
    return CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="§2")


def _ungrounded_cve_spec() -> AttackSpec:
    """A valid spec whose CVE severity claims ``external_api`` with NO matching trace.

    The grounding stack flags this as search-before-claim (a hallucination) — a
    retry-triggering finding the orchestrator must route, not the Extractor.
    """
    spec = _spec(facets=["target:aws"])
    spec.external_references = ExternalRefsBlock(
        cves=[
            CveReference(
                cve_id="CVE-2024-9999",  # type: ignore[arg-type]
                description=ProvenanceString(
                    value="v", source=ProvenanceSource.BLOG_EXPLICIT, citations=[_bcite()]
                ),
                severity=Provenance[Severity](
                    value=Severity.HIGH,
                    source=ProvenanceSource.EXTERNAL_API,
                    citations=[
                        _bcite(),
                        CitationBlock(
                            kind=CitationKind.EXTERNAL_API_RESPONSE, reference="nvd:CVE-2024-9999"
                        ),
                    ],
                ),
            )
        ]
    )
    return spec


def _structure_finding_spec() -> AttackSpec:
    """A valid spec with an external_api field missing its api-response citation.

    Produces an informational PROVENANCE_STRUCTURE finding (not a CVE, so no
    search-before-claim) — it must NOT trigger a retry and must reach the jury.
    """
    spec = _spec(facets=["target:aws"])
    spec.thesis.summary = Provenance[str](  # type: ignore[union-attr]
        value="an inferred-but-api-sourced summary",
        source=ProvenanceSource.EXTERNAL_API,
        citations=[_bcite()],  # blog citation only — missing the external_api_response citation
    )
    return spec


async def test_grounding_failure_routes_orchestrator_owned_retry_then_recovers() -> None:
    # extract#1 -> ungrounded external_api CVE (search-before-claim) -> the ORCHESTRATOR
    # routes a retry (the Extractor did NOT self-validate) -> extract#2 -> clean -> jury -> ship.
    ext = _FakeExtractor([_ungrounded_cve_spec(), _spec(facets=["target:aws"])])
    jury = _FakeJury([_verdict(Verdict.APPROVE)])
    run = build_pipeline(extractor=ext, validator=_validator(), jury=jury)
    state = await run(PipelineState(blog_content="blog", source_summary="url=..."))

    assert state.status is PipelineStatus.SHIPPED
    assert len(ext.calls) == 2  # the orchestrator re-ran the Extractor on the grounding finding
    assert state.grounding_attempts == 1
    # the grounding retry carried GROUNDING feedback (a full re-extract, not a refine patch)
    assert not ext.refine_calls
    assert "GROUNDING / SEARCH-BEFORE-CLAIM FAILURE" in ext.calls[1]
    assert jury.calls == 1  # the jury only saw the clean, recovered spec


async def test_grounding_retry_does_not_consume_structural_budget() -> None:
    # The grounding retry must be bounded by its OWN counter, never the structural one (the refinement budget).
    ext = _FakeExtractor([_ungrounded_cve_spec(), _spec(facets=["target:aws"])])
    jury = _FakeJury([_verdict(Verdict.APPROVE)])
    run = build_pipeline(extractor=ext, validator=_validator(), jury=jury)
    state = await run(PipelineState(blog_content="blog", source_summary="url=..."))

    assert state.status is PipelineStatus.SHIPPED
    assert state.grounding_attempts == 1
    assert state.structural_attempts == 1  # the grounding retry did NOT bump this


async def test_persistent_grounding_failure_halts_orchestrator_owned() -> None:
    # The Extractor keeps emitting the same ungrounded spec -> the ORCHESTRATOR halts the run
    # (HALTED_VALIDATION), and the jury is NEVER consulted. Proves the orchestrator owns the
    # budget + the halt (the old behaviour raised ExtractionError hidden inside extract()).
    ext = _FakeExtractor([_ungrounded_cve_spec()])  # same ungrounded spec every attempt
    jury = _FakeJury([_verdict(Verdict.APPROVE)])  # must never be consulted
    run = build_pipeline(
        extractor=ext, validator=_validator(), jury=jury, grounding_retry_attempts=3
    )
    state = await run(PipelineState(blog_content="blog", source_summary="url=..."))

    assert state.status is PipelineStatus.HALTED_VALIDATION
    assert "grounding" in (state.halt_reason or "").lower()
    assert jury.calls == 0


async def test_jury_consumes_grounding_findings_without_rederiving() -> None:
    # An informational provenance-structure finding (no retry) must reach the jury as the
    # orchestrator-computed findings set — the jury consumes it, it does not re-derive it.
    ext = _FakeExtractor([_structure_finding_spec()])
    jury = _FakeJury([_verdict(Verdict.APPROVE)])
    run = build_pipeline(extractor=ext, validator=_validator(), jury=jury)
    state = await run(PipelineState(blog_content="blog", source_summary="url=..."))

    assert state.status is PipelineStatus.SHIPPED
    assert len(ext.calls) == 1  # informational finding did NOT trigger a retry
    assert jury.calls == 1
    # the jury received the orchestrator's grounding findings (not None) at review time
    seen = jury.reviewed_findings[0]
    assert seen is not None
    assert any(f.code is GroundingCode.PROVENANCE_STRUCTURE for f in seen)


async def test_uncatalogued_mitre_ships_no_grounding_retry() -> None:
    # POST-0058 GUARD: a well-formed-but-uncatalogued MITRE id (T1195) must produce NO grounding
    # finding and ship — proving no seed-membership hard-gate was reintroduced in the relocation.
    spec = _spec(facets=["target:aws"])
    spec.chain.chain_steps[0].techniques.mitre = ["T1195"]  # type: ignore[union-attr,list-item]
    ext = _FakeExtractor([spec])
    jury = _FakeJury([_verdict(Verdict.APPROVE)])
    run = build_pipeline(extractor=ext, validator=_validator(), jury=jury)
    state = await run(PipelineState(blog_content="blog", source_summary="url=..."))

    assert state.status is PipelineStatus.SHIPPED
    assert len(ext.calls) == 1  # no retry
    assert state.grounding_attempts == 0
    assert state.grounding is not None
    assert state.grounding.findings == []


def test_grounding_retry_feedback_round_trips_and_renders() -> None:
    from cyberlab_gen.validators.grounding_validator import GroundingFinding

    fb = RefinementFeedback(
        kind=FeedbackKind.GROUNDING_RETRY,
        grounding_findings=[
            GroundingFinding(
                code=GroundingCode.SEARCH_BEFORE_CLAIM,
                location="external_references.cves[0].severity",
                detail="no external_lookup call recorded for CVE-2024-9999",
            )
        ],
    )
    restored = RefinementFeedback.model_validate(fb.model_dump())
    assert restored == fb
    rendered = fb.render()
    assert "GROUNDING / SEARCH-BEFORE-CLAIM FAILURE" in rendered
    assert "search_before_claim@external_references.cves[0].severity" in rendered


def test_grounding_retry_feedback_rejects_wrong_payload() -> None:
    with pytest.raises(PydanticValidationError):
        RefinementFeedback(
            kind=FeedbackKind.GROUNDING_RETRY,
            static_findings=[
                StaticSchemaFinding(
                    code=StaticSchemaCode.UNKNOWN_FACET, location="facets[0]", detail="x"
                )
            ],
        )
    with pytest.raises(PydanticValidationError):
        RefinementFeedback(kind=FeedbackKind.GROUNDING_RETRY)  # empty matching payload


# --- C1: enrichment runs BEFORE the jury (shipped == reviewed, ADR 0052/0061) ----


class _FakeNvd:
    """A fake NvdClient that always returns a CRITICAL 9.0 record (enrichment rewrites to it)."""

    def lookup_cve(self, cve_id: str) -> NvdCveData:
        return NvdCveData(cve_id=cve_id, cvss_score=9.0, cvss_severity="CRITICAL")


def _blog_cve_spec(cvss: float = 5.0) -> AttackSpec:
    """A spec with a blog_explicit CVE cvss_score that enrichment will rewrite to external_api."""
    spec = _spec(facets=["target:aws"])
    spec.external_references = ExternalRefsBlock(
        cves=[
            CveReference(
                cve_id="CVE-2021-44228",  # type: ignore[arg-type]
                description=ProvenanceString(
                    value="log4shell", source=ProvenanceSource.BLOG_EXPLICIT, citations=[_bcite()]
                ),
                cvss_score=ProvenanceFloat(
                    value=cvss, source=ProvenanceSource.BLOG_EXPLICIT, citations=[_bcite()]
                ),
            )
        ]
    )
    return spec


def _enrich_cfg() -> EnrichmentConfig:
    return EnrichmentConfig(nvd_client=_FakeNvd())


async def test_enrichment_runs_before_the_jury() -> None:
    # ADR 0052/0061: the jury must review the ENRICHED spec (shipped == reviewed). With an NVD
    # client wired, the blog_explicit cvss_score is rewritten to external_api + framework_enriched
    # BEFORE the jury sees it. The framework_enriched mark also keeps the grounding stack from
    # false-flagging the framework's own call (the C1<->A3/B1 interlock).
    ext = _FakeExtractor([_blog_cve_spec()])
    jury = _FakeJury([_verdict(Verdict.APPROVE)])
    run = build_pipeline(
        extractor=ext, validator=_validator(), jury=jury, enrichment_config=_enrich_cfg()
    )
    state = await run(PipelineState(blog_content="blog", source_summary="url=..."))

    assert state.status is PipelineStatus.SHIPPED
    reviewed = jury.reviewed_specs[0].external_references
    assert reviewed is not None
    cvss = reviewed.cves[0].cvss_score
    assert cvss is not None
    assert cvss.source is ProvenanceSource.EXTERNAL_API  # the jury saw the ENRICHED value
    assert cvss.framework_enriched is True
    assert cvss.value == 9.0  # the authoritative NVD value, not the blog's 5.0


async def test_refinement_re_runs_enrichment_before_re_review() -> None:
    # ADR 0052/0061: on a jury revise, the patched spec is re-enriched BEFORE the jury
    # re-reviews, so the invariant holds across refinement iterations. The refine echoes the
    # prior (enriched) spec; the re-review must still see an enriched spec.
    ext = _FakeExtractor([_blog_cve_spec()])
    jury = _FakeJury(
        [
            _verdict(Verdict.REVISE, feedback=[JuryFieldFeedback(field_path="x", problem="p")]),
            _verdict(Verdict.APPROVE),
        ]
    )
    run = build_pipeline(
        extractor=ext, validator=_validator(), jury=jury, enrichment_config=_enrich_cfg()
    )
    state = await run(PipelineState(blog_content="blog", source_summary="url=..."))

    assert state.status is PipelineStatus.SHIPPED
    assert jury.calls == 2
    # BOTH jury reviews saw an enriched spec (enrichment re-ran on the patched spec)
    for reviewed in jury.reviewed_specs:
        refs = reviewed.external_references
        assert refs is not None and refs.cves[0].cvss_score is not None
        assert refs.cves[0].cvss_score.framework_enriched is True


async def test_refinement_preserves_prior_enrichment_discrepancy() -> None:
    # ADR 0085 (regression for the ADR-0082 over-broad reset): a blog-vs-NVD MATERIAL discrepancy
    # established on the FIRST enrichment must SURVIVE a jury revise. The blanket neutralize at the
    # extract seam used to wipe the prior-iteration discrepancy off the merged refine output, and
    # re-enrichment (the field is now external_api, no blog_explicit value) could not re-detect it
    # — silently dropping a blog-vs-API disagreement the first jury had seen.
    ext = _FakeExtractor([_blog_cve_spec(cvss=5.0)])  # blog 5.0 vs NVD 9.0 = cross-tier MATERIAL
    jury = _FakeJury(
        [
            _verdict(Verdict.REVISE, feedback=[JuryFieldFeedback(field_path="x", problem="p")]),
            _verdict(Verdict.APPROVE),
        ]
    )
    run = build_pipeline(
        extractor=ext, validator=_validator(), jury=jury, enrichment_config=_enrich_cfg()
    )
    state = await run(PipelineState(blog_content="blog", source_summary="url=..."))

    assert state.status is PipelineStatus.SHIPPED
    assert jury.calls == 2
    # The FIRST review saw the discrepancy ...
    first = jury.reviewed_specs[0].external_references
    assert first is not None and first.cves[0].cvss_score is not None
    assert first.cves[0].cvss_score.discrepancy_with_blog is True
    # ... and the SHIPPED spec must still carry it: field-level marks AND the top-level index.
    shipped = state.spec
    assert shipped is not None
    refs = shipped.external_references
    assert refs is not None and refs.cves[0].cvss_score is not None
    score = refs.cves[0].cvss_score
    assert score.discrepancy_with_blog is True
    assert score.overridden_blog_value == 5.0
    assert score.discrepancy_classification is not None
    assert any(
        "CVE-2021-44228" in md.field_path and "cvss_score" in md.field_path
        for md in shipped.material_discrepancies
    ), f"the material_discrepancies index lost the cvss entry: {shipped.material_discrepancies}"


async def test_refine_producing_ungrounded_spec_routes_grounding_retry() -> None:
    # R2 (whole-spec re-check, ADR 0051/0060): the Extractor's refine() no longer self-checks
    # grounding; a jury-revise PATCH that introduces an ungrounded external_api field must be
    # caught by the ORCHESTRATOR's grounding stack on the patched spec, which then routes a
    # grounding retry (a full re-extract) that recovers. Proves R2 coverage survives the
    # relocation without a hidden Extractor loop.
    ext = _FakeExtractor([_spec(facets=["target:aws"])], refine_specs=[_ungrounded_cve_spec()])
    jury = _FakeJury(
        [
            _verdict(Verdict.REVISE, feedback=[JuryFieldFeedback(field_path="x", problem="p")]),
            _verdict(Verdict.APPROVE),
        ]
    )
    run = build_pipeline(extractor=ext, validator=_validator(), jury=jury)
    state = await run(PipelineState(blog_content="blog", source_summary="url=..."))

    assert state.status is PipelineStatus.SHIPPED
    assert len(ext.refine_calls) == 1  # the jury-revise patch ran
    assert state.grounding_attempts == 1  # the patched spec's ungrounded field was caught
    assert len(ext.calls) == 2  # the grounding retry was a full re-extract, not another patch
