# 0091 — Marker-aware refinement path→type resolver; `apply_field_patch` generic over `SpecEnvelope`

**Date:** 2026-06-17
**Phase:** 2 (Task 4 — Planner-Jury + refinement extension + route-back)
**Architecture refs:** `architecture.md §1.5`/`§1.6` (framework owns mechanical safety; the LLM never
authors framework-owned fields), `schema.md §4.8` (lab-level reproducibility is *derived*). Lands the
**"recorded, not built"** marker-aware resolver of **ADR 0087** (and `dev/phase-2-seams.md §2`),
whose stated trigger — "the first time refinement runs over a `LabManifest`" — fires in this task.
Builds on ADR 0054 (the patch mechanism) and ADR 0085 (the two neutralization seams).

## Context

The Phase-1 refinement patch path rejected a patch that *targets* a framework-owned field via
`framework_owned_path_buckets()` — a **flat positional denylist** generated from the inline
`FrameworkOwned` markers: a marker on the `AttackSpec` root matched a top-level segment, a marker on
a nested model (`Provenance`, `CveReference`) matched a target *leaf name*. ADR 0087 proved this is
correct for `AttackSpec` only because its one ambiguous name, `reproducibility`, is owned **only at
the top level** (`AttackSpec.reproducibility`, derived) while `chain.chain_steps[*].reproducibility`
is authored content — position separated them.

Task 4 makes the Planner↔Planner-Jury revise loop patch a **`LabManifest`**, the named expiry point:
`CoreBlock.reproducibility` (owned, derived) and `phases[*].steps[*].reproducibility` (authored
content) are **both nested with the same leaf name**, so a positional denylist cannot tell them
apart — it would either wrongly reject every per-step content patch or wrongly accept a forged
lab-level block. `apply_field_patch` was also hardcoded to `AttackSpec.model_validate`, so it could
not assemble a manifest at all.

## Decision

1. **A marker-aware path→type resolver** — `resolve_framework_owned(root, segments)` in
   `framework/provenance_guard.py` — walks the artifact **schema** (field annotations, not a data
   instance) from `root` to the exact `(model, field)` a `field_path` names, unwrapping `T | None`,
   `list[T]` (via `[i]` index segments), and the `Provenance[T]` generic, then reads **that field's**
   inline `FrameworkOwned` marker. It returns `False` for a path that does not resolve in the schema
   (or resolves ambiguously through a multi-model union mid-walk) — the deep-set then raises the
   precise `RefinementPathError`; the guard's sole job is to reject an owned *target*.

2. **It replaces the flat positional check for *both* artifacts**, and `framework_owned_path_buckets()`
   + its `_reachable_models` helper are **deleted** (not kept as an AttackSpec fast-path). One
   mechanism, no drift between two framework-owned computations. The resolver is behaviour-equivalent
   to the flat buckets on every AttackSpec case (the existing AttackSpec patch-path tests pass
   unchanged) and strictly more precise on the manifest. **[Corrected — see the Amendment below: as
   first written the resolver was *terminal-only* and was NOT behaviour-equivalent; it dropped the
   flat check's leading-segment whole-subtree protection. The made-ancestor-aware version is.]**

3. **`apply_field_patch` is generic over the versioned-artifact base** —
   `apply_field_patch[S: SpecEnvelope](prior: S, patch) -> S` — re-validating via
   `type(prior).model_validate`, so the same convergent deep-set + whole-spec re-validation (R2)
   serves both `Extractor.refine` (AttackSpec) and `Planner.refine` (LabManifest).

4. **`CoreBlock.reproducibility` is marked `Annotated[ReproducibilityBlock, FrameworkOwned()]`** at
   its definition site — the inline-declaration extension ADR 0087 anticipated ("mark the manifest's
   framework-owned fields inline at their definition sites then"). The marker is the manifest's
   nested counterpart to `AttackSpec.reproducibility`.

The patch-value shape scrub (`neutralize_patch_provenance` / `_scrub_node`) is **unchanged** and
remains as spec-agnostic defence-in-depth behind the path check (it already recognized `Provenance`
/ `CveReference` by shape, which works for the manifest's content fields too).

## Consequences

- `provenance_guard.py`: `resolve_framework_owned` + `_list_element_annotation` added;
  `framework_owned_path_buckets` + `_reachable_models` removed; `_models_in_annotation` retained
  (the resolver uses it); `functools.cache` import dropped (only the deleted bucket fn used it).
- `refinement.py`: `apply_field_patch` generic over `SpecEnvelope`; `_reject_framework_owned_path`
  takes the artifact `model_type` and calls the resolver.
- `manifest.py`: `CoreBlock.reproducibility` marked `FrameworkOwned`; `Annotated` +
  `framework_owned` imports added.
- Tests: the two AttackSpec marker-drift pins are rewired off the deleted helper onto
  `framework_owned_fields` directly (same drift coverage); new resolver unit tests
  (AttackSpec owned/authored separation, the manifest owned-vs-authored collision, unresolvable →
  `False`) and LabManifest patch tests (convergence/byte-identity, `core.reproducibility` rejected,
  per-step `reproducibility` allowed). Full suite green (819 passed / 1 skipped).
- **No architecture-doc change** — this implements `architecture.md §1.6` and ADR 0087; the manifest
  reproducibility field was already specified as derived (`schema.md §4.8`).

## Alternatives considered

- **Keep `framework_owned_path_buckets` as an AttackSpec fast-path, resolver for the manifest only.**
  Rejected: two framework-owned computations is exactly the drift ADR 0087 closed; the resolver is
  behaviour-equivalent on AttackSpec, so the flat helper earns nothing but a divergence risk.
- **A second, manifest-specific `apply_manifest_patch`.** Rejected: the deep-set + whole-spec
  re-validate is identical; the only artifact-specific part (the validate target + the owned check)
  is exactly what the generic `type(prior)` + the resolver already abstract.
- **Defer the resolver and reject all `reproducibility`-leaf patches on the manifest.** Rejected: it
  would wrongly reject legitimate authored per-step content patches — a correctness hole, not a
  conservative default.

## Amendment (2026-06-17): the resolver was terminal-only — made ancestor-aware

**Found during the Task-5 pre-work resolver audit** (an adversarial verification pass + a direct
read of the resolver against real `HEAD`). The resolver as first landed checked the
`FrameworkOwned` marker **only on the terminal `(model, field)`** the path named. The deleted flat
check had two rules — `head in root_names` (a *leading* segment that is a root-owned field rejects
the **whole sub-tree**) **and** `leaf in leaf_names`. Decision point 2 above carried over only the
leaf rule; the leading-segment rule was silently dropped. So the resolver was **not**
behaviour-equivalent on AttackSpec — it **regressed** the whole-sub-tree protection for any patch
that *descended into* a root-owned container:

- `material_discrepancies[0]` and `material_discrepancies[0].source_of_record` — trailing index /
  sub-field of the owned `material_discrepancies` list → the walk fell through to `return False`.
- `reproducibility.classification_lab_level` (AttackSpec) and `core.reproducibility.<sub>`
  (LabManifest) — a sub-field of the owned (framework-*derived*) reproducibility block.

Each **bypassed** `_reject_framework_owned_path`, so a jury-`revise` patch could author a
framework-owned field via the refine path — the exact `§1.6` / ADR 0085 forge hole the guard exists
to close. The patch-value shape scrub (`_scrub_node`) does **not** compensate: it only resets
`Provenance`/`cve_id`-shaped nodes, so a bare-enum `new_value` (e.g. `"full"`) and a
`MaterialDiscrepancy`'s own non-`Provenance` content survive untouched. (Confirmed against
`git show 6fc47f5^`: the old check's `if head in root_names: raise`.)

**Fix.** `resolve_framework_owned` now reads ownership at **every** string segment — terminal *or*
ancestor: if any segment names a field marked `FrameworkOwned` on its carrier model, the path
*targets or descends into* an owned field and is rejected. Because ownership is still read off each
**exact** model, the precision ADR 0091 was built for is preserved: the authored
`phases[*].steps[*].reproducibility` (`StepBlock` unmarked) stays a legitimate target, while the
owned `core.reproducibility` / `AttackSpec.reproducibility` and *all their sub-fields* are rejected.
This restores the flat check's whole-sub-tree protection without re-introducing its leaf-name
collision (the reason the resolver replaced it in the first place).

**Tests** (`tests/unit/framework/test_refinement.py`, RED→GREEN): resolver-level — descent into the
owned AttackSpec `material_discrepancies` (trailing index + sub-field) and `reproducibility` sub-field
→ `True`; manifest `core.reproducibility.classification_lab_level` → `True`; the over-reject guard —
`phases[0].steps[0].reproducibility.classification` stays `False`. End-to-end —
`apply_field_patch` on the always-reachable `core.reproducibility.classification_lab_level` raises
`RefinementPathError` before the deep-set. `just verify` green (848 passed / 1 skipped).
