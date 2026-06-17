"""Tests for the Phase-2 plan-refinement coordinator (``pipeline.md §3.2.6``-``§3.2.7``, Task 4).

Covers the Task-4 exit criteria with the narrow ``plan`` / ``refine`` / ``review`` fakes (so the
route-back-vs-refine *path* is asserted directly, not just the terminal state):

- an **incoherent** AttackSpec drives a **route-back to the Extractor** — the Planner flags it, the
  jury is never reached, and the Planner produced no manifest (it did not "fix" the AttackSpec);
- a jury ``revise`` drives a bounded **targeted-patch refine** loop that **converges** (refine is
  called with the jury's feedback; the jury re-reviews the patched manifest; then approves);
- exhausted ``revise`` ships with the ``low_jury_confidence`` flag;
- ``reject`` halts; ``cannot_plan`` halts; a sub-floor ``approve`` is refused (ADR-0067 mirror); the
  global iteration cap bounds a runaway.
"""

from __future__ import annotations

import pytest

from cyberlab_gen.agents.extractor_jury.schema import (
    JuryFieldFeedback,
    JuryScores,
    JuryVerdict,
    Verdict,
)
from cyberlab_gen.framework.orchestrator import DEFAULT_REFINEMENT_CAP, GLOBAL_ITERATION_CAP
from cyberlab_gen.framework.plan_orchestrator import (
    PlanPipelineOutcome,
    PlanPipelineState,
    PlanPipelineStatus,
    build_plan_pipeline,
    run_plan_pipeline,
)
from tests.unit.framework.pipeline_fakes import (
    FakePlanner,
    FakePlannerJury,
    make_cannot_plan_result,
    make_manifest,
    make_plan_result,
    make_route_back_result,
    make_spec,
    make_verdict,
)


def _revise(field_path: str = "phases[0].steps[0].description") -> JuryVerdict:
    return make_verdict(
        Verdict.REVISE,
        feedback=[JuryFieldFeedback(field_path=field_path, problem="too vague")],  # type: ignore[arg-type]
        scores=JuryScores(
            fidelity=0.65, completeness=0.65, provenance_correctness=0.65, structural_validity=0.65
        ),
    )


async def _run_state(
    planner: FakePlanner,
    jury: FakePlannerJury,
    *,
    refinement_cap: int = DEFAULT_REFINEMENT_CAP,
    global_iteration_cap: int = GLOBAL_ITERATION_CAP,
) -> PlanPipelineState:
    run = build_plan_pipeline(
        planner=planner,
        jury=jury,
        refinement_cap=refinement_cap,
        global_iteration_cap=global_iteration_cap,
    )
    return await run(PlanPipelineState(attack_spec=make_spec()))


# --- the happy path --------------------------------------------------------


async def test_plan_then_approve_ships() -> None:
    planner = FakePlanner([make_plan_result()])
    jury = FakePlannerJury([make_verdict(Verdict.APPROVE)])
    final = await _run_state(planner, jury)

    assert final.status is PlanPipelineStatus.PLANNED
    assert final.manifest is not None
    assert jury.calls == 1  # one review, no refinement


# --- route-back (THE Task-4 exit criterion) --------------------------------


async def test_incoherent_attackspec_routes_back_to_extractor() -> None:
    # §5.7: the Planner flags AttackSpec incoherence and the coordinator routes BACK to the
    # Extractor — the Planner does NOT repair the AttackSpec. The jury is never reached, and no
    # manifest was produced.
    planner = FakePlanner([make_route_back_result()])
    jury = FakePlannerJury([make_verdict(Verdict.APPROVE)])
    final = await _run_state(planner, jury)

    assert final.status is PlanPipelineStatus.ROUTE_BACK_TO_EXTRACTOR
    assert final.manifest is None  # the Planner flagged, it did not fix
    assert final.refusal is not None
    assert final.refusal.attack_spec_field_paths  # the structured route-back detail
    assert jury.calls == 0  # routed back before any jury review
    assert not planner.refine_calls  # the Planner never tried to repair


async def test_cannot_plan_halts() -> None:
    planner = FakePlanner([make_cannot_plan_result()])
    jury = FakePlannerJury([make_verdict(Verdict.APPROVE)])
    final = await _run_state(planner, jury)

    assert final.status is PlanPipelineStatus.HALTED_CANNOT_PLAN
    assert final.manifest is None
    assert final.refusal is not None
    assert jury.calls == 0


# --- the targeted-patch refine loop (converges) ----------------------------


async def test_jury_revise_drives_refine_and_converges() -> None:
    # revise -> the Planner refines (targeted patch) -> the jury re-reviews the patched manifest ->
    # approve. The loop converges (the byte-identical-unflagged-fields property is pinned at the
    # Planner unit level, test_planner.py).
    patched = make_manifest(step_tiers=None)
    planner = FakePlanner([make_plan_result()], refine_results=[make_plan_result(patched)])
    jury = FakePlannerJury([_revise(), make_verdict(Verdict.APPROVE)])
    final = await _run_state(planner, jury)

    assert final.status is PlanPipelineStatus.PLANNED
    assert final.refinement_iterations == 1
    # the Planner refined once, handed the jury's flagged field path:
    assert len(planner.refine_calls) == 1
    _prior, feedback = planner.refine_calls[0]
    assert feedback[0].field_path == "phases[0].steps[0].description"
    # the jury re-reviewed the PATCHED manifest (not the original) on the second pass:
    assert jury.calls == 2
    assert jury.reviewed_manifests[1] == patched


async def test_exhausted_revise_ships_low_confidence() -> None:
    # refinement_cap=1: one refine, then a second revise exhausts the budget -> ship with the
    # low_jury_confidence flag and the unresolved feedback in the outcome (pipeline.md §3.2.7 -> b).
    planner = FakePlanner([make_plan_result()])  # refine echoes the prior manifest (no-op)
    jury = FakePlannerJury([_revise(), _revise()])
    final = await _run_state(planner, jury, refinement_cap=1)

    assert final.status is PlanPipelineStatus.PLANNED_LOW_CONFIDENCE
    assert final.refinement_iterations == 1
    assert final.unresolved_feedback  # the unresolved flagged field(s) carried to the report


# --- halts -----------------------------------------------------------------


async def test_jury_reject_halts() -> None:
    planner = FakePlanner([make_plan_result()])
    jury = FakePlannerJury(
        [
            make_verdict(
                Verdict.REJECT,
                feedback=[JuryFieldFeedback(field_path="phases", problem="drops a stage")],  # type: ignore[arg-type]
                scores=JuryScores(
                    fidelity=0.2,
                    completeness=0.2,
                    provenance_correctness=0.2,
                    structural_validity=0.2,
                ),
            )
        ]
    )
    final = await _run_state(planner, jury)
    assert final.status is PlanPipelineStatus.HALTED_REJECT


async def test_subfloor_approve_is_refused() -> None:
    # Mechanical backstop (ADR-0067 mirror): an approve whose dimension scores fall below the floor
    # is self-contradictory; the framework refuses to ship it (§1.6).
    planner = FakePlanner([make_plan_result()])
    jury = FakePlannerJury(
        [
            make_verdict(
                Verdict.APPROVE,
                scores=JuryScores(
                    fidelity=0.9,
                    completeness=0.5,  # below the 0.7 floor
                    provenance_correctness=0.9,
                    structural_validity=0.9,
                ),
            )
        ]
    )
    final = await _run_state(planner, jury)
    assert final.status is PlanPipelineStatus.HALTED_JURY_INCONSISTENT


async def test_global_iteration_cap_bounds_a_runaway() -> None:
    # A jury that always revises would spin; the global iteration cap halts it cleanly even when the
    # per-agent refinement budget still has room (ADR 0056 backstop).
    planner = FakePlanner([make_plan_result()])
    jury = FakePlannerJury([_revise()])  # always revise (last verdict repeats)
    final = await _run_state(planner, jury, refinement_cap=100, global_iteration_cap=3)
    assert final.status is PlanPipelineStatus.HALTED_ITERATION_CAP


# --- the high-level driver (run_plan_pipeline -> outcome) -------------------


async def test_run_plan_pipeline_returns_shipped_outcome() -> None:
    outcome = await run_plan_pipeline(
        attack_spec=make_spec(),
        planner=FakePlanner([make_plan_result()]),
        jury=FakePlannerJury([make_verdict(Verdict.APPROVE)]),
    )
    assert isinstance(outcome, PlanPipelineOutcome)
    assert outcome.status is PlanPipelineStatus.PLANNED
    assert outcome.manifest is not None
    assert outcome.low_jury_confidence is False


async def test_run_plan_pipeline_returns_route_back_as_outcome_not_raise() -> None:
    # Route-back is a first-class returned outcome (Task 6 wires the cross-pipeline re-extract), not
    # an exception — the driver returns it for the caller to act on.
    outcome = await run_plan_pipeline(
        attack_spec=make_spec(),
        planner=FakePlanner([make_route_back_result()]),
        jury=FakePlannerJury([make_verdict(Verdict.APPROVE)]),
    )
    assert outcome.status is PlanPipelineStatus.ROUTE_BACK_TO_EXTRACTOR
    assert outcome.refusal is not None
    assert outcome.manifest is None


def test_build_plan_pipeline_validates_caps() -> None:
    planner = FakePlanner([make_plan_result()])
    jury = FakePlannerJury([make_verdict(Verdict.APPROVE)])
    with pytest.raises(ValueError, match="refinement_cap"):
        build_plan_pipeline(planner=planner, jury=jury, refinement_cap=-1)
    with pytest.raises(ValueError, match="global_iteration_cap"):
        build_plan_pipeline(planner=planner, jury=jury, global_iteration_cap=0)
