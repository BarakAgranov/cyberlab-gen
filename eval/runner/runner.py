"""The per-blog eval runner: invoke the Extractor pipeline N times, record metrics.

Architectural source: ``eval.md §7.4`` (the Phase-1 metrics), ``eval.md §7.6``
(repeated runs, N=3 default), ``implementation-plan.md §4.2`` "Per-blog eval
runner that invokes the Extractor pipeline N times and records metrics". Runner
seam + ``BlogRunRecord`` shape pinned in ADR 0025.

The harness drives the pipeline through a narrow injectable protocol
(:class:`EvalPipelineRunner`) so (a) the smoke test runs deterministically
offline and (b) a maintainer with a configured provider runs the *real* pipeline.
This mirrors Task 7's runner seam (ADR 0024) and ``eval.md §7.2``'s honest
framing: the harness's *logic* is the deliverable, independent of whether a given
invocation has a live model behind it.

The production runner (:class:`ProviderBackedEvalRunner`) wraps the Task-7
``PipelineExtractRunner`` (Ingestion → orchestrator) and the Task-6
``StaticSchemaValidator`` and yields a :class:`~eval.runner.metrics.BlogRunRecord` per
run. When no provider is configured the harness reports that cleanly and runs
nothing (it never fabricates results).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

from cyberlab_gen.agents.extractor_jury.schema import Verdict
from eval.runner.metrics import BlogAggregate, BlogRunRecord, structural_completeness
from eval.runner.report import EvalReport, SkippedBlog

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from cyberlab_gen.cli.extract import ExtractRunner
    from cyberlab_gen.providers.cost_ledger import CostLedger
    from cyberlab_gen.validators.static_schema_validator import StaticSchemaValidator
    from eval.runner.manifest import BlogSetManifest

#: Default repeated-run count per blog (``eval.md §7.6``; the Phase-1 exit
#: criterion runs N=3, ``implementation-plan.md §4.5``).
DEFAULT_N = 3

#: Reason recorded for a blog skipped because it has no live URL (ADR 0028).
_UNRESOLVED_URL_REASON = "synthetic fixture, no live URL"

#: Default cumulative spend ceiling for a provider-backed eval (ADR 0030). A real
#: run stops (and archives what it has) once cumulative spend reaches this.
DEFAULT_COST_CAP_USD = Decimal("5")

#: Abort the whole eval after this many *consecutive* runs fail with the same
#: non-retryable error (ADR 0030). A transient blip never counts.
DEFAULT_ABORT_AFTER_CONSECUTIVE_FAILURES = 2

#: Failure-kind tags recorded on a :class:`BlogRunRecord` (ADR 0030).
FAILURE_RETRYABLE = "retryable"
FAILURE_NON_RETRYABLE = "non_retryable"


def _normalize_failure(reason: str) -> str:
    """Strip run-varying detail from a halt reason so repeats compare equal (ADR 0030).

    The tool-loop 400 names a different ``toolu_`` id and ``messages.N`` index each
    run; without normalizing, two instances of the *same* systemic failure would
    look distinct and never trip the consecutive-failure abort. Collapse the
    variable bits (tool ids, message indices, any digits) to placeholders.
    """
    s = re.sub(r"toolu_[A-Za-z0-9]+", "toolu_X", reason)
    s = re.sub(r"messages\.\d+", "messages.N", s)
    s = re.sub(r"\d+", "N", s)
    return s.strip()


def _failure_signature(record: BlogRunRecord) -> str | None:
    """A normalized signature for a *non-retryable* failed run, else ``None``.

    Only non-retryable failures (``HardFailure``/4xx/malformed/halt) get a
    signature; a clean run or a retryable (transient) failure returns ``None`` so
    the fail-fast counter resets and a transient blip never aborts the eval.
    """
    if record.failure_kind != FAILURE_NON_RETRYABLE or not record.halt_reason:
        return None
    return _normalize_failure(record.halt_reason)


class EvalPipelineRunner(Protocol):
    """The pipeline surface the harness depends on (the testability boundary, ADR 0025).

    ``run_once`` runs the full Extractor pipeline for one blog and returns the
    measured :class:`BlogRunRecord`. The production implementation is
    :class:`ProviderBackedEvalRunner`; tests inject a scripted fake. ``run_index``
    is threaded through so the record carries which of the N runs it was.
    """

    def run_once(self, blog_id: str, *, run_index: int) -> BlogRunRecord: ...


class EvalProgress(Protocol):
    """Live-progress surface the run loop drives (ADR 0028).

    The production implementation is
    :class:`eval.runner.progress.StderrEvalProgress`, which writes one concise
    line per event to stderr. ``None`` means run silently. Defined here (not in
    ``progress.py``) so the runner depends only on the structural type and there
    is no import cycle.
    """

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
        record: BlogRunRecord,
        *,
        n: int,
        cost_so_far: Decimal,
        cost_cap_usd: Decimal | None = None,
    ) -> None: ...

    def blog_skipped(self, blog_id: str, *, reason: str) -> None: ...

    def run_aborted(self, reason: str) -> None: ...

    def report_archived(self, path: Path) -> None: ...


class ProviderBackedEvalRunner:
    """The production :class:`EvalPipelineRunner`: real Ingestion → orchestrator (ADR 0025).

    Wraps a Task-7 ``ExtractRunner`` (which drives Ingestion → the LangGraph
    pipeline) plus the Task-6 ``StaticSchemaValidator`` so the per-run record carries
    the Layer-1 pass/fail the ``RunResult`` omits (ADR 0024 left it off the CLI
    type). Requires a configured provider behind the runner; absent one the agents
    raise at resolve time (``provider-interface.md §6.3``) — the harness CLI guards
    against that before constructing this.

    ``url_for`` maps a blog id to its fetch URL (from the manifest); the runner
    ingests + caches the blog and runs the pipeline once per call.
    """

    def __init__(
        self,
        *,
        extract_runner_factory: Callable[[CostLedger], ExtractRunner],
        validator: StaticSchemaValidator,
        url_for: Callable[[str], str],
        cost_cap_usd: Decimal | None = DEFAULT_COST_CAP_USD,
    ) -> None:
        # The factory builds a fresh ``ExtractRunner`` per run from a per-run
        # ``CostLedger`` *this* class constructs (so the run can read real spend off
        # it — the provider the factory wires must record into the passed ledger).
        # Exercised by the provider-backed path, not run in CI (ADR 0025/0030).
        self._extract_runner_factory = extract_runner_factory
        self._validator = validator
        self._url_for = url_for
        self._cost_cap_usd = cost_cap_usd

    def run_once(self, blog_id: str, *, run_index: int) -> BlogRunRecord:  # pragma: no cover
        # The live path: build a fresh ledger + runner, resolve the URL, run, and
        # measure. Not exercised in CI (no provider); the metric mapping it shares
        # with the fake path is what :func:`record_from_run` tests cover.
        from cyberlab_gen.errors import CyberlabGenError, TransientFailure
        from cyberlab_gen.providers.cost_ledger import CostLedger

        url = self._url_for(blog_id)
        ledger = CostLedger(run_id="eval", cap_usd=self._cost_cap_usd)
        runner = self._extract_runner_factory(ledger)
        try:
            result = runner.run(url, ledger=ledger)
        except TransientFailure as exc:
            # A persistent transient failure (timeout/429/5xx after retries) —
            # record it, but tag it retryable so fail-fast never aborts on a blip.
            return self._halt_record(
                blog_id, run_index, ledger, exc, failure_kind=FAILURE_RETRYABLE
            )
        except CyberlabGenError as exc:
            # A non-retryable halt (HardFailure/4xx, malformed, Layer-1 exhaustion,
            # jury reject) — record a non-shipped run that fail-fast can count.
            return self._halt_record(
                blog_id, run_index, ledger, exc, failure_kind=FAILURE_NON_RETRYABLE
            )
        layer1 = self._validator.validate(result.spec)
        return record_from_run(
            blog_id=blog_id,
            run_index=run_index,
            shipped=True,
            layer1_passed=layer1.passed,
            cost_usd=ledger.total_usd,
            spec=result.spec,
            value_type_proposals=len(result.value_type_proposals),
            facet_proposals=len(result.facet_proposals),
            verdict=Verdict.APPROVE if not result.low_jury_confidence else Verdict.REVISE,
            low_jury_confidence=result.low_jury_confidence,
        )

    def _halt_record(  # pragma: no cover - live path
        self,
        blog_id: str,
        run_index: int,
        ledger: CostLedger,
        exc: Exception,
        *,
        failure_kind: str,
    ) -> BlogRunRecord:
        """Build a non-shipped record for a halted run, tagged with its failure kind."""
        return BlogRunRecord(
            blog_id=blog_id,
            run_index=run_index,
            shipped=False,
            layer1_passed=False,
            cost_usd=ledger.total_usd,
            completeness_score=0.0,
            structural_completeness=0.0,
            value_type_proposals=0,
            facet_proposals=0,
            extras_count=0,
            verdict=Verdict.REJECT,
            halt_reason=str(exc),
            failure_kind=failure_kind,
        )


def record_from_run(
    *,
    blog_id: str,
    run_index: int,
    shipped: bool,
    layer1_passed: bool,
    cost_usd: Decimal,
    spec: object,
    value_type_proposals: int,
    facet_proposals: int,
    verdict: Verdict,
    low_jury_confidence: bool = False,
    halt_reason: str | None = None,
) -> BlogRunRecord:
    """Build a :class:`BlogRunRecord` from a finished run's parts (ADR 0025).

    Computes the harness structural-completeness metric from the spec and reads
    the Extractor's own ``completeness_score`` + ``extras`` count off it. Shared by
    the provider-backed runner and the test fakes so the metric mapping is tested
    once. ``spec`` is typed ``object`` to keep this importable without forcing the
    schema import path at call sites; it is narrowed to ``AttackSpec`` here.
    """
    from cyberlab_gen.schemas.attack_spec import AttackSpec

    if not isinstance(spec, AttackSpec):  # pragma: no cover - defensive
        raise TypeError("record_from_run requires an AttackSpec")
    return BlogRunRecord(
        blog_id=blog_id,
        run_index=run_index,
        shipped=shipped,
        layer1_passed=layer1_passed,
        cost_usd=cost_usd,
        completeness_score=spec.extraction_metadata.completeness_score,
        structural_completeness=structural_completeness(spec),
        value_type_proposals=value_type_proposals,
        facet_proposals=facet_proposals,
        extras_count=len(spec.extras),
        verdict=verdict,
        low_jury_confidence=low_jury_confidence,
        halt_reason=halt_reason,
    )


def run_blog_set(
    *,
    manifest: BlogSetManifest,
    runner: EvalPipelineRunner,
    n: int = DEFAULT_N,
    provider_backed: bool,
    blog_ids: list[str] | None = None,
    generated_at: datetime | None = None,
    on_partial: Callable[[EvalReport], None] | None = None,
    progress: EvalProgress | None = None,
    cost_cap_usd: Decimal | None = None,
    abort_after_consecutive_failures: int = DEFAULT_ABORT_AFTER_CONSECUTIVE_FAILURES,
) -> EvalReport:
    """Run every curated blog ``n`` times through ``runner`` and build an :class:`EvalReport`.

    ``blog_ids`` defaults to the manifest's curated set (held-out rotation is
    Phase 4). Each blog is run ``n`` times (``eval.md §7.6``); the per-blog
    aggregate carries mean/median/variance, and the report carries the flat run
    records too. ``provider_backed`` is recorded on the report so an offline run
    (fake runner) is distinguishable from a real one in the archive.

    A *provider-backed* run cannot fetch a blog whose URL is the ``TBD`` sentinel
    (the synthetic long-blog fixture), so such a blog is **skipped** — recorded in
    ``EvalReport.skipped`` and left out of ``blog_ids`` — rather than crashing the
    run (ADR 0028). An offline run does not fetch URLs, so it skips nothing.

    ``on_partial`` is invoked with the report-so-far after each blog completes, so
    a caller can archive incrementally; a crash on a later blog then still leaves
    the earlier blogs' results on disk (ADR 0028, Problem 2). ``progress`` receives
    one event per step for live stderr output.

    Two spend guards stop a doomed run early (ADR 0030), each marking the
    not-yet-run blogs ``skipped`` and archiving the partial report:

    * **fail-fast** — ``abort_after_consecutive_failures`` consecutive runs failing
      with the *same non-retryable* error (normalized so a varying tool id/index
      still matches) abort the eval; a transient blip never counts.
    * **cost cap** — once cumulative spend reaches ``cost_cap_usd`` (``None`` =
      uncapped) the eval stops before launching the next run.
    """
    if n < 1:
        raise ValueError("n (runs per blog) must be >= 1")
    ids = blog_ids if blog_ids is not None else [e.id for e in manifest.curated]
    gen_at = generated_at if generated_at is not None else datetime.now(UTC)

    # Partition into blogs that will run vs. blogs skipped for an unresolved URL.
    # The skip is decided *before* any provider call (it never reaches the runner).
    ran_ids: list[str] = []
    skipped: list[SkippedBlog] = []
    for blog_id in ids:
        if provider_backed and not manifest.entry(blog_id).url_is_resolved():
            skipped.append(SkippedBlog(blog_id=blog_id, reason=_UNRESOLVED_URL_REASON))
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

    records: list[BlogRunRecord] = []
    aggregates: list[BlogAggregate] = []
    done_ids: list[str] = []
    total_cost = Decimal("0")
    total = len(ran_ids)
    consecutive_sig: str | None = None
    consecutive_count = 0

    def _build() -> EvalReport:
        return EvalReport(
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
        blog_runs: list[BlogRunRecord] = []
        for i in range(n):
            if progress is not None:
                progress.blog_run_started(blog_id, blog_pos=pos, blog_total=total, run_index=i, n=n)
            record = runner.run_once(blog_id, run_index=i)
            blog_runs.append(record)
            records.append(record)
            total_cost += record.cost_usd
            if progress is not None:
                progress.blog_run_finished(
                    record, n=n, cost_so_far=total_cost, cost_cap_usd=cost_cap_usd
                )

            # fail-fast: count consecutive identical non-retryable failures.
            sig = _failure_signature(record)
            if sig is None:
                consecutive_sig, consecutive_count = None, 0
            else:
                consecutive_count = consecutive_count + 1 if sig == consecutive_sig else 1
                consecutive_sig = sig
                if consecutive_count >= abort_after_consecutive_failures:
                    abort_reason = (
                        f"{consecutive_count} consecutive runs failed with the same "
                        f"non-retryable error; remaining runs skipped to avoid wasted spend"
                    )
            # cost cap: stop once cumulative spend reaches the ceiling.
            if abort_reason is None and cost_cap_usd is not None and total_cost >= cost_cap_usd:
                abort_reason = (
                    f"cost cap ${cost_cap_usd} reached (spent ${total_cost}); "
                    f"remaining runs skipped"
                )
            if abort_reason is not None:
                break

        # Record this blog's (possibly partial) aggregate from the runs that ran.
        if blog_runs:
            aggregates.append(BlogAggregate.from_runs(blog_id, blog_runs))
            done_ids.append(blog_id)

        if abort_reason is not None:
            # Mark every blog after this one as skipped (none of its runs happened).
            for remaining in ran_ids[pos:]:
                skipped.append(SkippedBlog(blog_id=remaining, reason=abort_reason))
                if progress is not None:
                    progress.blog_skipped(remaining, reason=abort_reason)
            if progress is not None:
                progress.run_aborted(abort_reason)

        # Archive what's finished so a crash/abort on a later blog never loses it.
        if on_partial is not None:
            on_partial(_build())

        if abort_reason is not None:
            break

    return _build()


__all__ = [
    "DEFAULT_ABORT_AFTER_CONSECUTIVE_FAILURES",
    "DEFAULT_COST_CAP_USD",
    "DEFAULT_N",
    "FAILURE_NON_RETRYABLE",
    "FAILURE_RETRYABLE",
    "EvalPipelineRunner",
    "EvalProgress",
    "ProviderBackedEvalRunner",
    "record_from_run",
    "run_blog_set",
]
