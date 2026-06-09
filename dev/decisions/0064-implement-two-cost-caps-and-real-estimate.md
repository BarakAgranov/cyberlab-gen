# 0064 — Implement the two distinct cost caps + a real per-iteration estimate (stopgaps marked)

**Date:** 2026-06-09
**Phase:** 1 (pre-Phase-2 consolidation batch — item **B2**)
**Architecture refs:** `architecture.md §1.7` (everyday budget + predictive interrupt), `pipeline.md
§3.1.1`/`§3.2.12`. **Implements ADR 0049** (two cost caps). **Reconciles ADR 0038** ("--max-llm-cost
lowers the catastrophe ceiling") with ADR 0049 ("--max-llm-cost configures the everyday budget"). The
catastrophe ceiling mechanism is ADR 0047 (unchanged). The stopgaps recorded here are removed by the
loop-budget threading work-stream, **ADR 0063**. Maintainer-directed scope (the AskUserQuestion answers
in this batch's session).

## Context

The code conflated two different mechanisms behind one number (`ledger.cap_usd`): the soft everyday
budget (a predictive, user-overridable, pre-spend interrupt) and the hard $25 catastrophe ceiling (a
mechanical, no-override, post-each-call backstop). The everyday machinery was inert — the
next-iteration estimate was hardwired to zero, so the predictive interrupt never fired. ADR 0049
settled that the two must be distinct and both live.

## Decision

**1. Split the two caps on the ledger.** `CostLedger` now carries `cap_usd` (the catastrophe ceiling,
hard, fixed, unraisable — `CostRecordingProvider._enforce_ceiling` still reads it, unchanged) **and**
`everyday_budget_usd` (the soft everyday budget, read only by the predictive interrupt in
`cli/extract.py`). New constant `DEFAULT_EVERYDAY_BUDGET_USD = $10` alongside
`DEFAULT_CATASTROPHE_CEILING_USD = $25`.

**2. `--max-llm-cost` re-binds from the ceiling to the everyday budget (supersedes ADR 0038).** `main.py`
sets `everyday_budget_usd = --max-llm-cost or $10` and `cap_usd = $25` (fixed). This is a deliberate,
maintainer-confirmed behaviour change: ADR 0038 made `--max-llm-cost` *lower the ceiling*; ADR 0049 (the
later, primary decision for B2) makes it *configure the everyday budget*, and the ceiling becomes a
fixed, unraisable backstop with **no override**. The two cannot both hold; ADR 0049 wins, and ADR 0038's
`--max-llm-cost`-lowers-the-ceiling clause is superseded.

**3. The predictive interrupt enforces the everyday budget with a REAL estimate (STOPGAP).** `_would_overrun`
and `_handle_budget_overrun` read `everyday_budget_usd` (not `cap_usd`). `RunResult.estimated_next_stage_cost`
is now set by the production runner from the billed ledger (`ledger.total_usd` delta around the pipeline
run ≈ the cost of the next feedback re-run), un-hardwiring the former zero. The interactive "raise" path
raises `everyday_budget_usd` (the ceiling is never raised). **STOPGAP (→ ADR 0063):** the interrupt fires
only at the post-Extractor boundary (the next *re-run*); the end-state computes a live estimate *inside*
the orchestrator loop and fires between refinement iterations, per `architecture.md §1.7`.

**4. `--auto` hard-stops at the soft budget (STOPGAP).** With no human to answer the interrupt,
exceeding the everyday budget in `--auto` halts the run (the soft budget's job is early-warning; warn-
and-proceed unattended would silently ignore the limit the user set). The $25 ceiling remains the
orthogonal hard backstop in both modes. **STOPGAP (→ ADR 0063):** the end-state's in-loop check makes
the run respect the budget *as it iterates*, so this coarse post-hoc hard-stop is no longer the mechanism.

## Consequences

- **The two caps are independent**: `CostRecordingProvider._enforce_ceiling` (the $25 ceiling) is
  untouched; the everyday budget is a separate value the provider never reads. The conflation bug ADR
  0049 exists to fix is gone.
- **Behaviour change for `--max-llm-cost`** (ceiling → everyday budget): existing CLI tests updated; the
  ceiling is now fixed at $25 with no flag override (ADR 0049 item 2's "no override").
- **Three stopgaps**, each marked temporary-with-expiry pointing at ADR 0063: the post-Extractor estimate
  (vs in-loop), the `--auto` hard-stop (vs in-loop respect), and (from ADR 0062) E1's over-cap report.
- **No `docs/` edit** — `architecture.md §1.7`/`pipeline.md §3.2.12` already describe the two distinct
  caps (the ADR-0049 design pass). The "$25 ceiling no longer `--max-llm-cost`-lowerable" nuance is
  recorded here rather than edited into ADR 0038 (kept as the historical record it superseded).
