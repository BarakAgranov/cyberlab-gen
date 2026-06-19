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

from cyberlab_gen.agents.extractor_jury.schema import JuryFieldFeedback, JuryVerdict, Verdict

# These artifact/result types are *runtime* imports (not TYPE_CHECKING) because
# ``PipelineState`` is a Pydantic model whose fields reference them and LangGraph calls
# ``typing.get_type_hints`` on the state schema at graph-build time, which evaluates the field
# annotations — so the referenced types must be importable at runtime. This holds with or
# without ``from __future__ import annotations``: PEP 563 defers *when* an annotation is
# evaluated, not *whether* its names must exist when ``get_type_hints`` resolves them (ADR 0083
# correction). ruff keeps these field-type imports out of a TYPE_CHECKING block via
# ``[tool.ruff.lint.flake8-type-checking] runtime-evaluated-base-classes`` (the Pydantic bases).
from cyberlab_gen.agents.results import ExtractionResult
from cyberlab_gen.errors import ValidationError
from cyberlab_gen.framework.enrichment import EnrichmentConfig, EnrichmentResult, enrich
from cyberlab_gen.framework.graph_support import traced_async, traced_sync
from cyberlab_gen.framework.provenance_guard import neutralize_framework_owned_provenance
from cyberlab_gen.schemas.attack_spec import AttackSpec
from cyberlab_gen.schemas.base import InternalModel
from cyberlab_gen.validators.grounding_validator import (
    GroundingFinding,
    GroundingResult,
    GroundingValidator,
)
from cyberlab_gen.validators.static_schema_validator import (
    PendingProposals,
    StaticSchemaFinding,
    StaticSchemaResult,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from typing import Any, Self

    from langchain_core.runnables import RunnableConfig
    from langgraph.checkpoint.base import BaseCheckpointSaver

    from cyberlab_gen.agents.extractor.extractor import Extractor
    from cyberlab_gen.agents.extractor_jury.jury import ExtractorJury
    from cyberlab_gen.schemas.ingestion import IngestionResult
    from cyberlab_gen.state.trajectory import RunTrajectoryRecorder
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

#: Total Extractor re-runs the orchestrator-owned grounding stack may request on a
#: grounding (search-before-claim / CVE-hallucination) failure (ADR 0051/0060). This is
#: the relocation of the Extractor's former internal ``hallucination_retry_attempts`` budget
#: (``architecture.md §1.5``: the orchestrator owns the budget now, not the agent). Grounding
#: is a *retry* mechanism (``validation.md §6.10.1``), distinct from the static-schema and
#: refinement budgets.
DEFAULT_GROUNDING_RETRY_ATTEMPTS = 2

#: Total Extractor runs across the whole pipeline (structural retries + refinements +
#: the first run). ``architecture.md §6`` "Total iteration cap (default 20)" — the
#: end-to-end bound that binds regardless of the per-node caps. With the default per-node
#: caps the realistic maximum is ~6 runs, so this is a backstop for raised/buggy per-node
#: budgets and routing pathologies, enforced as a clean ``HALTED_VALIDATION`` (L3, ADR 0056).
GLOBAL_ITERATION_CAP = 20

#: The LangGraph ``recursion_limit`` (a super-step bound), set as the final graph-level
#: backstop so the whole graph is bounded even if the application caps are mis-set. After the
#: ADR-0052/0061 reorder a refinement iteration costs at most 5 super-steps
#: (extract → validate → enrich → grounding → jury) before the next run, so
#: ``GLOBAL_ITERATION_CAP`` iterations consume under ``6 * GLOBAL_ITERATION_CAP`` super-steps —
#: sized so the *semantic* cap (which halts cleanly with a clear reason) always binds first in
#: a legitimate run, leaving this to catch only a genuine routing loop. ADR 0056 (multiplier
#: re-derived from 4x to 6x in ADR 0061 when the enrich + grounding nodes joined the per-iter path).
GRAPH_RECURSION_LIMIT = 6 * GLOBAL_ITERATION_CAP


# --- cross-stage feedback contract (pipeline.md §3.3) ----------------------


class FeedbackKind(StrEnum):
    """Why the Extractor is being re-run (``§1.7``; ADR 0051/0060 added grounding).

    ``STRUCTURAL_RETRY`` and ``GROUNDING_RETRY`` are both *retry*-class re-runs
    (``validation.md §6.10.1``): a full re-extract with the findings folded into the
    prompt, each bounded by its own orchestrator-owned budget. ``REFINEMENT`` is the
    quality-driven targeted-patch path (jury ``revise``). The three never cross.
    """

    STRUCTURAL_RETRY = "structural_retry"
    GROUNDING_RETRY = "grounding_retry"
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
    grounding_findings: list[GroundingFinding] = Field(default_factory=list[GroundingFinding])
    jury_feedback: list[JuryFieldFeedback] = Field(default_factory=list[JuryFieldFeedback])

    @model_validator(mode="after")
    def _payload_matches_kind(self) -> Self:
        if self.kind is FeedbackKind.STRUCTURAL_RETRY:
            if self.grounding_findings or self.jury_feedback:
                raise ValueError("a structural_retry feedback must carry only static_findings")
            if not self.static_findings:
                raise ValueError("a structural_retry feedback must carry static_findings")
        elif self.kind is FeedbackKind.GROUNDING_RETRY:
            if self.static_findings or self.jury_feedback:
                raise ValueError("a grounding_retry feedback must carry only grounding_findings")
            if not self.grounding_findings:
                raise ValueError("a grounding_retry feedback must carry grounding_findings")
        else:  # REFINEMENT
            if self.static_findings or self.grounding_findings:
                raise ValueError("a refinement feedback must carry only jury_feedback")
            if not self.jury_feedback:
                raise ValueError("a refinement feedback must carry jury_feedback")
        return self

    def feedback_lines(self) -> list[str]:
        """The per-finding one-line renderings (also reused for the run report).

        For a structural retry each line is the ``StaticSchemaFinding``'s own
        ``code@location: detail`` render; for a grounding retry each line is the
        ``GroundingFinding``'s render; for a refinement each line is ``field_path: problem``
        with the jury's ``suggested_fix`` appended when present (the part the old
        stringified boundary discarded).
        """
        if self.kind is FeedbackKind.STRUCTURAL_RETRY:
            return [finding.render() for finding in self.static_findings]
        if self.kind is FeedbackKind.GROUNDING_RETRY:
            return [finding.render() for finding in self.grounding_findings]
        return [_render_jury_feedback(item) for item in self.jury_feedback]

    def render(self) -> str:
        """Render the feedback as the prompt addendum the Extractor re-run sees."""
        if self.kind is FeedbackKind.STRUCTURAL_RETRY:
            header = (
                "STRUCTURAL VALIDATION FAILURE — the previous AttackSpec failed Validator "
                "static schema validation. Fix every item before resubmitting:"
            )
        elif self.kind is FeedbackKind.GROUNDING_RETRY:
            header = (
                "GROUNDING / SEARCH-BEFORE-CLAIM FAILURE — the previous AttackSpec claimed "
                "external_api values with no matching tool-call evidence in the trace, or an "
                "unresolved CVE. Call external_lookup for any external_api value you keep, or set "
                "the field to unknown_from_blog with a reason. Fix every item:"
            )
        else:  # REFINEMENT
            header = (
                "JURY REVISION REQUESTED — the previous AttackSpec was reviewed and needs "
                "field-targeted fixes. Address every item:"
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
    # Mechanical backstop (ADR 0067): the jury returned ``approve`` while a rubric
    # dimension scored below the floor — a self-contradictory verdict the framework
    # refuses to ship (defense-in-depth, ``architecture.md §1.6``).
    HALTED_JURY_INCONSISTENT = "halted_jury_inconsistent"


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
    grounding: GroundingResult | None = None
    verdict: JuryVerdict | None = None
    enrichment: EnrichmentResult | None = None

    # routing counters
    structural_attempts: int = 0
    grounding_attempts: int = 0
    refinement_iterations: int = 0
    # total Extractor runs across the whole pipeline (structural + grounding + refinement +
    # first); bounded by the global iteration cap (``architecture.md §6``, L3).
    total_iterations: int = 0

    # accumulated feedback for the next re-run (cleared after the re-run consumes)
    pending_feedback: RefinementFeedback | None = None

    # signature of the prior failed static-schema attempt's finding set, for the
    # no-progress early-bail (ADR 0057); reset to None whenever validation passes.
    last_static_finding_signature: str | None = None
    # the same no-progress guard for the grounding-retry loop (ADR 0057/0060); reset to
    # None whenever the grounding stack produces no retry-triggering findings.
    last_grounding_finding_signature: str | None = None

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

    async def refine(
        self,
        *,
        prior_spec: AttackSpec,
        feedback: list[JuryFieldFeedback],
        blog_content: str,
        source_summary: str,
    ) -> ExtractionResult: ...


class _JuryLike(Protocol):
    @property
    def rubric_floor(self) -> float: ...

    async def review(
        self,
        *,
        spec: AttackSpec,
        blog_content: str,
        grounding_findings: list[GroundingFinding] | None = None,
    ) -> JuryVerdict: ...


# --- node names ------------------------------------------------------------


class _Node(StrEnum):
    EXTRACT = "extract"
    VALIDATE = "validate_static_schema"
    GROUNDING = "grounding"
    JURY = "jury"
    ENRICH = "enrich"


# --- the graph builder -----------------------------------------------------


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
    grounding_validator: GroundingValidator | None = None,
    known_source_ids: frozenset[str] | None = None,
    enrichment_config: EnrichmentConfig | None = None,
    structural_retry_attempts: int = DEFAULT_STRUCTURAL_RETRY_ATTEMPTS,
    grounding_retry_attempts: int = DEFAULT_GROUNDING_RETRY_ATTEMPTS,
    refinement_cap: int = DEFAULT_REFINEMENT_CAP,
    global_iteration_cap: int = GLOBAL_ITERATION_CAP,
    # ``BaseCheckpointSaver`` is generic over its version type; we hold any concrete
    # saver opaquely (we only pass it to ``graph.compile``), hence ``Any``.
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    recorder: RunTrajectoryRecorder | None = None,
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
    if grounding_retry_attempts < 0:
        raise ValueError("grounding_retry_attempts must be >= 0")
    if refinement_cap < 0:
        raise ValueError("refinement_cap must be >= 0")
    if global_iteration_cap < 1:
        raise ValueError("global_iteration_cap must be >= 1")
    enrich_cfg = enrichment_config
    # The grounding stack is orchestrator-owned (ADR 0051/0060). A default is constructed
    # when the caller does not inject one so existing call sites stay unchanged; for a spec
    # with no external_api claims it produces no findings (a behaviour-neutral no-op).
    grounding = (
        grounding_validator
        if grounding_validator is not None
        else GroundingValidator(known_source_ids=known_source_ids)
    )

    def _global_cap_reached(state: PipelineState) -> bool:
        """True when the run has used its whole end-to-end iteration budget (L3)."""
        return state.total_iterations >= global_iteration_cap

    def _halt_global_cap(state: PipelineState) -> PipelineState:
        """Halt the pipeline cleanly at the global iteration cap (``architecture.md §6``)."""
        state.status = PipelineStatus.HALTED_VALIDATION
        state.halt_reason = (
            f"Global iteration cap of {global_iteration_cap} reached "
            f"({state.total_iterations} total Extractor runs); halting to bound the pipeline."
        )
        state.route = END
        logger.warning(
            "global iteration cap of %d reached; halting (HALTED_VALIDATION)", global_iteration_cap
        )
        return state

    async def extract_node(state: PipelineState) -> PipelineState:
        """Run (or re-run) the Extractor: full extract, structural retry, or targeted patch.

        Three entry conditions (``architecture.md §1.7``, ADR 0048/0054):

        * **first run** (no pending feedback) → full ``extract``;
        * **structural retry** (a static-schema ``STRUCTURAL_RETRY`` feedback) → full
          ``extract`` with the structural findings folded into the prompt — *never* a
          patch (``validation.md §6.10``, the non-negotiable discipline);
        * **jury ``revise``** (a ``REFINEMENT`` feedback, with a prior spec to patch) →
          targeted ``refine``: the structured field-level feedback drives a patch of only
          the flagged paths, convergent by construction.
        """
        # Every Extractor run — first, structural retry, or refinement — is one global
        # iteration; the cap on this counter bounds the whole pipeline end-to-end (L3).
        state.total_iterations += 1
        pending = state.pending_feedback
        is_refinement = (
            pending is not None
            and pending.kind is FeedbackKind.REFINEMENT
            and state.spec is not None
        )
        # Stamp the round the upcoming billed call(s) belong to, so the provider-side trajectory
        # capture (which sees the content but not the round) is grouped correctly (ADR 0098).
        if recorder is not None:
            recorder.enter_stage(
                round_index=state.total_iterations, stage="refine" if is_refinement else "extract"
            )
        if is_refinement:
            assert pending is not None and state.spec is not None  # narrowed by is_refinement
            result = await extractor.refine(
                prior_spec=state.spec,
                feedback=pending.jury_feedback,
                blog_content=state.blog_content,
                source_summary=state.source_summary,
            )
        else:
            summary = state.source_summary
            if pending is not None:
                summary = f"{summary}\n\n{pending.render()}"
            result = await extractor.extract(
                blog_content=state.blog_content, source_summary=summary
            )
            # Only a first run or a *structural*-retry extract spends the structural-retry
            # budget. A grounding retry is bounded by its OWN counter (``grounding_attempts``,
            # bumped in ``grounding_node``) and a jury-revise refinement by ``refinement_iterations``
            # (bumped in ``jury_node``); neither may charge the structural counter — the three
            # retry/refinement mechanisms have independent budgets (``architecture.md §1.7``; L2).
            if pending is None or pending.kind is FeedbackKind.STRUCTURAL_RETRY:
                state.structural_attempts += 1
        state.extraction = result
        # Neutralize the framework-owned fields the LLM may not author (framework_enriched, the
        # discrepancy record, CveReference.source_of_record, material_discrepancies, the derived
        # lab-level reproducibility block) BEFORE the spec reaches validation / enrichment /
        # grounding — the framework is the sole legitimate writer and runs later; an LLM-set
        # framework_enriched would otherwise skip enrichment's no-op guard AND the grounding
        # search-before-claim check (ADR 0082). The scope follows WHAT THIS RUN AUTHORED (ADR 0085):
        # a refinement is a targeted patch whose only LLM-authored content — the patch new_values —
        # was already neutralized at the merge seam (refinement.apply_field_patch ->
        # neutralize_patch_provenance), so re-scrubbing the merged spec here would wipe a PRIOR
        # iteration's legitimate enrichment (the discrepancy record + material_discrepancies index)
        # and silently drop a blog-vs-API disagreement. A first run / structural retry / grounding
        # retry (re)authored the whole spec, so the whole spec is scrubbed. state.extraction keeps
        # the raw agent output for the audit trail; state.spec is the framework-sanitized artifact.
        if is_refinement:
            state.spec = result.attack_spec
        else:
            state.spec = neutralize_framework_owned_provenance(result.attack_spec)
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
            # Cleared: end this structural-failure streak so a *later* failure (e.g. after a
            # refinement patch) is judged fresh, not against this run's pre-pass findings.
            state.last_static_finding_signature = None
            # Static schema passed → ENRICH first (ADR 0052/0061: enrichment runs before the
            # grounding stack + the jury, so the framework-written external_api fields reach
            # the grounding stack — which exempts framework_enriched ones — and the jury
            # reviews the enriched spec: *what ships equals what was reviewed*).
            state.route = _Node.ENRICH.value
            return state
        # No-progress early-bail (ADR 0057, mirrors the ADR-0032 call-surface bail): a
        # structural retry that reproduced the *identical* finding set is not converging.
        # Halt now rather than spending the rest of the (~$3-4) retry budget re-extracting
        # toward a finding that can never clear (e.g. an unconvergeable external-source
        # finding). A *changing* finding set may signal convergence, so the budget is
        # honoured then. This only ever halts *earlier* — same terminal status, no contract
        # change, never a raised ceiling.
        signature = _finding_signature(state.static_schema)
        if signature == state.last_static_finding_signature:
            state.status = PipelineStatus.HALTED_VALIDATION
            state.halt_reason = (
                "Static schema validation made no progress — the same findings recurred on "
                "consecutive Extractor attempts: "
                f"{'; '.join(state.static_schema.rendered_findings())}"
            )
            state.route = END
            logger.info("structural retry made no progress (identical findings); halting early")
            return state
        state.last_static_finding_signature = signature
        if state.structural_attempts < structural_retry_attempts:
            # Global backstop: never start another Extractor run past the end-to-end cap,
            # even if the per-node structural budget still has room (L3).
            if _global_cap_reached(state):
                return _halt_global_cap(state)
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

    def grounding_node(state: PipelineState) -> PipelineState:
        """Run the orchestrator-owned grounding stack and decide the next destination.

        The mechanical sibling layers (provenance-structure, search-before-claim, CVE,
        MITRE pass-through) run here, once, producing one findings set (``validation.md
        §6.10.2``, ADR 0051/0060). A *retry*-triggering finding (a hallucination —
        search-before-claim / CVE-hallucination) re-runs the Extractor with the findings
        folded into the prompt, bounded by the orchestrator-owned grounding-retry budget
        (the relocation of the Extractor's old hidden loop). Informational-only findings
        (provenance-structure) are carried to the jury, which *consumes* them — it does not
        re-derive them (``agents.md §5.5``). This is framework routing, never the LLM
        (``architecture.md §1.5``).
        """
        assert state.spec is not None
        lookups = state.extraction.lookups if state.extraction is not None else []
        # The CVE ship-gate consumes enrichment's per-CVE NVD outcome (enrich runs before
        # grounding); the validator itself stays no-network (ADR 0101 / §1.6).
        cve_resolution = state.enrichment.cve_resolution if state.enrichment is not None else None
        state.grounding = grounding.validate(state.spec, lookups, cve_resolution=cve_resolution)
        retry_findings = state.grounding.retry_findings()
        if not retry_findings:
            # Clean of hallucinations (any structure findings travel to the jury as grounding).
            state.last_grounding_finding_signature = None
            state.route = _Node.JURY.value
            return state
        # No-progress early-bail (ADR 0057, mirroring the static-schema bail): a grounding
        # retry that reproduced the *identical* retry-finding set is not converging — halt now
        # rather than spending the rest of the (~$3-4) retry budget re-extracting toward a
        # finding that can never clear. This only ever halts *earlier* — same terminal status.
        signature = _grounding_signature(retry_findings)
        if signature == state.last_grounding_finding_signature:
            state.status = PipelineStatus.HALTED_VALIDATION
            state.halt_reason = (
                "Grounding made no progress — the same search-before-claim/CVE findings "
                "recurred on consecutive Extractor attempts: "
                f"{'; '.join(f.render() for f in retry_findings)}"
            )
            state.route = END
            logger.info("grounding retry made no progress (identical findings); halting early")
            return state
        state.last_grounding_finding_signature = signature
        if state.grounding_attempts < grounding_retry_attempts:
            # Global backstop: never start another Extractor run past the end-to-end cap (L3).
            if _global_cap_reached(state):
                return _halt_global_cap(state)
            state.grounding_attempts += 1
            state.pending_feedback = RefinementFeedback(
                kind=FeedbackKind.GROUNDING_RETRY, grounding_findings=retry_findings
            )
            state.route = _Node.EXTRACT.value
            logger.info(
                "grounding failed; routing to Extractor RETRY (grounding attempt %d/%d)",
                state.grounding_attempts,
                grounding_retry_attempts,
            )
            return state
        # grounding-retry budget exhausted — halt (the former Extractor ``ExtractionError``,
        # now an orchestrator-owned clean halt; never escalate to refinement).
        state.status = PipelineStatus.HALTED_VALIDATION
        state.halt_reason = (
            f"Grounding still failing after {grounding_retry_attempts} Extractor retry "
            f"attempt(s): {'; '.join(f.render() for f in retry_findings)}"
        )
        state.route = END
        return state

    async def jury_node(state: PipelineState) -> PipelineState:
        """Run the Extractor-Jury and decide the next destination.

        ``approve`` → enrich; ``revise`` → bounded refinement (re-run Extractor) or
        ship-with-``low_jury_confidence`` on cap exhaustion; ``reject`` → halt
        (``pipeline.md §3.2.3``). The verdict is the jury's judgment; this node (the
        framework) maps it to control flow (``architecture.md §1.5``). The jury *consumes*
        the orchestrator-owned grounding findings (``agents.md §5.5``, ADR 0051/0060).
        """
        assert state.spec is not None
        if recorder is not None:
            recorder.enter_stage(round_index=state.total_iterations, stage="jury_review")
        findings = state.grounding.findings if state.grounding is not None else None
        verdict = await jury.review(
            spec=state.spec, blog_content=state.blog_content, grounding_findings=findings
        )
        state.verdict = verdict
        state.verdict_history = [*state.verdict_history, verdict.verdict]
        # The jury's verdict is the agent-decision outcome of this round (the framework's downstream
        # action — refine vs ship-low-confidence — is recoverable from the following records).
        if recorder is not None:
            recorder.routing_event(verdict.verdict.value)

        if verdict.verdict is Verdict.APPROVE:
            # Mechanical rubric-floor backstop (ADR 0067): an ``approve`` whose dimension
            # scores fall below the floor is a self-contradiction the verdict↔feedback
            # validator cannot catch. The framework reads ``verdict.scores`` against the floor
            # and refuses to ship it (defense-in-depth, ``architecture.md §1.6`` — mechanical
            # safety is framework-owned). The jury still owns the holistic verdict (``§1.5``);
            # this only vetoes a verdict that disagrees with itself. An ``approve`` carries no
            # feedback (the validator forbids it), so there is nothing to refine toward — the
            # safe deterministic action is a halt, not a re-run.
            if not verdict.scores.all_above(jury.rubric_floor):
                state.status = PipelineStatus.HALTED_JURY_INCONSISTENT
                state.halt_reason = (
                    "Extractor-Jury returned approve while a rubric dimension scored below the "
                    f"floor ({jury.rubric_floor}): lowest dimension "
                    f"{verdict.scores.min_dimension()}. The framework refuses an approve that "
                    "contradicts its own scores."
                )
                state.route = END
                logger.info(
                    "jury approve contradicts sub-floor scores (min %.3f < floor %.3f); halting",
                    verdict.scores.min_dimension(),
                    jury.rubric_floor,
                )
                return state
            # Enrichment already ran before this review (ADR 0052/0061), so an approve ships
            # directly: the jury owns the ship decision (``architecture.md §1.5``).
            state.status = PipelineStatus.SHIPPED
            state.route = END
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
            # Global backstop: never start another Extractor run past the end-to-end cap,
            # even if the per-agent refinement budget still has room (L3).
            if _global_cap_reached(state):
                return _halt_global_cap(state)
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
        # (the disagreement-without-progress (b) case, pipeline.md §3.2.3). Enrichment already
        # ran before this review (ADR 0052/0061), so this ships directly. The run report's
        # unresolved_feedback is the structured feedback rendered to lines (an output boundary,
        # so stringifying here is fine — it now also preserves suggested_fix).
        state.status = PipelineStatus.SHIPPED_LOW_CONFIDENCE
        state.unresolved_feedback = feedback.feedback_lines()
        state.route = END
        logger.info("refinement cap exhausted on revise; shipping with low_jury_confidence")
        return state

    def enrich_node(state: PipelineState) -> PipelineState:
        """Run the pre-Planner enrichment framework pass, *before* the grounding stack + jury.

        ADR 0052/0061: enrichment now runs mid-pipeline (``extract → validate → enrich →
        grounding → jury``), not as a terminal node, so the jury reviews the enriched spec and
        *what ships equals what was reviewed*. It re-runs on each refinement iteration's patched
        spec (idempotent on already-``framework_enriched`` fields). It no longer sets the terminal
        ``SHIPPED`` status — the jury owns the ship decision; enrichment only produces content.
        """
        assert state.spec is not None
        state.enrichment = enrich(state.spec, enrich_cfg)
        return state

    # --- routing functions: pure readers of the node-decided destination ---

    def route_after_validate(state: PipelineState) -> str:
        assert state.route is not None  # validate_node always sets it
        return state.route

    def route_after_grounding(state: PipelineState) -> str:
        assert state.route is not None  # grounding_node always sets it
        return state.route

    def route_after_jury(state: PipelineState) -> str:
        assert state.route is not None  # jury_node always sets it
        return state.route

    graph: StateGraph[PipelineState, None, PipelineState, PipelineState] = StateGraph(PipelineState)
    # Each node is wrapped in a stage span (ADR 0041) so the extract/validate/jury/
    # enrich tree shows under the agent spans in Phoenix. ``stage_span`` is a no-op
    # context manager when tracing is off, so this is zero-cost and behaviour-neutral
    # by default — an additive, traced-only touch of the ADR-0023-locked builder.
    graph.add_node(_Node.EXTRACT.value, traced_async(_Node.EXTRACT.value, extract_node))
    graph.add_node(_Node.VALIDATE.value, traced_sync(_Node.VALIDATE.value, validate_node))
    graph.add_node(_Node.GROUNDING.value, traced_sync(_Node.GROUNDING.value, grounding_node))
    graph.add_node(_Node.JURY.value, traced_async(_Node.JURY.value, jury_node))
    graph.add_node(_Node.ENRICH.value, traced_sync(_Node.ENRICH.value, enrich_node))

    graph.add_edge(START, _Node.EXTRACT.value)
    graph.add_edge(_Node.EXTRACT.value, _Node.VALIDATE.value)
    graph.add_conditional_edges(
        _Node.VALIDATE.value,
        route_after_validate,
        {
            _Node.ENRICH.value: _Node.ENRICH.value,
            _Node.EXTRACT.value: _Node.EXTRACT.value,
            END: END,
        },
    )
    # Enrichment runs mid-pipeline (ADR 0052/0061): always proceed to the grounding stack,
    # which sees the enriched spec (and exempts framework_enriched fields), then the jury.
    graph.add_edge(_Node.ENRICH.value, _Node.GROUNDING.value)
    graph.add_conditional_edges(
        _Node.GROUNDING.value,
        route_after_grounding,
        {_Node.JURY.value: _Node.JURY.value, _Node.EXTRACT.value: _Node.EXTRACT.value, END: END},
    )
    graph.add_conditional_edges(
        _Node.JURY.value,
        route_after_jury,
        {_Node.EXTRACT.value: _Node.EXTRACT.value, END: END},
    )
    compiled = graph.compile(checkpointer=checkpointer)

    async def run(state: PipelineState | None, *, thread_id: str | None = None) -> PipelineState:
        # LangGraph threads its own reconstructed state through the nodes (mutations
        # inside routing functions are discarded — that is why every decision is
        # made in a node, ADR 0023). The *returned* channel is the source of truth,
        # not the input object; coerce it back to the typed model.
        #
        # ``recursion_limit`` is always set as the graph-level backstop (L3, ADR 0056):
        # it bounds total super-steps regardless of the application caps, so a routing
        # pathology raises ``GraphRecursionError`` rather than spinning forever. It is sized
        # above the global iteration cap's super-steps, so the clean semantic halt wins first.
        #
        # When a checkpointer is configured, a thread_id namespaces this run's checkpoints
        # (ADR 0040); LangGraph requires one, so we synthesize a default if the caller didn't
        # supply one. ``state is None`` resumes the thread from its last checkpoint.
        config: RunnableConfig = {"recursion_limit": GRAPH_RECURSION_LIMIT}
        if checkpointer is not None:
            config["configurable"] = {"thread_id": thread_id or "default"}
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
    grounding_validator: GroundingValidator | None = None,
    known_source_ids: frozenset[str] | None = None,
    enrichment_config: EnrichmentConfig | None = None,
    structural_retry_attempts: int = DEFAULT_STRUCTURAL_RETRY_ATTEMPTS,
    grounding_retry_attempts: int = DEFAULT_GROUNDING_RETRY_ATTEMPTS,
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
        grounding_validator=grounding_validator,
        known_source_ids=known_source_ids,
        enrichment_config=enrichment_config,
        structural_retry_attempts=structural_retry_attempts,
        grounding_retry_attempts=grounding_retry_attempts,
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
    if state.status is PipelineStatus.HALTED_JURY_INCONSISTENT:
        raise JuryInconsistencyError(
            state.halt_reason or "Extractor-Jury approved a spec with sub-floor rubric scores"
        )

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


def _finding_signature(result: StaticSchemaResult) -> str:
    """An order-independent signature of a failed static-schema attempt's finding set.

    Used by the no-progress early-bail (ADR 0057): two consecutive structural attempts with
    the same signature are not converging. Sorting makes it insensitive to finding order so
    only a genuine change in *which* findings fired counts as progress.
    """
    return "\n".join(sorted(result.rendered_findings()))


def _grounding_signature(findings: list[GroundingFinding]) -> str:
    """An order-independent signature of a grounding-retry attempt's finding set (ADR 0057/0060).

    Two consecutive grounding attempts with the same signature are not converging. Sorting
    makes it insensitive to finding order so only a genuine change in *which* findings fired
    counts as progress (mirrors :func:`_finding_signature`).
    """
    return "\n".join(sorted(f.render() for f in findings))


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


class JuryInconsistencyError(JuryRejectionError):
    """The jury returned ``approve`` while a rubric dimension scored below the floor → halt.

    A mechanical defense-in-depth backstop (ADR 0067): the framework reads
    ``verdict.scores`` against the rubric floor and refuses to ship an ``approve`` that
    contradicts its own sub-floor scores (``architecture.md §1.6`` — mechanical safety
    checks are framework-owned, never LLM-routed). Subclasses :class:`JuryRejectionError`
    so it inherits the reject-class terminal-status mapping (a quality halt, no
    static-schema findings) while remaining a distinct type the run report and tests can
    name. The jury still owns the holistic verdict (``§1.5``); this only catches the
    self-contradiction the verdict↔feedback validator cannot see.
    """


__all__ = [
    "DEFAULT_GROUNDING_RETRY_ATTEMPTS",
    "DEFAULT_REFINEMENT_CAP",
    "DEFAULT_STRUCTURAL_RETRY_ATTEMPTS",
    "GLOBAL_ITERATION_CAP",
    "GRAPH_RECURSION_LIMIT",
    "FeedbackKind",
    "JuryInconsistencyError",
    "JuryRejectionError",
    "PipelineOutcome",
    "PipelineState",
    "PipelineStatus",
    "RefinementFeedback",
    "build_pipeline",
    "reject_interactive_when_headless",
    "run_pipeline",
]
