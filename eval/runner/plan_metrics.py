"""Phase-2 plan-stage eval metrics: manifest coverage, reproducibility, the per-run record.

Architectural source: ``eval.md §7.4`` (mechanical metrics — *manifest* field coverage,
per-step reproducibility distribution, lab-level reproducibility classification, cost,
registry proposals) and ``eval.md §7.6`` (repeated runs reported with mean/median/variance).
The Phase-2 counterpart of :mod:`eval.runner.metrics` (the Extractor-stage spine); shape pinned
in **ADR 0102**.

**The §7.4 F1 discipline holds across every metric here: the harness *measures the pipeline's
emitted output*, it never re-derives a pipeline decision.** Concretely:

* ``manifest_field_coverage`` and ``per_step_reproducibility_distribution`` read the *structure of
  the emitted ``LabManifest``* — they measure the artifact the pipeline produced (exactly as
  :func:`eval.runner.metrics.structural_completeness` measures the emitted AttackSpec). They do not
  re-run any pipeline stage.
* ``lab_level_classification`` reads the **already-derived** ``core.reproducibility.classification_lab_level``
  off the manifest — it does **not** re-run :func:`cyberlab_gen.framework.reproducibility.derive_lab_reproducibility`.
  The brief's "lab-level reproducibility classification (using Task 2's rule)" is satisfied by reading
  the value Task 2's rule *stamped onto the manifest*, not by recomputing it (re-deriving outside the
  pipeline would measure the harness, not the pipeline — ``architecture.md §1.8``; ADR 0102).
* ``layer2_passed`` / ``route_back`` are read off the pipeline's emitted *terminal status*
  (:class:`~cyberlab_gen.framework.plan_orchestrator.PlanPipelineStatus`), never by re-running the
  semantic cross-check.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import ConfigDict, Field

from cyberlab_gen.agents.extractor_jury.schema import Verdict
from cyberlab_gen.framework.plan_orchestrator import PlanPipelineStatus
from cyberlab_gen.schemas.base import ArtifactModel, InternalModel
from cyberlab_gen.schemas.enums import ReproducibilityLabLevel, ReproducibilityTier

# Reuse the Extractor-stage aggregation helpers so mean/median/CV and the high-variance
# threshold are defined once (``eval.md §7.6``).
from eval.runner.metrics import (
    HIGH_VARIANCE_CV,
    coefficient_of_variation,
    mean_of,
    median_of,
)

if TYPE_CHECKING:
    from cyberlab_gen.schemas.manifest import LabManifest

#: The optional top-level / ``core`` content collections that manifest field-coverage counts
#: (``eval.md §7.4`` "manifest field coverage: percentage of optional content fields populated").
#: A coarse top-level coverage proxy (ADR 0102), the manifest counterpart of
#: :data:`eval.runner.metrics._COMPLETENESS_BLOCKS`: each entry is "present" when its list is
#: non-empty. ``phases`` is excluded (required, ``min_length=1`` — never absent) and ``extras`` is
#: excluded (a schema-evolution feedback signal, ``schema.md §4.20``, not authored coverage); finer
#: per-phase content richness is a deliberate future refinement, not counted here.
_MANIFEST_COVERAGE_FIELDS: tuple[str, ...] = (
    "facets",
    "inputs",
    "lab_resources",
    "outputs",
    "core.mitre_tactics",
    "core.cve_references",
    "prereqs",  # present when either pre_lab or mid_lab is non-empty
)


def _field_present(manifest: LabManifest, name: str) -> bool:
    """True when a counted optional manifest collection is populated (non-empty)."""
    if name == "core.mitre_tactics":
        return bool(manifest.core.mitre_tactics)
    if name == "core.cve_references":
        return bool(manifest.core.cve_references)
    if name == "prereqs":
        return bool(manifest.prereqs.pre_lab) or bool(manifest.prereqs.mid_lab)
    return bool(getattr(manifest, name))


def manifest_field_coverage(manifest: LabManifest) -> float:
    """Fraction of the counted optional content collections that are populated (``eval.md §7.4``).

    A coarse top-level coverage proxy (ADR 0102): the share of
    :data:`_MANIFEST_COVERAGE_FIELDS` that are non-empty on the *emitted* manifest. Measures the
    artifact's structure (it never re-runs a pipeline stage), mirroring
    :func:`eval.runner.metrics.structural_completeness` for the AttackSpec.
    """
    present = sum(1 for name in _MANIFEST_COVERAGE_FIELDS if _field_present(manifest, name))
    return present / len(_MANIFEST_COVERAGE_FIELDS)


class StepReproDistribution(ArtifactModel):
    """Per-step reproducibility tier counts over a manifest's steps (``eval.md §7.4``).

    Counts ``phases[*].steps[*].reproducibility.classification`` on the *emitted* manifest — the
    Per-phase-Generator-facing per-step tiers (``StepBlock.reproducibility``), distinct from the
    AttackSpec chain steps the lab-level rollup is derived from (ADR 0081). ``ArtifactModel`` so it
    round-trips inside the archived report.
    """

    model_config = ConfigDict(frozen=True)

    full: int = Field(default=0, ge=0)
    partial_simulation: int = Field(default=0, ge=0)
    demonstration_only: int = Field(default=0, ge=0)
    not_reproducible: int = Field(default=0, ge=0)

    @property
    def total(self) -> int:
        """Total steps counted across all tiers."""
        return self.full + self.partial_simulation + self.demonstration_only + self.not_reproducible

    @classmethod
    def from_manifest(cls, manifest: LabManifest) -> StepReproDistribution:
        """Tally the per-step tiers across every phase of the emitted manifest."""
        counts: dict[ReproducibilityTier, int] = dict.fromkeys(ReproducibilityTier, 0)
        for phase in manifest.phases:
            for step in phase.steps:
                counts[step.reproducibility.classification] += 1
        return cls(
            full=counts[ReproducibilityTier.FULL],
            partial_simulation=counts[ReproducibilityTier.PARTIAL_SIMULATION],
            demonstration_only=counts[ReproducibilityTier.DEMONSTRATION_ONLY],
            not_reproducible=counts[ReproducibilityTier.NOT_REPRODUCIBLE],
        )


def per_step_reproducibility_distribution(manifest: LabManifest) -> StepReproDistribution:
    """Per-step reproducibility tier counts over the emitted manifest (``eval.md §7.4``).

    Thin function form of :meth:`StepReproDistribution.from_manifest` for callers that want the
    metric without naming the class (mirrors the free-function metrics in
    :mod:`eval.runner.metrics`).
    """
    return StepReproDistribution.from_manifest(manifest)


def lab_level_classification(manifest: LabManifest) -> ReproducibilityLabLevel:
    """Read the manifest's **already-derived** lab-level classification (``eval.md §7.4`` F1).

    Returns ``core.reproducibility.classification_lab_level`` — the value Task 2's
    :func:`cyberlab_gen.framework.reproducibility.derive_lab_reproducibility` stamped at plan time.
    The harness measures pipeline truth; it does **not** re-derive (ADR 0102).
    """
    return manifest.core.reproducibility.classification_lab_level


#: Failure-kind tags reused from the Extractor-stage runner (ADR 0030/0034); the plan run loop
#: routes on them identically. Re-exported here so the plan record/runner have one import home.
PLAN_FAILURE_RETRYABLE = "retryable"
PLAN_FAILURE_BLOG_FATAL = "blog_fatal"
PLAN_FAILURE_GLOBAL_FATAL = "global_fatal"


class PlanRunRecord(InternalModel):
    """One plan-pipeline run's measured outcome for one blog's AttackSpec (ADR 0102, ``eval.md §7.4``).

    ``InternalModel`` (a harness-internal measurement) embedded in the archived
    :class:`~eval.runner.plan_report.PlanEvalReport`. Every metric here is read off the pipeline's
    *emitted* output (the manifest + the terminal status), never re-derived (F1).
    """

    model_config = ConfigDict(frozen=True)

    blog_id: str
    run_index: int = Field(ge=0)
    #: The pipeline's emitted terminal status (the single source of the ship/route-back/halt facts).
    status: PlanPipelineStatus
    shipped: bool
    #: Emitted Layer-2 (semantic cross-check) result: a ship cleared the gate; the cross-check-halt
    #: status is the only Layer-2 failure. Read off ``status``, never by re-running the validator (F1).
    layer2_passed: bool
    #: The Planner routed AttackSpec incoherence back to the Extractor (an exit-criterion signal —
    #: the Planner must route back, not repair; ``implementation-plan.md §5.5``).
    route_back: bool
    cost_usd: Decimal = Decimal("0")
    #: Manifest structural metrics — ``0.0`` / empty / ``None`` on a non-ship (no manifest emitted).
    manifest_field_coverage: float = Field(ge=0.0, le=1.0)
    repro_distribution: StepReproDistribution = Field(default_factory=StepReproDistribution)
    lab_level: ReproducibilityLabLevel | None = None
    facet_proposals: int = Field(default=0, ge=0)
    verdict: Verdict | None = None
    low_jury_confidence: bool = False
    halt_reason: str | None = None
    #: For a failed run, its scope (``None`` on a clean run): ``retryable`` / ``blog_fatal`` /
    #: ``global_fatal`` (ADR 0030/0034; same taxonomy as the Extractor-stage runner).
    failure_kind: str | None = None


def record_from_plan_run(
    *,
    blog_id: str,
    run_index: int,
    status: PlanPipelineStatus,
    cost_usd: Decimal,
    manifest: LabManifest | None,
    facet_proposals: int,
    verdict: Verdict | None = None,
    low_jury_confidence: bool = False,
    halt_reason: str | None = None,
    failure_kind: str | None = None,
) -> PlanRunRecord:
    """Build a :class:`PlanRunRecord` from a finished plan run's parts (ADR 0102).

    Computes the manifest structural metrics from the *emitted* manifest (``None`` on a non-ship →
    zero coverage, empty distribution, no lab-level) and derives the ship / Layer-2 / route-back
    facts from the emitted ``status`` (F1). Shared by the provider-backed runner and the test fakes
    so the metric mapping is tested once (mirrors :func:`eval.runner.runner.record_from_run`).
    """
    shipped = status in (
        PlanPipelineStatus.PLANNED,
        PlanPipelineStatus.PLANNED_LOW_CONFIDENCE,
    )
    return PlanRunRecord(
        blog_id=blog_id,
        run_index=run_index,
        status=status,
        shipped=shipped,
        # A ship cleared the cross-check gate; the only Layer-2 failure is the cross-check halt.
        layer2_passed=shipped,
        route_back=status is PlanPipelineStatus.ROUTE_BACK_TO_EXTRACTOR,
        cost_usd=cost_usd,
        manifest_field_coverage=manifest_field_coverage(manifest) if manifest is not None else 0.0,
        repro_distribution=(
            StepReproDistribution.from_manifest(manifest)
            if manifest is not None
            else StepReproDistribution()
        ),
        lab_level=lab_level_classification(manifest) if manifest is not None else None,
        facet_proposals=facet_proposals,
        verdict=verdict,
        low_jury_confidence=low_jury_confidence,
        halt_reason=halt_reason,
        failure_kind=failure_kind,
    )


class PlanBlogAggregate(ArtifactModel):
    """Per-blog aggregate over the N plan runs (``eval.md §7.4`` / §7.6).

    Reports the plan-stage spine with mean/median + a high-variance flag (the CV of manifest field
    coverage exceeds :data:`eval.runner.metrics.HIGH_VARIANCE_CV`). ``layer2_pass_rate`` is the share
    of runs whose manifest cleared the semantic cross-check; ``route_back_count`` surfaces the
    route-back-not-repair exit criterion; ``lab_level_distribution`` counts which lab-level
    classification each shipped run produced.
    """

    model_config = ConfigDict(frozen=True)

    blog_id: str
    runs: int = Field(ge=0)
    shipped_count: int = Field(ge=0)
    layer2_pass_rate: float = Field(ge=0.0, le=1.0)
    route_back_count: int = Field(ge=0)
    low_confidence_count: int = Field(ge=0)
    mean_manifest_field_coverage: float = Field(ge=0.0, le=1.0)
    median_manifest_field_coverage: float = Field(ge=0.0, le=1.0)
    manifest_field_coverage_cv: float = Field(ge=0.0)
    high_variance: bool
    lab_level_distribution: dict[ReproducibilityLabLevel, int] = Field(
        default_factory=dict[ReproducibilityLabLevel, int]
    )
    total_facet_proposals: int = Field(ge=0)
    mean_cost_usd: Decimal

    @classmethod
    def from_runs(cls, blog_id: str, runs: list[PlanRunRecord]) -> PlanBlogAggregate:
        """Roll a blog's N :class:`PlanRunRecord` up into one aggregate (``eval.md §7.6``)."""
        n = len(runs)
        coverage = [r.manifest_field_coverage for r in runs]
        costs = [r.cost_usd for r in runs]
        mean_cost = sum(costs, start=Decimal("0")) / n if n else Decimal("0")
        cv = coefficient_of_variation(coverage)
        lab_dist: dict[ReproducibilityLabLevel, int] = {}
        for r in runs:
            if r.lab_level is not None:
                lab_dist[r.lab_level] = lab_dist.get(r.lab_level, 0) + 1
        return cls(
            blog_id=blog_id,
            runs=n,
            shipped_count=sum(1 for r in runs if r.shipped),
            layer2_pass_rate=(sum(1 for r in runs if r.layer2_passed) / n) if n else 0.0,
            route_back_count=sum(1 for r in runs if r.route_back),
            low_confidence_count=sum(1 for r in runs if r.low_jury_confidence),
            mean_manifest_field_coverage=mean_of(coverage),
            median_manifest_field_coverage=median_of(coverage),
            manifest_field_coverage_cv=cv,
            high_variance=cv > HIGH_VARIANCE_CV,
            lab_level_distribution=lab_dist,
            total_facet_proposals=sum(r.facet_proposals for r in runs),
            mean_cost_usd=mean_cost,
        )


__all__ = [
    "PLAN_FAILURE_BLOG_FATAL",
    "PLAN_FAILURE_GLOBAL_FATAL",
    "PLAN_FAILURE_RETRYABLE",
    "PlanBlogAggregate",
    "PlanRunRecord",
    "StepReproDistribution",
    "lab_level_classification",
    "manifest_field_coverage",
    "per_step_reproducibility_distribution",
    "record_from_plan_run",
]
