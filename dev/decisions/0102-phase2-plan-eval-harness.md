# 0102 — Phase-2 plan-eval harness: metrics, the `PlanRunner`-direct runner, real-blog curated growth

**Date:** 2026-06-19
**Phase:** 2 (Task 10)
**Architecture refs:** `eval.md §7.4` (mechanical metrics — manifest field coverage, per-step
reproducibility distribution, lab-level classification, cost, registry proposals), `§7.4` F1
(the harness *measures the pipeline's emitted output*, it never re-derives a pipeline decision),
`§7.5` (false-approval / false-rejection review; asymmetric calibration), `§7.6` (repeated runs
with mean/median/variance), `§7.2` (honest framing of OSS eval), `§7.3` (curated-set composition),
`implementation-plan.md §5.3` (curated-set growth), `§5.4` (the six calibration items), `§5.5`
(exit criteria — including "Planner routes back, does not repair"), `schema.md §4.8` (the
any-heterogeneity-mixed rule), `architecture.md §1.5`/`§1.8` (LLM/framework split; the harness is a
measuring peer), ADR 0025 (the Phase-1 harness shape this mirrors), ADR 0028/0030/0034
(resilience / spend guards / failure-scope taxonomy), ADR 0081/0088 (lab-level reproducibility
derivation — sourced from the AttackSpec chain; framework-derived), ADR 0096 (`extract`/`plan` are
dev/eval commands that run one stage in isolation), ADR 0100 (the run-scoped-promotion / overlay
thread — and its owned deferral that **Task 10's plan eval must drive the plan runner directly,
never `run_plan`'s promotion**).

## Context

Task 10 closes Phase 2: it adds the plan-stage eval metrics, grows the curated blog set, and leaves
the six calibration items as live placeholders for the architect's paid run (eval is user-run, real
money — `eval.md §7.2`). Exit criterion 3 ("the harness is ready for the architect's calibration
run") and the ADR-0100 owned deferral both require an actual *runner* that drives the Planner stage
across the set — metric functions with nothing to drive them would not satisfy "ready." Several
points were genuinely open and were resolved with the user rather than guessed (CLAUDE.md: never
resolve architectural ambiguities silently).

## Decision

1. **Every plan-stage metric measures the pipeline's *emitted* output; none re-derives a pipeline
   decision (F1).** `manifest_field_coverage` and `per_step_reproducibility_distribution` read the
   *structure of the emitted `LabManifest`* (exactly as `structural_completeness` measures the
   emitted AttackSpec — measuring an artifact is not re-running a stage). `lab_level_classification`
   **reads** `core.reproducibility.classification_lab_level` — it does **not** re-run Task 2's
   `derive_lab_reproducibility`. **Reconciliation:** the brief's "lab-level reproducibility
   classification (using Task 2's rule)" is satisfied by reading the value *Task 2's rule stamped onto
   the manifest at plan time*, not by recomputing it; re-deriving outside the pipeline would measure
   the harness, not the pipeline (`§1.8`), and could disagree with what shipped. `layer2_passed` and
   `route_back` are read off the emitted terminal `PlanPipelineStatus`, never by re-running the
   semantic cross-check.

2. **A parallel plan-eval spine, not a generalization of the Phase-1 loop.** `PlanRunRecord` /
   `PlanBlogAggregate` / `PlanEvalReport` / `run_plan_set` / `ProviderBackedPlanEvalRunner` mirror the
   Extractor-stage shapes (ADR 0025) rather than refactoring `run_blog_set` / `EvalReport` to be
   record-generic. Rationale: the Phase-1 harness is locked and load-bearing; a generalization
   touches it for no measurement gain, against the manifest-shape-instability discipline (`§5.6`).
   The only shared code is the pure stats helpers (`mean_of` / `median_of` /
   `coefficient_of_variation`) and `HIGH_VARIANCE_CV`, promoted from module-private to public in
   `eval/runner/metrics.py` (a behavior-preserving rename) so the high-variance signal has one
   definition across both stages.

3. **The plan-eval runner drives `PlanRunner.run()` directly — never `run_plan` (the
   eval-overlay-read-only guard, ADR 0100).** `run_plan` (the `plan` verb engine) promotes accepted
   facet proposals to a registry overlay; an eval sweep across the curated set must be **read-only**
   w.r.t. every overlay (the exact concern the run-scoped-promotion thread protected). So
   `ProviderBackedPlanEvalRunner` constructs the Planner pipeline and calls `PlanRunner.run()`
   directly, *counts* `facet_proposals`, and never reaches `_promote_facets` or any overlay write.
   This is enforced by a test, not just asserted.

4. **Plan-eval input is a committed `attack_spec:` fixture per blog (a new optional `BlogEntry`
   field).** `plan` consumes an `attack-spec.yaml` in isolation (ADR 0096), so the plan eval consumes
   committed attack-spec fixtures rather than re-running `extract`. A blog without a resolved
   `attack_spec:` is **skipped** in a provider-backed plan run (mirroring the TBD-URL skip, ADR 0028)
   — recorded, not crashed. The existing `tests/integration/fixtures/codebuild-attack-spec.yaml` is
   wired as the one runnable demo input so the harness is provably runnable today without fabricating
   inputs; the architect produces the rest by extracting the curated blogs (part of the paid pass).

5. **The curated set grows with REAL published blogs, not synthetic fixtures.** Synthetic blogs
   reverse-engineered to hit a coverage type test nothing (you assert an output you authored the
   input to produce); real blogs measure real-world behaviour (`§7.2`). The required **`runtime:*`
   trigger** blog must *genuinely* warrant a facet the registry **seeds nowhere** — outside the
   seeded runtime set {`runtime:aws`, `runtime:azure`, `runtime:gcp`, `runtime:github`,
   `runtime:local`} (`registry-details.md §3.3`; `aws`/`azure`/`gcp`/`github` are `first_class: true`,
   `local` is a *seeded* `first_class: false` best-effort runtime — none of the five needs proposing).
   The chosen blog targets **Netlify**, which is seeded nowhere, so the Planner must propose
   `runtime:netlify` — a real emergent proposal, verified adversarially, not a blog picked because we
   know which proposal it forces. Walks are the
   walker's **independent** expected-behaviour, not the system's own output frozen and asserted back.
   Copyright: store URLs + walks + minimal-necessary verbatim excerpts, never wholesale article text.

6. **Real-blog walks ship PROVISIONAL, pending a human ground-truth pass; no calibration value may
   be locked against an unreviewed walk.** An LLM reading the blog is not independent ground truth
   (same model class, same blind spots). The walks are honest enough to *build* the set and drive the
   harness; the architect's paid pass reviews the walks **first**, then calibrates. Recorded as a gate
   in `CALIBRATION.md`, not merely a note. **Update (2026-06-20):** the human ground-truth pass is
   complete and provisional status is lifted (ADR 0104); the calibration gate (the six values) remains
   separate and still pending the paid `--stage plan` run.

7. **The codebuild fixture's schema-currency is due now (promoted from the parked stale-fixture
   item).** Because it becomes the demo input, it must be confirmed schema-current (it is
   `spec_version: 1`, the current `AttackSpec` version) and annotated with a regeneration path, so a
   frozen input does not rot at the eval's front door.

8. **Planner-Jury review tooling = a `JuryKind` discriminator on the existing ledger.**
   `eval/runner/review.py` was already jury-agnostic in shape; add a `JuryKind` (`extractor` |
   `planner`) field (defaulting to `extractor` for forward-load of any pre-existing ledger — none
   exist yet) plus per-jury rate aggregation, so the one tool computes false-approval /
   false-rejection for both juries. The asymmetric discipline (`§7.5`, `CALIBRATION.md`) governs both.

9. **The six Phase-2 calibration items stay live placeholders — not locked here.** Planner token
   budget, Planner per-stage retry, the Planner-Jury asymmetric threshold, `on_dependency_failure`
   default `warn`, the per-run auto-accept cap, and the pre-Planner external-API budget (100) are
   recorded in `CALIBRATION.md` as placeholders; the architect's provider-backed run locks them at the
   `v0.3` tag. Tests use a fake plan runner; no provider-backed run happens in this task.

## Consequences

- The plan eval is invoked via `just eval --stage plan` (the extract path is `--stage extract`, the
  default; additive, the Phase-1 path is untouched). The plan report archives to `eval/reports/`
  alongside the extract reports, prefixed to avoid collision.
- **Coarse manifest coverage.** `manifest_field_coverage` counts seven top-level/`core` optional
  collections (a proxy mirroring `structural_completeness`); finer per-phase content richness
  (per-step detections, tradecraft, `produces_world_state`) is a deliberate future refinement, not
  counted in v0.3.
- **Owned deferral (closed):** ADR 0100's "Task 10 plan eval must be overlay-read-only" is satisfied
  by decision 3 + its test.

## Phase-boundary deferral ledger (carried into Phase 3+, owners named)

Task 10 closes Phase 2; these parked items remain genuinely recorded, not dropped:
- **Manifest Layer-1 facet-membership check** — owner Phase-3 `generate` Validator; provisional
  `PendingProposals` resolution lands with it; now also covers the human-edit-rename case (ADR 0099 §6).
- **`AttackSpec.reproducibility` derive-or-remove** — ADR 0088 (the field is framework-owned but the
  top-level AttackSpec copy's producer is still open).
- **Typed KEV/EPSS/MSRC/bulletin homes** — Phase-3 Generator; **OSV/GitHub package/repo targets** —
  Phase-3 schema work (ADR 0101).
- **`extract --auto` shared-overlay reconciliation** — the dev/eval-commands-shouldn't-write-production-
  vocabulary span (ADR 0100 tracked-for-architect); a `RunKind` write-guard is the candidate fix.
- **Live `httpx` external-source clients not wired into the production CLI** — owner a later config/keys
  task; production stays hermetic (ADR 0101).
- **`Finding`-base severity** — the cross-check warn-on-human-edit future item (ADR 0100; `seams §2`).
- **Extended-thinking per-agent** — eval-driven; the ADR-0098 trajectory hook is ready.
- **codebuild fixture hygiene** — addressed *in this task* (decision 7), no longer parked.
