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
    FakeCrossCheckValidator,
    FakePlanner,
    FakePlannerJury,
    make_cannot_plan_result,
    make_cross_check_finding,
    make_cross_check_result,
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
    validator: FakeCrossCheckValidator | None = None,
    refinement_cap: int = DEFAULT_REFINEMENT_CAP,
    global_iteration_cap: int = GLOBAL_ITERATION_CAP,
) -> PlanPipelineState:
    run = build_plan_pipeline(
        planner=planner,
        jury=jury,
        validator=validator or FakeCrossCheckValidator(),  # default: a clean cross-check pass
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


# --- ADR 0107: a Planner tool-loop overflow degrades to a named halt -------


async def test_planner_tool_loop_degrades_to_named_halt() -> None:
    # ADR 0107: when the Planner's tool loop overflows (even the ADR-0105 forced emit + its reserved
    # output-retries could not land a valid manifest), plan_node catches the ToolLoopError and halts
    # with a deterministic named status + reason — never letting the raw exception escape to the
    # eval/CLI boundary as an unclassified blog_fatal.
    from cyberlab_gen.errors import ToolLoopError

    planner = FakePlanner([], raises=ToolLoopError("tool-use loop exceeded its request budget"))
    jury = FakePlannerJury([make_verdict(Verdict.APPROVE)])
    final = await _run_state(planner, jury)

    assert final.status is PlanPipelineStatus.HALTED_PLANNER_EMIT_EXHAUSTED
    assert final.manifest is None  # the Planner produced nothing to route on
    assert final.halt_reason is not None
    assert "tool-use loop" in final.halt_reason
    assert jury.calls == 0  # the jury never ran
    assert planner.plan_calls == 1


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
        validator=FakeCrossCheckValidator(),
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
        validator=FakeCrossCheckValidator(),
    )
    assert outcome.status is PlanPipelineStatus.ROUTE_BACK_TO_EXTRACTOR
    assert outcome.refusal is not None
    assert outcome.manifest is None


def test_build_plan_pipeline_validates_caps() -> None:
    planner = FakePlanner([make_plan_result()])
    jury = FakePlannerJury([make_verdict(Verdict.APPROVE)])
    validator = FakeCrossCheckValidator()
    with pytest.raises(ValueError, match="refinement_cap"):
        build_plan_pipeline(planner=planner, jury=jury, validator=validator, refinement_cap=-1)
    with pytest.raises(ValueError, match="global_iteration_cap"):
        build_plan_pipeline(planner=planner, jury=jury, validator=validator, global_iteration_cap=0)


# --- the semantic cross-check node (Task 6: the second mechanical validation layer) ---------


async def test_approve_then_clean_cross_check_ships_planned() -> None:
    # The happy path now runs the cross-check after jury-approve; a clean pass ships PLANNED.
    planner = FakePlanner([make_plan_result()])
    jury = FakePlannerJury([make_verdict(Verdict.APPROVE)])
    validator = FakeCrossCheckValidator()  # default: pass
    final = await _run_state(planner, jury, validator=validator)

    assert final.status is PlanPipelineStatus.PLANNED
    assert validator.calls == 1  # the manifest was cross-checked before shipping


async def test_cross_check_findings_route_to_planner_refine_and_converge() -> None:
    # Cross-check findings route (via responsible_agent_for -> PLANNER) into a bounded refine; the
    # patched manifest re-passes the jury and the cross-check, and ships.
    patched = make_manifest()
    planner = FakePlanner([make_plan_result()], refine_results=[make_plan_result(patched)])
    jury = FakePlannerJury([make_verdict(Verdict.APPROVE), make_verdict(Verdict.APPROVE)])
    finding = make_cross_check_finding(location="facets[0]")
    validator = FakeCrossCheckValidator(
        [make_cross_check_result([finding]), make_cross_check_result()]  # findings, then clean
    )
    final = await _run_state(planner, jury, validator=validator)

    assert final.status is PlanPipelineStatus.PLANNED
    assert final.refinement_iterations == 1
    # the finding was adapted to refinement feedback the Planner consumed (structured locator + code):
    assert len(planner.refine_calls) == 1
    _prior, feedback = planner.refine_calls[0]
    assert feedback[0].field_path == "facets[0]"
    assert finding.code.value in feedback[0].problem
    assert validator.calls == 2  # re-checked after the refine


async def test_cross_check_unresolved_past_budget_halts_not_ships() -> None:
    # A persistently cross-check-invalid manifest is known-broken: it HALTS rather than shipping
    # behind any confidence flag (§1.6, exit-criterion 1). refinement_cap=1: one refine, still bad.
    planner = FakePlanner([make_plan_result()])  # refine echoes the (still-bad) manifest
    jury = FakePlannerJury([make_verdict(Verdict.APPROVE)])
    bad = make_cross_check_result([make_cross_check_finding(location="facets[0]")])
    validator = FakeCrossCheckValidator([bad])  # always returns findings
    final = await _run_state(planner, jury, validator=validator, refinement_cap=1)

    assert final.status is PlanPipelineStatus.HALTED_SEMANTIC_CROSS_CHECK_UNRESOLVED
    assert final.unresolved_feedback  # the unresolved findings carried to the report
    assert final.halt_reason is not None


async def test_low_confidence_ship_still_requires_cross_check_pass() -> None:
    # Jury revise-cap-exhausted would ship low_jury_confidence — but only if the mechanical
    # cross-check passes. Here it passes → PLANNED_LOW_CONFIDENCE.
    planner = FakePlanner([make_plan_result()])
    jury = FakePlannerJury([_revise(), _revise()])  # exhaust the cap, then ship low-confidence
    final = await _run_state(planner, jury, refinement_cap=1)

    assert final.status is PlanPipelineStatus.PLANNED_LOW_CONFIDENCE
    assert final.unresolved_feedback  # the jury's unresolved feedback


async def test_low_confidence_with_cross_check_findings_halts() -> None:
    # Jury revise-cap-exhausted (budget spent) AND the cross-check finds a mechanical issue → HALT,
    # never a low-confidence ship of a known-broken manifest. The cross-check budget is the SHARED
    # refinement budget, already spent by the jury revises.
    planner = FakePlanner([make_plan_result()])
    jury = FakePlannerJury([_revise(), _revise()])
    bad = make_cross_check_result([make_cross_check_finding(location="facets[0]")])
    final = await _run_state(
        planner, jury, validator=FakeCrossCheckValidator([bad]), refinement_cap=1
    )

    assert final.status is PlanPipelineStatus.HALTED_SEMANTIC_CROSS_CHECK_UNRESOLVED
