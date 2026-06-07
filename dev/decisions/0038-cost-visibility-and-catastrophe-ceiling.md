# 0038 — Per-call cost visibility + one high catastrophe ceiling

> **Superseded in part by [ADR 0047](0047-catastrophe-ceiling-enforced-on-billed-failures.md).**
> This ADR argued the catastrophe ceiling needs no enforcement on the failure path ("a failed
> call that crosses the line needs no extra abort"). That premise is false for retried billed
> failures (`MalformedOutput`), so ADR 0047 enforces the ceiling on **every** billed call,
> success or failure. The per-call cost-visibility decision below still stands; only the
> failure-path rationale is superseded.

**Date:** 2026-06-05
**Phase:** 1 (operational-foundation pass, outcome #6)
**Architecture refs:** `provider-interface.md §5` (cost tracking; the framework — not
the provider — owns budget-overrun decisions, §5.3). Builds on ADR 0030 (real
per-run cost via `CostRecordingProvider`), ADR 0033 (billed-on-raise accounting).
Reframes ADR 0030's everyday cost cap.

## Context

A provider-backed run spends real money, but the user could not answer "where does
the money go?" — there was no per-call breakdown, and the only ceiling was a
**guessed everyday cap** (`DEFAULT_COST_CAP_USD = $5`, ADR 0030) checked only
*between* whole runs. The brief's framing: setting an everyday dollar cap before
costs are even visible is premature — a fixed low cap just loses that money every
failing run while teaching nothing. What's needed first is **visibility**, then a
**high catastrophe backstop** (not an everyday brake), plus **cheap early failure**.

## Decision

### Cost visibility (the primary requirement)

`CostRecordingProvider` — the one place that already sees every billed call,
including billed-but-raised failures (ADR 0033) — now logs one INFO line per call:
agent, model, input/output/cache tokens, the cost of *that* call, and the running
cumulative total and call count. Written to the run-log file (ADR 0037), so after
one or two runs the user can see how much is re-sent input vs each emit, how many
calls per run, and where a failing run's spend goes — the data needed to set an
*informed* limit later.

### One high catastrophe ceiling, enforced mid-run

There is now a **single** cost ceiling, the high catastrophe backstop
(`DEFAULT_CATASTROPHE_CEILING_USD = $25`, configurable), carried as
`CostLedger.cap_usd`. After each *successful* billed call, the framework-side
`CostRecordingProvider` checks cumulative spend against it and raises the new
`BudgetExceeded` (a `HardFailure`) to abort immediately. The ledger itself still
never raises (`§5.3`); the wrapper makes the decision. A *failed* call that crosses
the line needs no extra abort — its own `ProviderError` already halts the run and
its spend is recorded. `BudgetExceeded` carries the crossing call's billed
`usage`/`model` (honest accounting) plus `spent_usd`/`ceiling_usd`, and its message
states plainly that it is a high backstop to replace with `--max-llm-cost` once real
costs are seen.

This is **mid-run** (after each billed call) — the gap ADR 0030 left: a single
runaway could bill unbounded before the between-runs check saw it.

### Scope: eval AND the CLI

`CostRecordingProvider` moved from `eval/runner/` into the package
(`cyberlab_gen/providers/`) so both paths use it:
- **Eval** already wrapped the provider per run; it now also enforces the ceiling
  mid-run and logs per call.
- **CLI `extract`** previously built a bare `AnthropicProvider` and `del`'d its
  ledger (cost was invisible). It now wraps the provider in `CostRecordingProvider`
  bound to the session ledger, and the ledger defaults to the $25 ceiling (not
  `None`) so even a no-flag run is bounded.

## Judgment calls (flagged)

- **Raised the everyday cap to a catastrophe backstop.** ADR 0030's `$5`
  `DEFAULT_COST_CAP_USD` is reframed to `$25` (the catastrophe ceiling). This is a
  deliberate behavior change following the brief's "don't keep a guessed everyday
  cap" framing — `$5` was exactly that. A normal-but-failing run (~$1–2) no longer
  trips it; only a genuine runaway does.
- **CLI default is now `$25`, not no-cap.** Omitting `--max-llm-cost` used to mean
  "no ceiling"; it now means "the high backstop." `--max-llm-cost` lowers it to an
  informed value.
- **One ceiling, not two.** Rather than a separate everyday-cap + catastrophe-ceiling
  pair, there is a single configurable ceiling (the ledger's `cap_usd`). The
  existing CLI pre-stage `_would_overrun` projection and the eval between-runs check
  remain as secondary guards on the same value.

## Early-cheap-failure (#6c) — status

Largely already addressed by ADR 0033: a truncating run now halts on the *first*
emit (~1 call, ~$1) instead of burning 6–9 retries. Making the failure cheaper still
— aborting *before* the ~16K-token output is generated — requires streaming /
chunked-continuation emit, the deferred **P4** gap (ADR 0032/0033/0036). Not forced
here; flagged. With per-failure cost already small and the $25 backstop in place, the
ceiling rarely matters — exactly the intended outcome.

## Consequences

- New `cyberlab_gen/providers/cost_recording_provider.py` (moved + enhanced),
  exported from `providers`; `eval/runner/cost_recording_provider.py` removed; eval
  cli/runner + `test_spend_guards` import from the package.
- New `BudgetExceeded(HardFailure)` (eval classifies it global-fatal → abort run +
  archive partial); `DEFAULT_CATASTROPHE_CEILING_USD` in `cost_ledger`.
- New tests: mid-run ceiling abort (spend recorded, usage attached), per-call cost
  logging. `test_max_llm_cost_flag_omitted_*` updated to the ceiling default.
- The persisted per-agent/per-model breakdown (`CostReportBlock`) into the eval
  report rides with the run-report writer in the guaranteed-persistence work (#5).
