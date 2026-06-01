"""Smoke test that the Phase-1 eval harness starts and archives a report (ADR 0025).

The Task-8 brief requires "a small smoke test under ``tests/eval/`` verifies the
harness starts" (``just eval`` is the real invocation; pytest just confirms the
harness wiring is intact). This drives :func:`run_eval` end-to-end with a fake
runner + a tmp reports dir (no live provider, ``eval.md §7.2``), and exercises
``main()``'s offline path (no provider configured → clean notice, exit 0).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from eval.runner.cli import main, run_eval
from eval.runner.report import load_report
from tests.eval.conftest import FakeEvalRunner

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_run_eval_archives_and_round_trips(tmp_path: Path) -> None:
    report, path = run_eval(
        runner=FakeEvalRunner(),
        provider_backed=False,
        n=3,
        reports_dir=tmp_path,
    )
    assert path.is_file()
    assert path.parent == tmp_path
    # the archived file reloads to an equal report (the archive is honest).
    assert load_report(path) == report
    # N=3 over the >=3 curated blogs (implementation-plan.md §4.5).
    assert report.runs_per_blog == 3
    assert len(report.blog_ids) >= 3
    # offline: provider_backed recorded as False so the archive is unambiguous.
    assert report.provider_backed is False


def test_main_offline_reports_no_provider_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Force the "no provider configured" branch regardless of the test env.
    def _no_provider() -> bool:
        return False

    monkeypatch.setattr("eval.runner.cli._provider_configured", _no_provider)
    rc = main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no live provider configured" in out
    assert "manifest OK" in out
    assert "nothing run" in out


def test_main_offline_fails_when_a_walk_is_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # If a manifest entry pointed at a missing walk, `just eval` must fail (exit 1).
    from eval.runner import cli as cli_mod

    def _broken(_m: object) -> list[str]:
        return ["broken-blog"]

    monkeypatch.setattr(cli_mod, "check_walks_resolve", _broken)
    rc = main([])
    assert rc == 1
    err = capsys.readouterr().err
    assert "broken-blog" in err
