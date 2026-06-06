"""The Phase-1 generation pipeline as a deterministic LangGraph state machine.

Architectural source: ``pipeline.md §3.1`` (deterministic state machine, typed
cross-stage boundaries, ``--auto`` / ``--interactive`` modes, headless
rejection), ``pipeline.md §3.2.1`` through §3.2.4 (the stages), ``pipeline.md §3.3`` (the
typed cross-stage contracts), ``validation.md §6.10`` (Layer-1 → retry, never
refinement), ``architecture.md §1.5`` (the orchestrator routes; agents never do),
``architecture.md §1.7`` (retry vs refinement), ADR 0023.

The Phase-1 pipeline wires:

    Ingestion → Extractor → Validator-Layer-1 → Extractor-Jury → enrichment

as a LangGraph ``StateGraph`` over a single typed ``PipelineState`` channel. Two
distinct failure mechanisms are encoded **explicitly** (ADR 0023):

* **Layer-1 (structural) failures → the Extractor's *retry* mechanism.** A
  failing ``StaticSchemaResult`` re-runs the Extractor with the findings as structural
  feedback, bounded by a per-stage structural-retry budget
  (``DEFAULT_STRUCTURAL_RETRY_ATTEMPTS``). On exhaustion the pipeline **halts**
  with ``ValidationError`` — it never escalates to refinement
  (``validation.md §6.10``, the non-negotiable discipline).

* **Jury ``revise`` (quality) → the *refinement* coordinator.** A ``revise``
  verdict re-runs the Extractor with the jury feedback wrapped in a typed
  ``RefinementFeedback`` object, bounded by a per-agent refinement cap
  (``DEFAULT_REFINEMENT_CAP``, placeholder 3). On cap exhaustion the last verdict
  decides (``pipeline.md §3.2.3``): ``revise`` → ship with
  ``low_jury_confidence=true`` and the unresolved feedback in the run report;
  ``reject`` → halt.

The orchestrator owns every routing decision; the Extractor, the Jury, and the
Validator only produce content / judgments / findings (``architecture.md §1.5``).
``low_jury_confidence`` lives on the run-report-facing ``PipelineOutcome``, not on
the ``AttackSpec`` artifact (ADR 0023).
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

# These artifact/result types are *runtime* imports (not TYPE_CHECKING) because
# ``PipelineState`` is a Pydantic model whose fields reference them, and LangGraph
# calls ``typing.get_type_hints`` on the state schema at graph-build time — under
# ``from __future__ import annotations`` the hints must resolve at runtime. ruff's
# TC001 wrongly wants them in a type-checking block; the noqa pins the requirement.
from cyberlab_gen.agents.extractor.extractor import ExtractionResult
from cyberlab_gen.agents.extractor_jury.schema import JuryVerdict, Verdict
from cyberlab_gen.errors import ValidationError
from cyberlab_gen.framework.enrichment import EnrichmentConfig, EnrichmentResult, enrich
from cyberlab_gen.schemas.attack_spec import AttackSpec
from cyberlab_gen.schemas.base import InternalModel
from cyberlab_gen.validators.static_schema_validator import StaticSchemaResult

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from cyberlab_gen.agents.extractor.extractor import Extractor
    from cyberlab_gen.agents.extractor.tools import ExternalLookupRecord
    from cyberlab_gen.agents.extractor_jury.jury import ExtractorJury
    from cyberlab_gen.schemas.ingestion import IngestionResult
    from cyberlab_gen.validators.static_schema_validator import StaticSchemaValidator

logger = logging.getLogger(__name__)

#: Total Extractor attempts on a Layer-1 (structural) failure, including the
#: first. ``architecture.md §1.7``: retry budget is stage-local, default 3.
DEFAULT_STRUCTURAL_RETRY_ATTEMPTS = 3

#: Per-agent refinement iterations on a Jury ``revise`` verdict. Placeholder per
#: ``implementation-plan.md §4.2`` (revisited in Phase 4); ``architecture.md
#: §1.7`` names a per-agent cap of 5 as the eventual default, but the Task-6
#: brief pins 3 for the minimal Phase-1 coordinator.
DEFAULT_REFINEMENT_CAP = 3


# --- cross-stage feedback contract (pipeline.md §3.3) ----------------------


class FeedbackKind(StrEnum):
    """Why the Extractor is being re-run (the two distinct mechanisms, §1.7)."""

    STRUCTURAL_RETRY = "structural_retry"
    REFINEMENT = "refinement"


class RefinementFeedback(InternalModel):
    """Typed structured feedback handed to a re-run Extractor (ADR 0023).

    The ``UserFeedback``-like object ``pipeline.md §3.3`` requires: no free text
    crosses the stage boundary untyped — this typed object *is* the contract, and
    ``render`` produces its prompt rendering inside the Extractor stage. ``kind``
    records whether the re-run is a structural retry (Layer-1 findings) or a
    quality refinement (Jury feedback) so the run report can distinguish them.
    """

    kind: FeedbackKind
    items: list[str] = Field(default_factory=list[str])

    def render(self) -> str:
        """Render the feedback as the prompt addendum the Extractor re-run sees."""
        header = (
            "STRUCTURAL VALIDATION FAILURE — the previous AttackSpec failed Validator "
            "Layer 1. Fix every item before resubmitting:"
            if self.kind is FeedbackKind.STRUCTURAL_RETRY
            else (
                "JURY REVISION REQUESTED — the previous AttackSpec was reviewed and needs "
                "field-targeted fixes. Address every item:"
            )
        )
        lines = [header, *(f"- {item}" for item in self.items)]
        return "\n".join(lines)


# --- the typed pipeline state (the LangGraph channel) ----------------------


class PipelineStatus(StrEnum):
    """Terminal classification of a pipeline run (for the run report)."""

    SHIPPED = "shipped"
    SHIPPED_LOW_CONFIDENCE = "shipped_low_jury_confidence"
    HALTED_VALIDATION = "halted_validation"
    HALTED_REJECT = "halted_reject"


class PipelineState(BaseModel):
    """The single typed channel threaded through the LangGraph graph.

    Holds only data (not the agent instances — those are captured by the node
    closures, ADR 0023). ``arbitrary_types_allowed`` because the in-flight
    ``ExtractionResult`` / ``AttackSpec`` / ``StaticSchemaResult`` / ``JuryVerdict`` are
    carried as live objects across nodes (they never serialize here — this is an
    internal runtime view, ADR 0008-style reasoning).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    blog_content: str
    source_summary: str

    # in-flight artifacts (None until the producing node runs)
    extraction: ExtractionResult | None = None
    spec: AttackSpec | None = None
    layer1: StaticSchemaResult | None = None
    verdict: JuryVerdict | None = None
    enrichment: EnrichmentResult | None = None

    # routing counters
    structural_attempts: int = 0
    refinement_iterations: int = 0

    # accumulated feedback for the next re-run (cleared after the re-run consumes)
    pending_feedback: RefinementFeedback | None = None

    # the node-decided next destination, read by the (pure) conditional edges.
    # LangGraph discards mutations made inside routing functions, so every
    # decision (and its counter/feedback bookkeeping) is made in a *node* and the
    # resolved destination is parked here for the edge to read. ADR 0023.
    route: str | None = None

    # terminal classification + the audit trail
    status: PipelineStatus | None = None
    halt_reason: str | None = None
    unresolved_feedback: list[str] = Field(default_factory=list[str])
    verdict_history: list[Verdict] = Field(default_factory=list[Verdict])


# --- the public outcome (pipeline.md §3.3 → run report) --------------------


class PipelineOutcome(InternalModel):
    """The run-report-facing result of one pipeline run (ADR 0023).

    ``low_jury_confidence`` lives here, not on the ``AttackSpec``: the spec is the
    Extractor's artifact and must not carry a framework-routing flag
    (``pipeline.md §3.2.3`` places the flag "in the run report"). ``shipped`` is
    ``True`` for both the clean and the low-confidence ship; a halt sets it
    ``False`` (those raise before an outcome is returned, but the field documents
    the invariant).
    """

    spec: AttackSpec
    enrichment: EnrichmentResult
    verdict: JuryVerdict
    status: PipelineStatus
    low_jury_confidence: bool = False
    unresolved_feedback: list[str] = Field(default_factory=list[str])
    structural_attempts: int = 0
    refinement_iterations: int = 0
    verdict_history: list[Verdict] = Field(default_factory=list[Verdict])


# --- headless-mode guard (pipeline.md §3.1) --------------------------------


def reject_interactive_when_headless(*, interactive: bool, stdin_is_tty: bool) -> None:
    """Reject ``--interactive`` when stdin is not a TTY (``pipeline.md §3.1``).

    The tool never hangs silently waiting for input that can't arrive. The CLI
    (Task 7) calls this at startup; it lives here so the orchestration contract —
    "headless usage rejects ``--interactive``" — is enforced at the framework
    layer, not only in the CLI. Raises ``ValueError`` with a message pointing to
    ``--auto``.
    """
    if interactive and not stdin_is_tty:
        raise ValueError(
            "--interactive requires an interactive terminal (stdin is not a TTY). "
            "Re-run with --auto for headless/CI usage (pipeline.md §3.1)."
        )


# --- the agent surfaces the graph needs (narrow protocols) -----------------


class _ExtractorLike(Protocol):
    async def extract(self, *, blog_content: str, source_summary: str) -> ExtractionResult: ...


class _JuryLike(Protocol):
    async def review(
        self,
        *,
        spec: AttackSpec,
        blog_content: str,
        lookups: list[ExternalLookupRecord] | None = None,
    ) -> JuryVerdict: ...


# --- node names ------------------------------------------------------------


class _Node(StrEnum):
    EXTRACT = "extract"
    VALIDATE = "validate_layer1"
    JURY = "jury"
    ENRICH = "enrich"


# --- the graph builder -----------------------------------------------------


def build_pipeline(
    *,
    extractor: _ExtractorLike,
    validator: StaticSchemaValidator,
    jury: _JuryLike,
    enrichment_config: EnrichmentConfig | None = None,
    structural_retry_attempts: int = DEFAULT_STRUCTURAL_RETRY_ATTEMPTS,
    refinement_cap: int = DEFAULT_REFINEMENT_CAP,
) -> Callable[[PipelineState], Awaitable[PipelineState]]:
    """Assemble the Phase-1 LangGraph pipeline and return its async ``run`` callable.

    The returned callable takes an initial ``PipelineState`` (with
    ``blog_content`` + ``source_summary`` set — these come from Ingestion, the
    Ingestion → Extractor contract of ``pipeline.md §3.3``) and runs it to a
    terminal state. The graph's nodes are the stages; its conditional edges encode
    the retry-vs-refinement-vs-halt routing (ADR 0023). Agents are captured here
    so ``PipelineState`` stays pure data.

    ``structural_retry_attempts`` is the *total* Extractor attempts on Layer-1
    failure (>=1). ``refinement_cap`` is the per-agent refinement-iteration cap on
    ``revise`` (>=0; 0 ships the first ``revise`` immediately as low-confidence).
    """
    if structural_retry_attempts < 1:
        raise ValueError("structural_retry_attempts must be >= 1")
    if refinement_cap < 0:
        raise ValueError("refinement_cap must be >= 0")
    enrich_cfg = enrichment_config

    async def extract_node(state: PipelineState) -> PipelineState:
        """Run (or re-run) the Extractor, folding any pending feedback into the prompt."""
        summary = state.source_summary
        if state.pending_feedback is not None:
            summary = f"{summary}\n\n{state.pending_feedback.render()}"
        result = await extractor.extract(blog_content=state.blog_content, source_summary=summary)
        state.structural_attempts += 1
        state.extraction = result
        state.spec = result.attack_spec
        # the feedback (if any) has now been consumed by this re-run
        state.pending_feedback = None
        return state

    def validate_node(state: PipelineState) -> PipelineState:
        """Run Validator Layer 1 and decide the next destination (framework, never LLM).

        All routing bookkeeping happens here, in a *node*, because LangGraph
        discards mutations made inside conditional-edge functions (the edge below
        is a pure reader of ``state.route``). The Layer-1 → *retry* discipline
        (``validation.md §6.10``) lives here: a failure re-runs the Extractor with
        structural feedback, never the refinement coordinator.
        """
        assert state.spec is not None  # extract_node always sets it
        state.layer1 = validator.validate(state.spec)
        if state.layer1.passed:
            state.route = _Node.JURY.value
            return state
        if state.structural_attempts < structural_retry_attempts:
            state.pending_feedback = RefinementFeedback(
                kind=FeedbackKind.STRUCTURAL_RETRY,
                items=state.layer1.rendered_findings(),
            )
            state.route = _Node.EXTRACT.value
            logger.info(
                "layer 1 failed; routing to Extractor RETRY (attempt %d/%d)",
                state.structural_attempts + 1,
                structural_retry_attempts,
            )
            return state
        # retry budget exhausted — halt, never escalate to refinement.
        state.status = PipelineStatus.HALTED_VALIDATION
        state.halt_reason = (
            f"Validator Layer 1 still failing after {structural_retry_attempts} Extractor "
            f"attempts: {'; '.join(state.layer1.rendered_findings())}"
        )
        state.route = END
        return state

    async def jury_node(state: PipelineState) -> PipelineState:
        """Run the Extractor-Jury and decide the next destination.

        ``approve`` → enrich; ``revise`` → bounded refinement (re-run Extractor) or
        ship-with-``low_jury_confidence`` on cap exhaustion; ``reject`` → halt
        (``pipeline.md §3.2.3``). The verdict is the jury's judgment; this node (the
        framework) maps it to control flow (``architecture.md §1.5``).
        """
        assert state.spec is not None
        lookups = state.extraction.lookups if state.extraction is not None else None
        verdict = await jury.review(
            spec=state.spec, blog_content=state.blog_content, lookups=lookups
        )
        state.verdict = verdict
        state.verdict_history = [*state.verdict_history, verdict.verdict]

        if verdict.verdict is Verdict.APPROVE:
            state.route = _Node.ENRICH.value
            return state
        if verdict.verdict is Verdict.REJECT:
            state.status = PipelineStatus.HALTED_REJECT
            state.halt_reason = (
                f"Extractor-Jury returned reject (systematic hallucination): {verdict.rationale}"
            )
            state.route = END
            return state
        # revise: refinement, bounded by the per-agent cap.
        feedback_items = [f"{f.field_path}: {f.problem}" for f in verdict.feedback]
        if state.refinement_iterations < refinement_cap:
            state.refinement_iterations += 1
            state.pending_feedback = RefinementFeedback(
                kind=FeedbackKind.REFINEMENT, items=feedback_items
            )
            state.route = _Node.EXTRACT.value
            logger.info(
                "jury revise; routing to Extractor REFINEMENT (iteration %d/%d)",
                state.refinement_iterations,
                refinement_cap,
            )
            return state
        # cap exhausted with a revise verdict — ship with low_jury_confidence
        # (the disagreement-without-progress (b) case, pipeline.md §3.2.3).
        state.status = PipelineStatus.SHIPPED_LOW_CONFIDENCE
        state.unresolved_feedback = feedback_items
        state.route = _Node.ENRICH.value
        logger.info("refinement cap exhausted on revise; shipping with low_jury_confidence")
        return state

    def enrich_node(state: PipelineState) -> PipelineState:
        """Run the pre-Planner enrichment framework pass (``pipeline.md §3.2.4``)."""
        assert state.spec is not None
        state.enrichment = enrich(state.spec, enrich_cfg)
        if state.status is None:
            state.status = PipelineStatus.SHIPPED
        return state

    # --- routing functions: pure readers of the node-decided destination ---

    def route_after_validate(state: PipelineState) -> str:
        assert state.route is not None  # validate_node always sets it
        return state.route

    def route_after_jury(state: PipelineState) -> str:
        assert state.route is not None  # jury_node always sets it
        return state.route

    graph: StateGraph[PipelineState, None, PipelineState, PipelineState] = StateGraph(PipelineState)
    graph.add_node(_Node.EXTRACT.value, extract_node)
    graph.add_node(_Node.VALIDATE.value, validate_node)
    graph.add_node(_Node.JURY.value, jury_node)
    graph.add_node(_Node.ENRICH.value, enrich_node)

    graph.add_edge(START, _Node.EXTRACT.value)
    graph.add_edge(_Node.EXTRACT.value, _Node.VALIDATE.value)
    graph.add_conditional_edges(
        _Node.VALIDATE.value,
        route_after_validate,
        {_Node.JURY.value: _Node.JURY.value, _Node.EXTRACT.value: _Node.EXTRACT.value, END: END},
    )
    graph.add_conditional_edges(
        _Node.JURY.value,
        route_after_jury,
        {
            _Node.ENRICH.value: _Node.ENRICH.value,
            _Node.EXTRACT.value: _Node.EXTRACT.value,
            END: END,
        },
    )
    graph.add_edge(_Node.ENRICH.value, END)
    compiled = graph.compile()

    async def run(state: PipelineState) -> PipelineState:
        # LangGraph threads its own reconstructed state through the nodes (mutations
        # inside routing functions are discarded — that is why every decision is
        # made in a node, ADR 0023). The *returned* channel is the source of truth,
        # not the input object; coerce it back to the typed model.
        result = await compiled.ainvoke(state)
        if isinstance(result, PipelineState):
            return result
        return PipelineState.model_validate(result)

    return run


# --- the high-level driver -------------------------------------------------


async def run_pipeline(
    *,
    ingestion: IngestionResult,
    blog_content: str,
    extractor: Extractor,
    validator: StaticSchemaValidator,
    jury: ExtractorJury,
    enrichment_config: EnrichmentConfig | None = None,
    structural_retry_attempts: int = DEFAULT_STRUCTURAL_RETRY_ATTEMPTS,
    refinement_cap: int = DEFAULT_REFINEMENT_CAP,
) -> PipelineOutcome:
    """Run the full Phase-1 pipeline and return a ``PipelineOutcome``.

    Takes the ``IngestionResult`` + cached blog content (the Ingestion → Extractor
    contract, ``pipeline.md §3.3``), drives the LangGraph state machine, and maps
    its terminal state onto the run-report-facing outcome.

    The two halt paths both raise (no outcome is returned on a halt):

    * **Layer-1 stays red past the structural-retry budget** → ``ValidationError``
      carrying the unresolved Layer-1 findings (``validation.md §6.10``).
    * **Jury ``reject``** → ``JuryRejectionError`` (a ``ValidationError`` subclass
      that carries no Layer-1 findings — a *quality* halt, ``pipeline.md §3.2.3``).

    The two ship paths return an outcome: a clean ``approve`` ships with
    ``low_jury_confidence=False``; a ``revise`` that exhausts the refinement cap
    ships with ``low_jury_confidence=True`` and the unresolved jury feedback in
    ``unresolved_feedback`` (``pipeline.md §3.2.3`` (b)).
    """
    run = build_pipeline(
        extractor=extractor,
        validator=validator,
        jury=jury,
        enrichment_config=enrichment_config,
        structural_retry_attempts=structural_retry_attempts,
        refinement_cap=refinement_cap,
    )
    source_summary = _ingestion_summary(ingestion)
    final = await run(PipelineState(blog_content=blog_content, source_summary=source_summary))
    return _finalize(final)


def _finalize(state: PipelineState) -> PipelineOutcome:
    """Map a terminal ``PipelineState`` onto a ``PipelineOutcome`` (or raise on halt)."""
    if state.status is PipelineStatus.HALTED_VALIDATION:
        findings = state.layer1.rendered_findings() if state.layer1 is not None else []
        raise ValidationError(
            state.halt_reason or "Validator Layer 1 failed past the retry budget",
            findings=findings,
        )
    if state.status is PipelineStatus.HALTED_REJECT:
        raise JuryRejectionError(state.halt_reason or "Extractor-Jury rejected the AttackSpec")

    # shipped (clean or low-confidence) — enrichment ran, everything is present.
    assert state.spec is not None
    assert state.enrichment is not None
    assert state.verdict is not None
    assert state.status is not None
    return PipelineOutcome(
        spec=state.spec,
        enrichment=state.enrichment,
        verdict=state.verdict,
        status=state.status,
        low_jury_confidence=state.status is PipelineStatus.SHIPPED_LOW_CONFIDENCE,
        unresolved_feedback=state.unresolved_feedback,
        structural_attempts=state.structural_attempts,
        refinement_iterations=state.refinement_iterations,
        verdict_history=state.verdict_history,
    )


def _ingestion_summary(ingestion: IngestionResult) -> str:
    """Render the Ingestion metadata the Extractor prompt folds into its source block."""
    return (
        f"url={ingestion.url}\n"
        f"canonical_url={ingestion.canonical_url}\n"
        f"fetched_at={ingestion.fetched_at.isoformat()}\n"
        f"fetch_method={ingestion.fetch_method}\n"
        f"content_hash={ingestion.content_hash}\n"
        f"word_count={ingestion.word_count}\n"
        f"publisher_domain={ingestion.publisher_domain}"
    )


class JuryRejectionError(ValidationError):
    """The Extractor-Jury returned ``reject`` (systematic hallucination) → halt.

    ``pipeline.md §3.2.3``: a ``reject`` with a fundamental concern halts the
    pipeline. Subclasses ``ValidationError`` so callers that halt on any
    structured-halt error catch both, but carries no Layer-1 ``findings`` (it is a
    *quality* halt, not a structural one — ``architecture.md §1.7``). The
    ``stage`` is still reported as ``'validation'`` via the parent; the message
    names the jury as the source.
    """


__all__ = [
    "DEFAULT_REFINEMENT_CAP",
    "DEFAULT_STRUCTURAL_RETRY_ATTEMPTS",
    "FeedbackKind",
    "JuryRejectionError",
    "PipelineOutcome",
    "PipelineState",
    "PipelineStatus",
    "RefinementFeedback",
    "build_pipeline",
    "reject_interactive_when_headless",
    "run_pipeline",
]
