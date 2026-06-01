"""Tests for the per-blog eval runner (``run_blog_set``, ADR 0025).

Asserts the N-runs-per-blog invocation pattern (``eval.md §7.6``), that the
report carries per-blog aggregates + flat records, and that ``record_from_run``
maps a finished run's parts onto the metrics correctly.
"""

from __future__ import annotations

import pytest

from eval.runner.manifest import load_manifest
from eval.runner.runner import run_blog_set
from tests.eval.conftest import FakeEvalRunner, make_record


def test_run_blog_set_invokes_each_curated_blog_n_times() -> None:
    manifest = load_manifest()
    runner = FakeEvalRunner()
    report = run_blog_set(manifest=manifest, runner=runner, n=3, provider_backed=False)

    curated_ids = [e.id for e in manifest.curated]
    # every curated blog ran exactly N=3 times, indices 0..2
    for blog_id in curated_ids:
        assert sorted(i for (b, i) in runner.calls if b == blog_id) == [0, 1, 2]
    assert len(report.records) == 3 * len(curated_ids)
    assert len(report.aggregates) == len(curated_ids)
    assert report.runs_per_blog == 3
    assert report.provider_backed is False
    assert report.rotation_generation == manifest.rotation_generation


def test_run_blog_set_rejects_zero_n() -> None:
    manifest = load_manifest()
    with pytest.raises(ValueError, match="must be >= 1"):
        run_blog_set(manifest=manifest, runner=FakeEvalRunner(), n=0, provider_backed=False)


def test_run_blog_set_honors_explicit_blog_ids() -> None:
    manifest = load_manifest()
    target = manifest.curated[0].id
    report = run_blog_set(
        manifest=manifest,
        runner=FakeEvalRunner(),
        n=2,
        provider_backed=False,
        blog_ids=[target],
    )
    assert report.blog_ids == [target]
    assert len(report.records) == 2


def test_scripted_records_flow_through_aggregates() -> None:
    manifest = load_manifest()
    target = manifest.curated[0].id
    scripted = {
        target: [
            make_record(target, 0, layer1_passed=True),
            make_record(target, 1, layer1_passed=False),
        ]
    }
    runner = FakeEvalRunner(scripted)
    report = run_blog_set(
        manifest=manifest, runner=runner, n=2, provider_backed=False, blog_ids=[target]
    )
    agg = report.aggregates[0]
    assert agg.blog_id == target
    assert agg.layer1_pass_rate == 0.5
