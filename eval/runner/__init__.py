"""Phase-1 eval-harness runner: manifest, metrics, per-blog runner, review, report.

Per ``CLAUDE.md`` cross-subpackage imports go through ``__init__.py`` re-exports.
The Phase-1 additions (``implementation-plan.md §4.2``; design in ADR 0025):

- :mod:`eval.runner.manifest` — the curated/held-out blog-set manifest loader
  (shape from ADR 0014).
- :mod:`eval.runner.metrics` — the per-run ``BlogRunRecord``, the structural-
  completeness formula, and per-blog aggregation (``eval.md §7.4`` / §7.6).
- :mod:`eval.runner.runner` — the per-blog eval runner: invoke the Extractor
  pipeline N times and record metrics; the injectable runner seam.
- :mod:`eval.runner.review` — manual jury-decision review tooling: false-approval
  / false-rejection rates (``eval.md §7.5``).
- :mod:`eval.runner.report` — the archived ``EvalReport`` + writer
  (``eval/reports/``, ``eval.md §7.13``).
- :mod:`eval.runner.cli` — the ``just eval`` entrypoint.
"""

from eval.runner.manifest import (
    BlogEntry,
    BlogSetManifest,
    load_manifest,
    walk_path,
)
from eval.runner.metrics import (
    BlogAggregate,
    BlogRunRecord,
    structural_completeness,
)
from eval.runner.report import (
    EvalReport,
    archive_report,
    load_report,
)
from eval.runner.review import (
    BlogReviewRates,
    JuryReviewEntry,
    JuryReviewLedger,
    ReviewMark,
)
from eval.runner.runner import (
    DEFAULT_N,
    EvalPipelineRunner,
    ProviderBackedEvalRunner,
    record_from_run,
    run_blog_set,
)

__all__ = [
    "DEFAULT_N",
    "BlogAggregate",
    "BlogEntry",
    "BlogReviewRates",
    "BlogRunRecord",
    "BlogSetManifest",
    "EvalPipelineRunner",
    "EvalReport",
    "JuryReviewEntry",
    "JuryReviewLedger",
    "ProviderBackedEvalRunner",
    "ReviewMark",
    "archive_report",
    "load_manifest",
    "load_report",
    "record_from_run",
    "run_blog_set",
    "structural_completeness",
    "walk_path",
]
