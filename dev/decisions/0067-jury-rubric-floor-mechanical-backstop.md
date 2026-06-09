# 0067 — The framework mechanically enforces the jury rubric floor

**Date:** 2026-06-10
**Phase:** 1 (pre-Phase-2 fix register — Batch A, item ①.1)
**Supersedes:** 0021 (point 5 only — the claim that a `model_validator` enforces the floor)
**Architecture refs:** `architecture.md §1.5` (the LLM owns the holistic verdict; the framework
routes control flow), `architecture.md §1.6` (mechanical safety checks are framework-owned, never
LLM-based), `agents.md §5.5` (the four-dimension rubric, 0.7 floor), `pipeline.md §3.2.3` (verdict
→ control flow). Source: investigation `0003 §4-A`, `0004 §1.1/§1.3` (S4/S10/S39/S43).

## Context

The 0.7 rubric floor was documented as a hard gate but reached **only the LLM prompt**
(`agents/extractor_jury/jury.py` `_build_user_turn`: "every dimension must score >= {floor}").
No framework code read it:

- `jury_node` (`framework/orchestrator.py`) routed **solely** on `verdict.verdict` and never
  inspected `verdict.scores`.
- `JuryScores.all_above` / `JuryScores.min_dimension` (`agents/extractor_jury/schema.py`) were
  **dead code** — zero call sites anywhere in the package.
- `JuryVerdict._verdict_consistency` validated only the verdict↔feedback **count** coupling
  (approve→0 items, revise→1-3, reject→≥1); it never compared scores to the floor.

So a verdict of `approve` with every dimension at 0.1 shipped at full confidence — a
self-contradiction nothing caught. **ADR 0021 point 5 asserted "a `model_validator` rejects a
verdict whose dimension scores breach the floor." That validator never existed** — point 5 was
aspirational and inaccurate. The test suite *masked* the gap: every jury fixture paired its verdict
with high scores (`pipeline_fakes.make_verdict` hardcoded 0.9; `test_extractor_jury.py` used only
0.9/0.65/0.2, chosen to satisfy the verdict↔feedback validator), so no test exercised an
`approve` with a sub-floor dimension.

## Decision

**Wire the floor mechanically as a framework-owned backstop — do not document the gap away.**

In `jury_node`, an `approve` ships only when `verdict.scores.all_above(jury.rubric_floor)`. An
`approve` that is *not* all-above-floor is a verdict that disagrees with itself; the framework
refuses to ship it and **halts** with the new terminal status `PipelineStatus.HALTED_JURY_INCONSISTENT`,
finalized as `JuryInconsistencyError`. This reuses the formerly-dead `all_above` / `min_dimension`.

- `JuryInconsistencyError` subclasses `JuryRejectionError` (itself a `ValidationError`), so it
  inherits the existing reject-class exception→`RunStatus` mapping (a *quality* halt carrying no
  static-schema findings) — **no CLI or eval persistence change is needed**. It remains a distinct
  type the run report and tests can name.
- The floor is exposed to the framework by adding a `rubric_floor` property to the `_JuryLike`
  protocol; the concrete `ExtractorJury` already had it.

This is **defense-in-depth** (`architecture.md §1.6`): the jury still owns the holistic verdict
(`§1.5`); the framework only vetoes a verdict that contradicts its own scores. It can only ever halt
a run that would otherwise have shipped a self-contradictory approve — it never loosens the floor
and never overrides a *consistent* verdict.

## Alternatives considered

- **Coerce the approve to `revise` and synthesize feedback** — rejected. An `approve` carries
  **no** feedback (the validator forbids it), so a refinement re-run would have nothing to target;
  and having the framework author jury feedback brushes against the `§1.5` split. A halt is the
  safe, deterministic action.
- **Ship with `low_jury_confidence`** — rejected. The defect is precisely that a sub-floor approve
  *ships*; the fix-register decision is "must NOT ship."
- **Correct ADR 0021 point 5 to say "prompt-only" and stop there** — rejected. That documents the
  hole instead of closing it; the rubric floor is a `§1.6` mechanical safety check and belongs in
  framework code, not prose.
- **Editing ADR 0021 in place** — rejected per `coding-conventions.md §7.3` (ADRs are append-only).
  This ADR supersedes point 5; 0021's other points (the verdict taxonomy and verdict→control-flow
  mapping) stand.

## Consequences

- A self-contradictory `approve` (any dimension < `rubric_floor`) halts instead of shipping;
  `run_pipeline` raises `JuryInconsistencyError`. Pinned by tests
  (`test_jury_approve_with_subfloor_score_halts_not_ships`,
  `test_run_pipeline_raises_jury_inconsistency_on_subfloor_approve`), with an inclusive-floor
  boundary control (`test_jury_approve_at_floor_still_ships`) and a direct helper unit test.
- The suite no longer masks the gap: `make_verdict` now takes explicit `scores`, so a sub-floor
  approve is expressible (and was used to demonstrate the pre-fix ship).
- The formerly-dead `JuryScores.all_above` / `min_dimension` are now live and unit-tested.
- **No `docs/` edit** — `agents.md §5.5` / `architecture.md §1.6` already describe the floor as a
  hard, framework-owned gate; the code now matches the docs, and the inaccurate ADR-0021 claim is
  corrected here.
- Phase 2's Planner-Jury and Critic reuse the `{verdict, scores}` shape; the backstop pattern (read
  scores against the floor in the framework's ship decision) carries forward.
