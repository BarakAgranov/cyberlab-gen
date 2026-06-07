# 0054 — Targeted-patch refinement: the code mechanism (RefinementPatch + deep-set + revalidate)

**Date:** 2026-06-08
**Phase:** 1 (code work-stream — implements the settled docs)
**Architecture refs:** `architecture.md §1.7` (retry-vs-refinement; refinement is a targeted
patch), `schema.md §4.9` ("Refinement addressing: field paths and patches"), `pipeline.md §3.2.12`,
`agents.md §5.4`/`§5.5`, `validation.md §6.10` (static-schema → retry, non-negotiable), `CLAUDE.md`
typed-boundary. Implements **A1** of ADR 0048; builds on **A2** ([the typed `RefinementFeedback`
boundary](0048-refinement-is-targeted-patch.md)). Upholds the `architecture.md §1.5` LLM/framework split.

## Context

ADR 0048 settled *what* refinement is (a targeted patch, convergent by construction) and A2 made the
cross-stage feedback carry the structured findings. This ADR records the *how* the code implements,
including the two correctness requirements the sign-off added (R1, R2) and the one design choice the
docs left open (how the model emits the patch).

The observed failure being fixed is the jury-revise loop's quality bounce (9→6→9→10): blind full
re-extraction re-rolls every field each pass, so an unflagged field can regress on any iteration and
the loop never converges. It also costs ~10× per iteration and risks the ADR-0032 truncation ceiling
(the whole AttackSpec is one forced emit).

## Decision

**Mechanism.** On a jury `revise`, the framework hands the Extractor the prior `AttackSpec` plus the
structured `JuryFieldFeedback`; the Extractor force-emits a small **`RefinementPatch`** — a list of
`FieldPatch{field_path: str, new_value: pydantic.JsonValue}` covering only the flagged paths. The
framework `apply_field_patch` deep-sets each `new_value` onto `prior.model_dump(mode="json",
by_alias=True)` and re-validates via `AttackSpec.model_validate`. Lives in
`cyberlab_gen/framework/refinement.py` (pure framework, provider-free, unit-testable). The agent
emits content (the patch); the deterministic deep-set + revalidate + bounded retry are framework code
(`§1.5`).

- **Patch emit (the open choice):** a `field_path → new_value` map, *not* a sparse all-optional
  AttackSpec mirror (huge, drifts, set-vs-absent ambiguity, still a big emit) nor a typed union of
  subtree types (path→type resolution is the framework's job). `new_value` is `JsonValue` (untyped
  per-path at emit); the strict shape is recovered at `model_validate`. The small emit also sidesteps
  the ADR-0032 truncation ceiling instead of inheriting it. Fallback if the recursive `JsonValue`
  schema misbehaves with a provider: emit `new_value` as a YAML/JSON string the framework parses
  (not yet needed).
- **Path convention:** dotted names + **integer** list indices (`chain.chain_steps[0].description`),
  matching `GapEntry`/`MaterialDiscrepancy` and the jury fixtures. A non-integer `[id]` segment, a
  malformed segment, an out-of-range index, or a key absent from the prior spec → `RefinementPathError`
  (the invented-path guard: a patch may only *replace* an existing field, never create one). The
  broader producer-convention drift (the static-schema validator's `[step-1]` string ids) is
  canonicalized under A3/B1, not here.
- **Provenance (no side-map):** `new_value` for a content field is the whole `Provenance[T]` subtree,
  so a patch updates a flagged field's content *and* provenance together; unflagged provenance is
  byte-identical (untouched in the dump). Confirms ADR 0048's "inline provenance preserved by
  patching; D1/D2 deferred".

**Boundary (confirmed Option A).** The patch path is the **jury `revise` path only**. Static-schema
(Layer-1) failures stay **structural retry = full re-extraction** (`validation.md §6.10`,
non-negotiable); the interactive natural-language-feedback path stays full re-extraction. "validator
finding → patch" in ADR 0048 refers to Layer-2/3 *quality* findings, which do not exist in Phase 1.
The orchestrator's `extract_node` routes `REFINEMENT` feedback (with a prior spec) to
`Extractor.refine`; first-run and `STRUCTURAL_RETRY` go to `Extractor.extract`.

**R1 — bounded loop, clean termination.** The convergence property proves non-regression, not that a
flagged field gets *satisfied*. So: (a) the patch path counts as a refinement iteration (the existing
per-agent `refinement_cap` in `jury_node`), and on cap exhaustion with the field still flagged the run
ships low-confidence (the existing `pipeline.md §3.2.3` (b) path) — never a spin; (b) `refine`'s own
re-prompt loop on an unapplyable / framework-rejected patch is bounded by the same content-retry
budget as `extract` and raises `ExtractionError` on exhaustion.

**R2 — whole-spec re-validation.** `apply_field_patch` re-validates the *entire* assembled spec
(`model_validate` runs every cross-field invariant: provenance rules, scope consistency, monotonic
step numbers, `extra="forbid"`), and `refine` re-runs the mechanical content checks
(search-before-claim / MITRE / CVE) over the **whole** patched spec, so a patch cannot introduce an
undetected cross-field problem in an unflagged field's relationship to a patched one.

## Consequences

- New `framework/refinement.py` (`FieldPatch`, `RefinementPatch`, `apply_field_patch`,
  `RefinementPathError`); new `Extractor.refine`; `extract_node` + `_ExtractorLike` routing; a jury
  prompt note pinning the dotted+integer `field_path` convention. `extractor.py` imports
  `framework.refinement` lazily (function-level) to avoid the agents↔framework load-time cycle.
- The A2-era end-to-end test that pinned "feedback renders into the re-extract prompt" is superseded
  by the routing test (structured feedback reaches `refine`); structural-retry and clean-approve tests
  gained explicit "never patches" assertions. Convergence/non-regression, R1, and R2 each have a test.
- Not verifiable without the provider-backed eval: that the live `RefinementPatch`/`JsonValue` forced
  emit behaves (schema-generation is unit-checked; the actual emit is the user's eval run), and that
  the patch path empirically converges + ships on the codebuild blog. The string-`new_value` fallback
  is the contingency if the recursive schema misbehaves.
- No change to `architecture.md §1.7` or `validation.md §6.10` — this implements them. Next item: B2
  (everyday budget + live predictive interrupt).
