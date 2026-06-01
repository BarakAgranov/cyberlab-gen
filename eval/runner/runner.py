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

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from cyberlab_gen.agents.extractor_jury.schema import Verdict
from eval.runner.metrics import BlogAggregate, BlogRunRecord, structural_completeness
from eval.runner.report import EvalReport

if TYPE_CHECKING:
    from collections.abc import Callable
    from decimal import Decimal

    from cyberlab_gen.cli.extract import ExtractRunner
    from cyberlab_gen.providers.cost_ledger import CostLedger
    from cyberlab_gen.validators.static_schema_validator import StaticSchemaValidator
    from eval.runner.manifest import BlogSetManifest

#: Default repeated-run count per blog (``eval.md §7.6``; the Phase-1 exit
#: criterion runs N=3, ``implementation-plan.md §4.5``).
DEFAULT_N = 3


class EvalPipelineRunner(Protocol):
    """The pipeline surface the harness depends on (the testability boundary, ADR 0025).

    ``run_once`` runs the full Extractor pipeline for one blog and returns the
    measured :class:`BlogRunRecord`. The production implementation is
    :class:`ProviderBackedEvalRunner`; tests inject a scripted fake. ``run_index``
    is threaded through so the record carries which of the N runs it was.
    """

    def run_once(self, blog_id: str, *, run_index: int) -> BlogRunRecord: ...


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
        extract_runner_factory: Callable[[], ExtractRunner],
        validator: StaticSchemaValidator,
        url_for: Callable[[str], str],
        cost_ledger_factory: Callable[[], CostLedger],
    ) -> None:
        # The factories build a fresh ``ExtractRunner`` + ``CostLedger`` per run so
        # cached blog content / cost don't bleed across blogs. They are exercised by
        # the provider-backed path, which is not run in CI (ADR 0025).
        self._extract_runner_factory = extract_runner_factory
        self._validator = validator
        self._url_for = url_for
        self._cost_ledger_factory = cost_ledger_factory

    def run_once(self, blog_id: str, *, run_index: int) -> BlogRunRecord:  # pragma: no cover
        # The live path: build a fresh runner + ledger, resolve the URL, run, and
        # measure. Not exercised in CI (no provider); the metric mapping it shares
        # with the fake path is what :func:`record_from_run` tests cover.
        from cyberlab_gen.errors import CyberlabGenError

        url = self._url_for(blog_id)
        ledger = self._cost_ledger_factory()
        runner = self._extract_runner_factory()
        try:
            result = runner.run(url, ledger=ledger)
        except CyberlabGenError as exc:
            # A halt (Layer-1 exhaustion / jury reject) — record a non-shipped run.
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
) -> EvalReport:
    """Run every curated blog ``n`` times through ``runner`` and build an :class:`EvalReport`.

    ``blog_ids`` defaults to the manifest's curated set (held-out rotation is
    Phase 4). Each blog is run ``n`` times (``eval.md §7.6``); the per-blog
    aggregate carries mean/median/variance, and the report carries the flat run
    records too. ``provider_backed`` is recorded on the report so an offline run
    (fake runner) is distinguishable from a real one in the archive.
    """
    if n < 1:
        raise ValueError("n (runs per blog) must be >= 1")
    ids = blog_ids if blog_ids is not None else [e.id for e in manifest.curated]
    records: list[BlogRunRecord] = []
    aggregates: list[BlogAggregate] = []
    for blog_id in ids:
        blog_runs = [runner.run_once(blog_id, run_index=i) for i in range(n)]
        records.extend(blog_runs)
        aggregates.append(BlogAggregate.from_runs(blog_id, blog_runs))
    return EvalReport(
        generated_at=generated_at if generated_at is not None else datetime.now(UTC),
        rotation_generation=manifest.rotation_generation,
        runs_per_blog=n,
        provider_backed=provider_backed,
        blog_ids=ids,
        aggregates=aggregates,
        records=records,
    )


__all__ = [
    "DEFAULT_N",
    "EvalPipelineRunner",
    "ProviderBackedEvalRunner",
    "record_from_run",
    "run_blog_set",
]
