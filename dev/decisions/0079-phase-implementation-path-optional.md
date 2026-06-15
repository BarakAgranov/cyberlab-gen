# 0079 — `PhaseImplementation.path` is optional (Planner skeleton; Generator materializes)

**Date:** 2026-06-15
**Phase:** 2 (Task 1 — LabManifest schema)
**Architecture refs:** `architecture.md §1.1` (the manifest is one incrementally-built model — no
draft/final split; structural validation runs after every stage), `agents.md §5.7` (the Planner
emits phases "without `implementation.path`"), `agents.md §9.4` (path is mechanically derived from
the phase `id`; "the Generator does the conversion"). Supersedes the `path: NonEmptyString`
required-no-default shape in `schema-details.md §5.5`.

## Context

`schema-details.md §5.5` showed `PhaseImplementation.path: NonEmptyString` — **required, no
default**. But Phase 2's deliverable is a Planner-stage manifest with **no code, therefore no
paths** (`agents.md §5.7`), and Layer 1 (static schema validation) runs after every stage
(`architecture.md §1.1`). A required `path` means the Planner's own output fails its own Layer-1
validation — Phase 2 could not validate its deliverable unless the Planner fabricated paths, which
violates `§5.7` and the `§9.4` rule that the Generator owns the `id` → path conversion.

Three normative statements agree the **Generator** owns `path` (`agents.md §5.7`, `§1.1`, `§9.4`);
`schema-details.md §5.5`'s required-no-default is the outlier. Implementation yields to architecture
(authority gradient). The honest invariant is **`path` ⟺ file**: a skeleton has neither, a shipped
lab has both, the Generator creates them together. A `path` pointing at a not-yet-existing file is
exactly the looks-complete-but-isn't state the architecture avoids.

## Decision

1. **`path: NonEmptyString | None = None`** on `PhaseImplementation`. The Planner leaves it `None`;
   the field is consistent with the rest of the manifest, which already expresses
   stage/condition-dependent presence via optional-plus-rule (`InputBlock.default`,
   `LabResourceBlock.discovery`, the `ProducesWorldState` identifier XOR, the conditional
   `PrereqBlock` commands).
2. **Lifecycle.** Planner → `path = None`. Per-phase Generator (Phase 3) → materializes the file and
   the path together via the `id` → path derivation, storing it in the shipped manifest.
3. **Enforcement seam (Phase 3).** Layer 2 (post-generation) enforces `path == derive(id)` **and**
   file-exists. Recorded as a `TODO(phase-3)` on `PhaseImplementation` and in `schema-details.md
   §5.5` so the check is not lost. Layer 1 does **not** enforce path presence.
4. **Docs reconciled** (architect-approved): `schema-details.md §5.5` updated to optional + the
   lifecycle note; `schema.md §4.5`'s example annotated that its populated `path:` is the
   post-Generator state, not Planner output.

## Alternatives considered

- **Keep `path` required (follow `schema-details.md §5.5` literally)** — rejected: it makes the
  Planner's own deliverable un-validatable and forces fabricated paths, contradicting `§5.7`/`§9.4`
  and the `path` ⟺ file invariant.
- **Defer to Task 3 as a "Planner behavior" concern** — rejected: the decision is forced *in the
  schema* now, because the schema is what Layer 1 validates the Planner's output against.

## Consequences

- The Planner (Task 3) emits phases with `implementation.path = None`; its output passes Layer 1.
- The Per-phase Generator (Phase 3) is the sole writer of `path`, paired with the file it creates.
- Layer 2 gains a Phase-3 `path == derive(id)` + file-exists check (the enforcement seam).
- Pinned by `test_phase_implementation_path_optional` (a path-less implementation validates and a
  manifest carrying one round-trips).
