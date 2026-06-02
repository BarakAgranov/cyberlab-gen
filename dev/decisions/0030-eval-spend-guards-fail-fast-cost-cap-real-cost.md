# 0030 — Eval spend guards: fail-fast, cost cap, and real per-run cost

**Date:** 2026-06-02
**Phase:** 1 (provider-backed eval hardening; "stop wasting money on doomed runs")
**Architecture refs:** `provider-interface.md §5` (cost tracking; the **framework** owns budget-overrun decisions, not the provider — §5.3), `eval.md §7.2`, ADR 0025 (eval shape — **amended**), ADR 0028 (incremental archive)

## Decision

Three additions to the provider-backed eval so a systemically-broken run stops early instead of burning money (a prior run spent ~$3.93 grinding through runs that all failed identically):

1. **Fail-fast on repeated non-retryable failure.** `BlogRunRecord` gains `failure_kind` (`"retryable"` | `"non_retryable"` | `None`). `ProviderBackedEvalRunner.run_once` tags a caught `TransientFailure` retryable and any other `CyberlabGenError` (HardFailure/4xx, malformed, Layer-1 exhaustion, jury reject) non-retryable. `run_blog_set` counts *consecutive* non-retryable failures whose **normalized** signature matches (`_normalize_failure` strips the varying `toolu_…` id, `messages.N` index, and digits — the tool-loop 400's variable bits); at `abort_after_consecutive_failures` (default 2) it aborts. A transient blip yields signature `None`, which resets the counter — transient never aborts.

2. **Cost cap.** `run_blog_set` / `run_eval` take `cost_cap_usd` (default `Decimal("5")`); once cumulative spend reaches it the eval stops before the next run. The per-run `CostLedger`, previously built with `cap_usd=None`, is now built with the cap.

3. **Real per-run cost** (so the cap is not hollow). New `eval/runner/cost_recording_provider.py::CostRecordingProvider` wraps the real provider and records each call's costed `usage` into the per-run ledger; `ProviderBackedEvalRunner` now builds the ledger itself and hands it to a `extract_runner_factory(ledger)` that wires the wrapper, then reads `ledger.total_usd` back as the run's real cost.

On either abort, the not-yet-run blogs are recorded `skipped` (with the abort reason), the partial report is archived (ADR 0028 incremental archive already does this per blog), and progress prints `eval: aborting early — …`. The cap + running total + headroom appear in the per-run progress lines.

## Context

The eval's `CostLedger` was hollow: `cli/extract.py::_drive` does `del ledger`, and the Anthropic adapter sums usage into a private `_UsageAccumulator`, so nothing ever fed the ledger and `BlogRunRecord.cost_usd` (read from `ledger.total_usd`) was always `0`. A cost cap built on that would never fire — exactly the "looks done, is hollow" trap this project keeps hitting. Rather than ship a dormant cap, the wrapper closes the gap honestly for the per-call totals the cap needs. Full per-attempt ledger→pipeline wiring (rows threaded through the orchestrator) remains the broader deferred task the adapter docstring names; this does not attempt it.

Fail-fast can't key on raw `halt_reason` equality because the 400 names a different tool id and message index each run (the diagnostic the user supplied: `messages.7` in five runs, `messages.5` in one). Hence normalization before comparison.

## Alternatives considered

- **Cap on the hollow ledger only** (set `cap_usd` but leave cost at 0) — rejected: a cap that never fires is worse than none (false safety). The wrapper makes it real.
- **Abort on any repeated failure, transient included** — rejected: the brief is explicit that a transient blip (timeout/429/`TransientFailure`) must not abort; only a systemic non-retryable repeat. Hence the `failure_kind` split.
- **Raw-string halt_reason equality for fail-fast** — rejected: the 400's varying id/index would make identical failures look distinct and never trip the abort. Normalize first.
- **Thread the ledger through the whole pipeline now** — deferred: large, cross-cutting (orchestrator + AgentRunner), and out of scope; the wrapper captures per-call totals without it.

## Consequences

- `BlogRunRecord` gains `failure_kind` (InternalModel, `extra="ignore"`) — old archived reports load unchanged (defaults `None`). `EvalReport` is otherwise unchanged; aborts surface via the existing `skipped` list.
- `ProviderBackedEvalRunner.__init__` changes (drops `cost_ledger_factory`; `extract_runner_factory` now takes a `CostLedger`; adds `cost_cap_usd`) — amends the ADR 0025 runner shape. Only constructed in the `# pragma: no cover` eval path; no test constructs it.
- New module `eval/runner/cost_recording_provider.py`; `EvalProgress` gains `run_aborted` + cost-cap params on `run_started`/`blog_run_finished`.
- New `tests/eval/test_spend_guards.py`: fail-fast aborts on normalized-identical non-retryable repeats; transient and distinct failures do **not** abort; cost cap aborts + archives the partial; `_normalize_failure` collapses varying ids; `CostRecordingProvider` records real cost into the ledger.
- Independent of the still-open tool-loop 400 (instrumented separately) — these guards bound the damage while that root cause is diagnosed from real data.
