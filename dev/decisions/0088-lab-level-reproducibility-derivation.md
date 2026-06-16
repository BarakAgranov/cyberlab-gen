# 0088 — Lab-level reproducibility derivation: the rule, the all-dropped edge, and `overall_assessment`

**Date:** 2026-06-16
**Phase:** 2, Task 2 (lab-level reproducibility derivation)
**Architecture refs:** `schema.md §4.8` (the any-heterogeneity-mixed rule; "required = not dropped to `not_reproducible`"; the framework derives {classification, caveats, derivation_trace} mechanically), `§4.9` (the closed five-value `ProvenanceSource` vocabulary — no framework source), `architecture.md §1.5` (framework computes; LLMs don't), `§0.7` (emergent lab class). Builds on **ADR 0081** (per-step reproducibility is authored on the AttackSpec and carried forward; lab-level derives from the AttackSpec's chain steps), **ADR 0087** (`AttackSpec.reproducibility` is `FrameworkOwned`, reset on first run). Schema widening of the Task-1 `ReproducibilityBlock` is **architect-approved** (2026-06-16).

## Context

Task 2 makes the lab-level rollup real — the `attack_spec.py:502` comment ("Framework-DERIVED … Reset today, derive when the rollup step exists") names exactly this task. Three things need pinning before code:

1. **The rule and its domain.** `schema.md §4.8`: `required` = chain steps not dropped to `not_reproducible`; all required share one tier → that tier; required span ≥2 tiers (any proportions) → `mixed`. ADR 0081 fixed the *domain* as the **AttackSpec's `chain.chain_steps`** (not manifest `StepBlock`s, not `alternative_paths`) — sourcing from the AttackSpec makes the rollup complete by construction, since a chain step that became a `lab_resource`/prereq still contributes its tier.

2. **The all-dropped / `n/a` edge.** `§4.8`'s rule text covers only the all-same and span-multiple cases. It is silent on the case where *every* chain step is `not_reproducible` (no required steps remain). The brief calls this "the `n/a` handling"; ADR 0081 noted "the all-dropped *refusal* is the Planner's `cannot_plan` job, Task 3, not Task 2's."

3. **`overall_assessment` has no honest framework source.** The whole `ReproducibilityBlock` is `FrameworkOwned` (ADR 0087, reset on first run), so an LLM never authors it. But `overall_assessment: ProvenanceString` is *required*, and `§4.9`'s source vocabulary is exactly `{blog_explicit, external_api, llm_inference, unknown_from_blog, user_provided}` — none means "the framework computed this." This would be the first framework-authored `ProvenanceString` in the codebase; the block cannot be assembled honestly without resolving it.

## Decision

1. **Pure rule function.** `framework/reproducibility.py::classify_lab_level(tiers) -> ReproducibilityLabLevel` implements `§4.8` exactly: filter out `not_reproducible`; if the remainder is one tier → that tier's lab-level value; if it spans ≥2 → `mixed`. Pure, no I/O. Framework code, never the Planner (`§1.5`).

2. **All-dropped / empty → `not_reproducible`.** When no required steps remain (every step `not_reproducible`, or — defensively — an empty list), the function returns lab-level `ReproducibilityLabLevel.NOT_REPRODUCIBLE`. This is the only coherent value: the enum carries it, and `§4.8`'s "required = not dropped to `not_reproducible`" implies a lab with nothing left is itself not reproducible. **The refusal stays Task 3's** — the Planner reads this classification and decides `cannot_plan`; Task 2 only classifies. The empty-list path is defensive only: `ChainBlock.chain_steps` is `Field(min_length=1)`, so a real spec always has ≥1 step.

3. **`overall_assessment` widened to optional; left `None` in v1.** `ReproducibilityBlock.overall_assessment` becomes `ProvenanceString | None = None` (amending the Task-1 block, architect-approved). The framework derives **only** the three fields `§4.8` names as framework-derived — `classification_lab_level`, `caveats`, `derivation_trace` — and leaves `overall_assessment = None`. A required prose field over-specified the framework's job; `None` honestly means "no prose authored yet," and a later prose-producer (Docs Generator / architect) fills it with a real source then. This is reversible and touches neither `§4.9` nor the forge surface.

   - **Rejected: add `ProvenanceSource.FRAMEWORK_DERIVED`.** It re-expands the shared-`Provenance` forge surface that ADR 0085/0087 (D5) just closed — a framework-authorship signal available on *every* `ProvenanceString`, and *worse* than `framework_enriched` (which has the `external_api` coupling to validate against; a bare `framework_derived` source has no field-position context at the `Provenance` validator, so it is structurally hard to guard). Not worth it for one prose field.
   - **Rejected: defer the whole block assembly.** Under-delivers ADR 0081's "Task 2 derives `CoreBlock.reproducibility`"; the three framework-derived fields are exactly what `§4.8` assigns and ship now.

4. **`caveats` and `derivation_trace` are mechanically templated.** `caveats` lists one line per distinct tier present, with proportions over all chain steps (`§4.8`: "surfaces which tiers are present and in what proportions"). `derivation_trace` lists each step's tier (marking `not_reproducible` steps as excluded from the rollup) plus the resulting classification. Both are deterministic, no LLM judgment (`§1.5`).

5. **`FrameworkOwned` marker untouched; wiring is Task 6.** `derive_lab_reproducibility(spec) -> ReproducibilityBlock` is delivered as a pure callable. The Planner (Task 3) and `plan` pipeline (Task 6) do not exist yet; the actual post-Planner graph insertion is Task 6's (it owns wiring). The ADR-0087 reset stays the active guard on the field; `FrameworkOwned`'s `mechanism=` stays unpopulated (no consumer dispatches on it yet).

## Alternatives considered

- **Roll up over manifest `StepBlock`s instead of AttackSpec chain steps** — rejected by ADR 0081 (omits chain steps that became resources/prereqs, mis-classifying `mixed` labs as homogeneous). Reaffirmed here.
- **Include `alternative_paths` in the rollup** — rejected: alt paths are captured-not-generated in v1 (`schema.md §4.8`); their `reproducibility_summary` is authored, not part of the canonical lab's reproducibility.
- **All-dropped → `mixed` or raise** — rejected: `mixed` is false (nothing is reproducible at any tier), and raising would move the refusal decision out of the Planner (`cannot_plan` is Task 3's, per `§1.5` control-flow ownership).
- **`FRAMEWORK_DERIVED` source / keep `overall_assessment` required** — rejected per Decision 3.

## Consequences

- New module `framework/reproducibility.py` with `classify_lab_level` + `derive_lab_reproducibility`, re-exported from `framework/__init__.py`.
- `ReproducibilityBlock.overall_assessment` is now optional. No production code read it (only test fixtures, which still pass with a value). Existing AttackSpec on-disk shape is unchanged (the field was always serialized; `None` serializes as absent/null and round-trips). Docs updated: `schema-details.md §4.7` (field contract) and a note on `schema.md §4.8`.
- **Task 3** (Planner) carries per-step tiers forward into `StepBlock` unchanged; after it emits, the framework calls `derive_lab_reproducibility` to populate `core.reproducibility`. **Task 6** wires that call into the `plan` graph as the post-Planner framework step.
- The all-dropped case yields a well-defined `not_reproducible` classification that Task 3 turns into a `cannot_plan` refusal; Task 2 carries no refusal logic.
