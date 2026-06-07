"""The Phase-1 generation pipeline as a deterministic LangGraph state machine.

Architectural source: ``pipeline.md §3.1`` (deterministic state machine, typed
cross-stage boundaries, ``--auto`` / ``--interactive`` modes, headless
rejection), ``pipeline.md §3.2.1`` through §3.2.4 (the stages), ``pipeline.md §3.3`` (the
typed cross-stage contracts), ``validation.md §6.10`` (static-schema → retry, never
refinement), ``architecture.md §1.5`` (the orchestrator routes; agents never do),
``architecture.md §1.7`` (retry vs refinement), ADR 0023.

The Phase-1 pipeline wires:

    Ingestion → Extractor → Validator-static-schema → Extractor-Jury → enrichment

as a LangGraph ``StateGraph`` over a single typed ``PipelineState`` channel. Two
distinct failure mechanisms are encoded **explicitly** (ADR 0023):

* **static-schema (structural) failures → the Extractor's *retry* mechanism.** A
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
from pydantic import BaseModel, ConfigDict, Field, model_validator

# These artifact/result types are *runtime* imports (not TYPE_CHECKING) because
# ``PipelineState`` is a Pydantic model whose fields reference them, and LangGraph
# calls ``typing.get_type_hints`` on the state schema at graph-build time — under
# ``from __future__ import annotations`` the hints must resolve at runtime. ruff's
# TC001 wrongly wants them in a type-checking block; the noqa pins the requirement.
from cyberlab_gen.agents.extractor.extractor import ExtractionResult
from cyberlab_gen.agents.extractor_jury.schema import JuryFieldFeedback, JuryVerdict, Verdict
from cyberlab_gen.errors import ValidationError
from cyberlab_gen.framework.enrichment import EnrichmentConfig, EnrichmentResult, enrich
from cyberlab_gen.schemas.attack_spec import AttackSpec
from cyberlab_gen.schemas.base import InternalModel
from cyberlab_gen.tracing_setup import stage_span
from cyberlab_gen.validators.static_schema_validator import (
    PendingProposals,
    StaticSchemaFinding,
    StaticSchemaResult,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from typing import Any, Self

    from langchain_core.runnables import RunnableConfig
    from langgraph.checkpoint.base import BaseCheckpointSaver

    from cyberlab_gen.agents.extractor.extractor import Extractor
    from cyberlab_gen.agents.extractor.tools import ExternalLookupRecord
    from cyberlab_gen.agents.extractor_jury.jury import ExtractorJury
    from cyberlab_gen.schemas.ingestion import IngestionResult
    from cyberlab_gen.validators.static_schema_validator import StaticSchemaValidator

logger = logging.getLogger(__name__)

#: Total Extractor attempts on a static-schema (structural) failure, including the
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
    """Typed structured feedback handed to a re-run Extractor (ADR 0023, ADR 0048).

    The typed-boundary contract (``CLAUDE.md``, ``architecture.md §1.7``) means typed
    *contents*, not a typed wrapper around stringified data: the structured findings
    travel between stages in their structured form, and ``render`` produces prompt text
    only at the prompt boundary (the Extractor stage), with the structured form retained
    for the framework. Retaining the structure — field paths and the jury's
    ``suggested_fix`` in particular — is the load-bearing prerequisite that lets the
    refinement coordinator address and patch by field path (ADR 0048 A1).

    ``kind`` discriminates the two re-run mechanisms (``architecture.md §1.7``) and
    selects which payload is carried: a structural retry carries the static-schema
    findings (``static_findings``); a quality refinement carries the jury's field-level
    feedback (``jury_feedback``). The ``_payload_matches_kind`` validator enforces that
    exactly the matching payload is populated, so a mis-built feedback object fails
    loudly rather than rendering to an empty or mismatched prompt.
    """

    kind: FeedbackKind
    static_findings: list[StaticSchemaFinding] = Field(default_factory=list[StaticSchemaFinding])
    jury_feedback: list[JuryFieldFeedback] = Field(default_factory=list[JuryFieldFeedback])

    @model_validator(mode="after")
    def _payload_matches_kind(self) -> Self:
        if self.kind is FeedbackKind.STRUCTURAL_RETRY:
            if self.jury_feedback:
                raise ValueError("a structural_retry feedback must not carry jury_feedback")
            if not self.static_findings:
                raise ValueError("a structural_retry feedback must carry static_findings")
        else:  # REFINEMENT
            if self.static_findings:
                raise ValueError("a refinement feedback must not carry static_findings")
            if not self.jury_feedback:
                raise ValueError("a refinement feedback must carry jury_feedback")
        return self

    def feedback_lines(self) -> list[str]:
        """The per-finding one-line renderings (also reused for the run report).

        For a structural retry each line is the ``StaticSchemaFinding``'s own
        ``code@location: detail`` render; for a refinement each line is
        ``field_path: problem`` with the jury's ``suggested_fix`` appended when present
        (the part the old stringified boundary discarded).
        """
        if self.kind is FeedbackKind.STRUCTURAL_RETRY:
            return [finding.render() for finding in self.static_findings]
        return [_render_jury_feedback(item) for item in self.jury_feedback]

    def render(self) -> str:
        """Render the feedback as the prompt addendum the Extractor re-run sees."""
        header = (
            "STRUCTURAL VALIDATION FAILURE — the previous AttackSpec failed Validator "
            "static schema validation. Fix every item before resubmitting:"
            if self.kind is FeedbackKind.STRUCTURAL_RETRY
            else (
                "JURY REVISION REQUESTED — the previous AttackSpec was reviewed and needs "
                "field-targeted fixes. Address every item:"
            )
        )
        lines = [header, *(f"- {line}" for line in self.feedback_lines())]
        return "\n".join(lines)


def _render_jury_feedback(item: JuryFieldFeedback) -> str:
    """One ``field_path: problem`` line, with the jury's ``suggested_fix`` when present."""
    base = f"{item.field_path}: {item.problem}"
    if item.suggested_fix:
        return f"{base} (suggested fix: {item.suggested_fix})"
    return base


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
    static_schema: StaticSchemaResult | None = None
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
    VALIDATE = "validate_static_schema"
    JURY = "jury"
    ENRICH = "enrich"


# --- the graph builder -----------------------------------------------------


def _traced_async(name: str, fn: Callable[[PipelineState], Awaitable[PipelineState]]):
    """Wrap an async node so its execution is a pipeline-stage span (ADR 0041).

    Return type is left to inference so it stays the precise coroutine type LangGraph's
    ``add_node`` expects (an ``Awaitable`` annotation is too broad to match).
    """

    async def _wrapped(state: PipelineState) -> PipelineState:
        with stage_span(name):
            return await fn(state)

    return _wrapped


def _traced_sync(name: str, fn: Callable[[PipelineState], PipelineState]):
    """Wrap a sync node so its execution is a pipeline-stage span (ADR 0041)."""

    def _wrapped(state: PipelineState) -> PipelineState:
        with stage_span(name):
            return fn(state)

    return _wrapped


class PipelineRun(Protocol):
    """The async callable :func:`build_pipeline` returns (ADR 0023 surface, ADR 0040).

    ``thread_id`` is the optional LangGraph checkpoint thread: supplied only when a
    ``checkpointer`` was passed to :func:`build_pipeline`, it keys this run's
    checkpoints so a mid-node crash leaves resumable state under that id. Omitting it
    (the default) reproduces the pre-checkpointer behaviour exactly.

    ``state`` may be ``None`` to **resume** a checkpointed thread: LangGraph picks up
    from the last completed node rather than re-running from the start. A normal run
    passes the initial ``PipelineState``.
    """

    def __call__(
        self, state: PipelineState | None, *, thread_id: str | None = None
    ) -> Awaitable[PipelineState]: ...


def build_pipeline(
    *,
    extractor: _ExtractorLike,
    validator: StaticSchemaValidator,
    jury: _JuryLike,
    enrichment_config: EnrichmentConfig | None = None,
    structural_retry_attempts: int = DEFAULT_STRUCTURAL_RETRY_ATTEMPTS,
    refinement_cap: int = DEFAULT_REFINEMENT_CAP,
    # ``BaseCheckpointSaver`` is generic over its version type; we hold any concrete
    # saver opaquely (we only pass it to ``graph.compile``), hence ``Any``.
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> PipelineRun:
    """Assemble the Phase-1 LangGraph pipeline and return its async ``run`` callable.

    The returned callable takes an initial ``PipelineState`` (with
    ``blog_content`` + ``source_summary`` set — these come from Ingestion, the
    Ingestion → Extractor contract of ``pipeline.md §3.3``) and runs it to a
    terminal state. The graph's nodes are the stages; its conditional edges encode
    the retry-vs-refinement-vs-halt routing (ADR 0023). Agents are captured here
    so ``PipelineState`` stays pure data.

    ``structural_retry_attempts`` is the *total* Extractor attempts on static-schema
    failure (>=1). ``refinement_cap`` is the per-agent refinement-iteration cap on
    ``revise`` (>=0; 0 ships the first ``revise`` immediately as low-confidence).

    ``checkpointer`` (ADR 0040, an additive amendment to the ADR-0023-locked
    surface) is an optional LangGraph ``BaseCheckpointSaver``. When given, the graph
    is compiled with it and the returned callable's ``thread_id`` keys this run's
    per-node checkpoints, so a mid-node crash survives and the run is resumable from
    the last completed node. ``None`` (the default) compiles exactly as before — no
    checkpointing, no behaviour change.
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
        """Run the static schema validator and decide the next destination (framework, never LLM).

        All routing bookkeeping happens here, in a *node*, because LangGraph
        discards mutations made inside conditional-edge functions (the edge below
        is a pure reader of ``state.route``). The static-schema → *retry* discipline
        (``validation.md §6.10``) lives here: a failure re-runs the Extractor with
        structural feedback, never the refinement coordinator.
        """
        assert state.spec is not None  # extract_node always sets it
        # Provisional resolution (ADR 0044): a facet/thesis-type reference absent from
        # the registry but proposed by the Extractor this run is a provisional pass, so
        # the proposal survives static schema validation to the overlay-write acceptance point.
        pending = _pending_from_extraction(state.extraction)
        state.static_schema = validator.validate(state.spec, pending=pending)
        if state.static_schema.passed:
            state.route = _Node.JURY.value
            return state
        if state.structural_attempts < structural_retry_attempts:
            state.pending_feedback = RefinementFeedback(
                kind=FeedbackKind.STRUCTURAL_RETRY,
                static_findings=state.static_schema.findings,
            )
            state.route = _Node.EXTRACT.value
            logger.info(
                "static schema validation failed; routing to Extractor RETRY (attempt %d/%d)",
                state.structural_attempts + 1,
                structural_retry_attempts,
            )
            return state
        # retry budget exhausted — halt, never escalate to refinement.
        state.status = PipelineStatus.HALTED_VALIDATION
        state.halt_reason = (
            f"Static schema validation still failing after {structural_retry_attempts} Extractor "
            f"attempts: {'; '.join(state.static_schema.rendered_findings())}"
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
        # revise: refinement, bounded by the per-agent cap. The structured jury feedback
        # (field paths + suggested_fix) is retained for the framework; rendering to prompt
        # text happens only at the Extractor boundary (ADR 0048 A2, the typed-contents rule).
        feedback = RefinementFeedback(kind=FeedbackKind.REFINEMENT, jury_feedback=verdict.feedback)
        if state.refinement_iterations < refinement_cap:
            state.refinement_iterations += 1
            state.pending_feedback = feedback
            state.route = _Node.EXTRACT.value
            logger.info(
                "jury revise; routing to Extractor REFINEMENT (iteration %d/%d)",
                state.refinement_iterations,
                refinement_cap,
            )
            return state
        # cap exhausted with a revise verdict — ship with low_jury_confidence
        # (the disagreement-without-progress (b) case, pipeline.md §3.2.3). The run report's
        # unresolved_feedback is the structured feedback rendered to lines (an output boundary,
        # so stringifying here is fine — it now also preserves suggested_fix).
        state.status = PipelineStatus.SHIPPED_LOW_CONFIDENCE
        state.unresolved_feedback = feedback.feedback_lines()
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
    # Each node is wrapped in a stage span (ADR 0041) so the extract/validate/jury/
    # enrich tree shows under the agent spans in Phoenix. ``stage_span`` is a no-op
    # context manager when tracing is off, so this is zero-cost and behaviour-neutral
    # by default — an additive, traced-only touch of the ADR-0023-locked builder.
    graph.add_node(_Node.EXTRACT.value, _traced_async(_Node.EXTRACT.value, extract_node))
    graph.add_node(_Node.VALIDATE.value, _traced_sync(_Node.VALIDATE.value, validate_node))
    graph.add_node(_Node.JURY.value, _traced_async(_Node.JURY.value, jury_node))
    graph.add_node(_Node.ENRICH.value, _traced_sync(_Node.ENRICH.value, enrich_node))

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
    compiled = graph.compile(checkpointer=checkpointer)

    async def run(state: PipelineState | None, *, thread_id: str | None = None) -> PipelineState:
        # LangGraph threads its own reconstructed state through the nodes (mutations
        # inside routing functions are discarded — that is why every decision is
        # made in a node, ADR 0023). The *returned* channel is the source of truth,
        # not the input object; coerce it back to the typed model.
        #
        # When a checkpointer is configured, a thread_id namespaces this run's
        # checkpoints (ADR 0040); LangGraph requires one, so we synthesize a default
        # if the caller didn't supply one. With no checkpointer the config is unused.
        # ``state is None`` resumes the thread from its last checkpoint.
        config: RunnableConfig | None = None
        if checkpointer is not None:
            config = {"configurable": {"thread_id": thread_id or "default"}}
        result = await compiled.ainvoke(state, config=config)
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

    * **static-schema stays red past the structural-retry budget** → ``ValidationError``
      carrying the unresolved static-schema findings (``validation.md §6.10``).
    * **Jury ``reject``** → ``JuryRejectionError`` (a ``ValidationError`` subclass
      that carries no static-schema findings — a *quality* halt, ``pipeline.md §3.2.3``).

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
        findings = (
            state.static_schema.rendered_findings() if state.static_schema is not None else []
        )
        raise ValidationError(
            state.halt_reason or "Static schema validation failed past the retry budget",
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


def _pending_from_extraction(extraction: ExtractionResult | None) -> PendingProposals:
    """Build the static-schema provisional-resolution set from this run's proposals (ADR 0044).

    A facet / value-type the Extractor proposed this run is carried so static schema validation
    provisionally resolves a reference to it (the overlay write happens later, at the
    acceptance point). ``None`` extraction (first node not yet run) → empty set.
    """
    if extraction is None:
        return PendingProposals()
    return PendingProposals(
        facets=frozenset(p.name for p in extraction.facet_proposals),
        value_types=frozenset(p.name for p in extraction.value_type_proposals),
        thesis_types=frozenset(p.name for p in extraction.thesis_type_proposals),
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
    structured-halt error catch both, but carries no static-schema ``findings`` (it is a
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
