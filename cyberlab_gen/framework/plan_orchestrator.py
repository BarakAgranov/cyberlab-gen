"""The Phase-2 plan pipeline as a deterministic LangGraph state machine (Task 4).

Architectural source: ``pipeline.md §3.2.6`` / ``§3.2.7`` (the Planner + Planner-Jury stages),
``§3.1`` (deterministic state machine, the orchestrator routes; agents never do),
``architecture.md §1.5`` (the LLM produces a manifest / a verdict / a structured refusal; the
framework maps them to control flow), ``§1.7`` (retry vs refinement), ``agents.md §5.7``/``§5.8``.
ADR 0092 (the Planner's ``PlanAttempt`` outcome), ADR 0054/0091 (targeted-patch refinement), ADR
0056 (the iteration caps), ADR 0078 (the verify-only jury).

The Phase-2 plan pipeline wires::

    Planner → Planner-Jury

as a LangGraph ``StateGraph`` over a single typed ``PlanPipelineState`` channel. It is **linear**
(no ``Stage``/``Node`` abstraction, no reducer channels — those land at the first *parallel* node,
the Phase-3 Generators; ``dev/phase-2-seams.md`` ③.1). Three control-flow mechanisms are encoded
explicitly:

* **Planner-Jury ``revise`` (quality) → the *refinement* coordinator.** A ``revise`` re-runs the
  Planner as a **targeted patch** of only the flagged manifest fields (``Planner.refine``, ADR
  0054/0091), bounded by a per-agent refinement cap (``DEFAULT_REFINEMENT_CAP``). On cap exhaustion
  the last verdict decides (``pipeline.md §3.2.7`` → ``§3.2.3``): ``revise`` → ship with
  ``low_jury_confidence``; ``reject`` → halt.

* **AttackSpec incoherence → route back to the Extractor.** When the Planner emits
  ``attackspec_incoherent`` (ADR 0092) it flagged a defect it is **not allowed to repair**
  (``agents.md §5.7``). The coordinator terminates with ``ROUTE_BACK_TO_EXTRACTOR`` — a returned
  outcome, not a raise: Task 6 connects this edge to a real Extractor re-run (cross-pipeline). Here
  the route-back **decision** is the asserted contract (the Task-4 exit criterion).

* **``cannot_plan`` → halt.** AttackSpec gaps too large to plan around → terminate with
  ``HALTED_CANNOT_PLAN`` and the structured gap report (``pipeline.md §3.2.6``).

The orchestrator owns every routing decision; the Planner and the Planner-Jury only produce content
/ judgments / a structured refusal (``architecture.md §1.5``). ``low_jury_confidence`` lives on the
run-report-facing ``PlanPipelineOutcome``, not on the ``LabManifest`` artifact (mirrors ADR 0023).

Terminal states are **returned** as a ``PlanPipelineOutcome`` (never raised) — including the halts —
so the framework caller (the Task-6 ``plan`` verb) owns the halt-vs-route-back-vs-ship policy and the
route-back outcome is a first-class value, not an exception.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from cyberlab_gen.agents.extractor_jury.schema import JuryFieldFeedback, JuryVerdict, Verdict

# Runtime imports (not TYPE_CHECKING): ``PlanPipelineState`` / ``PlanPipelineOutcome`` are Pydantic
# models whose fields reference these, and LangGraph calls ``typing.get_type_hints`` on the state
# schema at graph-build time — so the names must resolve at runtime (the same reasoning as the
# extract orchestrator's field-type imports).
from cyberlab_gen.agents.results import PlannerRefusal, PlanOutcome, PlanResult
from cyberlab_gen.framework.graph_support import traced_async
from cyberlab_gen.framework.orchestrator import (
    DEFAULT_REFINEMENT_CAP,
    GLOBAL_ITERATION_CAP,
    GRAPH_RECURSION_LIMIT,
)
from cyberlab_gen.schemas.attack_spec import AttackSpec
from cyberlab_gen.schemas.base import InternalModel
from cyberlab_gen.schemas.manifest import LabManifest

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from langchain_core.runnables import RunnableConfig

logger = logging.getLogger(__name__)


# --- terminal classification (for the run report) --------------------------


class PlanPipelineStatus(StrEnum):
    """Terminal classification of a plan-pipeline run (for the run report / the Task-6 verb)."""

    PLANNED = "planned"
    PLANNED_LOW_CONFIDENCE = "planned_low_jury_confidence"
    HALTED_REJECT = "halted_reject"
    # Mechanical backstop (mirrors ADR 0067): the jury returned ``approve`` while a rubric dimension
    # scored below the floor — a self-contradiction the framework refuses to ship (``§1.6``).
    HALTED_JURY_INCONSISTENT = "halted_jury_inconsistent"
    # The Planner flagged AttackSpec incoherence (ADR 0092); the framework routes back to the
    # Extractor. A returned outcome (Task 6 wires the cross-pipeline re-extract), not a raise.
    ROUTE_BACK_TO_EXTRACTOR = "route_back_to_extractor"
    HALTED_CANNOT_PLAN = "halted_cannot_plan"
    # The global iteration cap bound the whole pipeline (ADR 0056 backstop).
    HALTED_ITERATION_CAP = "halted_iteration_cap"


# --- the typed pipeline state (the LangGraph channel) ----------------------


class PlanPipelineState(BaseModel):
    """The single typed channel threaded through the plan graph (mirrors ``PipelineState``).

    Holds only data (not the agent instances — those are captured by the node closures).
    ``arbitrary_types_allowed`` because the in-flight ``PlanResult`` / ``LabManifest`` / ``JuryVerdict``
    are carried as live objects across nodes (they never serialize here — an internal runtime view).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    # inputs
    attack_spec: AttackSpec
    preferences: str | None = None

    # in-flight artifacts (None until the producing node runs)
    plan_result: PlanResult | None = None
    manifest: LabManifest | None = None
    verdict: JuryVerdict | None = None
    refusal: PlannerRefusal | None = None

    # routing
    pending_feedback: list[JuryFieldFeedback] | None = None
    refinement_iterations: int = 0
    # total Planner runs across the whole pipeline (first plan + refines); bounded by the global cap.
    total_iterations: int = 0
    # the node-decided next destination, read by the (pure) conditional edges (same pattern as the
    # extract orchestrator: LangGraph discards mutations made inside routing functions).
    route: str | None = None

    # terminal classification + audit trail
    status: PlanPipelineStatus | None = None
    halt_reason: str | None = None
    unresolved_feedback: list[str] = Field(default_factory=list[str])
    verdict_history: list[Verdict] = Field(default_factory=list[Verdict])


# --- the public outcome (→ run report / Task-6 verb) -----------------------


class PlanPipelineOutcome(InternalModel):
    """The run-report-facing result of one plan-pipeline run.

    Every terminal state maps to one of these (no raising): ``PLANNED`` /
    ``PLANNED_LOW_CONFIDENCE`` carry the ``manifest``; ``ROUTE_BACK_TO_EXTRACTOR`` /
    ``HALTED_CANNOT_PLAN`` carry the ``refusal`` (and ``manifest=None``); the jury-halt statuses
    carry the ``verdict``. ``low_jury_confidence`` lives here, not on the ``LabManifest`` (mirrors
    ADR 0023). The Task-6 ``plan`` verb maps ``status`` to CLI behaviour (ship / halt / re-extract).
    """

    status: PlanPipelineStatus
    manifest: LabManifest | None = None
    verdict: JuryVerdict | None = None
    refusal: PlannerRefusal | None = None
    low_jury_confidence: bool = False
    halt_reason: str | None = None
    unresolved_feedback: list[str] = Field(default_factory=list[str])
    refinement_iterations: int = 0
    verdict_history: list[Verdict] = Field(default_factory=list[Verdict])


# --- the agent surfaces the graph needs (narrow protocols) -----------------


class _PlannerLike(Protocol):
    async def plan(
        self, attack_spec: AttackSpec, *, preferences: str | None = None
    ) -> PlanResult: ...

    async def refine(
        self,
        *,
        prior_manifest: LabManifest,
        attack_spec: AttackSpec,
        feedback: list[JuryFieldFeedback],
        preferences: str | None = None,
    ) -> PlanResult: ...


class _PlannerJuryLike(Protocol):
    @property
    def rubric_floor(self) -> float: ...

    async def review(self, *, manifest: LabManifest, attack_spec: AttackSpec) -> JuryVerdict: ...


# --- node names ------------------------------------------------------------


class _Node(StrEnum):
    PLAN = "plan"
    JURY = "plan_jury"


# --- the graph builder -----------------------------------------------------


class PlanPipelineRun(Protocol):
    """The async callable :func:`build_plan_pipeline` returns."""

    def __call__(self, state: PlanPipelineState) -> Awaitable[PlanPipelineState]: ...


def build_plan_pipeline(
    *,
    planner: _PlannerLike,
    jury: _PlannerJuryLike,
    refinement_cap: int = DEFAULT_REFINEMENT_CAP,
    global_iteration_cap: int = GLOBAL_ITERATION_CAP,
) -> PlanPipelineRun:
    """Assemble the Phase-2 plan LangGraph and return its async ``run`` callable.

    The returned callable takes an initial ``PlanPipelineState`` (with ``attack_spec`` set — the
    enriched, jury-approved AttackSpec from the ``extract`` pipeline) and runs it to a terminal
    state. ``refinement_cap`` is the per-agent refinement-iteration cap on a Planner-Jury ``revise``
    (>=0; 0 ships the first ``revise`` immediately as low-confidence). ``global_iteration_cap`` is the
    end-to-end Planner-run backstop (ADR 0056). Agents are captured here so ``PlanPipelineState``
    stays pure data.
    """
    if refinement_cap < 0:
        raise ValueError("refinement_cap must be >= 0")
    if global_iteration_cap < 1:
        raise ValueError("global_iteration_cap must be >= 1")

    def _global_cap_reached(state: PlanPipelineState) -> bool:
        return state.total_iterations >= global_iteration_cap

    def _halt_global_cap(state: PlanPipelineState) -> PlanPipelineState:
        state.status = PlanPipelineStatus.HALTED_ITERATION_CAP
        state.halt_reason = (
            f"Global iteration cap of {global_iteration_cap} reached "
            f"({state.total_iterations} total Planner runs); halting to bound the pipeline."
        )
        state.route = END
        logger.warning("plan: global iteration cap of %d reached; halting", global_iteration_cap)
        return state

    async def plan_node(state: PlanPipelineState) -> PlanPipelineState:
        """Run (or re-run) the Planner: first ``plan`` or a jury-``revise`` targeted ``refine``.

        Reads the returned ``PlanResult.outcome`` and routes (``architecture.md §1.5``): ``planned``
        → the Planner-Jury; ``attackspec_incoherent`` → route back to the Extractor;
        ``cannot_plan`` → halt with the gap report. The Planner never repairs the AttackSpec.
        """
        # Every Planner run — first plan or a refine — is one global iteration (L3, ADR 0056).
        state.total_iterations += 1
        if state.pending_feedback is not None and state.manifest is not None:
            result = await planner.refine(
                prior_manifest=state.manifest,
                attack_spec=state.attack_spec,
                feedback=state.pending_feedback,
                preferences=state.preferences,
            )
            state.pending_feedback = None  # consumed by this re-run
        else:
            result = await planner.plan(state.attack_spec, preferences=state.preferences)
        state.plan_result = result

        if result.outcome is PlanOutcome.PLANNED:
            state.manifest = result.manifest
            state.route = _Node.JURY.value
            return state
        # A refusal — the Planner produced no manifest. Carry the structured detail for the report.
        state.refusal = result.refusal
        if result.outcome is PlanOutcome.ATTACKSPEC_INCOHERENT:
            state.status = PlanPipelineStatus.ROUTE_BACK_TO_EXTRACTOR
            state.halt_reason = (
                "Planner flagged AttackSpec incoherence; routing back to the Extractor "
                "(the Planner does not repair the AttackSpec): "
                f"{result.refusal.summary if result.refusal else 'no detail'}"
            )
            logger.info("plan: AttackSpec incoherent; routing back to the Extractor")
        else:  # CANNOT_PLAN
            state.status = PlanPipelineStatus.HALTED_CANNOT_PLAN
            state.halt_reason = (
                "Planner cannot plan a lab from this AttackSpec: "
                f"{result.refusal.summary if result.refusal else 'no detail'}"
            )
            logger.info("plan: cannot_plan; halting with the gap report")
        state.route = END
        return state

    async def jury_node(state: PlanPipelineState) -> PlanPipelineState:
        """Run the Planner-Jury and decide the next destination.

        ``approve`` → ship; ``revise`` → bounded refinement (re-run the Planner) or
        ship-with-``low_jury_confidence`` on cap exhaustion; ``reject`` → halt (``pipeline.md
        §3.2.7`` → ``§3.2.3``). The verdict is the jury's judgment; this node (the framework) maps it
        to control flow (``architecture.md §1.5``).
        """
        assert state.manifest is not None  # plan_node set it before routing here
        verdict = await jury.review(manifest=state.manifest, attack_spec=state.attack_spec)
        state.verdict = verdict
        state.verdict_history = [*state.verdict_history, verdict.verdict]

        if verdict.verdict is Verdict.APPROVE:
            # Mechanical rubric-floor backstop (mirrors ADR 0067): an ``approve`` whose dimension
            # scores fall below the floor is a self-contradiction the verdict↔feedback validator
            # cannot catch. The framework refuses it (defense-in-depth, ``§1.6``). An ``approve``
            # carries no feedback, so there is nothing to refine toward — the safe action is a halt.
            if not verdict.scores.all_above(jury.rubric_floor):
                state.status = PlanPipelineStatus.HALTED_JURY_INCONSISTENT
                state.halt_reason = (
                    "Planner-Jury returned approve while a rubric dimension scored below the floor "
                    f"({jury.rubric_floor}): lowest dimension {verdict.scores.min_dimension()}."
                )
                state.route = END
                logger.info("plan jury approve contradicts sub-floor scores; halting")
                return state
            state.status = PlanPipelineStatus.PLANNED
            state.route = END
            return state
        if verdict.verdict is Verdict.REJECT:
            state.status = PlanPipelineStatus.HALTED_REJECT
            state.halt_reason = f"Planner-Jury returned reject: {verdict.rationale}"
            state.route = END
            return state
        # revise: refinement, bounded by the per-agent cap. The structured field feedback (manifest
        # paths + suggested_fix) drives the Planner's targeted patch (ADR 0054/0091).
        if state.refinement_iterations < refinement_cap:
            # Global backstop: never start another Planner run past the end-to-end cap (L3).
            if _global_cap_reached(state):
                return _halt_global_cap(state)
            state.refinement_iterations += 1
            state.pending_feedback = verdict.feedback
            state.route = _Node.PLAN.value
            logger.info(
                "plan jury revise; routing to Planner REFINEMENT (iteration %d/%d)",
                state.refinement_iterations,
                refinement_cap,
            )
            return state
        # cap exhausted with a revise verdict — ship with low_jury_confidence (the
        # disagreement-without-progress (b) case, ``pipeline.md §3.2.7`` → ``§3.2.3``).
        state.status = PlanPipelineStatus.PLANNED_LOW_CONFIDENCE
        state.unresolved_feedback = [_feedback_line(item) for item in verdict.feedback]
        state.route = END
        logger.info("plan refinement cap exhausted on revise; shipping with low_jury_confidence")
        return state

    def route_after_plan(state: PlanPipelineState) -> str:
        assert state.route is not None  # plan_node always sets it
        return state.route

    def route_after_jury(state: PlanPipelineState) -> str:
        assert state.route is not None  # jury_node always sets it
        return state.route

    graph: StateGraph[PlanPipelineState, None, PlanPipelineState, PlanPipelineState] = StateGraph(
        PlanPipelineState
    )
    graph.add_node(_Node.PLAN.value, traced_async(_Node.PLAN.value, plan_node))
    graph.add_node(_Node.JURY.value, traced_async(_Node.JURY.value, jury_node))

    graph.add_edge(START, _Node.PLAN.value)
    graph.add_conditional_edges(
        _Node.PLAN.value,
        route_after_plan,
        {_Node.JURY.value: _Node.JURY.value, END: END},
    )
    graph.add_conditional_edges(
        _Node.JURY.value,
        route_after_jury,
        {_Node.PLAN.value: _Node.PLAN.value, END: END},
    )
    compiled = graph.compile()

    async def run(state: PlanPipelineState) -> PlanPipelineState:
        # The returned channel is the source of truth, not the input object (LangGraph reconstructs
        # state through the nodes). ``recursion_limit`` is the graph-level backstop (ADR 0056), sized
        # above the global iteration cap's super-steps so the clean semantic halt wins first.
        config: RunnableConfig = {"recursion_limit": GRAPH_RECURSION_LIMIT}
        result = await compiled.ainvoke(state, config=config)
        if isinstance(result, PlanPipelineState):
            return result
        return PlanPipelineState.model_validate(result)

    return run


# --- the high-level driver -------------------------------------------------


async def run_plan_pipeline(
    *,
    attack_spec: AttackSpec,
    planner: _PlannerLike,
    jury: _PlannerJuryLike,
    preferences: str | None = None,
    refinement_cap: int = DEFAULT_REFINEMENT_CAP,
) -> PlanPipelineOutcome:
    """Run the Phase-2 plan pipeline and return a ``PlanPipelineOutcome``.

    Drives the LangGraph plan machine over ``attack_spec`` and maps its terminal state onto the
    run-report-facing outcome. Every terminal state — ship, low-confidence ship, route-back, and the
    halts — is **returned** (never raised), so the Task-6 ``plan`` verb owns the halt-vs-route-back
    -vs-ship policy. (``Planner.refine`` may still raise ``PlanningError`` on patch-budget
    exhaustion; that propagates as a hard internal failure, mirroring ``Extractor.refine``.)
    """
    run = build_plan_pipeline(planner=planner, jury=jury, refinement_cap=refinement_cap)
    final = await run(PlanPipelineState(attack_spec=attack_spec, preferences=preferences))
    return finalize_plan_outcome(final)


def finalize_plan_outcome(state: PlanPipelineState) -> PlanPipelineOutcome:
    """Map a terminal ``PlanPipelineState`` onto a ``PlanPipelineOutcome`` (pure, never raises)."""
    assert state.status is not None  # every terminal node sets a status
    return PlanPipelineOutcome(
        status=state.status,
        # On a refusal (route-back / cannot_plan) the Planner produced no manifest, so this is None;
        # on a ship it is the finalized manifest.
        manifest=state.manifest,
        verdict=state.verdict,
        refusal=state.refusal,
        low_jury_confidence=state.status is PlanPipelineStatus.PLANNED_LOW_CONFIDENCE,
        halt_reason=state.halt_reason,
        unresolved_feedback=state.unresolved_feedback,
        refinement_iterations=state.refinement_iterations,
        verdict_history=state.verdict_history,
    )


def _feedback_line(item: JuryFieldFeedback) -> str:
    """One ``field_path: problem`` line, with the jury's ``suggested_fix`` when present."""
    base = f"{item.field_path}: {item.problem}"
    return f"{base} (suggested fix: {item.suggested_fix})" if item.suggested_fix else base


__all__ = [
    "PlanPipelineOutcome",
    "PlanPipelineRun",
    "PlanPipelineState",
    "PlanPipelineStatus",
    "build_plan_pipeline",
    "finalize_plan_outcome",
    "run_plan_pipeline",
]
