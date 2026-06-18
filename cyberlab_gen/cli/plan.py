"""The ``plan`` verb's engine: load an AttackSpec, run the plan pipeline, persist, write ``lab.yaml``.

Architectural source: ``architecture.md §2.1`` (``plan`` is a **developer / eval command**, not part
of the user surface — ADR 0096), ``pipeline.md §3.2.6``/``§3.2.7`` (the Planner + Planner-Jury stages),
``§3.1`` (deterministic state machine, typed cross-stage boundaries). The plan pipeline itself —
Planner → Planner-Jury → semantic cross-check (the second mechanical validation layer) — lives in
:mod:`cyberlab_gen.framework.plan_orchestrator`; this module is the thin CLI engine + run-store
persistence, mirroring :mod:`cyberlab_gen.cli.extract`. Real users invoke ``generate``, which runs the
same stages internally.

The ``plan`` verb in :mod:`cyberlab_gen.cli.main` is thin: it parses the argument, builds a
:class:`PlanRunner`, and delegates here. This module owns the load / persist / write-``lab.yaml``
logic so it can be tested without a live provider: tests inject a fake :class:`PlanRunner` returning a
scripted :class:`PlanRunResult`.

**Non-interactive in Phase 2.** The post-Planner interactive interrupt (``pipeline.md §3.2.8``) is
Task 8; this verb runs the pipeline and writes the artifact without pausing. Terminal outcomes the
coordinator *returns* (route-back, the halts) are surfaced as actionable, non-zero exits — the plan
pipeline returns its terminal states rather than raising (``plan_orchestrator``), so the verb maps
them here (ADR 0097).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from pydantic import Field
from ruamel.yaml import YAML, YAMLError
from ruamel.yaml.compat import StringIO

# Runtime imports (not TYPE_CHECKING): ``JuryVerdict`` / ``PlannerRefusal`` are field types on the
# ``PlanRunResult`` Pydantic model, so the names must resolve at runtime for the model to be built
# (same reasoning as the orchestrators' field-type imports).
from cyberlab_gen.agents.extractor_jury.schema import JuryVerdict
from cyberlab_gen.agents.results import PlannerRefusal
from cyberlab_gen.cli import output
from cyberlab_gen.framework.plan_orchestrator import (
    PlanPipelineOutcome,
    PlanPipelineStatus,
    run_plan_pipeline,
)
from cyberlab_gen.schemas.base import InternalModel
from cyberlab_gen.schemas.loading import load_spec
from cyberlab_gen.schemas.manifest import LabManifest
from cyberlab_gen.state.run_persistence import persist_plan_artifacts, stamp_framework_provenance
from cyberlab_gen.state.run_store import (
    MANIFEST_FILENAME,
    RunKind,
    RunLineage,
    RunStatus,
)

if TYPE_CHECKING:
    from cyberlab_gen.agents.planner.planner import Planner
    from cyberlab_gen.agents.planner_jury.jury import PlannerJury
    from cyberlab_gen.providers.cost_ledger import CostLedger
    from cyberlab_gen.providers.cost_recording_provider import CostRecordingProvider
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

#: Lossy bridge from the plan pipeline's terminal taxonomy to the run-store taxonomy. The two
#: taxonomies diverging is tracked debt (``dev/phase-2-seams.md §2`` — one shared mapping is the
#: deferred consolidation); this is its third consumer, mapped pragmatically with the halt_reason
#: carrying the precise plan-pipeline status.
_PLAN_STATUS_TO_RUN_STATUS: dict[PlanPipelineStatus, RunStatus] = {
    PlanPipelineStatus.PLANNED: RunStatus.SHIPPED,
    PlanPipelineStatus.PLANNED_LOW_CONFIDENCE: RunStatus.SHIPPED_LOW_CONFIDENCE,
    PlanPipelineStatus.HALTED_REJECT: RunStatus.HALTED_REJECT,
    PlanPipelineStatus.HALTED_JURY_INCONSISTENT: RunStatus.HALTED_VALIDATION,
    PlanPipelineStatus.HALTED_SEMANTIC_CROSS_CHECK_UNRESOLVED: RunStatus.HALTED_VALIDATION,
    PlanPipelineStatus.HALTED_CANNOT_PLAN: RunStatus.FAILED,
    PlanPipelineStatus.HALTED_ITERATION_CAP: RunStatus.FAILED,
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
    """

    def run(self, attack_spec: AttackSpec, *, ledger: CostLedger) -> PlanRunResult: ...


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
    ) -> None:
        self._planner = planner
        self._jury = jury
        self._validator = validator
        # The shared CostRecordingProvider the agents bill through; held so the trajectory recorder
        # can be attached at run start (the provider is built before the run handle exists).
        self._provider = provider
        self._recorder: RunTrajectoryRecorder | None = None

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
        outcome = asyncio.run(
            run_plan_pipeline(
                attack_spec=attack_spec,
                planner=self._planner,
                jury=self._jury,
                validator=self._validator,
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
    )


# --- YAML (de)serialization for the artifact -------------------------------


def _yaml() -> YAML:
    y = YAML()
    y.default_flow_style = False
    y.width = 4096  # don't wrap long citation strings
    return y


def manifest_to_yaml(manifest: LabManifest) -> str:
    """Serialize a ``LabManifest`` to YAML text (the on-disk ``lab.yaml`` form)."""
    buf = StringIO()
    _yaml().dump(manifest.model_dump(mode="json", by_alias=True), buf)
    return buf.getvalue()


def write_lab_manifest(manifest: LabManifest, *, directory: Path) -> Path:
    """Write ``manifest`` as ``lab.yaml`` in ``directory``; return the path."""
    path = directory / LAB_MANIFEST_FILENAME
    path.write_text(manifest_to_yaml(manifest), encoding="utf-8")
    return path


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


# --- the verb body ---------------------------------------------------------


def run_plan(
    *,
    spec_path: Path,
    runner: PlanRunner,
    ledger: CostLedger,
    out_dir: Path | None = None,
    run_store: RunStore | None = None,
) -> Path | None:
    """Run the ``plan`` pipeline on ``spec_path`` and write ``lab.yaml`` on a ship.

    Returns the path of the written ``lab.yaml`` on a ship (clean or low-confidence), or ``None`` when
    the run did not ship — a route-back to the Extractor or a halt (the actionable message is printed
    here; the caller maps ``None`` to a non-zero exit). Raises ``CyberlabGenError`` (bad/non-AttackSpec
    input, a ``PlanningError`` patch-budget halt) and ``KeyboardInterrupt`` to the caller for exit-code
    mapping.

    When ``run_store`` is supplied (production), a complete run record is persisted on **every** exit
    path (ADR 0039): the manifest (stamped, or the failed one for inspection), the jury verdict, the
    structured refusal, the cost, and a finalized ``run.json`` with the **billed** Planner model (ADR
    0065/0068). The cwd ``lab.yaml`` deliverable is unchanged; the run dir mirrors it.
    """
    target_dir = out_dir if out_dir is not None else Path.cwd()
    attack_spec = _load_attack_spec(spec_path)  # raises before any run dir / spend on a bad input

    if run_store is None:
        result = runner.run(attack_spec, ledger=ledger)
        return _ship_or_report(
            result,
            ledger=ledger,
            target_dir=target_dir,
            handle=None,
            source_url=_source_url(attack_spec),
        )

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
        path = _ship_or_report(
            result,
            ledger=ledger,
            target_dir=target_dir,
            handle=handle,
            source_url=_source_url(attack_spec),
        )
        return path
    finally:
        # Runs on ship, route-back, halt, Ctrl-C, and any crash: persist whatever the run produced so
        # a run never ends with nothing to read (ADR 0039).
        _persist_plan_run(
            handle,
            result=result,
            ledger=ledger,
            content_hash=attack_spec.source.content_hash,
            path=path,
        )


def _ship_or_report(
    result: PlanRunResult,
    *,
    ledger: CostLedger,
    target_dir: Path,
    handle: RunHandle | None,
    source_url: str,
) -> Path | None:
    """Write ``lab.yaml`` on a ship; otherwise print an actionable message and return ``None``."""
    if result.shipped():
        assert result.manifest is not None  # a ship always carries a manifest
        # Framework-stamp the manifest once at the ship boundary (billed Planner model, version,
        # generation metadata — ADR 0086) so the cwd deliverable and the run-dir mirror are byte-
        # identical; persistence then fills only the not-yet-mirrored partial/halt case.
        stamped = stamp_framework_provenance(result.manifest, ledger)
        path = write_lab_manifest(stamped, directory=target_dir)
        if handle is not None:
            handle.write_artifact(MANIFEST_FILENAME, stamped)
        _emit_plan_report(result)
        output.print_info(f"\nwrote {path}")
        return path
    if result.status is PlanPipelineStatus.ROUTE_BACK_TO_EXTRACTOR:
        _report_route_back(result, source_url=source_url)
        return None
    _report_halt(result)
    return None


def _emit_plan_report(result: PlanRunResult) -> None:
    """Print the ship-path report tail (low-confidence + unresolved jury feedback)."""
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
    del path  # the plan status is authoritative on a ship; no path-vs-status ambiguity to resolve
    if exc is not None:
        return _exception_status(exc)
    if result is None:  # crashed before the runner returned, without raising (defensive)
        return RunStatus.ABORTED, None
    return _PLAN_STATUS_TO_RUN_STATUS.get(result.status, RunStatus.FAILED), result.halt_reason


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
    "PipelinePlanRunner",
    "PlanRunResult",
    "PlanRunner",
    "manifest_to_yaml",
    "run_plan",
    "write_lab_manifest",
]
