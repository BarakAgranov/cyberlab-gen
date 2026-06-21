"""Live stderr progress for a provider-backed ``just eval`` run (ADR 0028).

A multi-minute, real-money run must not be silent. :class:`StderrEvalProgress`
emits one concise line per event to **stderr** so the machine-readable summary
and the archived report keep ``stdout`` clean. Output is flushed per line so it
appears in real time rather than buffered to the end.

The :class:`~eval.runner.runner.EvalProgress` protocol (defined alongside the
runner to avoid an import cycle) is the surface the harness drives; this is the
production implementation. Tests inject a recording fake or capture stderr.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from decimal import Decimal
    from pathlib import Path

    from eval.runner.metrics import BlogRunRecord
    from eval.runner.plan_metrics import PlanRunRecord


class StderrEvalProgress:
    """Write one progress line per event to stderr, flushed immediately (ADR 0028)."""

    def _emit(self, line: str) -> None:
        print(line, file=sys.stderr, flush=True)  # noqa: T201 -- progress goes to stderr

    def run_started(
        self,
        *,
        ran_ids: list[str],
        skipped_ids: list[str],
        n: int,
        provider_backed: bool,
        cost_cap_usd: Decimal | None = None,
    ) -> None:
        mode = "provider-backed" if provider_backed else "offline"
        line = (
            f"eval: starting {mode} run — {n} run(s) over {len(ran_ids)} blog(s): "
            f"{', '.join(ran_ids) or '(none)'}"
        )
        if skipped_ids:
            line += f"; {len(skipped_ids)} skipped: {', '.join(skipped_ids)}"
        line += f"; cost cap {'$' + str(cost_cap_usd) if cost_cap_usd is not None else 'none'}"
        self._emit(line)

    def blog_run_started(
        self, blog_id: str, *, blog_pos: int, blog_total: int, run_index: int, n: int
    ) -> None:
        self._emit(f"[{blog_pos}/{blog_total}] extracting {blog_id}, run {run_index + 1}/{n} ...")

    def blog_run_finished(
        self,
        record: BlogRunRecord,
        *,
        n: int,
        cost_so_far: Decimal,
        cost_cap_usd: Decimal | None = None,
    ) -> None:
        static_schema = "pass" if record.static_schema_passed else "FAIL"
        if cost_cap_usd is not None:
            spend = (
                f"${cost_so_far:.4f}/${cost_cap_usd} (headroom ${cost_cap_usd - cost_so_far:.4f})"
            )
        else:
            spend = f"${cost_so_far:.4f}"
        self._emit(
            f"      {record.blog_id} run {record.run_index + 1}/{n} done: "
            f"verdict={record.verdict}, static_schema={static_schema}, "
            f"shipped={record.shipped}, cost so far {spend}"
        )

    def blog_skipped(self, blog_id: str, *, reason: str) -> None:
        self._emit(f"eval: SKIP {blog_id} — {reason}")

    def run_aborted(self, reason: str) -> None:
        self._emit(f"eval: aborting early — {reason}")

    def report_archived(self, path: Path) -> None:
        self._emit(f"eval: report archived → {path}")


class StderrPlanEvalProgress:
    """Stderr progress for a provider-backed ``just eval --stage plan`` run (ADR 0102).

    The plan-stage counterpart of :class:`StderrEvalProgress`; implements
    :class:`~eval.runner.plan_runner.PlanEvalProgress`. Emits one flushed line per event so a
    multi-minute, real-money plan-calibration run is not silent, keeping ``stdout`` clean for the
    machine-readable summary.
    """

    def _emit(self, line: str) -> None:
        print(line, file=sys.stderr, flush=True)  # noqa: T201 -- progress goes to stderr

    def run_started(
        self,
        *,
        ran_ids: list[str],
        skipped_ids: list[str],
        n: int,
        provider_backed: bool,
        cost_cap_usd: Decimal | None = None,
    ) -> None:
        mode = "provider-backed" if provider_backed else "offline"
        line = (
            f"eval(plan): starting {mode} run — {n} run(s) over {len(ran_ids)} blog(s): "
            f"{', '.join(ran_ids) or '(none)'}"
        )
        if skipped_ids:
            line += f"; {len(skipped_ids)} skipped: {', '.join(skipped_ids)}"
        line += f"; cost cap {'$' + str(cost_cap_usd) if cost_cap_usd is not None else 'none'}"
        self._emit(line)

    def blog_run_started(
        self, blog_id: str, *, blog_pos: int, blog_total: int, run_index: int, n: int
    ) -> None:
        self._emit(f"[{blog_pos}/{blog_total}] planning {blog_id}, run {run_index + 1}/{n} ...")

    def blog_run_finished(
        self,
        record: PlanRunRecord,
        *,
        n: int,
        cost_so_far: Decimal,
        cost_cap_usd: Decimal | None = None,
    ) -> None:
        layer2 = "pass" if record.layer2_passed else "FAIL"
        if cost_cap_usd is not None:
            spend = (
                f"${cost_so_far:.4f}/${cost_cap_usd} (headroom ${cost_cap_usd - cost_so_far:.4f})"
            )
        else:
            spend = f"${cost_so_far:.4f}"
        # status is None only when the pipeline raised (no terminal status). Show the honest failure
        # scope — blog_fatal / global_fatal / retryable, the same label the report carries — never a
        # fabricated "infra_failure" that mislabels a blog-fatal tool loop as infrastructure (ADR 0106).
        status_label = (
            record.status.value if record.status is not None else (record.failure_kind or "failed")
        )
        self._emit(
            f"      {record.blog_id} run {record.run_index + 1}/{n} done: "
            f"status={status_label}, "
            f"layer2={layer2}, shipped={record.shipped}, "
            f"coverage={record.manifest_field_coverage:.0%}, cost so far {spend}"
        )

    def blog_skipped(self, blog_id: str, *, reason: str) -> None:
        self._emit(f"eval(plan): SKIP {blog_id} — {reason}")

    def run_aborted(self, reason: str) -> None:
        self._emit(f"eval(plan): aborting early — {reason}")

    def report_archived(self, path: Path) -> None:
        self._emit(f"eval(plan): report archived → {path}")


__all__ = ["StderrEvalProgress", "StderrPlanEvalProgress"]
