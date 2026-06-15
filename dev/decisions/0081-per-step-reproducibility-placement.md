# 0081 — Per-step reproducibility lives on the manifest `StepBlock`; lab-level derives from the AttackSpec

**Date:** 2026-06-16
**Phase:** 2 (reopens Task 1 — LabManifest schema; pins Task 2 — reproducibility derivation; feeds Task 0 — doc reconciliation)
**Architecture refs:** `architecture.md §0.7` (emergent lab class; "the Planner carries these classifications forward into the Manifest without modification"), `§1.1` (manifest is the SSOT for "reproducibility decisions"; lifecycle table lists "per-step reproducibility" in the Planner's skeleton), `§1.5` (framework computes; LLMs don't), `schema.md §4.8` (lab-level reproducibility is *derived* from "all required chain steps"; required = not dropped to `not_reproducible`), `agents.md §5.9` (Per-phase Generator inputs/tools), `§5.18` (tool-inventory matrix). **First-consumer-infeasibility escalation** under the Task-1 manifest lock (`dev/phase-2-agent-brief.md` lines 156–163), architect-ruled 2026-06-16.

## Context

Task 1 built `StepBlock` (`schemas/manifest.py`) per `schema-details.md §5.6`, which has **no** per-step reproducibility field; lab-level `reproducibility` reuses the AttackSpec `ReproducibilityBlock` on `CoreBlock` (derivation deferred to Task 2). A review flagged this as a drift against the brief (Task 2 inputs assume "per-step `ReproducibilityTier` fields exist"; Task 3 test #2 asserts "input tiers == manifest tiers") and proposed resolving it by editing `architecture.md §0.7`/`§1.1` *down* to match `schema-details.md §5.6` — i.e. "per-step reproducibility lives only on the AttackSpec; the manifest carries the derived lab-level only" (Reading B).

Two findings reverse that conclusion:

1. **Authority gradient.** The "per-step reproducibility is carried into the manifest" reading is asserted by the *architecture* tier — `architecture.md §0.7` (line 118), `§1.1` (lines 143, 152), `pipeline.md` (line 161), `agents.md §5.7` (lines 182, 235), `§5.8` (line 262) — and by the brief. Only `schema-details.md §5.6` (implementation tier) + the Task-1 code omit it. Per the project's authority order (`architecture.md` > other `docs/` > brief > code), `schema-details.md §5.6` is the *incomplete* doc; the architecture is the contract. Reading B inverts that order.

2. **The Per-phase Generator contract is decisive.** `agents.md §5.9` requires the Per-phase Generator to implement each step "at its declared reproducibility tier" (failing the stage otherwise). That agent is manifest-driven and isolated: its inputs are the phase block, `core`, the phase's blog excerpts, value types, and facets; its AttackSpec access is **"partial" = its phase's prose excerpts only** (`§5.18` matrix + the `partial` legend), **not** the structured `ChainStep.reproducibility`. Under Reading B the one agent contractually required to act on the per-step tier could read it nowhere. The lab-level rollup the review pointed at does not carry per-step granularity, so it cannot substitute.

The reproducibility *derivation domain* is **chain steps**, not manifest steps: `schema.md §4.8` rolls up over "all required chain steps," and a chain step may become a phase, a step, a `lab_resource`, a prereq, or be dropped (`agents.md §5.7`, `schema.md` line 26). So a rollup computed over manifest `StepBlock`s would silently omit the tiers of chain steps that became resources/prereqs (e.g. one `demonstration_only` chain-step-turned-prereq among otherwise-`full` steps would mis-classify the lab `full` instead of `mixed`).

## Decision

**Reading A.** Per-step reproducibility is authored once by the Extractor on the AttackSpec (`ChainStep.reproducibility: PerStepReproducibility`, unchanged) and is *carried forward into the manifest*, with two distinct consumers given two distinct homes:

1. **`StepBlock` gains a per-step reproducibility field** — for the Per-phase Generator. The Planner carries the tier forward from the chain step(s) the manifest step implements, **without modification** (`§0.7`: carrying-forward is explicitly not re-evaluation). The field reuses the AttackSpec's `PerStepReproducibility` block (classification + provenance-wrapped caveats/why), mirroring how `CoreBlock.reproducibility` reuses the AttackSpec's `ReproducibilityBlock`. This reopens the Task-1 schema — the legitimate first-consumer-infeasibility exception, not an implementer's call.

2. **Lab-level (`CoreBlock.reproducibility`) is derived from the AttackSpec, not from manifest steps** — Task 2 sources `chain.chain_steps[*].reproducibility.classification`, over **all non-dropped chain steps** (`§4.8` literal: required = everything except `not_reproducible`, including chain steps that became resources/prereqs). Framework code, never the Planner (`§1.5`). Sourcing from the AttackSpec makes the rollup complete by construction and sidesteps the manifest-step granularity gap entirely.

3. **No carry on `implements_chain_steps`, `LabResourceBlock`, or `PrereqBlock`.** Enriching `PhaseBlock.implements_chain_steps` to carry tiers (the review's counter-proposal) was rejected: it still misses chain steps that became resources/prereqs (those appear in no phase's `implements_chain_steps`), so it does not close the gap it was meant to close, and it is too coarse for the per-*step* Generator need (a phase can mix tiers). Nothing downstream consumes a per-resource or per-prereq tier, so none is stored; the rollup's completeness comes from sourcing the AttackSpec, not from a manifest carry.

4. **Doc direction follows the gradient: edit `schema-details.md`, not the architecture.** `schema-details.md §5.6` (and `§5.5` context) gains the `StepBlock` field; the architecture docs stay as written (they were already correct). This edit is architect-approved (same pattern as the ADR-0079 `PhaseImplementation.path` edit to `schema-details.md §5.5` / `schema.md §4.5` during Task 1).

5. **A `StepBlock → chain_step` back-reference is deferred to Task 3.** It is needed only if Layer 2 wants a carry-integrity check (`StepBlock` tier == referenced `ChainStep.reproducibility`); it is not load-bearing for Task 1.

## Doc-reconciliation follow-ups (Task 0, architect)

Three adjacent drifts in the *higher-authority* docs must be reconciled in the same pass, or they re-author the tier downstream. These are architecture/agents edits — **not** done here; recorded for Task 0:

- **D2 — who derives lab-level.** `schema.md §4.8` ("the Planner applies the rule mechanically") and `agents.md §5.7:201` read as Planner-authored. Lab-level derivation is *framework* code (`§1.5`, Task 2). → framework derives; the Planner does not.
- **D3 — who applies the §4.20 ladder.** Three-way muddle: `architecture.md §0.7:116` ("the generator… applies the preference ordering") vs `§0.7:118` (Extractor assigns / Planner carries) vs `agents.md §5.7:201` ("for each chain step, [the Planner] applies the ladder"). → the Extractor assigns the tier once; the Planner carries forward + decides keep/drop + grouping; the Generator implements. The `§0.7:116` and `§5.7:201` "applies the ladder" lines are stale.
- **D4 — re-tiering.** `agents.md §5.7:214` ("`not_reproducible` chain steps… become `demonstration_only` phases") contradicts `§0.7:118` "without modification." Architecture wins; `§5.7:214`'s upgrade clause is stale.

## Alternatives considered

- **Reading B** (per-step on the AttackSpec only; manifest carries lab-level only; edit the architecture down to match `schema-details.md §5.6`) — rejected: starves the Per-phase Generator (`§5.9`), and inverts the authority gradient by editing the contract to match the implementation.
- **Roll up lab-level over manifest `StepBlock`s** — rejected: omits chain steps that became resources/prereqs, mis-classifying `mixed` labs as homogeneous (`§4.8` derivation domain is chain steps).
- **Enrich `PhaseBlock.implements_chain_steps` to `list[{chain_step_id, reproducibility}]`** — rejected: incomplete (misses resource/prereq chain steps) and over-coupled for the per-step Generator need; the simpler `StepBlock` field plus AttackSpec-sourced rollup covers both consumers.
- **Restrict the rollup domain to chain steps that became phases/steps** (exclude resources/prereqs) — rejected: deviates from `§4.8`'s "all required chain steps" wording and would itself require an architecture edit; the architect ruled to follow `§4.8` literally.

## Consequences

- **Task 1 reopens** to add the `StepBlock` per-step reproducibility field + tests (per-step round-trip; `extra="forbid"`; a multi-phase manifest carrying mixed tiers). The rest of the Task-1 schema is unchanged.
- **Task 2** derives `CoreBlock.reproducibility` from the AttackSpec `chain.chain_steps[*].reproducibility` over all non-dropped chain steps; pure framework function (`§4.8` any-heterogeneity-mixed rule), `n/a` handling for the all-dropped case (the all-dropped refusal is the Planner's `cannot_plan` job, Task 3, not Task 2's).
- **Task 3** asserts the Planner carries per-step tiers forward unchanged into `StepBlock` (the brief's "input tiers == manifest tiers" test #2 is now satisfiable), and never re-evaluates or re-tiers.
- **The brief becomes internally consistent** — Task 2 inputs and Task 3 test #2 were written for Reading A; no brief rewrite is needed (confirming signal that A is the intended reading).
- **Task 0 (architect)** picks up the D2/D3/D4 reconciliations plus the `schema-details.md §5.6` field edit.
- `schema-details.md §5.6` was the incomplete doc; the architecture (`§0.7`, `§1.1`, `pipeline.md`) was right and is unchanged.
