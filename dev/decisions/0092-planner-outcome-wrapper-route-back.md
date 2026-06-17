# 0092 — The Planner signals route-back / cannot_plan via a discriminated `PlanAttempt` outcome

**Date:** 2026-06-17
**Phase:** 2 (Task 4 — Planner-Jury + refinement extension + route-back)
**Architecture refs:** `architecture.md §1.5` (the LLM produces a structured judgment; the
**framework** routes on it — the LLM never routes control flow), `agents.md §5.7` /
`pipeline.md §3.2.6` (the Planner's failure modes: `cannot_plan` for gaps / infeasibility; flag an
**incoherent** AttackSpec and route back to the Extractor — the Planner never repairs the AttackSpec),
`§0.7`. Extends ADR 0090 (the Planner output schema). Fork A, architect-approved.

## Context

Task 3 made `Planner.plan` force `output_schema=LabManifest` and return `PlanResult(manifest, lookups)`.
But `agents.md §5.7` / `pipeline.md §3.2.6` require the Planner to surface two non-manifest outcomes:

- **`cannot_plan`** — AttackSpec gaps too large to plan around, or infrastructure the system cannot
  express → refuse outright; the run halts with a structured gap report.
- **AttackSpec incoherence** (mismatched pre/postconditions, a value type the AttackSpec never
  typed) → **flag** with structured detail; the framework routes **back to the Extractor**. The
  Planner does **not** repair the AttackSpec (`agents.md §5.7`: AttackSpec authorship is the
  Extractor's; seeing a defect does not grant authority to fix it).

An incoherent or un-plannable AttackSpec has **no valid manifest to emit**, so a bare `LabManifest`
output gives the Planner no channel to surface either outcome. The Planner needs an in-band,
*structured* way to say "I cannot produce a manifest, and here is the why" that the **framework**
reads to route — never the LLM routing itself (`§1.5`).

## Decision

The Planner forces a discriminated wrapper, **`PlanAttempt`** (in `agents/results.py`):

```
PlanAttempt{ outcome: PlanOutcome, manifest: LabManifest | None, refusal: PlannerRefusal | None }
PlanOutcome  = planned | attackspec_incoherent | cannot_plan
PlannerRefusal{ summary, attack_spec_field_paths: [str] (>=1), detail }
```

A `model_validator` enforces the discriminator↔payload coupling (`planned` ⇒ manifest set, refusal
None; otherwise ⇒ refusal set, manifest None), mirroring `JuryVerdict`'s verdict↔feedback validator
so a malformed attempt fails *structurally* rather than mis-routing. `Planner.plan` reads `outcome`
to build a `PlanResult` (its `manifest` is now **optional** — the small ADR-0090 contract evolution;
nothing consumes `PlanResult` yet). The framework (the Task-4 coordinator, ADR 0093) routes on
`outcome`: `planned` → Planner-Jury; `attackspec_incoherent` → route back to the Extractor;
`cannot_plan` → halt with the gap report.

**Relation to the Extractor's `extraction_outcome`.** This mirrors the *spirit* of the Extractor's
in-band outcome discriminator (an outcome enum + a coupling validator) but is a **wrapper**, not an
in-band field — because, unlike an out-of-scope AttackSpec (a *complete* spec with its content
blocks nulled), a failed plan carries no manifest at all. So the discriminator cannot live on the
manifest; it wraps it.

**Placement (cycle-avoidance).** `PlanOutcome` / `PlannerRefusal` / `PlanAttempt` live in the leaf
`agents/results.py` alongside `PlanResult`, **not** in an `agents/planner/` submodule. `PlanResult`
references them, and importing them through the `agents.planner` package surface runs
`agents.planner.__init__` → `planner.py` → `extractor.extractor` mid-init, the exact
`agents`↔`framework` load-time cycle ADR 0075 dissolved. The leaf is the one home that both the
Planner (producer) and the coordinator (consumer) import without the cycle; the `agents.planner`
surface re-exports them.

A new stage error **`PlanningError`** (`errors.py`, `stage='planning'`, documented since Phase 0 but
first raised here) is the clean halt when `Planner.refine`'s bounded patch re-prompt budget is
exhausted — mirroring `ExtractionError` for the Extractor; distinct from the *route-back* (a
`PlanOutcome`, not an exception).

## Consequences

- `agents/results.py`: `PlanOutcome`, `PlannerRefusal`, `PlanAttempt` added; `PlanResult` gains
  `outcome` + optional `manifest`/`refusal` + `reprompts`. `agents/planner/outcome.py` (a first
  draft of these) was deleted into the leaf to break the import cycle.
- `planner.py`: `plan` forces `PlanAttempt` and maps it to a `PlanResult`; `_finalize_manifest`
  factored (re-derive `core.reproducibility`); `refine` added (ADR 0091's generic patch path).
- `errors.py`: `PlanningError` added.
- Re-exports (`agents.planner`, `agents`) expose `PlanAttempt` / `PlanOutcome` / `PlannerRefusal`.
- Tests: route-back / cannot_plan outcomes, the coupling validator, and the refine targeted-patch
  path (convergence, re-derive, owned-target rejection → `PlanningError`). Full suite green (826
  passed / 1 skipped).
- **No architecture-doc change** — `agents.md §5.7` / `pipeline.md §3.2.6` already specify these
  outcomes; this records the *mechanism* (the wrapper + where it lives).

## Alternatives considered

- **Keep `output_schema=LabManifest`; let the Planner-Jury be the sole route-back trigger.**
  Rejected: `agents.md §5.7` and the Planner prompt explicitly say the *Planner* surfaces
  incoherence, and an incoherent AttackSpec yields no manifest for the jury to review in the first
  place.
- **A union output `LabManifest | PlannerRefusal`.** Rejected: the forced-output call surface takes
  a single `type[T]`; a discriminated wrapper is the faithful single-type encoding (and matches the
  `extraction_outcome` / `JuryVerdict` precedent).
- **Put the outcome types in `agents/planner/outcome.py`.** Rejected: it re-introduces the ADR-0075
  `agents`↔`framework` load-time cycle via the package `__init__`; the leaf `results.py` is the
  cycle-free home.
