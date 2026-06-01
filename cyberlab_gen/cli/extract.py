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
they raise (``ValidationError`` on Layer-1 exhaustion, ``JuryRejectionError`` on a
jury ``reject``); the verb surfaces those as clean errors. Everything reaching
the interrupt is a *shipped* (clean or low-confidence) ``RunResult``.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import click
import typer
from pydantic import Field
from ruamel.yaml import YAML
from ruamel.yaml.compat import StringIO

from cyberlab_gen.agents.proposals import ProposedFacet, ProposedValueType
from cyberlab_gen.cli import output
from cyberlab_gen.framework.orchestrator import (
    PipelineStatus,
    reject_interactive_when_headless,
)
from cyberlab_gen.schemas.attack_spec import AttackSpec, MaterialDiscrepancy
from cyberlab_gen.schemas.base import InternalModel

if TYPE_CHECKING:
    from collections.abc import Callable

    from cyberlab_gen.agents.extractor.extractor import Extractor
    from cyberlab_gen.agents.extractor_jury.jury import ExtractorJury
    from cyberlab_gen.providers.cost_ledger import CostLedger
    from cyberlab_gen.schemas.ingestion import IngestionResult
    from cyberlab_gen.state.local_state import LocalState
    from cyberlab_gen.validators.layer1 import Layer1Validator

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
        validator: Layer1Validator,
        jury: ExtractorJury,
        state: LocalState | None = None,
    ) -> None:
        self._extractor = extractor
        self._validator = validator
        self._jury = jury
        self._state = state
        self._ingestion: IngestionResult | None = None
        self._blog_content: str | None = None

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
        run = build_pipeline(extractor=self._extractor, validator=self._validator, jury=self._jury)
        summary = _ingestion_summary(self._ingestion)
        if extra_feedback is not None:
            summary = f"{summary}\n\nUSER FEEDBACK (re-extract addressing this):\n{extra_feedback}"
        initial = PipelineState(blog_content=self._blog_content, source_summary=summary)

        async def _go() -> PipelineState:
            return await run(initial)

        final = asyncio.run(_go())
        return _state_to_run_result(final)


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

        findings = state.layer1.rendered_findings() if state.layer1 is not None else []
        raise ValidationError(
            state.halt_reason or "Validator Layer 1 failed past the retry budget",
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
            logger.info("edited AttackSpec failed structural revalidation: %s", exc)
            comment = _errors_as_comments(exc)
            # Reopen with the errors as leading comments + the user's text below.
            text = f"{comment}\n{edited}"


def _errors_as_comments(exc: Exception) -> str:
    """Render a validation/parse error as ``#``-prefixed editor comment lines."""
    lines = ["# STRUCTURAL VALIDATION FAILED — fix these and re-save:"]
    for raw_line in str(exc).splitlines():
        lines.append(f"# {raw_line}")
    return "\n".join(lines)


def _review_proposals_interactive(result: RunResult, *, editor: EditorFn) -> None:
    """Per-proposal Accept/Edit loop (``pipeline.md §3.2.5``).

    Each value-type and facet proposal is reviewed individually: Accept (the
    framework would write the overlay entry; Phase 1 records it in the report) or
    Edit (the proposal is re-opened, edited, and structurally revalidated; an
    invalid edit reopens the editor with error comments). No Reject — see
    :class:`ProposalChoice`.
    """
    for vt in result.value_type_proposals:
        _review_one_proposal(
            label=f"value_type proposal {vt.name!r}",
            model=vt,
            parse=ProposedValueType.model_validate,
            editor=editor,
        )
    for facet in result.facet_proposals:
        _review_one_proposal(
            label=f"facet proposal {facet.name!r} (category={facet.category})",
            model=facet,
            parse=ProposedFacet.model_validate,
            editor=editor,
        )


def _default_proposal_choice_reader() -> str:
    return typer.prompt("proposal action ([a]ccept / [e]dit)", default="a")


def _review_one_proposal[T: (ProposedValueType, ProposedFacet)](
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
            logger.info("edited proposal failed structural revalidation: %s", exc)
            text = f"{_errors_as_comments(exc)}\n{edited}"


def _proposal_to_yaml(model: ProposedValueType | ProposedFacet) -> str:
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
) -> Path | None:
    """Run the ``extract`` pipeline and the post-Extractor interrupt.

    Returns the path of the written ``attack-spec.yaml`` on a ship, or ``None``
    when the run was aborted (by the user, by an out-of-scope ``--auto`` halt, or
    by a budget-overrun abort). Raises ``typer.Exit`` / the orchestrator's
    halt errors to the caller for exit-code mapping.

    Mode resolution mirrors ADR 0013: neither flag set defaults to
    ``--interactive``; both set is a usage error (caught upstream in the verb).
    """
    del interactive  # mode is decided by ``auto``; default is interactive (ADR 0013)
    mode_interactive = not auto
    # Headless guard: reject --interactive when stdin is not a TTY (§3.1).
    reject_interactive_when_headless(interactive=mode_interactive, stdin_is_tty=stdin_is_tty)

    result = runner.run(url, ledger=ledger)
    target_dir = out_dir if out_dir is not None else Path.cwd()

    if mode_interactive:
        return _drive_interactive(
            result=result,
            runner=runner,
            ledger=ledger,
            editor=editor,
            target_dir=target_dir,
        )
    return _drive_auto(result=result, ledger=ledger, target_dir=target_dir)


def _drive_auto(*, result: RunResult, ledger: CostLedger, target_dir: Path) -> Path | None:
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
    _auto_accept_proposals(result)
    _emit_run_report(result)
    path = write_attack_spec(result.spec, directory=target_dir)
    output.print_info(f"\nwrote {path}")
    return path


def _auto_accept_proposals(result: RunResult) -> None:
    """Auto-accept proposals up to the per-run cap (``implementation-plan.md §4.2``)."""
    proposals: list[str] = [f"value_type {vt.name!r}" for vt in result.value_type_proposals] + [
        f"facet {f.name!r}" for f in result.facet_proposals
    ]
    accepted = proposals[:DEFAULT_AUTO_ACCEPT_PROPOSAL_CAP]
    deferred = proposals[DEFAULT_AUTO_ACCEPT_PROPOSAL_CAP:]
    if accepted:
        output.print_info(f"\nauto-accepted {len(accepted)} proposal(s) into the overlay:")
        for p in accepted:
            output.print_info(f"  + {p}")
    if deferred:
        output.print_info(
            f"{len(deferred)} proposal(s) over the auto-accept cap "
            f"({DEFAULT_AUTO_ACCEPT_PROPOSAL_CAP}); not accepted, listed for review:"
        )
        for p in deferred:
            output.print_info(f"  ? {p}")


def _drive_interactive(
    *,
    result: RunResult,
    runner: ExtractRunner,
    ledger: CostLedger,
    editor: EditorFn,
    target_dir: Path,
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
        # APPROVE: review proposals, check budget, write.
        _review_proposals_interactive(result, editor=editor)
        if _would_overrun(result, ledger) and not _handle_budget_overrun(
            result, ledger, interactive=True
        ):
            return None
        _emit_run_report(result)
        path = write_attack_spec(result.spec, directory=target_dir)
        output.print_info(f"\nwrote {path}")
        return path


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
