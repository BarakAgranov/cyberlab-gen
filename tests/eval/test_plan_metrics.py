"""Tests for the Phase-2 plan-stage metrics (ADR 0102).

The manifest field-coverage formula, the per-step reproducibility distribution, the
read-emitted lab-level classification (``eval.md §7.4`` F1 — measured, never re-derived), and the
per-blog aggregation (``eval.md §7.6``) are the spine of the plan report; these fail if any drifts.
"""

from __future__ import annotations

from decimal import Decimal

from cyberlab_gen.agents.extractor_jury.schema import Verdict
from cyberlab_gen.framework.plan_orchestrator import PlanPipelineStatus
from cyberlab_gen.schemas.enums import ReproducibilityLabLevel, ReproducibilityTier
from eval.runner.plan_metrics import (
    PlanBlogAggregate,
    StepReproDistribution,
    lab_level_classification,
    manifest_field_coverage,
    per_step_reproducibility_distribution,
    record_from_plan_run,
)
from tests.eval.conftest import make_manifest, make_plan_record

# --- manifest field coverage -----------------------------------------------


def test_field_coverage_all_populated_is_one() -> None:
    assert manifest_field_coverage(make_manifest()) == 1.0


def test_field_coverage_none_populated_is_zero() -> None:
    bare = make_manifest(
        facets=False,
        prereqs=False,
        inputs=False,
        lab_resources=False,
        outputs=False,
        mitre_tactics=False,
        cve_references=False,
    )
    assert manifest_field_coverage(bare) == 0.0


def test_field_coverage_partial_is_a_fraction() -> None:
    # Turn off 3 of the 7 counted collections → 4/7 populated.
    partial = make_manifest(facets=False, inputs=False, cve_references=False)
    assert abs(manifest_field_coverage(partial) - 4 / 7) < 1e-9


def test_field_coverage_counts_prereqs_when_only_mid_lab_present() -> None:
    # prereqs counts as present when EITHER pre_lab or mid_lab is non-empty; the builder's
    # prereqs=True populates pre_lab, so coverage with everything else off should be 1/7.
    only_prereqs = make_manifest(
        facets=False,
        inputs=False,
        lab_resources=False,
        outputs=False,
        mitre_tactics=False,
        cve_references=False,
    )
    assert abs(manifest_field_coverage(only_prereqs) - 1 / 7) < 1e-9


# --- per-step reproducibility distribution ---------------------------------


def test_repro_distribution_tallies_each_tier() -> None:
    manifest = make_manifest(
        step_tiers=[
            ReproducibilityTier.FULL,
            ReproducibilityTier.FULL,
            ReproducibilityTier.DEMONSTRATION_ONLY,
            ReproducibilityTier.NOT_REPRODUCIBLE,
        ]
    )
    dist = per_step_reproducibility_distribution(manifest)
    assert dist == StepReproDistribution(
        full=2, partial_simulation=0, demonstration_only=1, not_reproducible=1
    )
    assert dist.total == 4


def test_repro_distribution_empty_total_is_zero() -> None:
    assert StepReproDistribution().total == 0


# --- lab-level classification (read emitted, F1) ----------------------------


def test_lab_level_reads_the_emitted_value_not_recomputed() -> None:
    # The metric must READ core.reproducibility.classification_lab_level, not re-derive it from the
    # per-step tiers. Build a manifest whose stamped lab-level is MIXED while the (single) step is
    # FULL — a re-derivation would wrongly say FULL; reading the emitted value says MIXED.
    manifest = make_manifest(
        step_tiers=[ReproducibilityTier.FULL], lab_level=ReproducibilityLabLevel.MIXED
    )
    assert lab_level_classification(manifest) is ReproducibilityLabLevel.MIXED


# --- record_from_plan_run mapping ------------------------------------------


def test_record_from_shipped_run_reads_manifest_metrics() -> None:
    manifest = make_manifest(
        step_tiers=[ReproducibilityTier.FULL, ReproducibilityTier.DEMONSTRATION_ONLY],
        lab_level=ReproducibilityLabLevel.MIXED,
    )
    rec = record_from_plan_run(
        blog_id="b",
        run_index=0,
        status=PlanPipelineStatus.PLANNED_LOW_CONFIDENCE,
        cost_usd=Decimal("0.05"),
        manifest=manifest,
        facet_proposals=2,
        verdict=Verdict.REVISE,
        low_jury_confidence=True,
        halt_reason="shipped with unresolved jury feedback",
    )
    assert rec.shipped is True
    assert rec.layer2_passed is True
    assert rec.route_back is False
    assert rec.lab_level is ReproducibilityLabLevel.MIXED
    assert rec.repro_distribution.full == 1
    assert rec.repro_distribution.demonstration_only == 1
    assert rec.manifest_field_coverage == 1.0
    assert rec.facet_proposals == 2
    # the passthrough fields are threaded onto the record (not dropped).
    assert rec.verdict is Verdict.REVISE
    assert rec.low_jury_confidence is True
    assert rec.halt_reason == "shipped with unresolved jury feedback"


def test_record_from_route_back_has_no_manifest_metrics() -> None:
    rec = record_from_plan_run(
        blog_id="b",
        run_index=0,
        status=PlanPipelineStatus.ROUTE_BACK_TO_EXTRACTOR,
        cost_usd=Decimal("0.01"),
        manifest=None,
        facet_proposals=0,
        halt_reason="AttackSpec incoherent",
    )
    assert rec.shipped is False
    assert rec.layer2_passed is False  # never reached the cross-check gate
    assert rec.route_back is True
    assert rec.lab_level is None
    assert rec.manifest_field_coverage == 0.0
    assert rec.repro_distribution.total == 0


def test_record_cross_check_halt_is_layer2_fail() -> None:
    rec = record_from_plan_run(
        blog_id="b",
        run_index=0,
        status=PlanPipelineStatus.HALTED_SEMANTIC_CROSS_CHECK_UNRESOLVED,
        cost_usd=Decimal("0.01"),
        manifest=None,
        facet_proposals=0,
    )
    assert rec.shipped is False
    assert rec.layer2_passed is False


# --- aggregation (eval.md §7.6) --------------------------------------------


def test_aggregate_rates_and_means() -> None:
    runs = [
        make_plan_record("b", 0, cost="0.10"),
        make_plan_record("b", 1, cost="0.20"),
        make_plan_record(
            "b", 2, status=PlanPipelineStatus.HALTED_SEMANTIC_CROSS_CHECK_UNRESOLVED, cost="0.30"
        ),
    ]
    agg = PlanBlogAggregate.from_runs("b", runs)
    assert agg.runs == 3
    assert agg.shipped_count == 2
    assert abs(agg.layer2_pass_rate - 2 / 3) < 1e-9
    assert agg.mean_cost_usd == Decimal("0.20")
    # two shipped runs classified the same lab-level (FULL by default).
    assert agg.lab_level_distribution == {ReproducibilityLabLevel.FULL: 2}


def test_aggregate_counts_route_backs_and_low_confidence() -> None:
    runs = [
        make_plan_record("b", 0, status=PlanPipelineStatus.ROUTE_BACK_TO_EXTRACTOR, manifest=None),
        make_plan_record(
            "b", 1, status=PlanPipelineStatus.PLANNED_LOW_CONFIDENCE, low_jury_confidence=True
        ),
    ]
    agg = PlanBlogAggregate.from_runs("b", runs)
    assert agg.route_back_count == 1
    assert agg.low_confidence_count == 1
    assert agg.shipped_count == 1  # low-confidence still ships


def test_aggregate_flags_high_variance_on_field_coverage() -> None:
    lo = make_plan_record(
        "b",
        0,
        facets=False,
        inputs=False,
        lab_resources=False,
        outputs=False,
        mitre_tactics=False,
        cve_references=False,  # coverage 1/7
    )
    hi = make_plan_record("b", 1)  # coverage 7/7
    agg = PlanBlogAggregate.from_runs("b", [lo, hi])
    assert agg.high_variance is True


def test_aggregate_low_variance_not_flagged() -> None:
    runs = [make_plan_record("b", i) for i in range(3)]  # identical coverage → CV 0
    agg = PlanBlogAggregate.from_runs("b", runs)
    assert agg.manifest_field_coverage_cv == 0.0
    assert agg.high_variance is False


def test_aggregate_sums_facet_proposals() -> None:
    runs = [
        make_plan_record("b", 0, facet_proposals=2),
        make_plan_record("b", 1, facet_proposals=3),
    ]
    agg = PlanBlogAggregate.from_runs("b", runs)
    assert agg.total_facet_proposals == 5
