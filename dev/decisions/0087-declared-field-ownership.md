# 0087 — Framework-owned-field ownership is declared on the field, not re-derived

- **Status:** accepted
- **Date:** 2026-06-16
- **Deciders:** maintainer (architect), implementing agent
- **Frames:** supersedes the hand-audit rule + inventory table of ADR 0086 (keeps its
  four-mechanism split); narrows the patch seam of ADR 0085; relates to ADR 0082, ADR 0068,
  ADR 0065
- **Scope:** AttackSpec (the Phase-1 LLM-facing artifact). The LabManifest surface is
  recorded-not-built (see §"Recorded, not built").

## Context

ADR 0086 established the principle "one guard per framework-owned field" and an *audit rule*:
for each such field, name its mechanism and the seam that applies it, by hand, against a
markdown inventory table. The architect-review follow-up that produced ADR 0085 then found
**four** live patch-path holes (forged `source_of_record`, `material_discrepancies`,
`reproducibility`, and a bare-leaf `framework_enriched` on an already-`external_api` field)
plus two inventory fields — *all* found by hand, all tracing to the same root: **which fields
are framework-owned is re-derived from outside the field** — by shape heuristics in
`provenance_guard._scrub_node` (the `{source,citations,framework_enriched}` / `cve_id`
discriminators), by a hand-typed list, and by a markdown table. Three surfaces that drift
independently; the audit rule is a human process, and humans miss the fifth hole the same way
they missed the first four.

A concrete demonstration that names cannot carry ownership: `reproducibility` is
**framework-owned** on `AttackSpec` (lab-level, derived; `attack_spec.py:495`) and
**legitimate authored content** on `ChainStep` (per-step tier, carried unchanged;
`attack_spec.py:187`). Same name, opposite ownership, different type. A flat name/position
check tells them apart today only because the framework-owned one happens to sit at the
top level — a layout coincidence. It **already** recurs in the declared (unbuilt) LabManifest:
`CoreBlock.reproducibility` (framework-owned, `manifest.py:89`) is **nested**, not top-level,
alongside `StepBlock.reproducibility` (content, `manifest.py:288`) — so the positional trick
provably collapses there. Ownership is a property of the **(model, field)** pair, and the only
place that can be stated without drift is on the field itself.

## Decision

**Every framework-owned field declares its ownership inline, and every consumer derives from
that one declaration.** This replaces ADR 0086's hand-audit + inventory table. ADR 0086's
four-mechanism taxonomy (stamp / reset / derive / absent-from-LLM-schema) stands.

1. **The marker.** A `FrameworkOwned` marker (a frozen, slotted dataclass) is attached inline
   via `Annotated[T, FrameworkOwned()]` on each framework-owned field. A spike confirmed inline
   `Annotated` survives the `Provenance[T]` generic and the ADR-0066 custom `__reduce__` pickle
   path: the marker is introspectable on the base `Provenance`, on builtin aliases, and on a
   lazily-parametrized `Provenance[Severity]`, and it round-trips through pickle unchanged.
   The marker is **kept extensible** (it will later carry a `mechanism` field) but carries
   **no fields today** — until it does, it tags exactly the **reset-mechanism** owned fields
   (those the framework blanks at the extract seam and the LLM may never author).

2. **Consumers derive, never re-list.**
   - The refinement **patch-path check** (ADR 0085 narrowing) rejects any `field_path` that
     targets a framework-owned field, with the denylist **generated from the markers** — never
     hand-typed. Generation buckets by where the marker sits: a marker on the **root artifact**
     (`AttackSpec`) → matched as a **top-level segment**; a marker on a **nested/shared** model
     (`Provenance`, `CveReference`) → matched as a **leaf name**. This is exactly why
     top-level `reproducibility` is rejected while nested `chain.chain_steps[*].reproducibility`
     (and `chain.alternative_paths[*].reproducibility_summary`) are allowed.
   - The whole-spec **reset** (`neutralize_framework_owned_provenance`) becomes a marker-walk
     over the model tree (a guarded follow-on), replacing `_scrub_node`'s shape heuristics.
   - The completeness test enumerates the marked fields in the AttackSpec tree and drives each
     through **both** authoring paths (reset on whole-spec; reject on patch).

3. **The residual that stays shape-based — and why it's the deferred work, not new debt.**
   The **patch-value scrub** (`neutralize_patch_provenance`) runs on a pre-validation raw
   `JsonValue` whose type is unknown until merge+validate, so it cannot read field markers; it
   stays shape-based, backstopped by the path-check. Fully marker-aware patch scrubbing **is**
   the deferred runtime path→type resolution (below). Both mechanisms are kept: the path-check
   rejects framework-owned *target* paths; the value-scrub sanitizes framework fields nested
   *inside* a legitimately-targeted content sub-tree (e.g. a whole-`Provenance` patch at
   `cvss_score`, where `cvss_score` is content but the sub-tree may carry a forged flag).

## What is marked (AttackSpec tree)

`Provenance.framework_enriched`, `Provenance.discrepancy_with_blog`,
`Provenance.overridden_blog_value`, `Provenance.discrepancy_classification` (reset);
`CveReference.source_of_record` (reset); `AttackSpec.material_discrepancies` (reset);
`AttackSpec.reproducibility` (reset today; derive when the rollup step exists).

**Deliberately not marked:**
- `AlternativePath.reproducibility_summary` — Extractor-**authored**, captured from the blog
  (`schema.md` §4.8: alternative paths are "captured… not generated in this lab"; only the
  lab-level `ReproducibilityBlock` is "derived; not authored" per `schema-details.md` §4.20).
  Marking it would reject legitimate refinement. (Flagged for the architect: if the intent is
  that alt-path summaries be a framework rollup, this flips to owned — a schema-semantics call.)
- `ChainStep.reproducibility` — per-step authored content (carried unchanged, ADR 0081).
- `real_world_incidents.status` — dual-authored today (ADR 0086); marks when an incidents
  enrichment source is integrated.
- `spec_version`, `extraction_metadata.model` — **stamp**-mechanism, not reset. A
  mechanism-less marker would mis-drive the reset-walk to blank them. They join the declaration
  when `FrameworkOwned` carries `mechanism`. Their guard remains stamp-at-persist (ADR 0065/0068);
  a forged value reaches the jury but is overwritten in the shipped artifact (low severity).

## Recorded, not built (the deferred runtime resolution + LabManifest)

Resolving a runtime `field_path` **string** (with `[i]` indices, through `Optional` / `list[T]`
/ the `Provenance[T]` generic) to its exact `(model, field)` pair is the genuinely fiddly,
second-surface-benefiting part. It is **deferred to a structural trigger, not a vibe**: the
first time a framework-owned, ambiguously-named field appears where the flat positional rule
cannot separate it — i.e. **refinement over a LabManifest**, where `core.reproducibility`
(owned, nested) collides with `phases[*].steps[*].reproducibility` (content, nested). Built
then, against two real artifacts. Until then the flat check is correct (AttackSpec has exactly
one ambiguous name, `reproducibility`, and it is positionally separable). When LabManifest is
built, its framework-owned fields are marked inline at their definition sites (the declaration
extends to the second artifact for free) and the marker-aware resolver consumes the same marks.

## Consequences

- **+** Ownership has one home (the field). The denylist, the reset, and the completeness test
  derive from it; the markdown inventory (ADR 0086) and the hand-typed list are gone — three
  drift surfaces collapse to one. You cannot forget to register a field you are editing.
- **+** The (model, field) precision kills the name collision at the root: `ChainStep.reproducibility`
  and `AttackSpec.reproducibility` are simply different — no positional cleverness.
- **−** The patch-value scrub stays shape-based (necessarily — raw pre-validation JSON); it is
  the one remaining re-derivation, and it is exactly the residual the deferred resolver subsumes.
- **−** The flat path-check remains positional until the deferred resolver lands; it is correct
  for AttackSpec and expires precisely at LabManifest refinement (the named trigger).
- ADR 0086's inventory table is now descriptive history, not the source of truth; its
  four-mechanism split and its "recorded, not built" Planner prerequisites carry forward here.
