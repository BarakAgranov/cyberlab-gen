"""The ``plan`` verb's engine: load an AttackSpec, run the plan pipeline, the post-Planner interrupt,
persist, write ``lab.yaml``.

Architectural source: ``architecture.md §2.1`` (``plan`` is a **developer / eval command**, not part
of the user surface — ADR 0096), ``pipeline.md §3.2.6``/``§3.2.7`` (the Planner + Planner-Jury stages),
``§3.2.8`` (the post-Planner interrupt — the LabManifest four-option menu + per-facet-proposal
Accept/Edit), ``§3.1``/``§3.1.1`` (deterministic state machine; typed cross-stage boundaries; the
four-option menu; structural edit-revalidation). The plan pipeline itself — Planner → Planner-Jury →
semantic cross-check — lives in :mod:`cyberlab_gen.framework.plan_orchestrator`; this module is the
thin CLI engine: load / interrupt / promote / persist / write-``lab.yaml``, mirroring
:mod:`cyberlab_gen.cli.extract` (ADR 0024) and reusing the shared interrupt machinery
(:mod:`cyberlab_gen.cli.interrupt`, ADR 0100).

The ``plan`` verb in :mod:`cyberlab_gen.cli.main` is thin: it parses the argument + mode flags, builds
a :class:`PlanRunner`, and delegates here. This module owns the interaction so it can be tested without
a live provider: tests inject a fake :class:`PlanRunner` returning a scripted :class:`PlanRunResult`,
and an ``editor`` callable that simulates a ``$EDITOR`` session.

**Interactive by default with ``--auto`` bypass** (Task 8 / ADR 0100; mirrors ``extract``). Terminal
outcomes the coordinator *returns* (route-back, the halts) are surfaced as actionable, non-zero exits;
the plan pipeline returns its terminal states rather than raising (``plan_orchestrator``), so the verb
maps them here (ADR 0097).

**Facet promotion is run-scoped (ADR 0100).** Accepted Planner facet proposals promote to a
**run-local** overlay (``<run_dir>/registry-overlay``), never the shared production
``~/.cyberlab-gen/registry-overlay``: ``plan`` is a dev/eval command (ADR 0096) and must not silently
mutate the production vocabulary. Real production promotion is ``generate``'s job (Phase 3+).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

import click
import typer
from pydantic import Field
from ruamel.yaml import YAMLError
from ruamel.yaml.compat import StringIO

# Runtime imports (not TYPE_CHECKING): ``JuryVerdict`` / ``PlannerRefusal`` / ``ProposedFacet`` are
# field types on the ``PlanRunResult`` Pydantic model, so the names must resolve at runtime for the
# model to be built (same reasoning as the orchestrators' field-type imports).
from cyberlab_gen.agents.extractor_jury.schema import JuryVerdict
from cyberlab_gen.agents.proposals import ProposedFacet
from cyberlab_gen.agents.results import PlannerRefusal
from cyberlab_gen.cli import interrupt, output
from cyberlab_gen.framework.orchestrator import reject_interactive_when_headless
from cyberlab_gen.framework.plan_orchestrator import (
    PlanPipelineOutcome,
    PlanPipelineStatus,
    run_plan_pipeline,
)
from cyberlab_gen.framework.proposal_acceptance import (
    AcceptanceContext,
    AcceptanceResult,
    accept_proposals,
)
from cyberlab_gen.schemas.base import InternalModel
from cyberlab_gen.schemas.loading import load_spec
from cyberlab_gen.schemas.manifest import LabManifest
from cyberlab_gen.state.run_persistence import (
    billed_model,
    persist_plan_artifacts,
    stamp_framework_provenance,
)
from cyberlab_gen.state.run_store import (
    MANIFEST_FILENAME,
    RunKind,
    RunLineage,
    RunStatus,
)

if TYPE_CHECKING:
    from cyberlab_gen.agents.planner.planner import Planner
    from cyberlab_gen.agents.planner_jury.jury import PlannerJury
    from cyberlab_gen.cli.interrupt import EditorFn
    from cyberlab_gen.providers.cost_ledger import CostLedger
    from cyberlab_gen.providers.cost_recording_provider import CostRecordingProvider
    from cyberlab_gen.registries.merge import MergedRegistries
    from cyberlab_gen.schemas.attack_spec import AttackSpec
    from cyberlab_gen.state.run_store import RunHandle, RunStore
    from cyberlab_gen.state.trajectory import RunTrajectoryRecorder
    from cyberlab_gen.validators.semantic_cross_check_validator import SemanticCrossCheckValidator

logger = logging.getLogger(__name__)

#: The file the verb writes the planned manifest to, in the cwd (the user deliverable). The run-store
#: mirror is :data:`cyberlab_gen.state.run_store.MANIFEST_FILENAME`.
LAB_MANIFEST_FILENAME = "lab.yaml"
#: The structured Planner refusal persisted into the run dir on a route-back / cannot-plan exit.
PLANNER_REFUSAL_FILENAME = "planner-refusal.yaml"
#: The run-scoped overlay subdir for Planner facet promotion (ADR 0100). Structurally identical to
#: ``LocalState.registry_overlay_dir``'s ``registry-overlay`` subdir, but located in the *run* dir —
#: so a dev/eval ``plan`` run never writes the shared production overlay.
OVERLAY_DIRNAME = "registry-overlay"

#: Lossy bridge from the plan pipeline's terminal taxonomy to the run-store taxonomy. The two
#: taxonomies diverging is tracked debt (``dev/phase-2-seams.md §2`` — one shared mapping is the
#: deferred consolidation); this is its third consumer, mapped pragmatically with the halt_reason
#: carrying the precise plan-pipeline status. Public so the plan **eval** runner records the same
#: status fidelity as the verb (ADR 0102 — one shared mapping, a step toward the seams §2 convergence).
PLAN_STATUS_TO_RUN_STATUS: dict[PlanPipelineStatus, RunStatus] = {
    PlanPipelineStatus.PLANNED: RunStatus.SHIPPED,
    PlanPipelineStatus.PLANNED_LOW_CONFIDENCE: RunStatus.SHIPPED_LOW_CONFIDENCE,
    PlanPipelineStatus.HALTED_REJECT: RunStatus.HALTED_REJECT,
    PlanPipelineStatus.HALTED_JURY_INCONSISTENT: RunStatus.HALTED_VALIDATION,
    PlanPipelineStatus.HALTED_SEMANTIC_CROSS_CHECK_UNRESOLVED: RunStatus.HALTED_VALIDATION,
    PlanPipelineStatus.HALTED_CANNOT_PLAN: RunStatus.FAILED,
    PlanPipelineStatus.HALTED_ITERATION_CAP: RunStatus.FAILED,
    PlanPipelineStatus.HALTED_PLANNER_EMIT_EXHAUSTED: RunStatus.FAILED,
    PlanPipelineStatus.ROUTE_BACK_TO_EXTRACTOR: RunStatus.FAILED,
}


# --- the typed result the verb consumes ------------------------------------


class PlanRunResult(InternalModel):
    """Everything the ``plan`` verb needs from one plan-pipeline run, in one typed object.

    A thin CLI-facing view of :class:`PlanPipelineOutcome`: ``manifest`` is present only on a ship
    (``PLANNED`` / ``PLANNED_LOW_CONFIDENCE``); ``refusal`` on a route-back / cannot-plan; ``verdict``
    when the jury ran. No free text crosses the runner seam — the structured outcome is the contract
    (``architecture.md §1.5``).
    """

    status: PlanPipelineStatus
    manifest: LabManifest | None = None
    verdict: JuryVerdict | None = None
    refusal: PlannerRefusal | None = None
    low_jury_confidence: bool = False
    unresolved_feedback: list[str] = Field(default_factory=list[str])
    halt_reason: str | None = None
    #: The Planner's in-flight facet proposals (Task 7 / ADR 0099). Reviewed at the post-Planner
    #: interrupt and promoted to the run-scoped overlay on ship (Task 8 / ADR 0100).
    facet_proposals: list[ProposedFacet] = Field(default_factory=list[ProposedFacet])

    def shipped(self) -> bool:
        """True when the run produced a manifest to write (clean or low-confidence ship)."""
        return self.status in (
            PlanPipelineStatus.PLANNED,
            PlanPipelineStatus.PLANNED_LOW_CONFIDENCE,
        )


# --- the runner seam (mirrors ADR 0024's ExtractRunner) --------------------


class PlanRunner(Protocol):
    """The plan-pipeline surface the verb depends on (the testability boundary).

    The default :class:`PipelinePlanRunner` drives ``run_plan_pipeline``; tests inject a fake
    returning a scripted :class:`PlanRunResult`. ``run`` is **synchronous** at this seam (the verb
    calls it directly); the async pipeline is wrapped inside the production runner.
    :meth:`re_run_with_feedback` backs the four-option menu's Feedback path (natural-language
    feedback → the Planner re-runs). The free text never crosses the seam as a pipeline-stage
    payload — it is a Planner ``preferences`` prompt addendum, the typed ``PlanRunResult`` is the
    return contract (``architecture.md §1.5``; ADR 0024/0100).
    """

    def run(self, attack_spec: AttackSpec, *, ledger: CostLedger) -> PlanRunResult: ...

    def re_run_with_feedback(self, feedback: str, *, ledger: CostLedger) -> PlanRunResult: ...


class PipelinePlanRunner:
    """The production :class:`PlanRunner`: drives the plan LangGraph (Planner → Jury → cross-check).

    Requires configured agents (the provider is a ``CostRecordingProvider`` bound to the verb's
    ledger, so every billed call is recorded — the billed-model stamp reads that ledger at persist).
    The single real ``plan`` run is exercised end-to-end by the maintainer (a paid call); the verb
    wiring is exercised by a fake runner in the integration tests.
    """

    def __init__(
        self,
        *,
        planner: Planner,
        jury: PlannerJury,
        validator: SemanticCrossCheckValidator,
        provider: CostRecordingProvider | None = None,
        registries: MergedRegistries | None = None,
    ) -> None:
        self._planner = planner
        self._jury = jury
        self._validator = validator
        # The shared CostRecordingProvider the agents bill through; held so the trajectory recorder
        # can be attached at run start (the provider is built before the run handle exists).
        self._provider = provider
        # The merged registry snapshot, held so accept-time facet-proposal dedup can check "already
        # registered?" against the bundled+overlay vocabularies (ADR 0099/0100). ``None`` (a fake
        # test runner) leaves dedup off.
        self.registries = registries
        self._recorder: RunTrajectoryRecorder | None = None
        # Held from ``run`` so ``re_run_with_feedback`` re-plans the same AttackSpec (the Planner
        # consumes the AttackSpec + preferences; the feedback is the preferences addendum).
        self._attack_spec: AttackSpec | None = None

    def enable_trajectory(self, handle: RunHandle) -> None:
        """Capture the per-round agent trajectory to ``handle``'s run dir (Item 1, ADR 0098).

        Mirrors the extract runner: called at run start once the handle exists. Wires the recorder
        to the shared provider (every billed call's content) and to the plan orchestrator nodes (the
        round/outcome half, via :meth:`run`'s ``run_plan_pipeline`` call). No-op without a provider.
        """
        if self._provider is None:
            return
        from cyberlab_gen.state.trajectory import RunTrajectoryRecorder

        recorder = RunTrajectoryRecorder(handle)
        self._recorder = recorder
        self._provider.set_trajectory_sink(recorder)

    def run(self, attack_spec: AttackSpec, *, ledger: CostLedger) -> PlanRunResult:
        # The agents bill via their CostRecordingProvider; the runner needs no direct ledger access
        # (unlike extract's next-iteration estimate). The verb owns the ledger for persistence.
        del ledger
        self._attack_spec = attack_spec
        return self._drive(preferences=None)

    def re_run_with_feedback(self, feedback: str, *, ledger: CostLedger) -> PlanRunResult:
        del ledger
        if self._attack_spec is None:  # pragma: no cover - guard
            raise RuntimeError("re_run_with_feedback called before run")
        # The user's free text is folded into the Planner's ``preferences`` prompt channel — never a
        # stage-boundary payload; the typed result is the contract (``§1.5``; ADR 0024/0100).
        return self._drive(preferences=feedback)

    def _drive(self, *, preferences: str | None) -> PlanRunResult:
        assert self._attack_spec is not None
        outcome = asyncio.run(
            run_plan_pipeline(
                attack_spec=self._attack_spec,
                planner=self._planner,
                jury=self._jury,
                validator=self._validator,
                preferences=preferences,
                recorder=self._recorder,
            )
        )
        return _outcome_to_run_result(outcome)


def _outcome_to_run_result(outcome: PlanPipelineOutcome) -> PlanRunResult:
    """Map the coordinator's :class:`PlanPipelineOutcome` onto the verb-facing :class:`PlanRunResult`."""
    return PlanRunResult(
        status=outcome.status,
        manifest=outcome.manifest,
        verdict=outcome.verdict,
        refusal=outcome.refusal,
        low_jury_confidence=outcome.low_jury_confidence,
        unresolved_feedback=outcome.unresolved_feedback,
        halt_reason=outcome.halt_reason,
        facet_proposals=list(outcome.facet_proposals),
    )


# --- YAML (de)serialization for the artifact + editor round-trip -----------

#: The configured round-trip YAML, shared with ``cli.interrupt`` (ADR 0100).
_yaml = interrupt.yaml


def manifest_to_yaml(manifest: LabManifest) -> str:
    """Serialize a ``LabManifest`` to YAML text (the on-disk ``lab.yaml`` + editor form)."""
    buf = StringIO()
    _yaml().dump(manifest.model_dump(mode="json", by_alias=True), buf)
    return buf.getvalue()


def write_lab_manifest(manifest: LabManifest, *, directory: Path) -> Path:
    """Write ``manifest`` as ``lab.yaml`` in ``directory``; return the path."""
    path = directory / LAB_MANIFEST_FILENAME
    path.write_text(manifest_to_yaml(manifest), encoding="utf-8")
    return path


def _proposal_to_yaml(model: ProposedFacet) -> str:
    buf = StringIO()
    _yaml().dump(model.model_dump(mode="json"), buf)
    return buf.getvalue()


def _load_manifest_from_yaml(text: str) -> LabManifest:
    """Parse + structurally revalidate edited YAML into a ``LabManifest`` (``pipeline.md §3.1.1``).

    **Structural** revalidation only — ``§3.2.8``/``§3.1.1`` (the authority) over the brief's "Layer
    1/2" (ADR 0100); there is no manifest Layer-1 facet-membership gate to revalidate against in any
    case (ADR 0099 §6 owned deferral). After structural validation, an old-schema artifact is refused,
    never migrated (``architecture.md §0.6``; ADR 0069), surfacing as ``SpecVersionError``.
    """
    from cyberlab_gen.errors import SpecVersionError

    data = _yaml().load(StringIO(text))
    manifest = LabManifest.model_validate(data)
    if manifest.spec_version != LabManifest.CURRENT_VERSION:
        raise SpecVersionError(found=manifest.spec_version, expected=LabManifest.CURRENT_VERSION)
    return manifest


def _load_attack_spec(spec_path: Path) -> AttackSpec:
    """Load + validate the input ``attack-spec.yaml`` into an :class:`AttackSpec`.

    Uses the spec_kind-dispatching load gate (``schemas.loading.load_spec``, which refuses an
    old-schema artifact rather than migrating — ``architecture.md §0.6``) and then narrows to an
    ``AttackSpec``: ``plan`` consumes the front-half output, so a ``LabManifest`` (or anything else) is
    a usage error. Wraps the gate's ``ValueError`` as a ``CyberlabGenError`` for clean CLI exit
    mapping; ``SpecVersionError`` (already a ``CyberlabGenError``) propagates.
    """
    from cyberlab_gen.errors import CyberlabGenError
    from cyberlab_gen.schemas.attack_spec import AttackSpec

    try:
        raw = spec_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CyberlabGenError(f"cannot read attack-spec file {spec_path}: {exc}") from exc
    # The parse and the gate share one wrap: a malformed file (a ruamel ``YAMLError``) and a bad/missing
    # ``spec_kind`` (``load_spec``'s ``ValueError``) are both clean usage errors, never a raw traceback
    # (mirrors ``cli/extract``'s edit-revalidation wrap). ``SpecVersionError`` is already a
    # ``CyberlabGenError`` and propagates with its specific message.
    try:
        data = _yaml().load(StringIO(raw))
        spec = load_spec(data)
    except (YAMLError, ValueError) as exc:
        raise CyberlabGenError(f"{spec_path} is not a valid spec file: {exc}") from exc
    if not isinstance(spec, AttackSpec):
        raise CyberlabGenError(
            f"`plan` expects an attack-spec.yaml (an AttackSpec); got a {type(spec).__name__}. "
            "Run `cyberlab-gen extract <blog-url>` to produce one first."
        )
    return spec


# --- the post-Planner interrupt (interactive mode) -------------------------


def _format_manifest_summary(result: PlanRunResult) -> str:
    """A compact human summary shown above the four-option menu (``pipeline.md §3.2.8``)."""
    manifest = result.manifest
    assert manifest is not None  # only the ship path renders the summary
    lines = [
        "",
        "=== LabManifest ready for review (post-Planner interrupt) ===",
        f"lab: {manifest.core.id} — {manifest.core.name}",
        f"phases: {len(manifest.phases)}",
        f"facets: {', '.join(manifest.facets) if manifest.facets else '(none)'}",
        f"lab-level reproducibility: {manifest.core.reproducibility.classification_lab_level.value}",
        f"facet proposals: {len(result.facet_proposals)}",
    ]
    if result.low_jury_confidence:
        lines.append("NOTE: shipped with low_jury_confidence (unresolved jury feedback).")
    return "\n".join(lines)


def _edit_manifest_with_revalidation(manifest: LabManifest, *, editor: EditorFn) -> LabManifest:
    """Open the manifest in ``$EDITOR``; reopen with error-comments on invalid edits (``§3.1.1``).

    Structural revalidation only (the shared loop, ``cli.interrupt``; ADR 0100). The semantic
    cross-check is **not** re-run on a human edit — the human is the authority at the interrupt
    (``§3.1.1``: "semantic correctness of user edits is the user's responsibility"); a cross-check
    re-run as a warning is a future item (ADR 0100).
    """
    return interrupt.edit_with_revalidation(
        manifest, to_text=manifest_to_yaml, parse=_load_manifest_from_yaml, editor=editor
    )


def _review_facet_proposals(result: PlanRunResult, *, editor: EditorFn) -> list[ProposedFacet]:
    """Per-proposal Accept/Edit review collecting the (possibly edited) facets — no overlay write.

    The Planner proposes **facets only** (``runtime:*`` / lab-derived ``lab_class_signal:*``); value
    types are the Extractor's authority and were reviewed at the post-Extractor interrupt
    (``pipeline.md §3.2.8``). The overlay write is deferred to ship time (ADR 0050/0062), so a user
    abort after this review promotes nothing.
    """
    return [
        interrupt.review_one_proposal(
            label=f"facet proposal {facet.name!r} (category={facet.category})",
            model=facet,
            to_text=_proposal_to_yaml,
            parse=lambda text: ProposedFacet.model_validate(_yaml().load(StringIO(text))),
            editor=editor,
        )
        for facet in result.facet_proposals
    ]


# --- run-scoped facet promotion (ADR 0100) ---------------------------------


def _acceptance_context(
    result: PlanRunResult, *, overlay_dir: Path, handle: RunHandle | None, ledger: CostLedger
) -> AcceptanceContext:
    """Assemble the framework-known audit context for promoting this run's Planner facet proposals.

    ``proposed_by_model`` is the **billed** Planner model from the cost ledger (a framework fact, ADR
    0065), falling back to the manifest's framework-stamped ``core.generation.model`` only when the
    ledger is empty (tests). ``proposed_by``/``proposal_origin`` are the Planner-stage values (ADR
    0099); ``source_lab`` is the manifest's lab id — a Planner-stage proposal fills it in (ADR 0044),
    unlike the Extractor's (no lab exists at extraction time). ``source_blog`` is the blog the lab
    derives from.
    """
    manifest = result.manifest
    assert manifest is not None
    return AcceptanceContext(
        overlay_dir=overlay_dir,
        source_blog=str(manifest.core.source.url),
        proposed_by_model=billed_model(ledger) or manifest.core.generation.model,
        proposed_at=datetime.now(UTC),
        run_id=handle.record.run_id if handle is not None else None,
        proposed_by="planner",
        proposal_origin="llm_during_planning",
        source_lab=manifest.core.id,
    )


def _promote_facets(
    facets: list[ProposedFacet],
    *,
    result: PlanRunResult,
    overlay_dir: Path | None,
    handle: RunHandle | None,
    ledger: CostLedger,
    registries: MergedRegistries | None,
    approval: Literal["auto", "human"],
    cap: int | None,
) -> None:
    """Promote reviewed/accepted Planner facet proposals to the **run-scoped** overlay (ADR 0100).

    Gated on ship (the caller invokes this only after :func:`write_lab_manifest`), so an abort/halt
    promotes nothing (ADR 0050/0062). The target is run-local (``<run_dir>/registry-overlay``), never
    the shared production overlay — ``plan`` is a dev/eval command (ADR 0096). No-op when there is no
    target (``overlay_dir is None`` — a no-run-store unit path) or nothing to promote. Dedup runs
    against the merged-registries snapshot (bundled + global overlay) so an already-known facet is
    skipped; over-cap is ``deferred`` (``--auto``), surfaced not dropped.
    """
    if overlay_dir is None or not facets:
        return
    ctx = _acceptance_context(result, overlay_dir=overlay_dir, handle=handle, ledger=ledger)
    accepted = accept_proposals(
        list(facets), ctx, approval=approval, registries=registries, cap=cap
    )
    _report_promotion(accepted, overlay_dir=overlay_dir, cap=cap)


def _report_promotion(accepted: AcceptanceResult, *, overlay_dir: Path, cap: int | None) -> None:
    """Surface what was promoted to the run overlay vs. skipped (dedup) vs. deferred (over-cap)."""
    if accepted.accepted:
        output.print_info(
            f"\npromoted {len(accepted.accepted)} facet proposal(s) to the run overlay "
            f"({overlay_dir}) on ship:"
        )
        for label in accepted.accepted:
            output.print_info(f"  + {label}")
    if accepted.skipped:
        output.print_info(
            f"\n{len(accepted.skipped)} facet proposal(s) already registered (not promoted — a "
            "mechanical dedup, ADR 0099; promoting would silently shadow a bundled/accepted entry):"
        )
        for label in accepted.skipped:
            output.print_info(f"  - {label}")
    if accepted.deferred:
        output.print_info(
            f"\n{len(accepted.deferred)} facet proposal(s) over the per-run cap of {cap} were NOT "
            "promoted (reported, not halted — ADR 0050/0062 over-cap is bounded steering):"
        )
        for label in accepted.deferred:
            output.print_info(f"  - {label}")


# --- the verb body ---------------------------------------------------------


def run_plan(
    *,
    spec_path: Path,
    runner: PlanRunner,
    ledger: CostLedger,
    interactive: bool = False,
    auto: bool = False,
    stdin_is_tty: bool = True,
    editor: EditorFn = click.edit,
    out_dir: Path | None = None,
    run_store: RunStore | None = None,
    overlay_dir: Path | None = None,
) -> Path | None:
    """Run the ``plan`` pipeline + the post-Planner interrupt; write ``lab.yaml`` on a ship.

    Returns the path of the written ``lab.yaml`` on a ship (clean or low-confidence), or ``None`` when
    the run did not ship — an abort, a route-back to the Extractor, or a halt (the actionable message
    is printed here; the caller maps ``None`` to a non-zero exit). Raises ``CyberlabGenError``
    (bad/non-AttackSpec input, a ``PlanningError`` patch-budget halt), ``ValueError`` (headless
    ``--interactive`` rejection), and ``KeyboardInterrupt`` to the caller for exit-code mapping.

    Mode resolution mirrors ``extract`` / ADR 0013: neither flag defaults to ``--interactive``; both
    set is a usage error (caught upstream in the verb). ``overlay_dir`` (a test/dev seam) overrides
    the run-scoped facet-promotion target; in production it is left ``None`` and promotion targets
    ``<run_dir>/registry-overlay`` (ADR 0100), never the shared production overlay.

    When ``run_store`` is supplied (production), a complete run record is persisted on **every** exit
    path (ADR 0039): the manifest (stamped, or the failed one for inspection), the jury verdict, the
    structured refusal, the cost, and a finalized ``run.json`` with the **billed** Planner model (ADR
    0065/0068). The cwd ``lab.yaml`` deliverable is unchanged; the run dir mirrors it.
    """
    del (
        interactive
    )  # mode is decided by ``auto``; default is interactive (mirrors extract, ADR 0013)
    mode_interactive = not auto
    # Headless guard: reject --interactive when stdin is not a TTY (§3.1). A usage error before any
    # spend — fail before opening a run directory.
    reject_interactive_when_headless(interactive=mode_interactive, stdin_is_tty=stdin_is_tty)

    target_dir = out_dir if out_dir is not None else Path.cwd()
    attack_spec = _load_attack_spec(spec_path)  # raises before any run dir / spend on a bad input

    if run_store is None:
        result = runner.run(attack_spec, ledger=ledger)
        path, _ = _drive_plan_result(
            result=result,
            runner=runner,
            ledger=ledger,
            editor=editor,
            target_dir=target_dir,
            mode_interactive=mode_interactive,
            handle=None,
            overlay_dir=_promotion_overlay(overlay_dir, handle=None),
            source_url=_source_url(attack_spec),
        )
        return path

    handle = run_store.start(
        kind=RunKind.PLAN,
        label=str(spec_path),
        lineage=RunLineage(
            input_ref=str(spec_path),
            input_hash=attack_spec.source.content_hash,
            code_version=_code_version(),
        ),
    )
    # Capture the per-round agent trajectory to the run dir (ADR 0098), alongside the artifacts.
    if isinstance(runner, PipelinePlanRunner):
        runner.enable_trajectory(handle)
    result: PlanRunResult | None = None
    path: Path | None = None
    try:
        result = runner.run(attack_spec, ledger=ledger)
        # Rebind ``result`` to the driver's FINAL result (after any Feedback re-run / Edit) so the
        # ``finally`` persists what the run actually ended on — not the stale first run. The plan
        # orchestrator *returns* its terminal states (route-back, halts; ADR 0097) rather than raising,
        # so unlike extract there is no exc-path to recover them; the result must travel back here
        # explicitly (adversarial-review finding, ADR 0100).
        path, result = _drive_plan_result(
            result=result,
            runner=runner,
            ledger=ledger,
            editor=editor,
            target_dir=target_dir,
            mode_interactive=mode_interactive,
            handle=handle,
            overlay_dir=_promotion_overlay(overlay_dir, handle=handle),
            source_url=_source_url(attack_spec),
        )
        return path
    finally:
        # Runs on ship, abort, route-back, halt, Ctrl-C, and any crash: persist whatever the run
        # produced so a run never ends with nothing to read (ADR 0039).
        _persist_plan_run(
            handle,
            result=result,
            ledger=ledger,
            content_hash=attack_spec.source.content_hash,
            path=path,
        )


def _promotion_overlay(overlay_dir: Path | None, *, handle: RunHandle | None) -> Path | None:
    """The **run-scoped** overlay target for Planner facet promotion (ADR 0100).

    ``plan`` is a dev/eval command (ADR 0096); its promotion must never silently mutate the shared
    production overlay (``~/.cyberlab-gen/registry-overlay``). An explicit ``overlay_dir`` (a test/dev
    seam) wins; otherwise the target is scoped to this run's directory; with neither there is no
    target and promotion is skipped. Real production vocabulary promotion is ``generate``'s job
    (Phase 3+).
    """
    if overlay_dir is not None:
        return overlay_dir
    if handle is not None:
        return handle.directory / OVERLAY_DIRNAME
    return None


def _drive_plan_result(
    *,
    result: PlanRunResult,
    runner: PlanRunner,
    ledger: CostLedger,
    editor: EditorFn,
    target_dir: Path,
    mode_interactive: bool,
    handle: RunHandle | None,
    overlay_dir: Path | None,
    source_url: str,
) -> tuple[Path | None, PlanRunResult]:
    """Dispatch to the interactive or auto post-Planner flow.

    Returns ``(path, final_result)``: the written ``lab.yaml`` path (``None`` if not shipped) and the
    result the run *ended on* (the re-run/edited result on the interactive path), so the caller's
    persistence records the true terminal state (adversarial-review finding, ADR 0100).
    """
    # The production runner carries the merged registry snapshot, used for accept-time facet-proposal
    # dedup (ADR 0099/0100); a fake test runner does not — dedup is then off.
    registries = runner.registries if isinstance(runner, PipelinePlanRunner) else None
    if mode_interactive:
        return _drive_interactive(
            result=result,
            runner=runner,
            ledger=ledger,
            editor=editor,
            target_dir=target_dir,
            handle=handle,
            overlay_dir=overlay_dir,
            registries=registries,
            source_url=source_url,
        )
    return _drive_auto(
        result=result,
        ledger=ledger,
        target_dir=target_dir,
        handle=handle,
        overlay_dir=overlay_dir,
        registries=registries,
        source_url=source_url,
    )


def _drive_interactive(
    *,
    result: PlanRunResult,
    runner: PlanRunner,
    ledger: CostLedger,
    editor: EditorFn,
    target_dir: Path,
    handle: RunHandle | None,
    overlay_dir: Path | None,
    registries: MergedRegistries | None,
    source_url: str,
) -> tuple[Path | None, PlanRunResult]:
    """``--interactive``: the LabManifest four-option menu + per-facet-proposal review (``§3.2.8``).

    Returns ``(path, result)`` where ``result`` is the latest (re-run/edited) result, so the caller
    persists the true terminal state.
    """
    while True:
        # A non-ship terminal (route-back / halt) has no manifest to review — surface it and exit,
        # exactly as in --auto (no menu).
        if not result.shipped():
            return _report_non_ship(result, source_url=source_url), result
        output.print_info(_format_manifest_summary(result))
        choice = interrupt.prompt_artifact_choice(rerun_agent="Planner")

        if choice is interrupt.ArtifactChoice.ABORT:
            output.print_info("aborted; no lab.yaml written.")
            return None, result
        if choice is interrupt.ArtifactChoice.FEEDBACK:
            feedback = typer.prompt("feedback for the Planner")
            result = runner.re_run_with_feedback(feedback, ledger=ledger)
            continue  # re-show the menu against the re-run result
        if choice is interrupt.ArtifactChoice.EDIT:
            assert result.manifest is not None
            result = result.model_copy(
                update={
                    "manifest": _edit_manifest_with_revalidation(result.manifest, editor=editor)
                }
            )
            continue  # re-show the menu against the edited manifest
        # APPROVE: review facet proposals (NO overlay write yet), ship, THEN promote (ADR 0050/0062:
        # promotion gated on the manifest shipping, so an abort/edit-then-abort leaves no orphans).
        collected = _review_facet_proposals(result, editor=editor)
        path = _ship_manifest(result, ledger=ledger, target_dir=target_dir, handle=handle)
        _promote_facets(
            collected,
            result=result,
            overlay_dir=overlay_dir,
            handle=handle,
            ledger=ledger,
            registries=registries,
            approval="human",
            cap=None,  # the user acted on each proposal individually — no cap in interactive
        )
        output.print_info(f"\nwrote {path}")
        return path, result


def _drive_auto(
    *,
    result: PlanRunResult,
    ledger: CostLedger,
    target_dir: Path,
    handle: RunHandle | None,
    overlay_dir: Path | None,
    registries: MergedRegistries | None,
    source_url: str,
) -> tuple[Path | None, PlanRunResult]:
    """``--auto``: no interrupt; ship a planned manifest (auto-promoting facets up to the cap)."""
    if not result.shipped():
        return _report_non_ship(result, source_url=source_url), result
    path = _ship_manifest(result, ledger=ledger, target_dir=target_dir, handle=handle)
    _promote_facets(
        result.facet_proposals,
        result=result,
        overlay_dir=overlay_dir,
        handle=handle,
        ledger=ledger,
        registries=registries,
        approval="auto",
        cap=interrupt.DEFAULT_AUTO_ACCEPT_PROPOSAL_CAP,
    )
    output.print_info(f"\nwrote {path}")
    return path, result


def _ship_manifest(
    result: PlanRunResult, *, ledger: CostLedger, target_dir: Path, handle: RunHandle | None
) -> Path:
    """Stamp, write the cwd ``lab.yaml``, mirror to the run dir, emit the report tail (the ship boundary)."""
    assert result.manifest is not None  # a ship always carries a manifest
    # Framework-stamp the (possibly edited) manifest once at the ship boundary (billed Planner model,
    # version, generation metadata — ADR 0086) so the cwd deliverable and the run-dir mirror are
    # byte-identical; persistence then fills only the not-yet-mirrored partial/halt case.
    stamped = stamp_framework_provenance(result.manifest, ledger)
    path = write_lab_manifest(stamped, directory=target_dir)
    if handle is not None:
        handle.write_artifact(MANIFEST_FILENAME, stamped)
    _emit_plan_report(result)
    return path


def _report_non_ship(result: PlanRunResult, *, source_url: str) -> None:
    """Surface a non-ship terminal (route-back / halt) with an actionable message; returns ``None``."""
    if result.status is PlanPipelineStatus.ROUTE_BACK_TO_EXTRACTOR:
        _report_route_back(result, source_url=source_url)
    else:
        _report_halt(result)
    return None


def _emit_plan_report(result: PlanRunResult) -> None:
    """Print the ship-path report tail (low-confidence + unresolved jury feedback).

    Facet-proposal promotion prints its own lines (:func:`_promote_facets`); the proposals are no
    longer "captured, not promoted" — Task 8 / ADR 0100 wires the run-scoped promotion.
    """
    if result.low_jury_confidence:
        output.print_info("\nShipped with low_jury_confidence. Unresolved jury feedback:")
        for item in result.unresolved_feedback:
            output.print_info(f"  - {item}")


def _report_route_back(result: PlanRunResult, *, source_url: str) -> None:
    """Surface the AttackSpec-incoherence route-back as an actionable re-extract message (ADR 0097).

    The Planner flagged a defect it is not allowed to repair (``agents.md §5.7``); the corrective
    action is a re-extract, not a re-plan. The structured refusal is persisted to the run store.
    """
    refusal = result.refusal
    summary = (
        refusal.summary if refusal is not None else (result.halt_reason or "AttackSpec incoherent")
    )
    detail = refusal.detail if refusal is not None else ""
    fields = ", ".join(refusal.attack_spec_field_paths) if refusal is not None else ""
    output.print_error(
        "AttackSpec is incoherent; the Planner routed back to the Extractor (it does not repair the "
        f"AttackSpec).\n  problem: {summary}\n  detail: {detail}\n  attack-spec fields: {fields}\n"
        f"Re-run `cyberlab-gen extract {source_url}` addressing this, then re-run `cyberlab-gen plan` "
        "on the corrected attack-spec.yaml. The structured refusal was saved to the run store."
    )


def _report_halt(result: PlanRunResult) -> None:
    """Surface a halt (reject / cannot-plan / cross-check-unresolved / cap) with its reason."""
    output.print_error(
        f"planning halted ({result.status.value}): {result.halt_reason or 'no detail'}"
    )
    for item in result.unresolved_feedback:
        output.print_info(f"  - {item}")


# --- run-store persistence (ADR 0039) --------------------------------------


def _persist_plan_run(
    handle: RunHandle,
    *,
    result: PlanRunResult | None,
    ledger: CostLedger,
    content_hash: str | None,
    path: Path | None,
) -> None:
    """Persist the run's artifacts + cost and finalize ``run.json`` (best-effort, from a ``finally``).

    The manifest + verdict + billed-model lineage are the shared service's job (``persist_plan_artifacts``
    — the billed-model invariant has one home, ADR 0068); the structured refusal and the
    terminal-status resolution are verb-specific and stay here.
    """
    persist_plan_artifacts(
        handle,
        manifest=result.manifest if result is not None else None,
        verdict=result.verdict if result is not None else None,
        ledger=ledger,
        content_hash=content_hash,
    )
    if result is not None and result.refusal is not None:
        handle.write_artifact(PLANNER_REFUSAL_FILENAME, result.refusal)
    handle.write_cost(ledger)
    status, reason = _plan_run_status(result=result, path=path, exc=sys.exc_info()[1])
    handle.finalize(status, halt_reason=reason)


def _plan_run_status(
    *, result: PlanRunResult | None, path: Path | None, exc: BaseException | None
) -> tuple[RunStatus, str | None]:
    """Classify how the plan run ended into a :class:`RunStatus` (+ optional reason)."""
    if exc is not None:
        return _exception_status(exc)
    if result is None:  # crashed before the runner returned, without raising (defensive)
        return RunStatus.ABORTED, None
    # A shipped status with no path written means the user aborted at the interrupt (interactive):
    # the manifest was plan-ready but not accepted, so the run is ABORTED, not SHIPPED.
    if result.shipped() and path is None:
        return RunStatus.ABORTED, None
    return PLAN_STATUS_TO_RUN_STATUS.get(result.status, RunStatus.FAILED), result.halt_reason


def _exception_status(exc: BaseException) -> tuple[RunStatus, str | None]:
    """Map an in-flight exception to a terminal :class:`RunStatus`."""
    from cyberlab_gen.errors import BudgetExceeded, CyberlabGenError

    if isinstance(exc, KeyboardInterrupt):
        return RunStatus.INTERRUPTED, "interrupted"
    if isinstance(exc, BudgetExceeded):
        return RunStatus.BUDGET_EXCEEDED, str(exc)
    if isinstance(exc, CyberlabGenError):
        return RunStatus.FAILED, str(exc)  # a classified pipeline failure (e.g. PlanningError)
    return RunStatus.CRASHED, str(exc)  # genuinely unexpected


def _source_url(attack_spec: AttackSpec) -> str:
    """The blog URL behind the AttackSpec — for the route-back re-extract guidance."""
    return str(attack_spec.source.url)


def _code_version() -> str | None:
    """Best-effort code-version lineage: the installed package version (ADR 0039).

    Local mirror of ``cli/extract._code_version`` — both verbs need it; sharing a single home is a
    minor follow-up, not worth a new module here.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("cyberlab-gen")
    except PackageNotFoundError:
        return None


__all__ = [
    "LAB_MANIFEST_FILENAME",
    "PLANNER_REFUSAL_FILENAME",
    "PLAN_STATUS_TO_RUN_STATUS",
    "PipelinePlanRunner",
    "PlanRunResult",
    "PlanRunner",
    "manifest_to_yaml",
    "run_plan",
    "write_lab_manifest",
]
