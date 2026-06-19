"""Tests for the plan-eval runner: ``run_plan_set``, the provider runner, the overlay guard (ADR 0102).

Covers the N-runs-per-blog loop, the attack_spec skip, global-fatal / cost-cap abort, the
``record_from_plan_run`` mapping through ``ProviderBackedPlanEvalRunner``, and — the load-bearing
safety property — that the plan eval drives ``PlanRunner.run`` directly and is **read-only w.r.t.
every registry overlay** (it never promotes a facet proposal). The CLI ``--stage plan`` offline path
+ archive round-trip are here too.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from cyberlab_gen.state import RunStore
from eval.runner.manifest import BlogEntry, BlogSetManifest
from eval.runner.plan_metrics import PlanRunRecord
from eval.runner.plan_report import PlanEvalReport, load_plan_report
from eval.runner.plan_runner import ProviderBackedPlanEvalRunner, run_plan_set
from eval.runner.runner import FAILURE_GLOBAL_FATAL
from tests.eval.conftest import (
    FakePlanEvalRunner,
    FakePlanRunner,
    make_plan_record,
    make_plan_result,
    make_proposed_facet,
    make_spec,
)

if TYPE_CHECKING:
    import pytest as _pytest


def _entry(blog_id: str, *, attack_spec: str | None = None) -> BlogEntry:
    return BlogEntry(
        id=blog_id,
        shape="x",
        url="https://example.com/x",
        title="t",
        publisher="p",
        accessed_date="2026-01-01",
        walk="dev/x.md",
        attack_spec=attack_spec,
    )


def _manifest(*entries: BlogEntry) -> BlogSetManifest:
    return BlogSetManifest(spec_version=1, rotation_generation=0, curated=list(entries))


# --- run_plan_set loop ------------------------------------------------------


def test_run_plan_set_invokes_each_blog_n_times() -> None:
    manifest = _manifest(_entry("a"), _entry("b"))
    runner = FakePlanEvalRunner()
    report = run_plan_set(manifest=manifest, runner=runner, n=3, provider_backed=False)
    for blog_id in ("a", "b"):
        assert sorted(i for (b, i) in runner.calls if b == blog_id) == [0, 1, 2]
    assert len(report.records) == 6
    assert len(report.aggregates) == 2
    assert report.runs_per_blog == 3
    assert report.spec_kind == "PlanEvalReport"


def test_provider_backed_skips_blog_without_attack_spec() -> None:
    # A provider-backed plan run can only run a blog with a committed attack_spec fixture.
    manifest = _manifest(_entry("a", attack_spec="eval/x.yaml"), _entry("b"))
    runner = FakePlanEvalRunner()
    report = run_plan_set(manifest=manifest, runner=runner, n=2, provider_backed=True)
    assert report.blog_ids == ["a"]
    assert [s.blog_id for s in report.skipped] == ["b"]
    assert {b for (b, _) in runner.calls} == {"a"}  # b never ran


def test_offline_run_skips_nothing() -> None:
    manifest = _manifest(_entry("a"), _entry("b"))  # neither has an attack_spec
    report = run_plan_set(
        manifest=manifest, runner=FakePlanEvalRunner(), n=1, provider_backed=False
    )
    assert set(report.blog_ids) == {"a", "b"}
    assert report.skipped == []


def test_global_fatal_aborts_the_whole_run() -> None:
    fatal = PlanRunRecord(
        blog_id="a",
        run_index=0,
        status=None,
        shipped=False,
        layer2_passed=False,
        route_back=False,
        cost_usd=Decimal("0"),
        manifest_field_coverage=0.0,
        halt_reason="auth failure (no served model)",
        failure_kind=FAILURE_GLOBAL_FATAL,
    )
    manifest = _manifest(_entry("a"), _entry("b"))
    runner = FakePlanEvalRunner(records_for={"a": [fatal]})
    report = run_plan_set(manifest=manifest, runner=runner, n=3, provider_backed=False)
    # a aborted after its first run; b never ran (marked skipped).
    assert "b" in {s.blog_id for s in report.skipped}
    assert {b for (b, _) in runner.calls} == {"a"}
    assert len([r for r in report.records if r.blog_id == "a"]) == 1


def test_cost_cap_aborts_the_run() -> None:
    manifest = _manifest(_entry("a"), _entry("b"))
    runner = FakePlanEvalRunner()  # default record cost 0.02
    report = run_plan_set(
        manifest=manifest,
        runner=runner,
        n=3,
        provider_backed=False,
        cost_cap_usd=Decimal("0.03"),
    )
    # run0 (0.02) under cap; run1 (0.04) hits cap → abort; b skipped for cost.
    assert "b" in {s.blog_id for s in report.skipped}


def test_run_plan_set_rejects_n_below_one() -> None:
    with pytest.raises(ValueError, match=r"n .* must be >= 1"):
        run_plan_set(
            manifest=_manifest(_entry("a")),
            runner=FakePlanEvalRunner(),
            n=0,
            provider_backed=False,
        )


# --- ProviderBackedPlanEvalRunner: maps emitted output to a record ----------


def test_provider_runner_maps_shipped_result() -> None:
    result = make_plan_result(
        facet_proposals=[make_proposed_facet(), make_proposed_facet("runtime:vercel")]
    )
    fake = FakePlanRunner(result=result)
    runner = ProviderBackedPlanEvalRunner(
        plan_runner_factory=lambda _ledger: fake,
        attack_spec_for=lambda _blog_id: make_spec(),
    )
    rec = runner.plan_once("a", run_index=0)
    assert fake.run_calls == 1  # the runner.run seam was driven directly
    assert rec.shipped is True
    assert rec.layer2_passed is True
    assert rec.facet_proposals == 2  # counted off the result, NOT promoted
    assert rec.lab_level is not None


def test_provider_runner_records_returned_halt_status_and_reason(tmp_path: Path) -> None:
    # A RETURNED non-ship terminal (e.g. HALTED_REJECT) must land in the run dir with its TRUE status
    # + halt_reason — the same fidelity the `plan` verb records — not a reasonless FAILED (ADR 0102).
    import json

    from cyberlab_gen.framework.plan_orchestrator import PlanPipelineStatus

    result = make_plan_result(
        status=PlanPipelineStatus.HALTED_REJECT,
        manifest=None,
        verdict=None,
        halt_reason="jury rejected the manifest",
    )
    runner = ProviderBackedPlanEvalRunner(
        plan_runner_factory=lambda _ledger: FakePlanRunner(result=result),
        attack_spec_for=lambda _blog_id: make_spec(),
        run_store=RunStore(tmp_path / "runs"),
    )
    rec = runner.plan_once("a", run_index=0)
    assert rec.shipped is False
    run_jsons = list((tmp_path / "runs").rglob("run.json"))
    assert len(run_jsons) == 1
    record = json.loads(run_jsons[0].read_text(encoding="utf-8"))
    assert record["status"] == "halted_reject"  # distinct status, not a flattened "failed"
    assert record["halt_reason"] == "jury rejected the manifest"


def test_provider_runner_classifies_infra_failure() -> None:
    from cyberlab_gen.errors import HardFailure

    fake = FakePlanRunner(raises=HardFailure("no served model / auth"))
    runner = ProviderBackedPlanEvalRunner(
        plan_runner_factory=lambda _ledger: fake,
        attack_spec_for=lambda _blog_id: make_spec(),
    )
    rec = runner.plan_once("a", run_index=0)
    assert rec.shipped is False
    assert rec.status is None  # an infra failure never produced a terminal status
    assert rec.failure_kind == FAILURE_GLOBAL_FATAL  # auth/no-model is systemic


# --- the eval-overlay-read-only guard (ADR 0100/0102) -----------------------


def test_plan_eval_never_writes_a_registry_overlay(tmp_path: Path) -> None:
    # The load-bearing safety property: a plan-eval run that sees facet proposals must NOT promote
    # them — no registry overlay may appear anywhere, even with a run store writing run dirs.
    result = make_plan_result(
        facet_proposals=[make_proposed_facet(), make_proposed_facet("runtime:vercel")]
    )
    runner = ProviderBackedPlanEvalRunner(
        plan_runner_factory=lambda _ledger: FakePlanRunner(result=result),
        attack_spec_for=lambda _blog_id: make_spec(),
        manifests_dir=tmp_path / "manifests",
        run_store=RunStore(tmp_path / "runs"),
    )
    rec = runner.plan_once("a", run_index=0)
    assert rec.facet_proposals == 2  # proposals were counted ...
    # ... but nothing was promoted: no overlay directory or file anywhere under the run tree.
    assert list(tmp_path.rglob("registry-overlay")) == []
    assert [p for p in tmp_path.rglob("*") if "overlay" in p.name.lower()] == []
    # sanity: a run dir WAS written (so the absence above is meaningful, not a no-op).
    assert any((tmp_path / "runs").rglob("run.json"))


def test_plan_runner_module_has_no_promotion_machinery() -> None:
    # Structural backstop to the behavioral guard above: the module must not reference the verb's
    # promotion path at all. We check the concrete promotion *code tokens* (not the word "overlay",
    # which legitimately appears throughout this module's safety documentation).
    import eval.runner.plan_runner as m

    src = Path(m.__file__).read_text(encoding="utf-8")
    assert "_promote_facets" not in src  # the verb's promotion function
    assert "accept_proposals" not in src  # the accept machinery
    assert "OVERLAY_DIRNAME" not in src  # the verb's overlay-subdir constant
    assert "registry-overlay" not in src  # the literal overlay dir name
    assert "run_plan(" not in src  # never calls the verb engine (run_plan_set != run_plan)


# --- PlanEvalReport aggregate methods + archive round-trip ------------------


def test_report_methods_count_layer2_route_backs_and_planned() -> None:
    from cyberlab_gen.framework.plan_orchestrator import PlanPipelineStatus

    records = [
        make_plan_record("a", 0),
        make_plan_record("a", 1),
        make_plan_record("b", 0, status=PlanPipelineStatus.ROUTE_BACK_TO_EXTRACTOR, manifest=None),
        make_plan_record(
            "c", 0, status=PlanPipelineStatus.HALTED_SEMANTIC_CROSS_CHECK_UNRESOLVED, manifest=None
        ),
    ]
    report = PlanEvalReport(
        generated_at=make_spec().source.fetched_at,
        rotation_generation=0,
        runs_per_blog=2,
        provider_backed=False,
        blog_ids=["a", "b", "c"],
        records=records,
    )
    assert abs(report.overall_layer2_pass_rate() - 2 / 4) < 1e-9
    assert report.blogs_planned() == 1  # only "a" shipped in every run
    assert report.total_route_backs() == 1


def test_run_plan_eval_archives_and_round_trips(tmp_path: Path) -> None:
    from eval.runner.cli import run_plan_eval

    report, path = run_plan_eval(
        runner=FakePlanEvalRunner(),
        provider_backed=False,
        n=3,
        reports_dir=tmp_path,
    )
    assert path.is_file()
    assert "plan" in path.name  # the -plan- infix keeps it distinct from extract reports
    assert load_plan_report(path) == report
    assert report.provider_backed is False


# --- CLI --stage plan offline notice ----------------------------------------


def test_main_stage_plan_offline_reports_no_provider(
    monkeypatch: _pytest.MonkeyPatch, capsys: _pytest.CaptureFixture[str]
) -> None:
    from eval.runner.cli import main

    monkeypatch.setattr("eval.runner.cli._provider_configured", lambda: False)
    rc = main(["--stage", "plan"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "eval(plan): no live provider configured" in out
    assert "nothing run" in out
