# 0051 — One orchestrator-owned mechanical-validator stack (the Extractor stops self-validating; the jury consumes findings)

**Date:** 2026-06-07
**Phase:** 1 (design-alignment / docs-revision pass)
**Architecture refs:** `validation.md §6.10.2` (new) + `§6.10.1`, `agents.md §5.4`/`§5.5`,
`architecture.md §1.5`. **Amends ADR 0021** (points 4 and 6) and **refines ADR 0023** (point 1's
retry-budget taxonomy). This is item **A3/B1** of the A1–G1 design-alignment plan.

## Context

The mechanical checks on extraction output are split across three owners with three separate retry
budgets, and one check is implemented twice:

- Call-surface malformed-output retry (ADR 0018) — generic, fine.
- **Extractor-stage internal hallucination / search-before-claim loop** (ADR 0021 pt 4): after a
  structurally valid `AttackSpec`, the *Extractor stage* runs search-before-claim, MITRE, and CVE
  checks and re-prompts on its own `hallucination_retry_attempts` budget — a budget ADR 0023 pt 1
  explicitly calls "the Extractor's internal … budget" handling "failures the orchestrator never
  sees." An LLM-producing stage running its own framework-check retry loop is the
  `architecture.md §1.5` wrinkle (LLMs never decide their own retry budgets), and a feedback
  channel the orchestrator can't route or audit uniformly.
- Orchestrator Layer-1 structural-retry (ADR 0023 pt 1).

And the external-API-trace (search-before-claim) check runs **twice**: once in the Extractor stage
(ADR 0021 pt 4) and again in the jury (ADR 0021 pt 6 — "the jury independently re-runs
search-before-claim"). Two implementations of one mechanical check = drift risk + wasted work.

## Decision

**One stack, orchestrator-owned.** The extraction-stage mechanical checks — **static schema
(Layer 1), provenance structure, grounding / search-before-claim** — are sibling layers of a single
stack the orchestrator owns and routes, producing **one findings set**. On a finding the
orchestrator routes it to the producing agent, which re-emits a **patch** (ADR 0048).

- **The Extractor stops running its own validation-retry loop.** The checks stay framework-level
  and mechanical (unchanged from ADR 0021 pt 4), but ownership of the *retry/route* moves from the
  Extractor stage to the orchestrator; the `hallucination_retry_attempts` budget the orchestrator
  "never saw" (ADR 0023 pt 1) folds into the orchestrator-routed stack. This restores the
  `architecture.md §1.5` invariant.
- **The jury consumes the findings; it does not re-derive them.** The mechanical presence check
  (an `external_api` field has a matching trace tool-call) lives in the stack once; the jury keeps
  only the *semantic* judgment it is uniquely for — "does the cited passage actually support this
  claim?" This amends ADR 0021 pt 6 (the jury no longer independently re-runs search-before-claim;
  `verify_provenance`'s mechanical half becomes the stack's, consumed by the jury), mirroring how
  the Critic reads the Validator report "without re-checking" (`agents.md §5.14`).

## Consequences

- **Docs updated in this pass:** `validation.md §6.10.1` row 3 (owner → the orchestrator stack) and
  a new `§6.10.2` ("One orchestrator-owned mechanical-validator stack"); `agents.md §5.4` (the
  Extractor produces content and does not own a validation-retry budget) and `§5.5` (the jury
  consumes the findings set; its provenance discipline keeps the semantic half only).
- **Makes A1 cleaner:** one accumulating findings set to patch from, instead of the Extractor's
  inner loop + Layer-1 + the jury's re-derivation as three separate channels.
- **Code is a separate, later work-stream:** move search-before-claim / MITRE / CVE out of the
  Extractor stage into the orchestrator-routed stack; delete the jury's independent
  search-before-claim re-run (consume the findings instead); collapse `hallucination_retry_attempts`
  into the orchestrator's routing; reconcile ADR 0021's `ExtractionResult` retry shapes.
- ADR 0021 and ADR 0023 annotated with amended-by / refined-by pointers.
