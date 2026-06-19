"""Tests for the archived eval report (``eval/reports/``, ADR 0025, ``eval.md §7.13``).

Covers the round-trip (write → load equals), the archive filename + directory
creation, and the exit-criterion helpers (overall static-schema pass rate; the
">=4 of 5 valid-spec blogs" count from ``implementation-plan.md §4.5``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from eval.runner.manifest import load_manifest
from eval.runner.report import archive_report, load_report
from eval.runner.runner import run_blog_set
from tests.eval.conftest import FakeEvalRunner, make_record

if TYPE_CHECKING:
    from pathlib import Path

    from eval.runner.report import EvalReport


def _report() -> EvalReport:
    manifest = load_manifest()
    return run_blog_set(manifest=manifest, runner=FakeEvalRunner(), n=3, provider_backed=False)


def test_archive_then_load_round_trips(tmp_path: Path) -> None:
    report = _report()
    path = archive_report(report, reports_dir=tmp_path)
    assert path.is_file()
    assert path.parent == tmp_path
    loaded = load_report(path)
    assert loaded == report


def test_archive_creates_missing_dir(tmp_path: Path) -> None:
    report = _report()
    nested = tmp_path / "reports" / "deep"
    path = archive_report(report, reports_dir=nested)
    assert path.is_file()
    assert nested.is_dir()


def test_archive_filename_carries_rotation_generation(tmp_path: Path) -> None:
    report = _report()
    path = archive_report(report, reports_dir=tmp_path)
    assert path.name.startswith(f"gen{report.rotation_generation}-")
    assert path.suffix == ".yaml"


def test_overall_static_schema_pass_rate_and_valid_spec_count() -> None:
    manifest = load_manifest()
    # Make the first blog fail static schema validation on one of its 3 runs (size-agnostic over the
    # curated set, which grows across phases).
    first = manifest.curated[0].id
    scripted = {
        first: [
            make_record(first, 0, static_schema_passed=True),
            make_record(first, 1, static_schema_passed=True),
            make_record(first, 2, static_schema_passed=False),
        ]
    }
    report = run_blog_set(
        manifest=manifest, runner=FakeEvalRunner(scripted), n=3, provider_backed=False
    )
    # N=3 over every curated blog → one failed run out of (3 * curated) total.
    total_runs = 3 * len(manifest.curated)
    assert abs(report.overall_static_schema_pass_rate() - (total_runs - 1) / total_runs) < 1e-9
    # the first blog had a failing run → not a clean valid-spec blog; all the others are.
    assert report.blogs_with_valid_spec() == len(manifest.curated) - 1


def test_total_cost_offline_is_runner_supplied() -> None:
    report = _report()
    # the fake runner stamps each record with $0.01; 3 blogs x 3 runs = $0.09.
    assert report.total_cost_usd() > 0
