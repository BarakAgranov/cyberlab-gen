"""The per-blog plan-eval runner: invoke the Planner pipeline N times, record metrics (ADR 0102).

The Phase-2 counterpart of :mod:`eval.runner.runner` (the Extractor-stage runner). It drives the
plan pipeline through the same narrow injectable seam (:class:`PlanEvalPipelineRunner`) so the tests
run deterministically offline and a maintainer with a configured provider runs the real Planner.

**The eval-overlay-read-only guarantee (ADR 0100 owned deferral, ADR 0102 decision 3).** The ``plan``
*verb* engine (``cyberlab_gen.cli.plan``) promotes accepted facet proposals into a registry overlay.
An eval sweep across the curated set must be **read-only** w.r.t. every overlay. So
:class:`ProviderBackedPlanEvalRunner` drives :meth:`PlanRunner.run` **directly** — it never calls the
verb's promotion path, never constructs an overlay, and only *counts* the Planner's facet proposals.
This module therefore contains no promotion/overlay machinery at all; a test asserts that absence and
that a full run leaves no overlay on disk.

The production runner reads the pipeline's *emitted* output — the terminal ``PlanPipelineStatus`` and
the shipped ``LabManifest`` — and maps it to a :class:`~eval.runner.plan_metrics.PlanRunRecord` via
:func:`~eval.runner.plan_metrics.record_from_plan_run`, never re-deriving a pipeline decision (F1,
``eval.md §7.4``). When no provider is configured the harness reports that cleanly and runs nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

from eval.runner.plan_metrics import PlanBlogAggregate, PlanRunRecord, record_from_plan_run
from eval.runner.plan_report import PlanEvalReport
from eval.runner.report import SkippedBlog
from eval.runner.runner import (
    DEFAULT_COST_CAP_USD,
    DEFAULT_N,
    FAILURE_GLOBAL_FATAL,
    classify_pipeline_failure,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from cyberlab_gen.cli.plan import PlanRunner
    from cyberlab_gen.providers.cost_ledger import CostLedger
    from cyberlab_gen.schemas.attack_spec import AttackSpec
    from cyberlab_gen.state.run_store import RunHandle, RunStore
    from eval.runner.manifest import BlogSetManifest

#: Reason recorded for a blog skipped because it has no committed attack-spec fixture (ADR 0102).
_NO_ATTACK_SPEC_REASON = "no committed attack_spec fixture (extract the blog first)"


class PlanEvalPipelineRunner(Protocol):
    """The plan-pipeline surface the harness depends on (the testability boundary, ADR 0102).

    ``plan_once`` runs the full Planner pipeline for one blog's AttackSpec and returns the measured
    :class:`PlanRunRecord`. Production: :class:`ProviderBackedPlanEvalRunner`; tests inject a scripted
    fake.
    """

    def plan_once(self, blog_id: str, *, run_index: int) -> PlanRunRecord: ...


class PlanEvalProgress(Protocol):
    """Live-progress surface the plan run loop drives (mirrors ``EvalProgress``, ADR 0102)."""

    def run_started(
        self,
        *,
        ran_ids: list[str],
        skipped_ids: list[str],
        n: int,
        provider_backed: bool,
        cost_cap_usd: Decimal | None = None,
    ) -> None: ...

    def blog_run_started(
        self, blog_id: str, *, blog_pos: int, blog_total: int, run_index: int, n: int
    ) -> None: ...

    def blog_run_finished(
        self,
        record: PlanRunRecord,
        *,
        n: int,
        cost_so_far: Decimal,
        cost_cap_usd: Decimal | None = None,
    ) -> None: ...

    def blog_skipped(self, blog_id: str, *, reason: str) -> None: ...

    def run_aborted(self, reason: str) -> None: ...

    def report_archived(self, path: Path) -> None: ...


class ProviderBackedPlanEvalRunner:
    """The production :class:`PlanEvalPipelineRunner`: real Planner pipeline, overlay-read-only (ADR 0102).

    Wraps a :class:`PlanRunner` (built per run from a per-run :class:`CostLedger`) and calls its
    ``run`` **directly** — *not* the ``plan`` verb's ``run_plan``, which would promote facet proposals
    to an overlay. Facet proposals are *counted*, never promoted. The per-run static facts are read
    off the pipeline's emitted status + manifest (F1). ``attack_spec_for`` maps a blog id to its
    committed AttackSpec fixture; ``manifests_dir`` (optional) saves each shipped manifest for
    inspection; ``run_store`` (optional) writes a complete run directory per run.
    """

    def __init__(
        self,
        *,
        plan_runner_factory: Callable[[CostLedger], PlanRunner],
        attack_spec_for: Callable[[str], AttackSpec],
        cost_cap_usd: Decimal | None = DEFAULT_COST_CAP_USD,
        manifests_dir: Path | None = None,
        run_store: RunStore | None = None,
    ) -> None:
        self._plan_runner_factory = plan_runner_factory
        self._attack_spec_for = attack_spec_for
        self._cost_cap_usd = cost_cap_usd
        self._manifests_dir = manifests_dir
        self._run_store = run_store

    def plan_once(self, blog_id: str, *, run_index: int) -> PlanRunRecord:
        from cyberlab_gen.errors import CyberlabGenError
        from cyberlab_gen.providers.cost_ledger import CostLedger

        ledger = CostLedger(run_id="eval-plan", cap_usd=self._cost_cap_usd)
        runner = self._plan_runner_factory(ledger)
        attack_spec = self._attack_spec_for(blog_id)
        handle = self._start_run_dir(blog_id, run_index=run_index)
        try:
            try:
                # DIRECT drive of the runner seam — never the verb's run_plan (no promotion, ADR 0102).
                result = runner.run(attack_spec, ledger=ledger)
            except CyberlabGenError as exc:
                self._persist(handle, manifest=None, verdict=None, ledger=ledger, shipped=False)
                return self._failure_record(
                    blog_id,
                    run_index,
                    ledger,
                    str(exc),
                    failure_kind=classify_pipeline_failure(exc),
                )
            verdict = result.verdict.verdict if result.verdict is not None else None
            record = record_from_plan_run(
                blog_id=blog_id,
                run_index=run_index,
                status=result.status,
                cost_usd=ledger.total_usd,
                manifest=result.manifest,
                facet_proposals=len(result.facet_proposals),  # counted, NOT promoted
                verdict=verdict,
                low_jury_confidence=result.low_jury_confidence,
                halt_reason=result.halt_reason,
            )
            self._write_manifest(blog_id, run_index, result)
            self._persist(
                handle,
                manifest=result.manifest,
                verdict=result.verdict,
                ledger=ledger,
                shipped=record.shipped,
                low_confidence=result.low_jury_confidence,
            )
            return record
        except BaseException as exc:  # persist the partial, then re-raise (KeyboardInterrupt/crash)
            self._persist(
                handle,
                manifest=None,
                verdict=None,
                ledger=ledger,
                shipped=False,
                interrupted=isinstance(exc, KeyboardInterrupt),
                crash_reason=str(exc),
            )
            raise

    def _start_run_dir(self, blog_id: str, *, run_index: int) -> RunHandle | None:
        if self._run_store is None:
            return None
        from cyberlab_gen.state.run_store import RunKind, RunLineage

        return self._run_store.start(
            kind=RunKind.PLAN,
            label=blog_id,
            id_hint=f"{blog_id}-plan-run{run_index}",
            lineage=RunLineage(input_ref=blog_id),
        )

    def _write_manifest(self, blog_id: str, run_index: int, result: object) -> None:
        """Write a shipped run's LabManifest to ``manifests_dir`` (no-op when unset / no manifest)."""
        manifest = getattr(result, "manifest", None)
        if self._manifests_dir is None or manifest is None:
            return
        from cyberlab_gen.cli.plan import manifest_to_yaml
        from cyberlab_gen.schemas.manifest import LabManifest

        if not isinstance(manifest, LabManifest):  # pragma: no cover - defensive
            return
        self._manifests_dir.mkdir(parents=True, exist_ok=True)
        path = self._manifests_dir / f"{blog_id}-run{run_index}.yaml"
        path.write_text(manifest_to_yaml(manifest), encoding="utf-8")

    def _persist(
        self,
        handle: RunHandle | None,
        *,
        manifest: object,
        verdict: object,
        ledger: CostLedger,
        shipped: bool,
        low_confidence: bool = False,
        interrupted: bool = False,
        crash_reason: str | None = None,
    ) -> None:
        """Best-effort run-dir persistence using the shared service (no overlay write, ADR 0102)."""
        if handle is None:
            return
        from cyberlab_gen.agents.extractor_jury.schema import JuryVerdict
        from cyberlab_gen.schemas.manifest import LabManifest
        from cyberlab_gen.state.run_persistence import persist_plan_artifacts
        from cyberlab_gen.state.run_store import RunStatus

        persist_plan_artifacts(
            handle,
            manifest=manifest if isinstance(manifest, LabManifest) else None,
            verdict=verdict if isinstance(verdict, JuryVerdict) else None,
            ledger=ledger,
            content_hash=None,
        )
        handle.write_cost(ledger)
        if interrupted:
            status = RunStatus.INTERRUPTED
        elif crash_reason is not None:
            status = RunStatus.CRASHED
        elif shipped:
            status = RunStatus.SHIPPED_LOW_CONFIDENCE if low_confidence else RunStatus.SHIPPED
        else:
            status = RunStatus.FAILED
        handle.finalize(status, halt_reason=crash_reason)

    def _failure_record(
        self,
        blog_id: str,
        run_index: int,
        ledger: CostLedger,
        halt_reason: str,
        *,
        failure_kind: str,
    ) -> PlanRunRecord:
        """A record for an *infra* failure (a raised ``CyberlabGenError`` — no terminal status)."""
        return PlanRunRecord(
            blog_id=blog_id,
            run_index=run_index,
            status=None,
            shipped=False,
            layer2_passed=False,
            route_back=False,
            cost_usd=ledger.total_usd,
            manifest_field_coverage=0.0,
            facet_proposals=0,
            verdict=None,
            halt_reason=halt_reason,
            failure_kind=failure_kind,
        )


def run_plan_set(
    *,
    manifest: BlogSetManifest,
    runner: PlanEvalPipelineRunner,
    n: int = DEFAULT_N,
    provider_backed: bool,
    blog_ids: list[str] | None = None,
    generated_at: datetime | None = None,
    on_partial: Callable[[PlanEvalReport], None] | None = None,
    progress: PlanEvalProgress | None = None,
    cost_cap_usd: Decimal | None = None,
) -> PlanEvalReport:
    """Run every curated blog ``n`` times through the plan ``runner`` and build a :class:`PlanEvalReport`.

    Mirrors :func:`eval.runner.runner.run_blog_set`, leaner where the plan stage allows:

    * **skip blogs with no committed ``attack_spec`` fixture** (provider-backed only) — recorded in
      ``skipped`` rather than crashing (ADR 0102; the plan analog of the TBD-URL skip, ADR 0028). An
      offline (fake-runner) run skips nothing.
    * **global-fatal → abort the whole run** — a failure where the next blog would fail identically
      (no served model, auth/quota/config). Aborts on sight, marks the rest ``skipped``.
    * **cost cap → abort** — once cumulative spend reaches ``cost_cap_usd``.
    * **incremental archive** via ``on_partial`` so a later-blog crash never loses earlier results.

    It deliberately omits the Extractor stage's *within-blog consecutive-failure* fast-stop: the plan
    pipeline *returns* its terminal states (route-back, halts) as records rather than raising, so
    re-running a deterministically-halting spec N times yields N informative terminal records (a
    calibration signal), not a money-burning retry loop — the global-abort + cost-cap cover the only
    systemic money risk. (This is a documented difference, not a silent coverage cap: every selected
    blog still gets all N runs attempted.)
    """
    if n < 1:
        raise ValueError("n (runs per blog) must be >= 1")
    ids = blog_ids if blog_ids is not None else [e.id for e in manifest.curated]
    gen_at = generated_at if generated_at is not None else datetime.now(UTC)

    ran_ids: list[str] = []
    skipped: list[SkippedBlog] = []
    for blog_id in ids:
        if provider_backed and not manifest.entry(blog_id).attack_spec_is_resolved():
            skipped.append(SkippedBlog(blog_id=blog_id, reason=_NO_ATTACK_SPEC_REASON))
        else:
            ran_ids.append(blog_id)

    if progress is not None:
        progress.run_started(
            ran_ids=ran_ids,
            skipped_ids=[s.blog_id for s in skipped],
            n=n,
            provider_backed=provider_backed,
            cost_cap_usd=cost_cap_usd,
        )
        for s in skipped:
            progress.blog_skipped(s.blog_id, reason=s.reason)

    records: list[PlanRunRecord] = []
    aggregates: list[PlanBlogAggregate] = []
    done_ids: list[str] = []
    total_cost = Decimal("0")
    total = len(ran_ids)

    def _build() -> PlanEvalReport:
        return PlanEvalReport(
            generated_at=gen_at,
            rotation_generation=manifest.rotation_generation,
            runs_per_blog=n,
            provider_backed=provider_backed,
            blog_ids=list(done_ids),
            aggregates=list(aggregates),
            records=list(records),
            skipped=list(skipped),
        )

    abort_reason: str | None = None
    for pos, blog_id in enumerate(ran_ids, start=1):
        blog_runs: list[PlanRunRecord] = []
        for i in range(n):
            if progress is not None:
                progress.blog_run_started(blog_id, blog_pos=pos, blog_total=total, run_index=i, n=n)
            try:
                record = runner.plan_once(blog_id, run_index=i)
            except BaseException:  # archive the partial, then re-raise (KeyboardInterrupt/crash)
                if on_partial is not None:
                    on_partial(_build())
                raise
            blog_runs.append(record)
            records.append(record)
            total_cost += record.cost_usd
            if progress is not None:
                progress.blog_run_finished(
                    record, n=n, cost_so_far=total_cost, cost_cap_usd=cost_cap_usd
                )

            if record.failure_kind == FAILURE_GLOBAL_FATAL:
                abort_reason = (
                    f"global failure (every remaining blog would fail identically): "
                    f"{record.halt_reason}"
                )
            if abort_reason is None and cost_cap_usd is not None and total_cost >= cost_cap_usd:
                abort_reason = f"cost cap ${cost_cap_usd} reached (spent ${total_cost}); remaining runs skipped"
            if abort_reason is not None:
                break

        if blog_runs:
            aggregates.append(PlanBlogAggregate.from_runs(blog_id, blog_runs))
            done_ids.append(blog_id)

        if abort_reason is not None:
            for remaining in ran_ids[pos:]:
                skipped.append(SkippedBlog(blog_id=remaining, reason=abort_reason))
                if progress is not None:
                    progress.blog_skipped(remaining, reason=abort_reason)
            if progress is not None:
                progress.run_aborted(abort_reason)

        if on_partial is not None:
            on_partial(_build())

        if abort_reason is not None:
            break

    return _build()


__all__ = [
    "DEFAULT_N",
    "PlanEvalPipelineRunner",
    "PlanEvalProgress",
    "ProviderBackedPlanEvalRunner",
    "run_plan_set",
]
