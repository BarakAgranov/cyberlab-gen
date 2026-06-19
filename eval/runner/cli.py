"""The ``just eval`` entrypoint for the Phase-1 eval harness.

Architectural source: ``coding-conventions.md §10`` (the harness is invoked via
``just eval``, not pytest), ``eval.md §7.13`` (results archive to
``eval/reports/``), Task-8 exit criteria. Design in ADR 0025.

The entrypoint loads the blog-set manifest, decides whether a live provider is
configured (``is_provider_configured`` per ADR 0011), and either:

* runs the provider-backed harness over the curated set N times and archives the
  report (``eval/reports/``); or
* when no provider is configured, prints a clear notice and exits without
  fabricating results (``eval.md §7.2`` honest framing). It still validates that
  the manifest loads and its walks resolve, so ``just eval`` is a meaningful
  smoke check even offline.

The smoke test (``tests/eval/``) calls :func:`run_eval` with an injected fake
runner so the full archive path is exercised deterministically.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from cyberlab_gen.state import RunStore
from eval.runner.manifest import attack_spec_path, load_manifest, walk_path
from eval.runner.plan_report import PlanEvalReport, archive_plan_report
from eval.runner.plan_runner import run_plan_set
from eval.runner.report import REPORTS_RELDIR, EvalReport, archive_report
from eval.runner.runner import DEFAULT_COST_CAP_USD, DEFAULT_N, run_blog_set

if TYPE_CHECKING:
    from decimal import Decimal
    from pathlib import Path

    from cyberlab_gen.providers.cost_ledger import CostLedger
    from eval.runner.manifest import BlogSetManifest
    from eval.runner.plan_runner import PlanEvalPipelineRunner, PlanEvalProgress
    from eval.runner.runner import EvalPipelineRunner, EvalProgress

#: The provider whose configuration gates a real (provider-backed) eval run.
_LIVE_PROVIDER = "anthropic"


def _provider_configured() -> bool:
    """True when a real provider is configured for a provider-backed run (ADR 0011)."""
    from cyberlab_gen.providers import is_provider_configured

    return is_provider_configured(_LIVE_PROVIDER)


def check_walks_resolve(manifest: BlogSetManifest, *, root: Path | None = None) -> list[str]:
    """Return the list of blog ids whose ``walk:`` path does not resolve to a file.

    Empty list ⇒ every walk resolves (the healthy case). Used by the offline
    smoke notice and by ``tests/eval/test_manifest.py`` so a manifest entry can
    never silently point at a missing walk (ADR 0014 forward-compat note).
    """
    missing: list[str] = []
    for entry in (*manifest.curated, *manifest.held_out):
        if not walk_path(entry, root=root).is_file():
            missing.append(entry.id)
    return missing


def run_eval(
    *,
    runner: EvalPipelineRunner,
    provider_backed: bool,
    n: int = DEFAULT_N,
    manifest_path: Path | None = None,
    reports_dir: Path | None = None,
    progress: EvalProgress | None = None,
    cost_cap_usd: Decimal | None = DEFAULT_COST_CAP_USD,
    blog_ids: list[str] | None = None,
) -> tuple[EvalReport, Path]:
    """Run the curated set through ``runner`` N times, archive the report, return both.

    Injectable for tests (the smoke test passes a fake runner + a tmp reports
    dir). ``provider_backed`` is recorded on the report so offline runs are
    distinguishable in the archive. Returns the report and the path it was
    archived to.

    ``blog_ids`` (default ``None`` = the whole curated set) restricts the run to a
    subset — the ``--blog <id>`` single-blog path. Ids must exist in the manifest;
    the CLI validates that before calling here.

    The report is archived **incrementally** — after each blog completes — so a
    crash on a later blog never loses the money already spent on the earlier ones
    (ADR 0028). The run also stops early on repeated non-retryable failures or once
    cumulative spend reaches ``cost_cap_usd`` (ADR 0030). ``progress`` (when given)
    receives live events for stderr output.
    """
    manifest = load_manifest(manifest_path)
    target_dir = reports_dir if reports_dir is not None else _default_reports_dir(REPORTS_RELDIR)

    def _archive_partial(partial: EvalReport) -> None:
        archive_report(partial, reports_dir=target_dir)

    report = run_blog_set(
        manifest=manifest,
        runner=runner,
        n=n,
        provider_backed=provider_backed,
        blog_ids=blog_ids,
        on_partial=_archive_partial,
        progress=progress,
        cost_cap_usd=cost_cap_usd,
    )
    path = archive_report(report, reports_dir=target_dir)
    if progress is not None:
        progress.report_archived(path)
    return report, path


def run_plan_eval(
    *,
    runner: PlanEvalPipelineRunner,
    provider_backed: bool,
    n: int = DEFAULT_N,
    manifest_path: Path | None = None,
    reports_dir: Path | None = None,
    progress: PlanEvalProgress | None = None,
    cost_cap_usd: Decimal | None = DEFAULT_COST_CAP_USD,
    blog_ids: list[str] | None = None,
) -> tuple[PlanEvalReport, Path]:
    """Run the curated set through the plan ``runner`` N times, archive the report, return both.

    The plan-stage counterpart of :func:`run_eval` (ADR 0102). Injectable for tests (the smoke test
    passes a fake plan runner + a tmp reports dir). Archives **incrementally** (after each blog) so a
    later-blog crash never loses earlier results. Blogs with no committed ``attack_spec`` fixture are
    skipped in a provider-backed run by :func:`~eval.runner.plan_runner.run_plan_set`.
    """
    manifest = load_manifest(manifest_path)
    target_dir = reports_dir if reports_dir is not None else _default_reports_dir(REPORTS_RELDIR)

    def _archive_partial(partial: PlanEvalReport) -> None:
        archive_plan_report(partial, reports_dir=target_dir)

    report = run_plan_set(
        manifest=manifest,
        runner=runner,
        n=n,
        provider_backed=provider_backed,
        blog_ids=blog_ids,
        on_partial=_archive_partial,
        progress=progress,
        cost_cap_usd=cost_cap_usd,
    )
    path = archive_plan_report(report, reports_dir=target_dir)
    if progress is not None:
        progress.report_archived(path)
    return report, path


def _default_reports_dir(reldir: str) -> Path:
    from eval.runner.manifest import repo_root

    return repo_root() / reldir


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse the ``just eval`` flags.

    ``--blog <id>`` restricts the run to one blog; ``--stage {extract,plan}`` (default ``extract``)
    selects which pipeline stage to evaluate (the plan stage is the Phase-2 addition, ADR 0102).
    """
    parser = argparse.ArgumentParser(
        prog="eval",
        description="Run the cyberlab-gen eval over the curated blog set.",
    )
    parser.add_argument(
        "--blog",
        metavar="ID",
        default=None,
        help="run only the curated blog with this id (default: all curated blogs)",
    )
    parser.add_argument(
        "--stage",
        choices=("extract", "plan"),
        default="extract",
        help="which pipeline stage to evaluate (default: extract; plan is the Phase-2 stage)",
    )
    return parser.parse_args(argv)


def _resolve_selected_blogs(
    manifest: BlogSetManifest, blog: str | None
) -> tuple[list[str] | None, str | None]:
    """Map a ``--blog`` value to a ``blog_ids`` override (or an error message).

    Returns ``(blog_ids, error)``: ``blog_ids`` is ``None`` for the whole curated
    set (no ``--blog``) or a one-element list for the selected blog; ``error`` is a
    user-facing message (with the valid ids) when the id is unknown, else ``None``.
    """
    if blog is None:
        return None, None
    curated_ids = [e.id for e in manifest.curated]
    if blog not in curated_ids:
        return None, (
            f"eval: unknown blog id {blog!r}; valid curated ids: {', '.join(curated_ids)}"
        )
    return [blog], None


def main(argv: list[str] | None = None) -> int:
    """``just eval`` body. Returns a process exit code.

    ``--stage`` (default ``extract``) selects which pipeline stage to evaluate; the shared prefix
    (logging, manifest load, walk-resolution check, ``--blog`` resolution) runs for both, then the
    run dispatches to the Extractor- or plan-stage flow. With a provider configured each flow builds
    its provider-backed runner, runs N=3 over the selected blogs, archives the report, and prints the
    headline numbers; without one it reports that no provider is configured and exits 0 (the
    manifest/walk validation still ran — a useful offline smoke check). An unknown ``--blog`` id exits
    2; an unresolved walk exits 1.
    """
    from cyberlab_gen.logging_setup import setup_logging
    from cyberlab_gen.tracing_setup import setup_tracing

    args = _parse_args(argv)
    log_path = setup_logging(run_id="eval")
    # Stream traces to a local Phoenix if one is running; a no-op otherwise (ADR 0041).
    setup_tracing()
    print(f"eval: run log -> {log_path}", file=sys.stderr)  # noqa: T201
    manifest = load_manifest()
    missing = check_walks_resolve(manifest)
    if missing:
        print(  # noqa: T201 -- CLI user-facing output
            f"eval: manifest walk paths do not resolve for: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1

    blog_ids, blog_error = _resolve_selected_blogs(manifest, args.blog)
    if blog_error is not None:
        print(blog_error, file=sys.stderr)  # noqa: T201
        return 2

    if args.stage == "plan":
        return _main_plan(manifest, blog=args.blog, blog_ids=blog_ids)

    if not _provider_configured():
        scope = (
            f"only blog {args.blog!r}"
            if args.blog is not None
            else f"{len(manifest.curated)} curated blog(s)"
        )
        print(  # noqa: T201
            "eval: no live provider configured "
            f"(set ANTHROPIC_API_KEY to run the provider-backed harness).\n"
            f"eval: manifest OK — would run {scope}, "
            f"rotation generation {manifest.rotation_generation}; all walk paths resolve.\n"
            "eval: nothing run (the harness never fabricates results without a model)."
        )
        return 0

    from cyberlab_gen.runtime import persisting_signal_guard
    from eval.runner.progress import StderrEvalProgress

    # pragma: no cover - needs a live provider
    runner = _build_provider_backed_runner(manifest, cost_cap_usd=DEFAULT_COST_CAP_USD)
    try:
        # SIGTERM->KeyboardInterrupt so a terminate unwinds through the run-store and
        # incremental-archive finally blocks (a partial run is saved), like Ctrl-C.
        with persisting_signal_guard():
            report, path = run_eval(  # pragma: no cover
                runner=runner,
                provider_backed=True,
                progress=StderrEvalProgress(),
                cost_cap_usd=DEFAULT_COST_CAP_USD,
                blog_ids=blog_ids,
            )
    except KeyboardInterrupt:  # pragma: no cover - live path
        print("eval: interrupted; partial results were archived.", file=sys.stderr)  # noqa: T201
        return 130
    # Final machine-readable summary stays on stdout (progress went to stderr).
    print(  # noqa: T201 # pragma: no cover
        f"eval: ran {report.runs_per_blog} run(s) x {len(report.blog_ids)} blog(s)"
        f"{f' ({len(report.skipped)} skipped)' if report.skipped else ''}; "
        f"static-schema pass rate {report.overall_static_schema_pass_rate():.0%}; "
        f"valid-spec blogs {report.blogs_with_valid_spec()}/{len(report.blog_ids)}; "
        f"archived to {path}"
    )
    return 0


def _build_provider_backed_runner(
    manifest: BlogSetManifest,
    *,
    cost_cap_usd: Decimal | None = DEFAULT_COST_CAP_USD,
) -> EvalPipelineRunner:  # pragma: no cover - needs a live provider
    """Wire the production provider-backed runner (only reached with a live provider).

    Reuses the *same* production construction as the ``extract`` verb
    (``cyberlab_gen.cli.main._build_extract_runner``): Ingestion + Extractor +
    Jury + Validator-static-schema on the orchestrator. Heavy imports are deferred so
    this module imports cleanly with no provider configured. Not exercised in CI;
    the metric mapping it relies on is tested via :func:`record_from_run`.

    Each run gets a fresh ``ExtractRunner`` (so cached blog content from one blog
    doesn't bleed into the next). The provider is wrapped in a
    :class:`~cyberlab_gen.providers.cost_recording_provider.CostRecordingProvider` so each
    call's cost lands in the per-run ``CostLedger`` (built by the runner with the
    cap) — giving the cost cap real spend to act on (ADR 0030). The URL comes from
    the manifest entry; a ``TBD`` URL is skipped upstream by ``run_blog_set`` before
    any run, so ``url_for`` never sees one (it still raises as a backstop, ADR 0028).
    """
    from cyberlab_gen.agents.extractor.extractor import Extractor
    from cyberlab_gen.agents.extractor_jury.jury import ExtractorJury
    from cyberlab_gen.cli.extract import PipelineExtractRunner
    from cyberlab_gen.providers.anthropic_provider import AnthropicProvider
    from cyberlab_gen.providers.cost_recording_provider import CostRecordingProvider
    from cyberlab_gen.providers.ranking import build_provider_registry
    from cyberlab_gen.registries.merge import load_merged_registries
    from cyberlab_gen.validators.static_schema_validator import StaticSchemaValidator
    from eval.runner.runner import ProviderBackedEvalRunner

    registry = build_provider_registry()
    registries = load_merged_registries()
    validator = StaticSchemaValidator(registries=registries)

    def url_for(blog_id: str) -> str:
        entry = manifest.entry(blog_id)
        if not entry.url_is_resolved():
            raise ValueError(
                f"blog {blog_id!r} has no resolved URL (it is a synthetic fixture); "
                "provider-backed eval cannot fetch it"
            )
        return entry.url

    def extract_runner_factory(ledger: CostLedger) -> PipelineExtractRunner:
        # One wrapped provider per run, recording every call's cost into this run's
        # ledger so ``ledger.total_usd`` (read back by the runner) is real spend.
        provider = CostRecordingProvider(AnthropicProvider(), ledger)
        return PipelineExtractRunner(
            extractor=Extractor(provider=provider, registry=registry, registries=registries),
            validator=validator,
            jury=ExtractorJury(provider=provider, registry=registry, registries=registries),
        )

    return ProviderBackedEvalRunner(
        extract_runner_factory=extract_runner_factory,
        url_for=url_for,
        cost_cap_usd=cost_cap_usd,
        # Shipped specs land alongside the reports, under eval/reports/specs/, so a
        # maintainer can read the actual emitted AttackSpec (the report holds only
        # metrics). Co-located with the default report dir (REPORTS_RELDIR).
        specs_dir=_default_reports_dir(REPORTS_RELDIR) / "specs",
        # Every run (shipped or halted) also gets a complete, non-overwriting run
        # directory under eval/reports/runs/ — its spec, jury verdict, enrichment,
        # cost breakdown and run.json grouped together for inspection (ADR 0039).
        run_store=RunStore(_default_reports_dir(REPORTS_RELDIR) / "runs"),
    )


def _main_plan(manifest: BlogSetManifest, *, blog: str | None, blog_ids: list[str] | None) -> int:
    """The ``--stage plan`` flow (ADR 0102): offline notice or the provider-backed plan-eval run.

    Mirrors the Extractor-stage path. Offline (no provider) it reports which selected blogs have a
    committed ``attack_spec`` fixture and *would* run vs. be skipped — and runs nothing.
    """
    if not _provider_configured():
        selected = blog_ids if blog_ids is not None else [e.id for e in manifest.curated]
        runnable = [bid for bid in selected if manifest.entry(bid).attack_spec_is_resolved()]
        scope = f"only blog {blog!r}" if blog is not None else f"{len(selected)} curated blog(s)"
        print(  # noqa: T201
            "eval(plan): no live provider configured "
            "(set ANTHROPIC_API_KEY to run the provider-backed plan harness).\n"
            f"eval(plan): manifest OK — would plan {scope}, of which {len(runnable)} have a "
            f"committed attack_spec fixture and would actually run "
            f"({', '.join(runnable) or 'none'}); the rest are skipped until extracted. "
            f"rotation generation {manifest.rotation_generation}.\n"
            "eval(plan): nothing run (the harness never fabricates results without a model)."
        )
        return 0

    from cyberlab_gen.runtime import persisting_signal_guard  # pragma: no cover - live path
    from eval.runner.progress import StderrPlanEvalProgress  # pragma: no cover - live path

    runner = _build_provider_backed_plan_runner(  # pragma: no cover
        manifest, cost_cap_usd=DEFAULT_COST_CAP_USD
    )
    try:  # pragma: no cover - live path
        with persisting_signal_guard():
            report, path = run_plan_eval(
                runner=runner,
                provider_backed=True,
                progress=StderrPlanEvalProgress(),
                cost_cap_usd=DEFAULT_COST_CAP_USD,
                blog_ids=blog_ids,
            )
    except KeyboardInterrupt:  # pragma: no cover - live path
        print(  # noqa: T201
            "eval(plan): interrupted; partial results were archived.", file=sys.stderr
        )
        return 130
    print(  # noqa: T201 # pragma: no cover
        f"eval(plan): ran {report.runs_per_blog} run(s) x {len(report.blog_ids)} blog(s)"
        f"{f' ({len(report.skipped)} skipped)' if report.skipped else ''}; "
        f"layer-2 pass rate {report.overall_layer2_pass_rate():.0%}; "
        f"planned blogs {report.blogs_planned()}/{len(report.blog_ids)}; "
        f"route-backs {report.total_route_backs()}; archived to {path}"
    )
    return 0


def _build_provider_backed_plan_runner(
    manifest: BlogSetManifest,
    *,
    cost_cap_usd: Decimal | None = DEFAULT_COST_CAP_USD,
) -> PlanEvalPipelineRunner:  # pragma: no cover - needs a live provider
    """Wire the production provider-backed plan runner (only reached with a live provider, ADR 0102).

    Reuses the *same* Planner construction as the ``plan`` verb
    (``cyberlab_gen.cli.main._build_plan_runner``): Planner + Planner-Jury + the semantic
    cross-check, on one ``CostRecordingProvider`` bound to the per-run ledger. Critically, this drives
    the runner's ``run`` **directly** (via ``ProviderBackedPlanEvalRunner``) — never the ``plan``
    verb's ``run_plan``, so the eval never promotes a facet proposal to any overlay (ADR 0100/0102).
    ``attack_spec_for`` loads each blog's committed fixture; a blog with none is skipped upstream.
    """
    from ruamel.yaml import YAML

    from cyberlab_gen.agents.planner.planner import Planner
    from cyberlab_gen.agents.planner_jury.jury import PlannerJury
    from cyberlab_gen.cli.plan import PipelinePlanRunner
    from cyberlab_gen.providers.anthropic_provider import AnthropicProvider
    from cyberlab_gen.providers.cost_recording_provider import CostRecordingProvider
    from cyberlab_gen.providers.ranking import build_provider_registry
    from cyberlab_gen.registries.merge import load_merged_registries
    from cyberlab_gen.schemas.attack_spec import AttackSpec
    from cyberlab_gen.schemas.loading import load_spec
    from cyberlab_gen.validators.semantic_cross_check_validator import SemanticCrossCheckValidator
    from eval.runner.plan_runner import ProviderBackedPlanEvalRunner

    registry = build_provider_registry()
    registries = load_merged_registries()

    def plan_runner_factory(ledger: CostLedger) -> PipelinePlanRunner:
        provider = CostRecordingProvider(AnthropicProvider(), ledger)
        return PipelinePlanRunner(
            planner=Planner(provider=provider, registry=registry, registries=registries),
            jury=PlannerJury(provider=provider, registry=registry, registries=registries),
            validator=SemanticCrossCheckValidator(registries=registries),
            provider=provider,
            registries=registries,
        )

    def attack_spec_for(blog_id: str) -> AttackSpec:
        entry = manifest.entry(blog_id)
        path = attack_spec_path(entry)
        if path is None:
            raise ValueError(
                f"blog {blog_id!r} has no committed attack_spec fixture; plan eval cannot run it"
            )
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
        spec = load_spec(data)
        if not isinstance(spec, AttackSpec):
            raise ValueError(f"attack_spec fixture for {blog_id!r} is not an AttackSpec")
        return spec

    return ProviderBackedPlanEvalRunner(
        plan_runner_factory=plan_runner_factory,
        attack_spec_for=attack_spec_for,
        cost_cap_usd=cost_cap_usd,
        # Shipped manifests land under eval/reports/plan-manifests/ for inspection (the report holds
        # only metrics); each run also gets a non-overwriting run dir under eval/reports/plan-runs/.
        manifests_dir=_default_reports_dir(REPORTS_RELDIR) / "plan-manifests",
        run_store=RunStore(_default_reports_dir(REPORTS_RELDIR) / "plan-runs"),
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
