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
from cyberlab_gen.providers.cost_ledger import DEFAULT_CATASTROPHE_CEILING_USD
from eval.runner.metrics import BlogAggregate, BlogRunRecord, structural_completeness
from eval.runner.report import EvalReport, SkippedBlog

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from cyberlab_gen.cli.extract import ExtractRunner
    from cyberlab_gen.providers.cost_ledger import CostLedger
    from cyberlab_gen.schemas.attack_spec import AttackSpec
    from cyberlab_gen.validators.static_schema_validator import StaticSchemaValidator
    from eval.runner.manifest import BlogSetManifest

#: Default repeated-run count per blog (``eval.md §7.6``; the Phase-1 exit
#: criterion runs N=3, ``implementation-plan.md §4.5``).
DEFAULT_N = 3

#: Reason recorded for a blog skipped because it has no live URL (ADR 0028).
_UNRESOLVED_URL_REASON = "synthetic fixture, no live URL"

#: Default cumulative spend ceiling for a provider-backed eval. Reframed by ADR 0038
#: from the earlier $5 *everyday* cap (ADR 0030) to the high *catastrophe* backstop:
#: its only job is to stop a pathological runaway, not to be an everyday brake. Now
#: that per-call cost is visible in the run log, the user sets an informed value once
#: real costs are observed. Enforced mid-run (after each billed call) by
#: ``CostRecordingProvider`` and across runs by ``run_blog_set``.
DEFAULT_COST_CAP_USD = DEFAULT_CATASTROPHE_CEILING_USD

#: Stop a *single blog* after this many *consecutive* runs fail with the same
#: blog-specific error (ADR 0030, scope split ADR 0034). A transient blip never
#: counts; a global failure aborts the whole run on sight without counting.
DEFAULT_ABORT_AFTER_CONSECUTIVE_FAILURES = 2

#: Failure-kind tags recorded on a :class:`BlogRunRecord` (ADR 0030/0034). The run
#: loop treats them differently:
#: * ``FAILURE_RETRYABLE`` — a persistent ``TransientFailure`` (timeout/429/5xx/
#:   connection after retries). A blip: never aborts or skips; resets the counter.
#: * ``FAILURE_BLOG_FATAL`` — tied to *this* blog's content/size/URL (truncation,
#:   malformed/won't-validate, hallucination budget, tool loop, jury/Layer-1
#:   reject, unreachable/paywalled/bot-blocked URL, a content/size 4xx). Skips this
#:   blog's remaining runs and moves to the next blog.
#: * ``FAILURE_GLOBAL_FATAL`` — the next blog will fail identically (no served
#:   model, auth/credential/quota/config ``HardFailure``). Aborts the whole run.
FAILURE_RETRYABLE = "retryable"
FAILURE_BLOG_FATAL = "blog_fatal"
FAILURE_GLOBAL_FATAL = "global_fatal"

#: HTTP statuses on a ``HardFailure`` cause that mean the whole run is doomed —
#: authentication (401), payment/credit i.e. quota (402), permission (403), and
#: model-not-found (404). Every blog would fail identically, so these are
#: global-fatal; a content/size 4xx (400 'request too large', 413, 422) is
#: blog-specific (ADR 0034, classification confirmed with the user).
_GLOBAL_HTTP_STATUSES = frozenset({401, 402, 403, 404})


def _classify_pipeline_failure(exc: BaseException) -> str:
    """Map a caught pipeline exception to its failure kind / scope (ADR 0034).

    Eval-runner-only triage: it decides whether the *next blog* should still get a
    turn. The underlying *halt* already happened in the provider/orchestrator; this
    never re-decides a single blog's fate, only whether the run continues.
    """
    from cyberlab_gen.errors import CapabilityUnreachable, HardFailure, TransientFailure

    if isinstance(exc, TransientFailure):
        return FAILURE_RETRYABLE
    if isinstance(exc, CapabilityUnreachable):
        # No served model for the capability -> every blog uses the same capability
        # and fails the same way. Systemic.
        return FAILURE_GLOBAL_FATAL
    if isinstance(exc, HardFailure):
        return FAILURE_GLOBAL_FATAL if _hard_failure_is_global(exc) else FAILURE_BLOG_FATAL
    # Everything else (EmitTruncated, MalformedOutput, AgentFailure, ExtractionError,
    # ToolLoopError, ValidationError/JuryRejectionError, ingestion URL errors) is
    # specific to this blog's content/size/URL.
    return FAILURE_BLOG_FATAL


def _hard_failure_is_global(exc: BaseException) -> bool:
    """A ``HardFailure`` is global unless it is a content/size-specific HTTP 4xx.

    Auth/permission/payment/model-not-found (401/402/403/404) and any ``HardFailure``
    with no HTTP status at all (client-init / missing API key / no-pricing config)
    are systemic — every blog fails the same way. A request/content 4xx (400
    'request too large', 413, 422) is specific to this blog, so it is blog-fatal.
    The status code is read off the stored ``cause`` (the vendor ``APIStatusError``).
    """
    status = getattr(getattr(exc, "cause", None), "status_code", None)
    if status is None:
        return True
    return int(status) in _GLOBAL_HTTP_STATUSES


def _normalize_failure(reason: str) -> str:
    """Strip run-varying detail from a halt reason so repeats compare equal (ADR 0030).

    The tool-loop 400 names a different ``toolu_`` id, ``request_id`` (``req_…``),
    and ``messages.N`` index each run; without normalizing, two instances of the
    *same* systemic failure would look distinct and never trip the
    consecutive-failure abort. Collapse the variable bits (tool ids, request ids,
    message indices, any digits) to placeholders. The ``req_…`` id is mixed
    alphanumeric, so the digit-only collapse alone left it varying — the gap that
    let six identical 400s run in full in the gen0-20260602 archive (ADR 0032).
    """
    s = re.sub(r"toolu_[A-Za-z0-9]+", "toolu_X", reason)
    s = re.sub(r"req_[A-Za-z0-9]+", "req_X", s)
    s = re.sub(r"messages\.\d+", "messages.N", s)
    s = re.sub(r"\d+", "N", s)
    return s.strip()


def _failure_signature(record: BlogRunRecord) -> str | None:
    """A normalized signature for a *blog-fatal* failed run, else ``None`` (ADR 0034).

    Only blog-specific failures get a signature — the within-blog fail-fast counts
    consecutive identical ones to stop a blog early. A clean run, a retryable blip,
    or a global-fatal failure (which aborts the whole run on sight, without
    counting) returns ``None`` so the within-blog counter resets.
    """
    if record.failure_kind != FAILURE_BLOG_FATAL or not record.halt_reason:
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
        specs_dir: Path | None = None,
    ) -> None:
        # The factory builds a fresh ``ExtractRunner`` per run from a per-run
        # ``CostLedger`` *this* class constructs (so the run can read real spend off
        # it — the provider the factory wires must record into the passed ledger).
        # Exercised by the provider-backed path, not run in CI (ADR 0025/0030).
        self._extract_runner_factory = extract_runner_factory
        self._validator = validator
        self._url_for = url_for
        self._cost_cap_usd = cost_cap_usd
        # When set, each *shipped* run's AttackSpec is written here as
        # ``<blog_id>-run<run_index>.yaml`` — the report records only metrics, so
        # the spec is otherwise unreadable from disk (it's the thing a maintainer
        # needs to judge quality, e.g. whether a `completeness=0.85` ship was
        # actually a truncated emit). Halted runs ship no spec, so none is written.
        self._specs_dir = specs_dir

    def run_once(self, blog_id: str, *, run_index: int) -> BlogRunRecord:  # pragma: no cover
        # The live path: build a fresh ledger + runner, resolve the URL, run, and
        # measure. Not exercised in CI (no provider); the metric mapping it shares
        # with the fake path is what :func:`record_from_run` tests cover.
        from cyberlab_gen.errors import CyberlabGenError
        from cyberlab_gen.providers.cost_ledger import CostLedger

        url = self._url_for(blog_id)
        ledger = CostLedger(run_id="eval", cap_usd=self._cost_cap_usd)
        runner = self._extract_runner_factory(ledger)
        try:
            result = runner.run(url, ledger=ledger)
        except CyberlabGenError as exc:
            # Classify the halt so the run loop knows whether to skip just this blog
            # (blog-fatal: truncation, malformed, jury/Layer-1 reject, bad URL) or
            # abort the whole run (global: no served model, auth/quota/config), or
            # treat it as a transient blip (retryable). See _classify_pipeline_failure.
            return self._halt_record(
                blog_id, run_index, ledger, exc, failure_kind=_classify_pipeline_failure(exc)
            )
        layer1 = self._validator.validate(result.spec)
        self._write_spec(blog_id, run_index, result.spec)
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

    def _write_spec(self, blog_id: str, run_index: int, spec: AttackSpec) -> None:
        """Write a shipped run's AttackSpec to ``specs_dir`` (no-op when unset).

        Keyed ``<blog_id>-run<run_index>.yaml`` and re-uses the ``extract`` verb's
        serializer so the eval spec and a hand-run ``extract`` spec are byte-identical.
        Re-running an eval overwrites the same-keyed file (the latest run wins); the
        timestamped report remains the historical record of the metrics.
        """
        if self._specs_dir is None:
            return
        from cyberlab_gen.cli.extract import spec_to_yaml

        self._specs_dir.mkdir(parents=True, exist_ok=True)
        path = self._specs_dir / f"{blog_id}-run{run_index}.yaml"
        path.write_text(spec_to_yaml(spec), encoding="utf-8")

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

    Failure handling distinguishes *which blog* from *the whole run* (ADR 0034):

    * **blog-fatal → skip this blog** — a failure tied to one blog's content/size/
      URL (truncation, malformed/won't-validate, hallucination budget, tool loop,
      jury/Layer-1 reject, bad URL). ``abort_after_consecutive_failures`` consecutive
      runs of *this blog* failing with the same (normalized) blog-fatal error stop
      that blog's remaining runs; the run then **continues to the next blog** (its
      size/content problem says nothing about other blogs).
    * **global-fatal → abort the whole run** — a failure where the next blog would
      fail identically (no served model, auth/credential/quota/config). Aborts on
      sight, marking the not-yet-run blogs ``skipped`` and archiving the partial.
    * **cost cap → abort the whole run** — once cumulative spend reaches
      ``cost_cap_usd`` (``None`` = uncapped) the eval stops before the next run.

    A transient blip (``retryable``) never aborts or skips and resets the
    within-blog counter.
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
        # Within-blog fail-fast state, reset per blog: a blog-specific failure that
        # repeats identically stops THIS blog, not the whole run.
        blog_sig: str | None = None
        blog_sig_count = 0
        stop_blog_reason: str | None = None
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

            if record.failure_kind == FAILURE_GLOBAL_FATAL:
                # Systemic: the next blog would fail identically. Abort the whole run.
                abort_reason = (
                    f"global failure (every remaining blog would fail identically): "
                    f"{record.halt_reason}"
                )
            else:
                # Blog-fatal: count consecutive identical failures *within this blog*;
                # at the threshold, stop this blog and move on. A clean run, a
                # retryable blip, or a global-fatal (handled above) yields no
                # signature and resets the counter.
                sig = _failure_signature(record)
                if sig is None:
                    blog_sig, blog_sig_count = None, 0
                else:
                    blog_sig_count = blog_sig_count + 1 if sig == blog_sig else 1
                    blog_sig = sig
                    if blog_sig_count >= abort_after_consecutive_failures:
                        stop_blog_reason = (
                            f"{blog_sig_count} consecutive runs of {blog_id!r} failed with the "
                            f"same blog-specific error; skipping its remaining runs and moving on"
                        )
            # cost cap: stop the whole run once cumulative spend reaches the ceiling.
            if abort_reason is None and cost_cap_usd is not None and total_cost >= cost_cap_usd:
                abort_reason = (
                    f"cost cap ${cost_cap_usd} reached (spent ${total_cost}); "
                    f"remaining runs skipped"
                )
            if abort_reason is not None or stop_blog_reason is not None:
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
    "FAILURE_BLOG_FATAL",
    "FAILURE_GLOBAL_FATAL",
    "FAILURE_RETRYABLE",
    "EvalPipelineRunner",
    "EvalProgress",
    "ProviderBackedEvalRunner",
    "record_from_run",
    "run_blog_set",
]
