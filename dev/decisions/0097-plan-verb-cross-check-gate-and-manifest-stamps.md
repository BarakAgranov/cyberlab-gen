# 0097 — The `plan` verb: the semantic-cross-check ship gate, generalized manifest stamps, and the scoped route-back

**Date:** 2026-06-17
**Phase:** 2 (Task 6 — `plan` verb + orchestrator wiring + persistence; the slice end-to-end)
**Deciders:** maintainer (architect — ruled the four forks), implementing agent
**Architecture refs:** `pipeline.md §3.2.6`/`§3.2.7` (the Planner stages), `§3.1` (deterministic state
machine), `architecture.md §1.5`/`§1.6` (LLM/framework split; mechanical safety), `§2.1` (the `plan`
verb — ADR 0096), `validation.md §6.5` (the semantic cross-check). Builds on ADR 0086/0087 (the
framework-owned-field guard), ADR 0068 (one shared persistence home), ADR 0065/0069 (billed-model /
schema-version stamps), ADR 0091/0092/0093/0094 (the plan coordinator + cross-check validator), ADR
0056 (the iteration caps), ADR 0046 / `coding-conventions.md §5.5` (descriptive naming).

## Context

Task 6 makes the Phase-2 slice runnable: `cyberlab-gen plan <attack-spec.yaml>` runs Planner →
Planner-Jury → semantic cross-check and persists the run, producing a Layer-1+Layer-2-valid `lab.yaml`.
The maintainer ruled four forks before implementation; this ADR records how they were built (the forks
were pre-decided, so this is an implementation record, not a fresh decision — with one
implementation-level design call called out in §3).

## Decision

### 1. Linear graph reuse (Fork 1)

The plan pipeline reuses the existing linear `plan_orchestrator` graph + the shared `graph_support`
trace wrappers. **No `Stage`/`Node` abstraction, no reducer channels** — the pipeline is still
sequential; that refactor lands at the first *parallel* node (the Phase-3 Generators; seams ③.1).

### 2. One generalized stamp home; the billed-model invariant is not copied (Fork 2)

`stamp_framework_provenance` is now generic over `SpecEnvelope` and **dispatches on artifact type**:
an `AttackSpec` is stamped on `extraction_metadata.model` (billed Extractor model); a `LabManifest` on
`core.generation.{model, tool_version, timestamp}` (billed **Planner** model + package version + stamp
time). Both read the one `billed_model(ledger, agent_label=…)` reader and the one generic
`stamp_spec_version`. A thin `persist_plan_artifacts` is the plan-side sibling of
`persist_pipeline_artifacts`; the two are separate **only** because their in-flight state shapes differ
(`PipelineState` vs the coordinator's outcome) — they **call** the shared invariant, they do not copy
it (ADR 0068's whole point). **All three** `GenerationBlock` fields are stamped (the block is wholly
framework-owned; leaving any to the LLM would be an unguarded hole — ADR 0086's audit rule). They are
**not** marked `FrameworkOwned` inline — stamp-mechanism fields stay unmarked until `FrameworkOwned`
gains a `mechanism` field, else the reset-walk would blank them (seams §2 / ADR 0087). The verb stamps
**once** at the ship boundary (so the cwd `lab.yaml` and the run-dir mirror share one timestamp);
persistence re-stamps only a not-yet-mirrored partial/halt manifest.

### 3. The semantic cross-check is a ship gate — including on the low-confidence path (Fork 4)

The cross-check (the second mechanical validation layer) is a sync graph node after the jury. **Every
manifest the jury would ship must clear it** — a clean `approve` *and* a revise-cap-exhausted
low-confidence ship both route through `CROSS_CHECK` (the jury node sets a `pending_low_confidence`
flag and routes there; the cross-check node owns the terminal ship status). Pass → `PLANNED` /
`PLANNED_LOW_CONFIDENCE`; findings → adapted to `JuryFieldFeedback` (structured→structured) and routed
to the responsible agent via `responsible_agent_for` (Phase 2: always the Planner) for a bounded
refine on the **shared** `refinement_iterations` cap; budget-spent unresolved →
**`HALTED_SEMANTIC_CROSS_CHECK_UNRESOLVED`**.

**The implementation-level call (mine, surfaced):** routing the *low-confidence* ship through the
cross-check too (not only the clean approve). The architect's ruling — "cross-check unresolved → halt;
jury-only uncertain (cross-check passes) → ship low-confidence" — requires the cross-check to be
evaluated on the low-confidence path, so a revise-cap-exhausted manifest cannot ship behind a
confidence flag while mechanically broken. This is the §1.6 reading: a cross-check-invalid manifest is
*known-broken* (e.g. a dangling `identifier_source`), not *uncertain*, so it **never** ships behind a
confidence flag (exit-criterion 1). The terminal carries a **descriptive** name (no `LAYER2`/`L2`
ordinal token — §5.5; the node id is `semantic_cross_check`).

### 4. Route-back is a scoped, persisted, actionable terminal (Fork 3)

`ROUTE_BACK_TO_EXTRACTOR` (the Planner flagged AttackSpec incoherence it may not repair) maps to a
clean non-zero verb exit with an **actionable re-extract message** (referencing the real
`source.url`) and a **persisted `PlannerRefusal`**. The full auto cross-pipeline re-extract loop —
re-ingest the blog, re-run `extract` with the incoherence as feedback, re-plan, bounded by a new
cross-pipeline iteration cap — is **deferred** until a genuinely incoherent spec exercises it
(Task 7+); building it now against a coherent slice risks the wrong abstraction (the same
design-at-second-use discipline as Fork 1). Its shape is recorded here for when it lands.

### 5. The `plan` verb

A new top-level verb mirroring `extract`'s structure: a `PlanRunner` seam (sync at the verb boundary;
the async pipeline is inside `PipelinePlanRunner`), a `plan_runner_factory` test seam, run-store
persistence on **every** exit path (ADR 0039), and the spec_kind load gate to reject a non-`AttackSpec`
input cleanly. **Non-interactive** in Phase 2 — the post-Planner interrupt is Task 8. The
`PlanPipelineStatus → RunStatus` bridge is lossy (a *third* status taxonomy consumer; the
consolidation is tracked debt, seams §2), mapped pragmatically with the precise status carried in
`halt_reason`.

## Consequences

- The slice is runnable end-to-end with fakes (the verb wiring + persistence are tested without a
  provider); the single real paid `plan` run on the codebuild fixture is the maintainer's
  (eval-is-user-run). The committed codebuild AttackSpec fixture makes that run reproducible with no
  new paid `extract`.
- The manifest's framework-owned fields are guarded on every persist path by construction; the
  billed-model invariant has one home across both artifacts.
- A cross-check-invalid manifest can never reach `lab.yaml` — mechanical correctness gates the ship,
  confidence does not override it.

## Deferred (recorded, not built)

- The auto cross-pipeline re-extract loop behind `ROUTE_BACK_TO_EXTRACTOR` (§4) — Task 7+.
- The post-Planner interactive interrupt + `--interactive`/`--auto` on `plan` — Task 8.
- `Stage`/`Node` + reducer channels — the first parallel node (Phase-3 Generators; seams ③.1).
- `PlanPipelineStatus`/`RunStatus` taxonomy consolidation — seams §2.
- `StepBlock.reproducibility` carry-integrity (a cross-*artifact* Layer-2 check) — still open (seams §2).
- A shared home for `_code_version` (duplicated thinly in `cli/extract` and `cli/plan`) — minor.
