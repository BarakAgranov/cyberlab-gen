# 0049 — Two cost caps: everyday refinement budget vs catastrophe backstop

**Date:** 2026-06-07
**Phase:** 1 (design-alignment / docs-revision pass)
**Architecture refs:** `architecture.md §1.7`, `pipeline.md §3.2.12`. Builds on ADR 0030
(everyday cost cap origin), ADR 0038 (one high catastrophe ceiling), ADR 0047 (ceiling
enforced on billed failures too). This is item **B2** of the A1–G1 design-alignment plan.

## Context

The docs and the code disagreed on "the cost cap," and the disagreement hid two *different*
mechanisms behind one number:

- `architecture.md §1.7` described a **$10** everyday LLM budget plus a predictive
  budget-overrun interrupt that fires "when the next iteration's estimated cost would push spend
  past the cap," plus iteration caps (20 total / 5 per agent).
- The code implements **none** of that everyday machinery. The only enforced limit is the **$25**
  catastrophe ceiling in `CostRecordingProvider` (ADR 0038/0047). The iteration caps are absent
  and the predictive interrupt is dead code — the estimated-next-iteration cost is hardwired to
  zero, so the interrupt never fires.

Calling both "the cost cap" conflated a soft everyday budget (enforced *before* spending, by a
predictive interrupt the user can override) with a hard catastrophe backstop (enforced *after*
each billed call, mechanically, with no override). They are different things with different
owners and different trigger points.

## Decision

Document **two distinct caps**, and keep them distinct in the eventual code:

1. **Everyday refinement budget** — default **$10**, configurable via `--max-llm-cost`. A *soft*
   cap that the **predictive budget-overrun interrupt** enforces *before* an iteration whose
   estimated cost would cross it. The user can raise it, abort, or proceed past it. This is the
   limit the user normally operates against; it sits alongside the iteration caps (20 total / 5
   per agent). All three are v1 placeholders pending eval calibration.

2. **Catastrophe ceiling** — default **$25**. A *hard* backstop enforced mechanically by
   `CostRecordingProvider` on **every billed call, success or failure** (ADR 0047). No override,
   no LLM involvement — it is a mechanical safety check (`architecture.md §1.6`). It exists to
   stop a runaway the predictive interrupt fails to catch: a single call far over its estimate, or
   a failure-dominated loop that never reaches the predictive check. It stays well above the
   everyday budget and is a backstop, not a number to calibrate.

**Tie to A1.** With targeted-patch refinement (ADR 0048), each iteration costs ~10× less than a
full re-extraction, so the per-iteration estimate is small and the everyday budget rarely binds in
practice. The budget is a guard for pathological loops, not an everyday friction.

**Both must be live.** A placeholder *value* is fine; an inert *mechanism* is not. The predictive
interrupt must compute a real next-iteration estimate (not a hardwired zero), and the iteration
caps must actually count — otherwise "$10 budget" is documentation of a thing that does not exist.

## Consequences

- **Docs updated in this pass:** `architecture.md §1.7` (everyday-budget bullet renamed; a "two
  distinct cost caps" paragraph added; the placeholder-caps note flags the live-mechanism
  requirement) and `pipeline.md §3.2.12` cost discipline (everyday budget + interrupt vs the $25
  backstop).
- **Code is a separate, later work-stream:** implement the everyday budget, the iteration caps
  (20 / 5), and a *live* predictive interrupt that computes a real next-iteration estimate.
  Currently only the $25 catastrophe ceiling exists — and its enforcement on every billed call is
  already done (ADR 0047), so only the everyday-budget side is outstanding.
- No change to the catastrophe ceiling's value or mechanism; this ADR only separates it, in the
  documentation, from the everyday budget it was being conflated with.
