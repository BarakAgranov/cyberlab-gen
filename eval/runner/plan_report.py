"""The archived plan-eval report (``eval/reports/``) and its writer (ADR 0102).

The Phase-2 plan-stage counterpart of :mod:`eval.runner.report` (``eval.md §7.13``: results archive
to ``eval/reports/``). An :class:`ArtifactModel` so it round-trips losslessly through YAML; the
harness writes one timestamped file per ``just eval --stage plan`` run, named
``gen<rotation-gen>-plan-<timestamp>.yaml`` — the ``-plan-`` infix keeps plan reports from colliding
with the Extractor-stage reports (``gen<gen>-<timestamp>.yaml``) in the same directory.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import ConfigDict, Field
from ruamel.yaml import YAML
from ruamel.yaml.compat import StringIO

from cyberlab_gen.schemas.base import ArtifactModel
from eval.runner.plan_metrics import PlanBlogAggregate, PlanRunRecord
from eval.runner.report import SkippedBlog

if TYPE_CHECKING:
    from pathlib import Path


class PlanEvalReport(ArtifactModel):
    """One ``just eval --stage plan`` run's archived report (ADR 0102).

    Carries the manifest ``rotation_generation``, the run configuration (N, the blog ids covered),
    the per-blog aggregates, and the flat list of run records so the raw per-run numbers are
    recoverable from the archive alone — mirroring :class:`eval.runner.report.EvalReport`. Reuses
    :class:`~eval.runner.report.SkippedBlog` for blogs with no committed ``attack_spec`` (skipped in a
    provider-backed plan run, like the TBD-URL skip in the Extractor stage).
    """

    model_config = ConfigDict(frozen=True)

    spec_version: int = 1
    spec_kind: str = "PlanEvalReport"
    generated_at: datetime
    rotation_generation: int = Field(ge=0)
    runs_per_blog: int = Field(ge=1)
    provider_backed: bool
    blog_ids: list[str] = Field(default_factory=list[str])
    aggregates: list[PlanBlogAggregate] = Field(default_factory=list[PlanBlogAggregate])
    records: list[PlanRunRecord] = Field(default_factory=list[PlanRunRecord])
    skipped: list[SkippedBlog] = Field(default_factory=list[SkippedBlog])

    def overall_layer2_pass_rate(self) -> float:
        """Semantic-cross-check (Layer-2) pass rate across every run (``implementation-plan.md §5.5``).

        The exit criterion is ">=90% of curated runs pass Layer 1 + Layer 2"; a run counts when its
        emitted manifest cleared the cross-check gate.
        """
        if not self.records:
            return 0.0
        return sum(1 for r in self.records if r.layer2_passed) / len(self.records)

    def blogs_planned(self) -> int:
        """Count of blogs that shipped a manifest in *every* run.

        The plan-stage analog of ``EvalReport.blogs_with_valid_spec`` (``implementation-plan.md §5.5``
        "produces a valid LabManifest for ... blogs"): a blog counts when all its runs shipped.
        """
        by_blog: dict[str, list[PlanRunRecord]] = {}
        for r in self.records:
            by_blog.setdefault(r.blog_id, []).append(r)
        return sum(1 for runs in by_blog.values() if runs and all(r.shipped for r in runs))

    def total_route_backs(self) -> int:
        """Total runs where the Planner routed AttackSpec incoherence back to the Extractor.

        The exit-criterion signal (``implementation-plan.md §5.5``): the Planner must route back, not
        repair. A non-zero count on a *coherent* curated spec is the calibration red flag.
        """
        return sum(1 for r in self.records if r.route_back)

    def total_cost_usd(self) -> Decimal:
        """Summed cost across every run (provider-backed runs only; 0 offline)."""
        return sum((r.cost_usd for r in self.records), start=Decimal("0"))


def _yaml() -> YAML:
    y = YAML()
    y.default_flow_style = False
    y.width = 4096
    return y


def plan_report_to_yaml(report: PlanEvalReport) -> str:
    """Serialize a :class:`PlanEvalReport` to YAML text."""
    buf = StringIO()
    _yaml().dump(report.model_dump(mode="json", by_alias=True), buf)
    return buf.getvalue()


def archive_plan_report(report: PlanEvalReport, *, reports_dir: Path) -> Path:
    """Write ``report`` to ``reports_dir`` and return the path.

    Filename: ``gen<rotation-gen>-plan-<UTC-timestamp>.yaml`` (sortable, collision-free, and
    distinct from the Extractor-stage reports in the same directory). Creates ``reports_dir`` if
    absent.
    """
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.generated_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = reports_dir / f"gen{report.rotation_generation}-plan-{stamp}.yaml"
    path.write_text(plan_report_to_yaml(report), encoding="utf-8")
    return path


def load_plan_report(path: Path) -> PlanEvalReport:
    """Load an archived plan report back into a :class:`PlanEvalReport` (round-trip check)."""
    data = _yaml().load(path.read_text(encoding="utf-8"))
    return PlanEvalReport.model_validate(data)


__all__ = [
    "PlanEvalReport",
    "archive_plan_report",
    "load_plan_report",
    "plan_report_to_yaml",
]
