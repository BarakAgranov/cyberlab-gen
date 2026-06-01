"""Tests for the three provider-backed-eval robustness fixes (Task follow-up).

Covers, in order:

* **Problem 1** — a blog whose ``url`` is the ``TBD`` sentinel is *skipped*
  (never run, recorded in ``EvalReport.skipped``) on a provider-backed run rather
  than crashing the whole run.
* **Problem 2** — a mid-run crash on a later blog still leaves the earlier blogs'
  results archived on disk (incremental archive after each completed blog).
* **Problem 3** — the stderr progress reporter emits one concise line per event.

These exercise the harness logic deterministically with fakes (no live provider,
``eval.md §7.2``); the real ``just eval`` run is what a maintainer drives.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from eval.runner.cli import run_eval
from eval.runner.manifest import load_manifest
from eval.runner.report import load_report
from eval.runner.runner import EvalPipelineRunner, run_blog_set
from tests.eval.conftest import FakeEvalRunner, make_record

if TYPE_CHECKING:
    from eval.runner.metrics import BlogRunRecord

_TBD_BLOG = "long-multi-stage-cloud-campaign"


# --- Problem 1: skip unresolved-URL blogs instead of crashing --------------


def test_provider_backed_run_skips_unresolved_url_blog() -> None:
    manifest = load_manifest()
    runner = FakeEvalRunner()
    report = run_blog_set(manifest=manifest, runner=runner, n=2, provider_backed=True)

    called_ids = {blog_id for (blog_id, _) in runner.calls}
    # the synthetic TBD-url fixture is never run (skipped before any provider call)...
    assert _TBD_BLOG not in called_ids
    # ...while the two real curated blogs run normally.
    assert "ai-assisted-aws-intrusion" in called_ids
    assert "aws-codebuild-actor-id-regex-bypass" in called_ids

    # it is recorded as skipped, with a human reason, and kept out of blog_ids.
    skipped = {s.blog_id: s.reason for s in report.skipped}
    assert set(skipped) == {_TBD_BLOG}
    assert skipped[_TBD_BLOG]  # non-empty reason
    assert _TBD_BLOG not in report.blog_ids
    assert len(report.blog_ids) == 2


def test_offline_run_does_not_skip_the_tbd_blog() -> None:
    # An offline (fake) run does not fetch URLs, so the TBD blog is NOT skipped;
    # all three curated blogs run (keeps the demonstration fixture honest).
    manifest = load_manifest()
    report = run_blog_set(manifest=manifest, runner=FakeEvalRunner(), n=2, provider_backed=False)
    assert report.skipped == []
    assert _TBD_BLOG in report.blog_ids


# --- Problem 2: a later-blog crash still archives the completed blogs -------


class _CrashOnBlogRunner(EvalPipelineRunner):
    """Succeeds for every blog except ``crash_id``, where ``run_once`` raises."""

    def __init__(self, crash_id: str) -> None:
        self._crash_id = crash_id

    def run_once(self, blog_id: str, *, run_index: int) -> BlogRunRecord:
        if blog_id == self._crash_id:
            raise RuntimeError("boom mid-run")
        return make_record(blog_id, run_index)


def test_partial_report_archived_when_a_later_blog_crashes(tmp_path: Path) -> None:
    manifest = load_manifest()
    curated = [e.id for e in manifest.curated]
    assert len(curated) >= 3
    earlier, crash_id = curated[:2], curated[2]

    with pytest.raises(RuntimeError, match="boom"):
        run_eval(
            runner=_CrashOnBlogRunner(crash_id),
            provider_backed=False,
            n=3,
            reports_dir=tmp_path,
        )

    # the crash propagated, but the earlier blogs' results are already on disk.
    archived = list(tmp_path.glob("*.yaml"))
    assert archived, "a partial report must be archived before the crash propagates"
    report = load_report(archived[0])
    saved = {r.blog_id for r in report.records}
    assert set(earlier) <= saved
    assert crash_id not in saved


# --- Problem 3: live stderr progress ---------------------------------------


def test_stderr_progress_emits_one_line_per_event(capsys: pytest.CaptureFixture[str]) -> None:
    from eval.runner.progress import StderrEvalProgress

    progress = StderrEvalProgress()
    progress.run_started(ran_ids=["a", "b"], skipped_ids=["c"], n=3, provider_backed=True)
    progress.blog_run_started("b", blog_pos=2, blog_total=2, run_index=0, n=3)
    progress.blog_run_finished(make_record("b", 0), n=3, cost_so_far=Decimal("0.5000"))
    progress.blog_skipped("c", reason="synthetic fixture, no live URL")
    progress.report_archived(Path("eval/reports/gen0-x.yaml"))

    captured = capsys.readouterr()
    assert captured.out == ""  # progress never pollutes stdout
    err = captured.err
    assert "starting" in err
    assert "run 1/3" in err  # 1-based run counter
    assert "verdict=" in err and "layer1=" in err
    assert "SKIP c" in err and "synthetic fixture" in err
    assert "archived" in err and "gen0-x.yaml" in err


def test_run_eval_drives_progress_and_archives(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from eval.runner.progress import StderrEvalProgress

    report, path = run_eval(
        runner=FakeEvalRunner(),
        provider_backed=False,
        n=2,
        reports_dir=tmp_path,
        progress=StderrEvalProgress(),
    )
    err = capsys.readouterr().err
    assert "starting" in err
    assert "archived" in err
    assert path.is_file()
    assert report.runs_per_blog == 2
