"""The ``extract`` verb's engine: runner seam, interrupt menus, YAML write.

Architectural source: ``pipeline.md §3.1`` / §3.1.1 (modes, headless rejection,
budget-overrun interrupts in both modes), ``pipeline.md §3.2.5`` (the
post-Extractor interrupt — the four-option menu, the per-proposal Accept/Edit
menu, ``$EDITOR`` revalidation), ``implementation-plan.md §4.2`` ("Post-Extractor
interrupt"), ADR 0024.

The ``extract`` verb in :mod:`cyberlab_gen.cli.main` is thin: it parses flags,
builds an :class:`ExtractRunner`, and delegates to :func:`run_extract`. This
module owns the interaction logic so it can be tested without a live provider:
tests inject a fake :class:`ExtractRunner` returning a scripted
:class:`RunResult`, and an ``editor`` callable that simulates a ``$EDITOR``
session.

Two failure mechanisms cross into this layer from the orchestrator (ADR 0023):
they raise (``ValidationError`` on static-schema exhaustion, ``JuryRejectionError`` on a
jury ``reject``); the verb surfaces those as clean errors. Everything reaching
the interrupt is a *shipped* (clean or low-confidence) ``RunResult``.
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import click
import typer
from pydantic import Field
from ruamel.yaml import YAML
from ruamel.yaml.compat import StringIO

from cyberlab_gen.agents.proposals import ProposedFacet, ProposedThesisType, ProposedValueType
from cyberlab_gen.cli import output
from cyberlab_gen.framework.orchestrator import (
    PipelineStatus,
    reject_interactive_when_headless,
)
from cyberlab_gen.framework.proposal_acceptance import (
    AcceptanceContext,
    accept_facet,
    accept_thesis_type,
    accept_value_type,
    auto_accept_to_overlay,
)
from cyberlab_gen.providers import AgentLabel
from cyberlab_gen.schemas.attack_spec import AttackSpec, MaterialDiscrepancy
from cyberlab_gen.schemas.base import InternalModel
from cyberlab_gen.state.run_store import (
    ENRICHMENT_FILENAME,
    JURY_VERDICT_FILENAME,
    SPEC_FILENAME,
    RunKind,
    RunLineage,
    RunStatus,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from cyberlab_gen.agents.extractor.extractor import Extractor
    from cyberlab_gen.agents.extractor_jury.jury import ExtractorJury
    from cyberlab_gen.framework.orchestrator import PipelineState
    from cyberlab_gen.providers.cost_ledger import CostLedger
    from cyberlab_gen.schemas.ingestion import IngestionResult
    from cyberlab_gen.state.local_state import LocalState
    from cyberlab_gen.state.run_store import RunHandle, RunStore
    from cyberlab_gen.validators.static_schema_validator import StaticSchemaValidator

logger = logging.getLogger(__name__)

#: The file the verb writes the approved/accepted AttackSpec to, in the cwd.
ATTACK_SPEC_FILENAME = "attack-spec.yaml"

#: Per-run cap on auto-accepted proposals in ``--auto`` mode (placeholder 5,
#: ``implementation-plan.md §4.2``; revisited in Phase 4). ``--interactive`` has
#: no cap — the user acts on every proposal individually.
DEFAULT_AUTO_ACCEPT_PROPOSAL_CAP = 5


# --- the typed result the interrupt consumes (ADR 0024) --------------------


class RunResult(InternalModel):
    """Everything the post-Extractor interrupt needs, in one typed object.

    ``run_pipeline`` (ADR 0023) returns a ``PipelineOutcome`` that carries neither
    the registry proposals nor a next-stage cost estimate; the per-proposal review
    surface (``pipeline.md §3.2.5``) and the budget-overrun interrupt (§3.1.1)
    both need data ``PipelineOutcome`` omits. The runner packs it here. ``spec``
    is the *enriched* AttackSpec (enrichment ran inside the pipeline);
    ``material_discrepancies`` mirrors ``spec.material_discrepancies`` for the
    run-report listing (Phase 1 surfaces them in the report only, not at an
    interrupt — the third review surface lands in Phase 4).
    """

    spec: AttackSpec
    value_type_proposals: list[ProposedValueType] = Field(default_factory=list[ProposedValueType])
    facet_proposals: list[ProposedFacet] = Field(default_factory=list[ProposedFacet])
    thesis_type_proposals: list[ProposedThesisType] = Field(
        default_factory=list[ProposedThesisType]
    )
    material_discrepancies: list[MaterialDiscrepancy] = Field(
        default_factory=list[MaterialDiscrepancy]
    )
    status: PipelineStatus = PipelineStatus.SHIPPED
    low_jury_confidence: bool = False
    unresolved_feedback: list[str] = Field(default_factory=list[str])
    #: Estimated USD spend of the *next* stage (the Planner, once it ships). The
    #: budget-overrun interrupt compares ``ledger.total_usd + this`` to the cap.
    #: Phase 1 has no Planner, so the default runner supplies ``0``.
    estimated_next_stage_cost: Decimal = Decimal("0")

    def is_out_of_scope(self) -> bool:
        """True when the Extractor judged the blog out of scope (``§3.1.1``)."""
        from cyberlab_gen.schemas.enums import ExtractionOutcome

        return self.spec.extraction_outcome is ExtractionOutcome.OUT_OF_SCOPE


# --- the runner seam (ADR 0024) --------------------------------------------


class ExtractRunner(Protocol):
    """The pipeline surface the verb depends on (the testability boundary).

    The default :class:`PipelineExtractRunner` wires Ingestion → the orchestrator;
    tests inject a fake returning a scripted :class:`RunResult`. ``run`` is the
    first pass; ``re_run_with_feedback`` backs option 2 of the four-option menu
    (natural-language feedback → Extractor re-runs). The free text never crosses
    the seam as a pipeline-stage payload — it is a prompt addendum the runner
    folds in, the typed ``RunResult`` is the return contract (``architecture.md
    §1.5``).
    """

    def run(self, url: str, *, ledger: CostLedger) -> RunResult: ...

    def re_run_with_feedback(self, feedback: str, *, ledger: CostLedger) -> RunResult: ...


class PipelineExtractRunner:
    """The production :class:`ExtractRunner`: Ingestion → the orchestrator (ADR 0024).

    Drives :func:`build_pipeline` (not :func:`run_pipeline`) so it can read the
    final ``PipelineState.extraction`` for the registry proposals the per-proposal
    review surface needs (``PipelineOutcome`` omits them, ADR 0024). The first
    :meth:`run` ingests + caches the blog; :meth:`re_run_with_feedback` reuses the
    cached content (``pipeline.md §3.2.1`` — downstream never re-fetches) and folds
    the user's feedback into the Extractor's prompt channel (the same tunnelling as
    ADR 0023's ``RefinementFeedback``).

    Requires a configured provider; absent one the agents raise ``HardFailure``
    at resolve time (``provider-interface.md §6.3``). Phase-1 Task-7 *tests* use a
    fake runner — this path is exercised end-to-end by the eval harness (Task 8).
    """

    def __init__(
        self,
        *,
        extractor: Extractor,
        validator: StaticSchemaValidator,
        jury: ExtractorJury,
        state: LocalState | None = None,
    ) -> None:
        self._extractor = extractor
        self._validator = validator
        self._jury = jury
        self._state = state
        self._ingestion: IngestionResult | None = None
        self._blog_content: str | None = None
        self._checkpoint_db: Path | None = None
        self._checkpoint_thread_base: str | None = None
        self._checkpoint_seq = 0

    def enable_checkpointing(self, db_path: Path, *, thread_id: str) -> None:
        """Persist completed-node state to ``db_path`` so a mid-node crash resumes (ADR 0040).

        Each :meth:`_drive` call (the first run and every feedback re-run) gets a
        *distinct* thread (``<thread_id>-<seq>``), so a re-run is always a fresh graph
        run — it never accidentally resumes a previously-completed graph. The
        checkpoints for a crashed run persist under ``db_path``, keyed by their thread,
        for a future ``--resume`` to pick up (the flag itself is deferred).
        """
        self._checkpoint_db = db_path
        self._checkpoint_thread_base = thread_id

    @property
    def last_state(self) -> PipelineState | None:
        """The latest (possibly partial) ``PipelineState`` this run reached, read from the checkpoint.

        G1/ADR 0053: the run store's single source of truth for what a run produced is
        the checkpoint the LangGraph checkpointer wrote on each completed super-step —
        never an in-memory field set only on a clean graph return. Reading it here is what
        lets a mid-graph abort (Ctrl-C / budget halt / crash), which never returns
        cleanly, still surface the partial AttackSpec for persistence — closing the L4
        drop-partial-on-abort gap (a spec already in ``checkpoint.sqlite`` that the run
        record never listed). On a clean ship the latest checkpoint is the final enriched
        state, so the same read serves both halt and ship. ``None`` when this run was
        driven without a checkpoint (no persistence) or none was written yet.
        """
        if self._checkpoint_db is None:
            return None
        from cyberlab_gen.framework.checkpointing import read_latest_pipeline_state

        return read_latest_pipeline_state(self._checkpoint_db)

    @property
    def content_hash(self) -> str | None:
        """The ingested blog's content hash, for run lineage (``None`` before :meth:`run`)."""
        return self._ingestion.content_hash if self._ingestion is not None else None

    def run(self, url: str, *, ledger: CostLedger) -> RunResult:
        from cyberlab_gen.framework.ingestion import ingest, read_cached_text

        ingestion = ingest(url, state=self._state)
        text = read_cached_text(ingestion.content_hash, state=self._state)
        if text is None:  # pragma: no cover - ingest always writes the cache
            raise RuntimeError("ingestion did not write normalized text to the cache")
        self._ingestion = ingestion
        self._blog_content = text
        return self._drive(extra_feedback=None, ledger=ledger)

    def re_run_with_feedback(self, feedback: str, *, ledger: CostLedger) -> RunResult:
        if self._ingestion is None or self._blog_content is None:  # pragma: no cover - guard
            raise RuntimeError("re_run_with_feedback called before run")
        return self._drive(extra_feedback=feedback, ledger=ledger)

    def _drive(self, *, extra_feedback: str | None, ledger: CostLedger) -> RunResult:
        import asyncio

        from cyberlab_gen.framework.orchestrator import (
            PipelineState,
            _ingestion_summary,  # pyright: ignore[reportPrivateUsage]
            build_pipeline,
        )

        del ledger  # the agents record into the ledger via the provider; not read here
        assert self._ingestion is not None
        assert self._blog_content is not None
        summary = _ingestion_summary(self._ingestion)
        if extra_feedback is not None:
            summary = f"{summary}\n\nUSER FEEDBACK (re-extract addressing this):\n{extra_feedback}"
        initial = PipelineState(blog_content=self._blog_content, source_summary=summary)

        async def _go() -> PipelineState:
            if self._checkpoint_db is None:
                run = build_pipeline(
                    extractor=self._extractor, validator=self._validator, jury=self._jury
                )
                return await run(initial)
            # Checkpointed: a fresh thread per drive (so a feedback re-run never
            # resumes a completed graph) under a sqlite saver held open across the run
            # (ADR 0040). The saver requires an open async connection.
            from cyberlab_gen.framework.checkpointing import open_sqlite_checkpointer

            thread = f"{self._checkpoint_thread_base}-{self._checkpoint_seq}"
            self._checkpoint_seq += 1
            async with open_sqlite_checkpointer(self._checkpoint_db) as saver:
                run = build_pipeline(
                    extractor=self._extractor,
                    validator=self._validator,
                    jury=self._jury,
                    checkpointer=saver,
                )
                return await run(initial, thread_id=thread)

        # The run's state (partial or terminal) lives in the checkpoint the saver wrote on
        # every completed super-step; :attr:`last_state` reads it back. We do NOT cache the
        # clean-return value in memory — that in-memory "second path" is what missed the
        # mid-graph abort case (G1/ADR 0053). ``_go`` may abort before returning; the
        # checkpoint still holds whatever the pipeline reached.
        return _state_to_run_result(asyncio.run(_go()))


def _state_to_run_result(state: object) -> RunResult:
    """Map a terminal ``PipelineState`` onto the Task-7 ``RunResult`` (ADR 0024).

    Raises the orchestrator's halt errors (``ValidationError`` /
    ``JuryRejectionError``) so the verb maps them to a clean error + exit code;
    only a *shipped* state produces a ``RunResult``.
    """
    from cyberlab_gen.framework.orchestrator import (
        JuryRejectionError,
        PipelineState,
        PipelineStatus,
    )

    if not isinstance(state, PipelineState):  # pragma: no cover - defensive
        raise TypeError("expected a terminal PipelineState")
    if state.status is PipelineStatus.HALTED_VALIDATION:
        from cyberlab_gen.errors import ValidationError

        findings = (
            state.static_schema.rendered_findings() if state.static_schema is not None else []
        )
        raise ValidationError(
            state.halt_reason or "Static schema validation failed past the retry budget",
            findings=findings,
        )
    if state.status is PipelineStatus.HALTED_REJECT:
        raise JuryRejectionError(state.halt_reason or "Extractor-Jury rejected the AttackSpec")

    assert state.spec is not None
    extraction = state.extraction
    return RunResult(
        spec=state.spec,
        value_type_proposals=extraction.value_type_proposals if extraction is not None else [],
        facet_proposals=extraction.facet_proposals if extraction is not None else [],
        thesis_type_proposals=extraction.thesis_type_proposals if extraction is not None else [],
        material_discrepancies=list(state.spec.material_discrepancies),
        status=state.status or PipelineStatus.SHIPPED,
        low_jury_confidence=state.status is PipelineStatus.SHIPPED_LOW_CONFIDENCE,
        unresolved_feedback=state.unresolved_feedback,
    )


# --- menu option enums -----------------------------------------------------


class ArtifactChoice(StrEnum):
    """The four-option menu for the AttackSpec itself (``pipeline.md §3.1.1``)."""

    APPROVE = "approve"
    FEEDBACK = "feedback"
    EDIT = "edit"
    ABORT = "abort"


class ProposalChoice(StrEnum):
    """The per-proposal menu (``pipeline.md §3.2.5``): Accept or Edit only.

    Rejecting a single proposal in isolation has no coherent semantics
    (``pipeline.md §3.2.5`` note) — the value exists in the AttackSpec and the
    system requires typed values. A user who disagrees Edits, gives Extractor
    feedback at the artifact level, or Aborts.
    """

    ACCEPT = "accept"
    EDIT = "edit"


class BudgetChoice(StrEnum):
    """The budget-overrun menu (``pipeline.md §3.1.1``), honored in both modes."""

    RAISE = "raise"
    PROCEED = "proceed"
    ABORT = "abort"


#: Type of the editor callable: takes the text to edit, returns the edited text
#: (or ``None`` if the user made no change / aborted the editor). ``click.edit``
#: matches this; tests inject a fake.
type EditorFn = Callable[[str], str | None]


# --- YAML (de)serialization for the artifact + editor round-trip -----------


def _yaml() -> YAML:
    y = YAML()
    y.default_flow_style = False
    y.width = 4096  # don't wrap long citation strings
    return y


def spec_to_yaml(spec: AttackSpec) -> str:
    """Serialize an ``AttackSpec`` to YAML text (the on-disk + editor form)."""
    buf = StringIO()
    _yaml().dump(spec.model_dump(mode="json", by_alias=True), buf)
    return buf.getvalue()


def write_attack_spec(spec: AttackSpec, *, directory: Path) -> Path:
    """Write ``spec`` as ``attack-spec.yaml`` in ``directory``; return the path."""
    path = directory / ATTACK_SPEC_FILENAME
    path.write_text(spec_to_yaml(spec), encoding="utf-8")
    return path


def _load_spec_from_yaml(text: str) -> AttackSpec:
    """Parse + structurally revalidate edited YAML into an ``AttackSpec``.

    Raises ``pydantic.ValidationError`` (or a YAML parse error) on a structurally
    invalid edit; the caller turns that into editor-reopening comments.
    """
    data = _yaml().load(StringIO(text))
    return AttackSpec.model_validate(data)


# --- the post-Extractor interrupt (interactive mode) -----------------------


def _format_spec_summary(result: RunResult) -> str:
    """A compact human summary shown above the four-option menu."""
    spec = result.spec
    lines = [
        "",
        "=== AttackSpec ready for review (post-Extractor interrupt) ===",
        f"scope: {spec.extraction_outcome.value}",
        f"facets: {', '.join(spec.facets) if spec.facets else '(none)'}",
        f"gaps: {len(spec.gaps)}",
        f"value-type proposals: {len(result.value_type_proposals)}",
        f"facet proposals: {len(result.facet_proposals)}",
        f"thesis-type proposals: {len(result.thesis_type_proposals)}",
        f"material discrepancies (report-only): {len(result.material_discrepancies)}",
    ]
    if result.low_jury_confidence:
        lines.append("NOTE: shipped with low_jury_confidence (unresolved jury feedback).")
    return "\n".join(lines)


def _prompt_artifact_choice() -> ArtifactChoice:
    """Render and read the four-option menu (``pipeline.md §3.1.1``)."""
    output.print_info(
        "\nChoose: [a]pprove  [f]eedback (re-run Extractor)  [e]dit in $EDITOR  a[b]ort"
    )
    raw = typer.prompt("action", default="a").strip().lower()
    mapping = {
        "a": ArtifactChoice.APPROVE,
        "approve": ArtifactChoice.APPROVE,
        "f": ArtifactChoice.FEEDBACK,
        "feedback": ArtifactChoice.FEEDBACK,
        "e": ArtifactChoice.EDIT,
        "edit": ArtifactChoice.EDIT,
        "b": ArtifactChoice.ABORT,
        "abort": ArtifactChoice.ABORT,
    }
    choice = mapping.get(raw)
    if choice is None:
        output.print_error(f"unrecognized choice {raw!r}; treating as abort")
        return ArtifactChoice.ABORT
    return choice


def _edit_spec_with_revalidation(spec: AttackSpec, *, editor: EditorFn) -> AttackSpec:
    """Open the spec in ``$EDITOR``; reopen with error-comments on invalid edits.

    ``pipeline.md §3.1.1`` / §3.2.5: user edits are *structurally* re-validated
    only; a structurally invalid edit reopens the editor with the errors prepended
    as comments. The user may abort the editor (return ``None``/unchanged) to keep
    the original. Semantic correctness of edits is the user's responsibility.
    """
    text = spec_to_yaml(spec)
    while True:
        edited = editor(text)
        if edited is None or edited == text:
            return spec  # no change / editor aborted → keep the original
        try:
            return _load_spec_from_yaml(edited)
        except Exception as exc:
            # Broad by design: the user can paste arbitrary YAML into the editor, so
            # any parse/validation failure re-prompts with the errors inlined below.
            # Logged at WARNING with the traceback so a genuine bug surfaces in the
            # run log rather than being silently mistaken for a user typo.
            logger.warning(
                "edited AttackSpec failed structural revalidation: %s", exc, exc_info=True
            )
            comment = _errors_as_comments(exc)
            # Reopen with the errors as leading comments + the user's text below.
            text = f"{comment}\n{edited}"


def _errors_as_comments(exc: Exception) -> str:
    """Render a validation/parse error as ``#``-prefixed editor comment lines."""
    lines = ["# STRUCTURAL VALIDATION FAILED — fix these and re-save:"]
    for raw_line in str(exc).splitlines():
        lines.append(f"# {raw_line}")
    return "\n".join(lines)


def _review_proposals_interactive(
    result: RunResult, *, editor: EditorFn, ctx: AcceptanceContext
) -> None:
    """Per-proposal Accept/Edit loop, writing each accepted entry to the overlay.

    ``pipeline.md §3.2.5`` / ADR 0044: each value-type and facet proposal is reviewed
    individually — Accept (write the entry to the overlay, marked human-approved) or
    Edit (re-open, edit, structurally revalidate, then write; an invalid edit reopens
    the editor with error comments). No Reject — see :class:`ProposalChoice`. No cap in
    interactive mode: the user acts on every proposal.
    """
    for vt in result.value_type_proposals:
        reviewed = _review_one_proposal(
            label=f"value_type proposal {vt.name!r}",
            model=vt,
            parse=ProposedValueType.model_validate,
            editor=editor,
        )
        accept_value_type(reviewed, ctx, approval="human")
        output.print_info(f"  + accepted value_type {reviewed.name!r} into the overlay")
    for facet in result.facet_proposals:
        reviewed = _review_one_proposal(
            label=f"facet proposal {facet.name!r} (category={facet.category})",
            model=facet,
            parse=ProposedFacet.model_validate,
            editor=editor,
        )
        accept_facet(reviewed, ctx, approval="human")
        output.print_info(f"  + accepted facet {reviewed.name!r} into the overlay")
    for thesis in result.thesis_type_proposals:
        reviewed = _review_one_proposal(
            label=f"thesis_type proposal {thesis.name!r}",
            model=thesis,
            parse=ProposedThesisType.model_validate,
            editor=editor,
        )
        accept_thesis_type(reviewed, ctx, approval="human")
        output.print_info(f"  + accepted thesis_type {reviewed.name!r} into the overlay")


def _default_proposal_choice_reader() -> str:
    return typer.prompt("proposal action ([a]ccept / [e]dit)", default="a")


def _review_one_proposal[T: (ProposedValueType, ProposedFacet, ProposedThesisType)](
    *,
    label: str,
    model: T,
    parse: Callable[[object], T],
    editor: EditorFn,
    choice_reader: Callable[[], str] = _default_proposal_choice_reader,
) -> T:
    """Run the Accept/Edit menu for one proposal; return the (possibly edited) one.

    ``choice_reader`` is injectable so the per-proposal menu can be tested without
    stdin; it defaults to a ``typer.prompt``. The CLI path drives the prompt via
    ``CliRunner(input=...)``.
    """
    output.print_info(f"\nProposal: {label}")
    raw = choice_reader().strip().lower()
    if raw in ("a", "accept"):
        return model
    # edit-with-revalidation, same loop as the artifact edit
    text = _proposal_to_yaml(model)
    while True:
        edited = editor(text)
        if edited is None or edited == text:
            return model
        try:
            data = _yaml().load(StringIO(edited))
            return parse(data)
        except Exception as exc:
            # Broad by design (see _edit_spec): arbitrary user edits re-prompt with
            # the errors inlined; logged with the traceback so a real bug is visible.
            logger.warning("edited proposal failed structural revalidation: %s", exc, exc_info=True)
            text = f"{_errors_as_comments(exc)}\n{edited}"


def _proposal_to_yaml(model: ProposedValueType | ProposedFacet | ProposedThesisType) -> str:
    buf = StringIO()
    _yaml().dump(model.model_dump(mode="json"), buf)
    return buf.getvalue()


# --- the budget-overrun interrupt (both modes, ``pipeline.md §3.1.1``) ------


def _would_overrun(result: RunResult, ledger: CostLedger) -> bool:
    """True when the estimated next-stage spend would push past the cap."""
    if ledger.cap_usd is None:
        return False
    projected = ledger.total_usd + result.estimated_next_stage_cost
    return projected > ledger.cap_usd


def _handle_budget_overrun(result: RunResult, ledger: CostLedger, *, interactive: bool) -> bool:
    """Surface the budget-overrun interrupt; return ``True`` to proceed.

    Honored in **both** modes (``pipeline.md §3.1.1`` — the one exception to
    "``--auto`` has no interrupts"). In a headless/non-interactive context the
    only safe non-hanging action is to abort. Returns ``True`` if the run should
    continue, ``False`` if it must abort.
    """
    projected = ledger.total_usd + result.estimated_next_stage_cost
    output.print_info(
        f"\nBUDGET OVERRUN: next stage est. ${result.estimated_next_stage_cost} would bring "
        f"accumulated spend to ${projected}, past the cap of ${ledger.cap_usd}."
    )
    if not interactive:
        # --auto: surface the choice; without a TTY we cannot prompt, so the
        # framework's safe default is to abort rather than silently overspend.
        output.print_info(
            "Running in --auto: aborting rather than exceeding the cap. "
            "Re-run with a higher --max-llm-cost to proceed."
        )
        return False
    raw = (
        typer.prompt("budget ([r]aise cap / [p]roceed past cap / a[b]ort)", default="b")
        .strip()
        .lower()
    )
    if raw in ("r", "raise"):
        new_cap = typer.prompt("new cap (USD)", default=str(projected))
        ledger.cap_usd = Decimal(str(new_cap))
        output.print_info(f"cap raised to ${ledger.cap_usd}; proceeding.")
        return True
    if raw in ("p", "proceed"):
        output.print_info("proceeding past the cap explicitly.")
        return True
    output.print_info("aborting on budget overrun.")
    return False


# --- the run report (Phase 1: material discrepancies report-only) ----------


def _emit_run_report(result: RunResult) -> None:
    """Print the Phase-1 run-report tail (``implementation-plan.md §4.2``).

    Material discrepancies are listed here only (no interrupt in Phase 1 — the
    third review surface lands in Phase 4). Unresolved jury feedback and the
    low-confidence flag are also surfaced.
    """
    if result.material_discrepancies:
        output.print_info("\nMaterial discrepancies (report-only, blog vs. external API):")
        for md in result.material_discrepancies:
            output.print_info(f"  - {md.field_path}: {md.summary}")
    if result.low_jury_confidence:
        output.print_info("\nShipped with low_jury_confidence. Unresolved jury feedback:")
        for item in result.unresolved_feedback:
            output.print_info(f"  - {item}")


# --- the verb body ---------------------------------------------------------


def run_extract(
    *,
    url: str,
    interactive: bool,
    auto: bool,
    runner: ExtractRunner,
    ledger: CostLedger,
    stdin_is_tty: bool,
    editor: EditorFn = click.edit,
    out_dir: Path | None = None,
    run_store: RunStore | None = None,
    overlay_dir: Path | None = None,
) -> Path | None:
    """Run the ``extract`` pipeline and the post-Extractor interrupt.

    Returns the path of the written ``attack-spec.yaml`` on a ship, or ``None``
    when the run was aborted (by the user, by an out-of-scope ``--auto`` halt, or
    by a budget-overrun abort). Raises ``typer.Exit`` / the orchestrator's
    halt errors to the caller for exit-code mapping.

    When ``run_store`` is supplied (production), a complete run-record directory is
    persisted on **every** exit path — ship, halt, budget abort, ``KeyboardInterrupt``,
    or crash (ADR 0039): the spec, jury verdict and enrichment (complete or partial),
    the cost breakdown, and a finalized ``run.json``. The cwd ``attack-spec.yaml``
    deliverable is unchanged; the run dir mirrors it. When ``run_store`` is ``None``
    (unit tests not exercising persistence) the behaviour is exactly as before.

    Mode resolution mirrors ADR 0013: neither flag set defaults to
    ``--interactive``; both set is a usage error (caught upstream in the verb).
    """
    del interactive  # mode is decided by ``auto``; default is interactive (ADR 0013)
    mode_interactive = not auto
    # Headless guard: reject --interactive when stdin is not a TTY (§3.1). This is a
    # usage error before any spend — fail before opening a run directory.
    reject_interactive_when_headless(interactive=mode_interactive, stdin_is_tty=stdin_is_tty)

    target_dir = out_dir if out_dir is not None else Path.cwd()
    from cyberlab_gen.registries.loader import default_overlay_dir

    overlay = overlay_dir if overlay_dir is not None else default_overlay_dir()

    if run_store is None:
        result = runner.run(url, ledger=ledger)
        return _drive_result(
            result=result,
            runner=runner,
            ledger=ledger,
            editor=editor,
            target_dir=target_dir,
            mode_interactive=mode_interactive,
            handle=None,
            overlay_dir=overlay,
        )

    handle = run_store.start(
        kind=RunKind.EXTRACT,
        label=url,
        lineage=RunLineage(input_ref=url, code_version=_code_version()),
    )
    # Persist completed-node state so a mid-node crash is resumable (ADR 0040). Only
    # the production runner supports it; a fake test runner is left untouched.
    if isinstance(runner, PipelineExtractRunner):
        runner.enable_checkpointing(
            handle.directory / "checkpoint.sqlite", thread_id=handle.record.run_id
        )
    result: RunResult | None = None
    path: Path | None = None
    try:
        result = runner.run(url, ledger=ledger)
        path = _drive_result(
            result=result,
            runner=runner,
            ledger=ledger,
            editor=editor,
            target_dir=target_dir,
            mode_interactive=mode_interactive,
            handle=handle,
            overlay_dir=overlay,
        )
        return path
    finally:
        # Runs on success, halt, budget abort, Ctrl-C and any crash: persist whatever
        # the pipeline produced so a run never ends with nothing to read (ADR 0039).
        _persist_run(handle, runner=runner, result=result, ledger=ledger, path=path)


def _drive_result(
    *,
    result: RunResult,
    runner: ExtractRunner,
    ledger: CostLedger,
    editor: EditorFn,
    target_dir: Path,
    mode_interactive: bool,
    handle: RunHandle | None,
    overlay_dir: Path,
) -> Path | None:
    """Dispatch to the interactive or auto post-Extractor flow."""
    if mode_interactive:
        return _drive_interactive(
            result=result,
            runner=runner,
            ledger=ledger,
            editor=editor,
            target_dir=target_dir,
            handle=handle,
            overlay_dir=overlay_dir,
        )
    return _drive_auto(
        result=result, ledger=ledger, target_dir=target_dir, handle=handle, overlay_dir=overlay_dir
    )


def _acceptance_context(
    result: RunResult, *, overlay_dir: Path, handle: RunHandle | None
) -> AcceptanceContext:
    """Assemble the framework-known audit context for accepting this run's proposals.

    ``source_blog`` and ``proposed_by_model`` come off the shipped spec; ``run_id``
    (the proposal's ``proposed_in_run``) comes from the run store when present. No
    ``source_lab`` — the lab does not exist at extraction time (ADR 0044).
    """
    return AcceptanceContext(
        overlay_dir=overlay_dir,
        source_blog=str(result.spec.source.url),
        proposed_by_model=str(result.spec.extraction_metadata.model),
        proposed_at=datetime.now(UTC),
        run_id=handle.record.run_id if handle is not None else None,
    )


def _drive_auto(
    *,
    result: RunResult,
    ledger: CostLedger,
    target_dir: Path,
    handle: RunHandle | None,
    overlay_dir: Path,
) -> Path | None:
    """``--auto``: no interrupts except budget-overrun; out-of-scope halts (§3.1.1)."""
    if result.is_out_of_scope():
        output.print_info(
            "\nOut-of-scope content detected; halting in --auto "
            f"(reason: {result.spec.extraction_outcome_reason}). "
            "Re-run with --interactive to proceed anyway."
        )
        return None
    if _would_overrun(result, ledger) and not _handle_budget_overrun(
        result, ledger, interactive=False
    ):
        return None
    # Over-cap proposals halt for inspection (schema.md §4.16 (c), ADR 0044) — raises
    # ProposalCapExceeded before any overlay or spec write, so it is never a silent drop.
    _auto_accept_proposals(result, overlay_dir=overlay_dir, handle=handle)
    _emit_run_report(result)
    path = write_attack_spec(result.spec, directory=target_dir)
    _mirror_spec(handle, result.spec)
    output.print_info(f"\nwrote {path}")
    return path


def _auto_accept_proposals(
    result: RunResult, *, overlay_dir: Path, handle: RunHandle | None
) -> None:
    """Auto-accept proposals into the overlay up to the per-run cap (ADR 0044).

    Beyond the cap the run **halts** with :class:`ProposalCapExceeded` (``schema.md
    §4.16`` option (c)) — a clear report for inspection, not a silent drop. Writing
    happens only when the run is within the cap, so the halt leaves the overlay
    untouched and no ``attack-spec.yaml`` is produced.
    """
    total = (
        len(result.value_type_proposals)
        + len(result.facet_proposals)
        + len(result.thesis_type_proposals)
    )
    if total > DEFAULT_AUTO_ACCEPT_PROPOSAL_CAP:
        from cyberlab_gen.errors import ProposalCapExceeded

        labels = (
            [f"value_type {vt.name!r}" for vt in result.value_type_proposals]
            + [f"facet {f.name!r}" for f in result.facet_proposals]
            + [f"thesis_type {t.name!r}" for t in result.thesis_type_proposals]
        )
        raise ProposalCapExceeded(
            f"--auto produced {total} registry proposals, over the per-run cap of "
            f"{DEFAULT_AUTO_ACCEPT_PROPOSAL_CAP}; halting for review (no overlay or "
            "attack-spec.yaml written). Re-run with --interactive to review each. "
            f"Proposals: {', '.join(labels)}"
        )
    ctx = _acceptance_context(result, overlay_dir=overlay_dir, handle=handle)
    accepted = auto_accept_to_overlay(
        value_type_proposals=result.value_type_proposals,
        facet_proposals=result.facet_proposals,
        thesis_type_proposals=result.thesis_type_proposals,
        ctx=ctx,
        cap=DEFAULT_AUTO_ACCEPT_PROPOSAL_CAP,
    )
    if accepted.accepted:
        output.print_info(
            f"\nauto-accepted {len(accepted.accepted)} proposal(s) into the overlay "
            f"({overlay_dir}):"
        )
        for label in accepted.accepted:
            output.print_info(f"  + {label}")


def _drive_interactive(
    *,
    result: RunResult,
    runner: ExtractRunner,
    ledger: CostLedger,
    editor: EditorFn,
    target_dir: Path,
    handle: RunHandle | None,
    overlay_dir: Path,
) -> Path | None:
    """``--interactive``: the four-option menu + per-proposal review + budget check."""
    while True:
        # Out-of-scope surfaces as a normal interrupt in --interactive (§3.1.1):
        # the four-option menu still applies (the user may give feedback or abort).
        if result.is_out_of_scope():
            output.print_info(
                "\nOut-of-scope content detected "
                f"(reason: {result.spec.extraction_outcome_reason})."
            )
        output.print_info(_format_spec_summary(result))
        choice = _prompt_artifact_choice()

        if choice is ArtifactChoice.ABORT:
            output.print_info("aborted; no attack-spec.yaml written.")
            return None
        if choice is ArtifactChoice.FEEDBACK:
            feedback = typer.prompt("feedback for the Extractor")
            result = runner.re_run_with_feedback(feedback, ledger=ledger)
            continue  # re-show the menu against the re-run result
        if choice is ArtifactChoice.EDIT:
            result = result.model_copy(
                update={"spec": _edit_spec_with_revalidation(result.spec, editor=editor)}
            )
            continue  # re-show the menu against the edited spec
        # APPROVE: review proposals (writing each accepted one to the overlay),
        # check budget, write.
        ctx = _acceptance_context(result, overlay_dir=overlay_dir, handle=handle)
        _review_proposals_interactive(result, editor=editor, ctx=ctx)
        if _would_overrun(result, ledger) and not _handle_budget_overrun(
            result, ledger, interactive=True
        ):
            return None
        _emit_run_report(result)
        path = write_attack_spec(result.spec, directory=target_dir)
        _mirror_spec(handle, result.spec)
        output.print_info(f"\nwrote {path}")
        return path


# --- run-store persistence (ADR 0039) --------------------------------------


def _mirror_spec(handle: RunHandle | None, spec: AttackSpec) -> None:
    """Mirror the shipped (post-edit) AttackSpec into the run directory."""
    if handle is not None:
        handle.write_artifact(SPEC_FILENAME, spec)


def _persist_run(
    handle: RunHandle,
    *,
    runner: ExtractRunner,
    result: RunResult | None,
    ledger: CostLedger,
    path: Path | None,
) -> None:
    """Persist the run's artifacts + cost and finalize ``run.json`` (best-effort).

    Called from a ``finally`` so it runs on every exit path. The per-stage artifacts
    come from the runner's last ``PipelineState`` (available even on a halt, before the
    halt errors are raised); a clean ship has already mirrored the post-edit spec.
    """
    _persist_from_state(handle, getattr(runner, "last_state", None))
    handle.write_cost(ledger)
    _populate_lineage(handle, runner=runner, ledger=ledger)
    status, reason = _resolve_status(result=result, path=path, exc=sys.exc_info()[1])
    handle.finalize(status, halt_reason=reason)


def _billed_extractor_model(ledger: CostLedger) -> str | None:
    """The provider model the framework actually billed for extraction — the authoritative
    source for ``lineage.model``.

    Provenance is a framework fact: ``lineage.model`` must come from the billed cost ledger,
    **never** from the LLM-authored ``extraction_metadata.model`` (``architecture.md §1.5``;
    investigation 0002 §7 — a real run self-reported ``"claude-sonnet"`` into its own spec
    metadata while the ledger correctly billed ``claude-opus-4-8``). Prefers the last
    ``EXTRACTOR``-labelled entry (the model that produced the spec); falls back to the last
    billed entry, then ``None`` (empty ledger — nothing billed yet).
    """
    extractor_entries = [e for e in ledger.entries if e.agent_label is AgentLabel.EXTRACTOR]
    pool = extractor_entries or ledger.entries
    return pool[-1].model if pool else None


def _populate_lineage(handle: RunHandle, *, runner: ExtractRunner, ledger: CostLedger) -> None:
    """Fill run lineage knowable even on a failed (pre-emit) run — what makes runs
    comparable (ADR 0039).

    ``extractor_version`` comes from the emitted spec (config/code provenance,
    :func:`_persist_from_state`); ``model`` is sourced **here, from the billed ledger**
    (:func:`_billed_extractor_model`) regardless of whether a spec was emitted — never from
    the LLM-authored ``extraction_metadata.model``. ``input_hash`` is the ingested content
    hash. ``update_lineage`` ignores ``None`` fields, so an empty ledger never clears a known
    value.
    """
    content_hash = getattr(runner, "content_hash", None)
    handle.update_lineage(
        input_hash=content_hash if isinstance(content_hash, str) else None,
        model=_billed_extractor_model(ledger),
    )


def _persist_from_state(handle: RunHandle, state: PipelineState | None) -> None:
    """Persist the per-stage artifacts the pipeline produced (complete or partial)."""
    if state is None:
        return
    if state.spec is not None:
        meta = state.spec.extraction_metadata
        # extractor_version is config/code provenance (legitimately spec-authored). model is
        # NOT taken from the spec here: it is the billed provider model, sourced from the
        # ledger in _populate_lineage (architecture.md §1.5; investigation 0002 §7).
        handle.update_lineage(extractor_version=str(meta.extractor_version))
        # A clean ship already mirrored the post-edit spec; only fill in the partial.
        if SPEC_FILENAME not in handle.record.artifacts:
            handle.write_artifact(SPEC_FILENAME, state.spec)
    if state.verdict is not None:
        handle.write_artifact(JURY_VERDICT_FILENAME, state.verdict)
    if state.enrichment is not None:
        handle.write_artifact(ENRICHMENT_FILENAME, state.enrichment)


def _resolve_status(
    *, result: RunResult | None, path: Path | None, exc: BaseException | None
) -> tuple[RunStatus, str | None]:
    """Classify how the run ended into a :class:`RunStatus` (+ optional reason)."""
    if exc is not None:
        return _exception_status(exc)
    if path is not None:
        if result is not None and result.low_jury_confidence:
            return RunStatus.SHIPPED_LOW_CONFIDENCE, None
        return RunStatus.SHIPPED, None
    if result is not None and result.is_out_of_scope():
        return RunStatus.OUT_OF_SCOPE, result.spec.extraction_outcome_reason
    return RunStatus.ABORTED, None


def _exception_status(exc: BaseException) -> tuple[RunStatus, str | None]:
    """Map an in-flight exception to a terminal :class:`RunStatus`."""
    from cyberlab_gen.errors import BudgetExceeded, CyberlabGenError, ValidationError
    from cyberlab_gen.framework.orchestrator import JuryRejectionError

    if isinstance(exc, KeyboardInterrupt):
        return RunStatus.INTERRUPTED, "interrupted"
    # JuryRejectionError is a ValidationError subclass — check it first.
    if isinstance(exc, JuryRejectionError):
        return RunStatus.HALTED_REJECT, str(exc)
    if isinstance(exc, ValidationError):
        return RunStatus.HALTED_VALIDATION, str(exc)
    if isinstance(exc, BudgetExceeded):
        return RunStatus.BUDGET_EXCEEDED, str(exc)
    if isinstance(exc, CyberlabGenError):
        return RunStatus.FAILED, str(exc)  # a classified pipeline failure
    return RunStatus.CRASHED, str(exc)  # genuinely unexpected


def _code_version() -> str | None:
    """Best-effort code-version lineage: the installed package version (ADR 0039)."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("cyberlab-gen")
    except PackageNotFoundError:
        return None


__all__ = [
    "ATTACK_SPEC_FILENAME",
    "DEFAULT_AUTO_ACCEPT_PROPOSAL_CAP",
    "ArtifactChoice",
    "BudgetChoice",
    "EditorFn",
    "ExtractRunner",
    "ProposalChoice",
    "RunResult",
    "run_extract",
    "spec_to_yaml",
    "write_attack_spec",
]
