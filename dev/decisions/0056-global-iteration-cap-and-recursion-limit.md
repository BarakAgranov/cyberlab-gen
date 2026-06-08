# 0056 — Global iteration cap + LangGraph recursion_limit backstop (L3)

**Date:** 2026-06-08
**Phase:** 1 (pre-Phase-2 safety/lossless batch)
**Architecture refs:** `architecture.md §6` ("Total iteration cap (default 20)",
"Per-agent iteration cap (default 5)", "the total cap is what binds in typical practice"),
`pipeline.md §3.2.x` (the refinement loop), `architecture.md §1.5` (the framework routes,
never the LLM). Companion to ADR 0018/0023 (the per-node retry/refinement budgets).

## Context

The Phase-1 orchestrator bounded each *per-node* mechanism — structural retry
(`structural_retry_attempts`, default 3) and refinement (`refinement_cap`, default 3) —
but **nothing bounded total pipeline iterations end-to-end**, and LangGraph's
`recursion_limit` was left unset (its library default, 25). `architecture.md §6` documents
a **Total iteration cap (default 20)** as the bound that "binds in typical practice"; it
was not enforced. With the per-node caps composing (and after ADR 0056's sibling fix L2
made the two budgets independent), the realistic maximum is ~6 Extractor runs, but a
raised/mis-set per-node cap or a routing pathology had no end-to-end ceiling, and an
unset `recursion_limit` meant the only graph-level bound was an undocumented library
default that could trip at an arbitrary point relative to the application's own logic.

## Decision

Two bounds, in two units, recorded as named constants:

1. **`GLOBAL_ITERATION_CAP = 20`** — the documented total iteration cap, counted in
   **Extractor runs** (the unit `architecture.md §6` means by "iteration": one re-run of an
   agent). A new `PipelineState.total_iterations` is incremented on **every** `extract_node`
   entry (first run, structural retry, or refinement). Before either re-routing node
   (`validate_node`'s structural-retry branch, `jury_node`'s refinement branch) starts
   another Extractor run, it checks the cap and, if reached, halts **cleanly** as
   `HALTED_VALIDATION` with an explicit `halt_reason` — never an opaque library error. This
   is the user-facing, semantic bound; it is plumbed as a `build_pipeline(global_iteration_cap=…)`
   parameter (default `GLOBAL_ITERATION_CAP`) so it is testable and tunable.

2. **`GRAPH_RECURSION_LIMIT = 4 * GLOBAL_ITERATION_CAP` (= 80)** — the LangGraph
   `recursion_limit`, a **super-step** bound, set in the `RunnableConfig` on every
   `ainvoke` (with and without a checkpointer). This is the final graph-level backstop: it
   bounds total super-steps regardless of the application caps, so a routing pathology
   raises `GraphRecursionError` rather than spinning forever.

**Why two units, and why `4×`.** "Iterations" (agent re-runs) and "super-steps" (LangGraph
node executions) are different units: each iteration costs at most 3 super-steps
(extract → validate → jury) before the next run or a terminal node, so
`GLOBAL_ITERATION_CAP` iterations consume at most ~3·20 + a terminal node ≈ 62 super-steps.
Setting `recursion_limit = 4 · cap = 80` keeps it strictly above that worst case (with
margin), so the **semantic** cap — which halts cleanly with a clear reason — always binds
first in a legitimate run, and the `recursion_limit` only ever catches a genuine routing
loop the application logic somehow let through. (Setting `recursion_limit` literally equal
to 20 would, in super-step units, trip at ~8–10 iterations and make the documented
20-iteration cap unreachable — the wrong reading of "the documented value".)

Both are framework code: deterministic, auditable, never LLM-decided
(`architecture.md §1.5`). Neither raises a budget; they only ever *stop earlier*.

## Consequences

- `PipelineState` gains `total_iterations: int = 0` (defaulted, so old checkpoints still
  validate; it round-trips through the checkpointer like the other counters).
- `build_pipeline` gains `global_iteration_cap` (validated `>= 1`); `run()` always sets
  `recursion_limit`. No change to the ADR-0023-locked `build_pipeline`/`run_pipeline`
  *return* surface or the node topology — only an added keyword arg (additive, like ADR
  0040's `checkpointer`) and the config.
- Tests: a pathological structural loop with `structural_retry_attempts=100` halts at the
  global cap (20 runs), not the per-node budget — which also implies `recursion_limit`
  exceeds the cap's super-steps, since a clean halt fires instead of a `GraphRecursionError`;
  the `recursion_limit` constant is pinned at `4 × GLOBAL_ITERATION_CAP`; and with both
  application caps disabled a runaway loop still raises `GraphRecursionError` (the backstop).
- **No validation-contract change** — what counts as a blocking finding is untouched; this
  only bounds *how many times* the pipeline may iterate.

## Alternatives considered

- **Set `recursion_limit = 20` (the documented number, literally).** Rejected: it conflates
  super-steps with iterations and would trip before the documented 20-iteration cap could,
  making the semantic cap unreachable. The cap belongs in iteration units; the
  `recursion_limit` is a derived super-step backstop above it.
- **Enforce the cap only via `recursion_limit` (no semantic counter).** Rejected: a raw
  `GraphRecursionError` is an opaque library failure with no run-report-friendly reason;
  the user/eval should see a clean `HALTED_VALIDATION` naming the iteration cap. The
  `recursion_limit` stays as the last-resort backstop, not the primary bound.
- **Derive `total_iterations` from `structural_attempts + refinement_iterations`.** Viable
  (they sum to the same count) but an explicit counter is clearer and robust to future
  routing changes; the tiny extra field is cheap.
