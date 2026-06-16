# 0090 — The Planner emits the full `LabManifest`; the framework derives `core.reproducibility` in `plan()`

**Date:** 2026-06-16
**Phase:** 2 (Task 3 — the Planner; [CONTRACT])
**Architecture refs:** `architecture.md §1.5` (LLMs don't compute their own behaviour; the
framework derives), `§0.7` (emergent lab class — per-step tiers roll up to the lab level),
`schema.md §4.8` (lab-level reproducibility is *derived*, not authored), `agents.md §5.7`.
Builds on ADR 0081 (per-step placement), ADR 0086/0087 (framework-owned-field guards), ADR 0088
(the derivation function + optional `overall_assessment`).

## Context

`CoreBlock.reproducibility` is a **required** field on the manifest, but it is
**framework-derived**: the framework rolls the AttackSpec's per-step tiers up to the lab level
(`derive_lab_reproducibility`, ADR 0088), and the Planner must not author it (`§1.5`/`§0.7`). The
question for the Planner's LLM call is whether the emitted output schema should *contain* that
field at all.

Two mechanisms from the ADR-0086 guard catalogue apply:

1. **Derive-at-seam** — the LLM emits the full `LabManifest`; a deterministic framework step
   overwrites `core.reproducibility` with the derived block before the manifest is used. (This is
   the same family as the Extractor emitting a full `AttackSpec` whose framework-owned fields are
   reset/stamped at the persist seam.)
2. **Absent-from-LLM-schema** (ADR 0086 mechanism #4) — the LLM's output schema is a *reduced*
   draft that omits `core.reproducibility`; the framework assembles the full manifest. The LLM
   literally cannot author the field.

## Decision

**Derive-at-seam, in `plan()`.** The Planner's output schema is the full `LabManifest`. After the
emit, `Planner.plan()` runs a deterministic framework finalize that overwrites
`core.reproducibility = derive_lab_reproducibility(attack_spec)` (ADR 0088) and returns the
finalized manifest. The field **stays required** — it is *computed*, not deferred; a shipped
manifest always carries it (unlike Task-2's `overall_assessment`, which the framework legitimately
leaves `None`).

The finalize lives *inside* `plan()` so the raw LLM output is never exposed and the overwrite
always runs — pinned by a test that feeds a mock manifest with a **deliberately wrong**
`core.reproducibility` and asserts the returned value is the *derived* one (and that the LLM's
prose `overall_assessment` is dropped to `None`, since the framework owns the whole block).

`spec_version` and `GenerationBlock.model` are the manifest's *other* framework-owned fields; they
are stamped at the **persist seam** (`state/run_persistence.py` — `stamp_spec_version` /
`stamp_billed_model`), wired in Task 6, exactly as the Extractor defers its own spec-version /
billed-model stamps to persistence (ADR 0068/0069; ADR 0086 marks the manifest `spec_version`
stamp "prospective (Planner)"). `plan()` does **not** re-implement those stamps — the billed-model
invariant must not be copied a third time.

## Alternatives considered

- **Absent-from-LLM-schema (reduced `PlannerManifestDraft` / `CoreBlockDraft`)** — rejected. It is
  the stronger isolation in principle (the LLM cannot author the field), but it duplicates the
  manifest schema into a parallel draft — and the manifest is the **actively-evolving central
  artifact of this phase**, so the draft is a high-drift mirror, exactly the kind of forge/mirror
  surface the D5 / ADR-0085–0087 arc just spent itself closing. For **one** derived field the
  duplication is not worth it. Derive-at-seam is also consistent with how the manifest's other
  framework-owned fields (`spec_version`, `GenerationBlock`) are overwritten at their seam.
- **Defer the derive to Task 6 (a separate post-Planner node)** — rejected. Task 3's exit
  criterion is a *Layer-1-valid manifest with the correct, framework-owned* lab-level
  reproducibility; leaving the LLM's value in place until Task 6 ships a non-authoritative field
  through the Planner-Jury (Task 4). The derive needs only the AttackSpec, which `plan()` already
  holds. Task 6 owns wiring `plan()` into the `plan` verb / graph + persistence — not this
  within-stage finalize (the "graph insertion" ADR 0088 deferred).

## Consequences

- `Planner.plan(attack_spec) -> PlanResult` returns a complete, Layer-1-valid manifest whose
  `core.reproducibility` is framework-derived; the LLM-emitted value is always overwritten.
- The Planner's per-step carry-forward (`StepBlock.reproducibility`) stays *content the LLM emits*
  (ADR 0081 — authored, not framework-owned); only the **lab-level** block is framework-derived.
  The `StepBlock.reproducibility` carry-integrity check (does each step's tier match its
  AttackSpec source?) remains a Layer-2 concern (ADR 0086 / seams), not built here.
- **Trigger to revisit:** if framework-*derived* fields on the manifest multiply beyond
  `reproducibility`, the isolation of a reduced/absent-from-schema output starts to outweigh the
  draft-drift cost — re-open the mechanism choice then.
- **No `docs/` edit** — `schema.md §4.8` / `schema-details.md §5.1` already state lab-level
  reproducibility is framework-derived; this ADR records *where* (in `plan()`) and *which
  mechanism* (derive-at-seam vs absent-from-schema).
