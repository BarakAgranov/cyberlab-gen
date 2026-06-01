"""Tests for the eval metrics: completeness formula + per-blog aggregation (ADR 0025).

The completeness formula (``eval.md §7.4``) and the mean/median/variance roll-up
(``eval.md §7.6``) are the spine of the report; these fail if either drifts.
"""

from __future__ import annotations

from decimal import Decimal

from cyberlab_gen.agents.extractor_jury.schema import Verdict
from eval.runner.metrics import (
    HIGH_VARIANCE_CV,
    BlogAggregate,
    structural_completeness,
)
from tests.eval.conftest import make_record, make_spec


def test_out_of_scope_spec_scores_zero_completeness() -> None:
    spec = make_spec(in_scope=False)
    assert structural_completeness(spec) == 0.0


def test_in_scope_spec_scores_partial_completeness() -> None:
    # the builder populates thesis + chain only (2 of 7 counted slots).
    spec = make_spec()
    score = structural_completeness(spec)
    assert 0.0 < score < 1.0
    # thesis + chain present, the other 5 absent → 2/7.
    assert abs(score - 2 / 7) < 1e-9


def test_aggregate_layer1_pass_rate_and_means() -> None:
    runs = [
        make_record("b", 0, layer1_passed=True, cost="0.10", extras=1),
        make_record("b", 1, layer1_passed=True, cost="0.20", extras=3),
        make_record("b", 2, layer1_passed=False, cost="0.30", extras=2),
    ]
    agg = BlogAggregate.from_runs("b", runs)
    assert agg.runs == 3
    assert agg.shipped_count == 3
    assert abs(agg.layer1_pass_rate - 2 / 3) < 1e-9
    assert agg.mean_cost_usd == Decimal("0.20")
    assert agg.mean_extras_count == 2.0


def test_aggregate_flags_high_variance() -> None:
    # widely-varying structural completeness → CV above the threshold.
    lo = make_record("b", 0)
    hi = make_record("b", 1)
    # Force divergent structural completeness by swapping in specs of different shape.
    lo = lo.model_copy(update={"structural_completeness": 0.1})
    hi = hi.model_copy(update={"structural_completeness": 0.9})
    agg = BlogAggregate.from_runs("b", [lo, hi])
    assert agg.structural_completeness_cv > HIGH_VARIANCE_CV
    assert agg.high_variance is True


def test_aggregate_low_variance_not_flagged() -> None:
    runs = [make_record("b", i) for i in range(3)]  # identical specs → CV 0
    agg = BlogAggregate.from_runs("b", runs)
    assert agg.structural_completeness_cv == 0.0
    assert agg.high_variance is False


def test_aggregate_counts_proposals_separately() -> None:
    runs = [
        make_record("b", 0, vt_proposals=2, facet_proposals=1),
        make_record("b", 1, vt_proposals=1, facet_proposals=3),
    ]
    agg = BlogAggregate.from_runs("b", runs)
    assert agg.total_value_type_proposals == 3
    assert agg.total_facet_proposals == 4


def test_record_reads_extractor_self_score_and_extras() -> None:
    rec = make_record("b", 0, completeness=0.42, extras=5, verdict=Verdict.REVISE)
    assert rec.completeness_score == 0.42
    assert rec.extras_count == 5
    assert rec.verdict is Verdict.REVISE
    # the harness metric is independent of the agent self-score
    assert rec.structural_completeness == 2 / 7
