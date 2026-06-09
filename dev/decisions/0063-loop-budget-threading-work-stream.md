# 0063 — The loop-budget threading work-stream (the immediate-next architecture work; replaces three stopgaps)

**Date:** 2026-06-09
**Phase:** 1 → 1.5 boundary (named, scoped follow-on; **not** part of the pre-Phase-2 consolidation batch)
**Status:** PLANNED — the immediate next architecture work-stream after the pre-Phase-2 batch.
**Architecture refs:** `architecture.md §1.7` (everyday budget + predictive interrupt "before the next
iteration"), `pipeline.md §3.2.12`, ADR 0049 (two cost caps), ADR 0050 (over-cap = in-loop steering
bounded by the ADR-0049 caps), ADR 0023/0056 (the locked orchestrator graph + the caps). Owns the
removal of the stopgaps recorded in ADR 0062 (E1) and ADR 0064 (B2).

## Context

ADR 0049 and ADR 0050 both specify behaviour that lives **inside the orchestrator's iteration loop**:
the everyday-budget predictive interrupt is meant to fire "before the next *iteration*" (between
refinement/structural-retry iterations), and over-cap proposal handling is meant to **steer the
Extractor in-loop** (reuse/refactor instead of over-proposing) bounded by the ADR-0049 caps. The
current code cannot do either: the refinement/structural iterations run to completion inside the
LangGraph loop with **no cost-aware check threaded in**, and the only spend signal the CLI sees is a
fully-shipped `RunResult` at the post-Extractor interrupt. The proposal cap is enforced at the CLI
post-ship boundary, not in the loop. Threading the ledger + a live per-iteration estimate + the budget
caps into the orchestrator loop is real, cross-cutting surgery on the **ADR-0023-locked** graph, so it
deserves its own focused, gated work-stream rather than being smuggled into the consolidation batch.

The maintainer's explicit direction (pre-Phase-2 batch): implement the feasible CLI-level slices now as
**stopgaps marked temporary-with-expiry**, and capture the real work here as a named, scoped item — the
stopgaps are scaffolding this work-stream **removes**, not the chosen design.

## The three stopgaps this work-stream replaces

1. **E1 over-cap (ADR 0062):** "write up to the cap at ship time, report the remainder" in
   `cli/extract.py::_promote_proposals_auto`. The end-state steers the Extractor *in-loop* to propose
   fewer (the registry digest is the first lever toward that, shipped in E1), bounded by the ADR-0049
   caps — so over-cap rarely arises and is handled mid-loop, not reported after the fact.
2. **B2 post-Extractor estimate (ADR 0064):** `RunResult.estimated_next_stage_cost` made a real
   ledger-derived estimate so the everyday-budget interrupt fires before the next *re-run*. The
   end-state computes a live per-*iteration* estimate inside the orchestrator and fires the interrupt
   **between** refinement/structural iterations, per `architecture.md §1.7`.
3. **B2 `--auto` hard-stop (ADR 0064):** in `--auto` (no human to answer the interrupt), exceeding the
   soft everyday budget hard-stops the run. The end-state's in-loop predictive check makes the run
   respect the budget *as it goes*, so the coarse post-hoc hard-stop is no longer the mechanism.

## Decision (scope of the work-stream)

Thread cost-awareness into the orchestrator iteration loop:

- Pass the `CostLedger` (and the everyday-budget value + ADR-0049 iteration caps) into
  `build_pipeline`/the orchestrator as additive keyword args (the same additive discipline ADR 0040 /
  0056 / 0060 / 0061 used on the locked surface).
- Compute a **live per-iteration cost estimate** from the ledger inside the loop, and fire the
  everyday-budget predictive interrupt **before each next Extractor run** would cross the soft budget
  (interactive: pause-and-ask; `--auto`: the framework decides per ADR 0049/0064 — and the in-loop
  check means the run is bounded *as it iterates*, not only at the post-Extractor boundary).
- Move over-cap proposal handling into the loop: when the run would exceed the per-run proposal cap,
  steer the Extractor (via the prompt + the registry digest) to reuse/refactor instead of proposing,
  bounded by the ADR-0049 iteration/budget caps — replacing the CLI "report the remainder" stopgap.
- Keep the $25 catastrophe ceiling untouched (ADR 0047) — it is the orthogonal hard mechanical backstop.

This is **graph surgery on a locked surface**: it requires its own ADR for the build_pipeline signature
change and its own verification gate. It does **not** land in the pre-Phase-2 batch.

## Consequences

- Removes all three stopgaps; the everyday budget and over-cap steering then behave as ADR 0049/0050 and
  `architecture.md §1.7` literally specify ("before the next iteration", "in-loop steering bounded by
  the caps").
- Until this lands, the stopgaps are the honest interim behaviour — each marked in its own ADR and in
  the pre-Phase-2 checklist as temporary-with-expiry, pointing here.
