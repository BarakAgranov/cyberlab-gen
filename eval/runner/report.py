"""The archived eval report (``eval/reports/``) and its writer.

Architectural source: ``eval.md §7.13`` ("Its results live in eval reports
archived in the repo (``eval/reports/``)"), the Task-8 exit criterion "eval
reports archive cleanly to ``eval/reports/``". Shape pinned in ADR 0025.

The report is an :class:`ArtifactModel` so it round-trips losslessly through YAML
(``schema-details.md §1``); the harness writes one timestamped file per ``just
eval`` run, named ``<rotation-gen>-<timestamp>.yaml`` so reports sort by rotation
generation then time and never collide across runs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import ConfigDict, Field
from ruamel.yaml import YAML
from ruamel.yaml.compat import StringIO

from cyberlab_gen.schemas.base import ArtifactModel
from eval.runner.metrics import BlogAggregate, BlogRunRecord

if TYPE_CHECKING:
    from pathlib import Path

#: Repo-root-relative directory the reports archive to (``eval.md §7.13``).
REPORTS_RELDIR = "eval/reports"


class SkippedBlog(ArtifactModel):
    """A curated blog the run could not execute, with the reason (ADR 0028).

    A provider-backed run cannot fetch a blog whose ``url`` is the ``TBD``
    sentinel (the synthetic long-blog fixture). Rather than crash the whole run,
    the harness records the blog here and continues; the report stays an honest
    account of what ran and what did not.
    """

    model_config = ConfigDict(frozen=True)

    blog_id: str
    reason: str


class EvalReport(ArtifactModel):
    """One ``just eval`` run's archived report (ADR 0025).

    Carries the manifest ``rotation_generation`` (eval runs record which
    generation they used, ``eval.md §7.3``), the run configuration (N, the blog
    ids covered), the per-blog aggregates, and the flat list of run records so the
    raw per-run numbers are recoverable from the archive alone.
    """

    model_config = ConfigDict(frozen=True)

    spec_version: int = 1
    spec_kind: str = "EvalReport"
    generated_at: datetime
    rotation_generation: int = Field(ge=0)
    runs_per_blog: int = Field(ge=1)
    provider_backed: bool
    blog_ids: list[str] = Field(default_factory=list[str])
    aggregates: list[BlogAggregate] = Field(default_factory=list[BlogAggregate])
    records: list[BlogRunRecord] = Field(default_factory=list[BlogRunRecord])
    #: Blogs that could not run (e.g. an unresolved ``TBD`` URL). Defaults empty so
    #: pre-existing archived reports (which omit it) still load (ADR 0028).
    skipped: list[SkippedBlog] = Field(default_factory=list[SkippedBlog])

    def overall_layer1_pass_rate(self) -> float:
        """Layer-1 pass rate across every run in the report (``implementation-plan.md §4.5``)."""
        if not self.records:
            return 0.0
        return sum(1 for r in self.records if r.layer1_passed) / len(self.records)

    def blogs_with_valid_spec(self) -> int:
        """Count of blogs that shipped a valid AttackSpec in *every* run.

        The headline exit criterion (``implementation-plan.md §4.5``) is ">=4 of 5
        curated blogs produce a valid AttackSpec in N=3 runs"; a blog counts when
        all its runs shipped and passed Layer 1.
        """
        by_blog: dict[str, list[BlogRunRecord]] = {}
        for r in self.records:
            by_blog.setdefault(r.blog_id, []).append(r)
        return sum(
            1
            for runs in by_blog.values()
            if runs and all(r.shipped and r.layer1_passed for r in runs)
        )

    def total_cost_usd(self) -> Decimal:
        """Summed cost across every run (provider-backed runs only; 0 offline)."""
        return sum((r.cost_usd for r in self.records), start=Decimal("0"))


def _yaml() -> YAML:
    y = YAML()
    y.default_flow_style = False
    y.width = 4096
    return y


def report_to_yaml(report: EvalReport) -> str:
    """Serialize an :class:`EvalReport` to YAML text."""
    buf = StringIO()
    _yaml().dump(report.model_dump(mode="json", by_alias=True), buf)
    return buf.getvalue()


def archive_report(report: EvalReport, *, reports_dir: Path) -> Path:
    """Write ``report`` to ``reports_dir`` and return the path.

    Filename: ``<rotation-gen>-<UTC-timestamp>.yaml`` (sortable, collision-free).
    Creates ``reports_dir`` if absent. The directory is the repo's
    ``eval/reports/`` by default (``eval.md §7.13``); tests pass a tmp dir.
    """
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.generated_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = reports_dir / f"gen{report.rotation_generation}-{stamp}.yaml"
    path.write_text(report_to_yaml(report), encoding="utf-8")
    return path


def load_report(path: Path) -> EvalReport:
    """Load an archived report back into an :class:`EvalReport` (round-trip check)."""
    data = _yaml().load(path.read_text(encoding="utf-8"))
    return EvalReport.model_validate(data)


__all__ = [
    "REPORTS_RELDIR",
    "EvalReport",
    "SkippedBlog",
    "archive_report",
    "load_report",
    "report_to_yaml",
]
