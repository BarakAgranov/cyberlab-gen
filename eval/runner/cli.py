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

import sys
from typing import TYPE_CHECKING

from eval.runner.manifest import load_manifest, walk_path
from eval.runner.report import EvalReport, archive_report
from eval.runner.runner import DEFAULT_N, run_blog_set

if TYPE_CHECKING:
    from pathlib import Path

    from eval.runner.manifest import BlogSetManifest
    from eval.runner.runner import EvalPipelineRunner

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
) -> tuple[EvalReport, Path]:
    """Run the curated set through ``runner`` N times, archive the report, return both.

    Injectable for tests (the smoke test passes a fake runner + a tmp reports
    dir). ``provider_backed`` is recorded on the report so offline runs are
    distinguishable in the archive. Returns the report and the path it was
    archived to.
    """
    from eval.runner.report import REPORTS_RELDIR

    manifest = load_manifest(manifest_path)
    report = run_blog_set(manifest=manifest, runner=runner, n=n, provider_backed=provider_backed)
    target_dir = reports_dir if reports_dir is not None else _default_reports_dir(REPORTS_RELDIR)
    path = archive_report(report, reports_dir=target_dir)
    return report, path


def _default_reports_dir(reldir: str) -> Path:
    from eval.runner.manifest import repo_root

    return repo_root() / reldir


def main(argv: list[str] | None = None) -> int:
    """``just eval`` body. Returns a process exit code.

    With a provider configured: build the provider-backed runner, run N=3 over the
    curated set, archive the report, print the headline numbers. Without one: load
    + validate the manifest, report that no provider is configured, and exit 0
    (the manifest/walk validation still ran — a useful offline smoke check).
    """
    del argv  # no flags in Phase 1; N and paths are defaults
    manifest = load_manifest()
    missing = check_walks_resolve(manifest)
    if missing:
        print(  # noqa: T201 -- CLI user-facing output
            f"eval: manifest walk paths do not resolve for: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1

    if not _provider_configured():
        print(  # noqa: T201
            "eval: no live provider configured "
            f"(set ANTHROPIC_API_KEY to run the provider-backed harness).\n"
            f"eval: manifest OK — {len(manifest.curated)} curated blog(s), "
            f"rotation generation {manifest.rotation_generation}; all walk paths resolve.\n"
            "eval: nothing run (the harness never fabricates results without a model)."
        )
        return 0

    runner = _build_provider_backed_runner(manifest)  # pragma: no cover - needs a live provider
    report, path = run_eval(runner=runner, provider_backed=True)  # pragma: no cover
    print(  # noqa: T201 # pragma: no cover
        f"eval: ran {report.runs_per_blog} run(s) x {len(report.blog_ids)} blog(s); "
        f"Layer-1 pass rate {report.overall_layer1_pass_rate():.0%}; "
        f"valid-spec blogs {report.blogs_with_valid_spec()}/{len(report.blog_ids)}; "
        f"archived to {path}"
    )
    return 0


def _build_provider_backed_runner(
    manifest: BlogSetManifest,
) -> EvalPipelineRunner:  # pragma: no cover - needs a live provider
    """Wire the production provider-backed runner (only reached with a live provider).

    Reuses the *same* production construction as the ``extract`` verb
    (``cyberlab_gen.cli.main._build_extract_runner``): Ingestion + Extractor +
    Jury + Validator-Layer-1 on the orchestrator. Heavy imports are deferred so
    this module imports cleanly with no provider configured. Not exercised in CI;
    the metric mapping it relies on is tested via :func:`record_from_run`.

    Each run gets a fresh ``ExtractRunner`` (so cached blog content from one blog
    doesn't bleed into the next) and a fresh ``CostLedger`` (so ``cost_usd`` is
    per-run). The URL comes from the manifest entry; a ``TBD`` URL (the synthetic
    long-blog fixture) is skipped with a clear error rather than fetched.
    """
    from cyberlab_gen.agents.extractor.extractor import Extractor
    from cyberlab_gen.agents.extractor_jury.jury import ExtractorJury
    from cyberlab_gen.cli.extract import PipelineExtractRunner
    from cyberlab_gen.providers.anthropic_provider import AnthropicProvider
    from cyberlab_gen.providers.cost_ledger import CostLedger
    from cyberlab_gen.providers.ranking import build_provider_registry
    from cyberlab_gen.registries.merge import load_merged_registries
    from cyberlab_gen.validators.layer1 import Layer1Validator
    from eval.runner.runner import ProviderBackedEvalRunner

    registry = build_provider_registry()
    registries = load_merged_registries()
    validator = Layer1Validator(registries=registries)

    def url_for(blog_id: str) -> str:
        entry = manifest.entry(blog_id)
        if not entry.url_is_resolved():
            raise ValueError(
                f"blog {blog_id!r} has no resolved URL (it is a synthetic fixture); "
                "provider-backed eval cannot fetch it"
            )
        return entry.url

    def extract_runner_factory() -> PipelineExtractRunner:
        provider = AnthropicProvider()
        return PipelineExtractRunner(
            extractor=Extractor(provider=provider, registry=registry, registries=registries),
            validator=validator,
            jury=ExtractorJury(provider=provider, registry=registry, registries=registries),
        )

    def cost_ledger_factory() -> CostLedger:
        return CostLedger(run_id="eval", cap_usd=None)

    return ProviderBackedEvalRunner(
        extract_runner_factory=extract_runner_factory,
        validator=validator,
        url_for=url_for,
        cost_ledger_factory=cost_ledger_factory,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
