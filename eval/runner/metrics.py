"""Phase-1 eval metrics: the per-run record, the completeness formula, aggregation.

Architectural source: ``eval.md §7.4`` (mechanical metrics available in Phase 1 —
Layer 1 pass rate, cost per AttackSpec, structural completeness, registry
proposals issued, ``extras`` count) and ``eval.md §7.6`` (repeated runs reported
with mean/median/variance). The completeness formula and the ``BlogRunRecord``
shape are pinned in ADR 0025.

Phase-1 scope deliberately omits the metrics that have no producer yet: validator
Layers 2/3/5 (Phase 2+), refinement-loop oscillation/best-state metrics (the full
coordinator is Phase 4), and the Critic's subjective scores (Phase 3). Those
report fields land when their producers do (``eval.md §7.13`` harness evolution).
"""

from __future__ import annotations

import statistics
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import ConfigDict, Field

from cyberlab_gen.agents.extractor_jury.schema import (
    Verdict,  # noqa: TC001 -- Pydantic field type, needed at runtime for model build
)
from cyberlab_gen.schemas.base import ArtifactModel, InternalModel
from cyberlab_gen.schemas.enums import ExtractionOutcome

if TYPE_CHECKING:
    from cyberlab_gen.schemas.attack_spec import AttackSpec

#: The optional top-level AttackSpec content blocks that structural completeness
#: counts (ADR 0025). ``thesis`` and ``chain`` are required when in-scope but are
#: included so an out-of-scope spec (which carries neither) scores 0 cleanly.
_COMPLETENESS_BLOCKS: tuple[str, ...] = (
    "thesis",
    "chain",
    "external_references",
    "real_world_incidents",
    "reproducibility",
)


def structural_completeness(spec: AttackSpec) -> float:
    """Fraction of optional top-level content blocks populated (``eval.md §7.4``).

    A coarse coverage proxy (ADR 0025): the share of the counted content blocks
    that are present (``thesis`` / ``chain`` / ``external_references`` /
    ``real_world_incidents`` / ``reproducibility``) plus the list-valued
    ``defender_techniques`` / ``defenses`` (counted as present when non-empty).
    Out-of-scope specs carry no content and score ``0.0``. This is intentionally
    structural and external to the Extractor's own ``completeness_score`` (which
    is recorded alongside, not in place of, this metric).
    """
    if spec.extraction_outcome is ExtractionOutcome.OUT_OF_SCOPE:
        return 0.0
    present = sum(1 for name in _COMPLETENESS_BLOCKS if getattr(spec, name) is not None)
    total = len(_COMPLETENESS_BLOCKS) + 2  # + defender_techniques + defenses
    present += 1 if spec.defender_techniques else 0
    present += 1 if spec.defenses else 0
    return present / total


class BlogRunRecord(InternalModel):
    """One pipeline run's measured outcome for one blog (ADR 0025, ``eval.md §7.4``).

    ``InternalModel`` because it is a harness-internal measurement; the archived
    :class:`~eval.runner.report.EvalReport` (an artifact) embeds these as nested
    data. ``completeness_score`` is the *Extractor's own* self-assessment;
    ``structural_completeness`` is the harness-computed coverage fraction — the two
    are tracked separately on purpose (ADR 0025).
    """

    model_config = ConfigDict(frozen=True)

    blog_id: str
    run_index: int = Field(ge=0)
    shipped: bool
    layer1_passed: bool
    cost_usd: Decimal = Decimal("0")
    completeness_score: float = Field(ge=0.0, le=1.0)
    structural_completeness: float = Field(ge=0.0, le=1.0)
    value_type_proposals: int = Field(ge=0)
    facet_proposals: int = Field(ge=0)
    extras_count: int = Field(ge=0)
    verdict: Verdict
    low_jury_confidence: bool = False
    halt_reason: str | None = None
    #: For a failed run, whether the failure is ``"retryable"`` (a persistent
    #: ``TransientFailure`` — timeout/429/5xx) or ``"non_retryable"`` (a
    #: ``HardFailure``/4xx/malformed/extraction halt). ``None`` on a clean run.
    #: The harness's fail-fast uses this to abort only on *repeating non-retryable*
    #: failures, never a transient blip (ADR 0030).
    failure_kind: str | None = None


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _cv(values: list[float]) -> float:
    """Coefficient of variation (stddev / mean); 0 for <2 samples or zero mean."""
    if len(values) < 2:
        return 0.0
    m = statistics.fmean(values)
    if m == 0:
        return 0.0
    return statistics.pstdev(values) / abs(m)


#: ``eval.md §7.6`` default coefficient-of-variation threshold above which a blog
#: is flagged *high-variance* (a weaker signal for comparison).
HIGH_VARIANCE_CV = 0.3


class BlogAggregate(ArtifactModel):
    """Per-blog aggregate over the N runs (``eval.md §7.4`` / §7.6).

    Reports the spine metrics with mean/median and a high-variance flag (the CV of
    structural completeness exceeds :data:`HIGH_VARIANCE_CV`). ``layer1_pass_rate``
    is the share of runs whose AttackSpec passed Validator Layer 1
    (``implementation-plan.md §4.5`` headline criterion: >=95% on the curated set).
    """

    model_config = ConfigDict(frozen=True)

    blog_id: str
    runs: int = Field(ge=0)
    shipped_count: int = Field(ge=0)
    layer1_pass_rate: float = Field(ge=0.0, le=1.0)
    mean_cost_usd: Decimal
    mean_completeness_score: float = Field(ge=0.0, le=1.0)
    mean_structural_completeness: float = Field(ge=0.0, le=1.0)
    median_structural_completeness: float = Field(ge=0.0, le=1.0)
    structural_completeness_cv: float = Field(ge=0.0)
    high_variance: bool
    total_value_type_proposals: int = Field(ge=0)
    total_facet_proposals: int = Field(ge=0)
    mean_extras_count: float = Field(ge=0.0)

    @classmethod
    def from_runs(cls, blog_id: str, runs: list[BlogRunRecord]) -> BlogAggregate:
        """Roll a blog's N :class:`BlogRunRecord` up into one aggregate (``eval.md §7.6``)."""
        n = len(runs)
        struct = [r.structural_completeness for r in runs]
        costs = [r.cost_usd for r in runs]
        mean_cost = sum(costs, start=Decimal("0")) / n if n else Decimal("0")
        cv = _cv(struct)
        return cls(
            blog_id=blog_id,
            runs=n,
            shipped_count=sum(1 for r in runs if r.shipped),
            layer1_pass_rate=(sum(1 for r in runs if r.layer1_passed) / n) if n else 0.0,
            mean_cost_usd=mean_cost,
            mean_completeness_score=_mean([r.completeness_score for r in runs]),
            mean_structural_completeness=_mean(struct),
            median_structural_completeness=_median(struct),
            structural_completeness_cv=cv,
            high_variance=cv > HIGH_VARIANCE_CV,
            total_value_type_proposals=sum(r.value_type_proposals for r in runs),
            total_facet_proposals=sum(r.facet_proposals for r in runs),
            mean_extras_count=_mean([float(r.extras_count) for r in runs]),
        )


__all__ = [
    "HIGH_VARIANCE_CV",
    "BlogAggregate",
    "BlogRunRecord",
    "structural_completeness",
]
