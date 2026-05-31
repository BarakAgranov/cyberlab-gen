# ADR 0017: Shape of the AttackSpec `material_discrepancies` field

**Status:** Accepted
**Date:** 2026-06-01
**Deciders:** Phase 1 Task 1 implementation agent, pending maintainer ratification

## Context

Phase 1 Task 1 (the brief, work item 2) and `implementation-plan.md §4.2` both
require a top-level `material_discrepancies` list on `AttackSpec`:

> Material discrepancies populate a separate `material_discrepancies` field in
> the AttackSpec for Phase 1; Phase 4 wires this into the post-Extractor
> interrupt as a third review surface.

The field must be **declared now** (Task 1) and is **populated later** by the
pre-Planner enrichment pass (Task 4). The enrichment pass is the only writer:
per `schema.md §4.9` framework-only-authorship, only the framework — never an
agent — records a discrepancy.

The problem: `schema-details.md §4` (the authoritative Pydantic-shape doc for
AttackSpec) does **not** define a model for `material_discrepancies`, nor list
the field on the `AttackSpec` envelope. The §7 cross-reference table has no row
for it either. So the element type is genuinely under-specified. Per CLAUDE.md
("Never resolve architectural ambiguities silently") this gets an ADR and the
most conservative reading.

What we *do* know from the surrounding docs:

- A discrepancy is recorded when an `external_api` value overrides a
  `blog_explicit` value during enrichment (`pipeline.md §3.2.4`).
- The per-field audit trail already lives inside `Provenance[T]`
  (`discrepancy_with_blog`, `overridden_blog_value`,
  `discrepancy_classification` — already built in Phase 0). A *material*
  discrepancy is exactly a `Provenance` field whose
  `discrepancy_classification == "material"`.
- The top-level list is therefore an **index/summary** pointing at those
  fields so the Phase 4 interrupt (and the Phase 1 run report) can surface them
  without walking the whole spec. It is not a new authority for the values —
  the authoritative record stays in the field's own `Provenance`.

## Decision

Declare a closed `MaterialDiscrepancy` artifact sub-block and type the field as
`list[MaterialDiscrepancy]` defaulting to empty:

```python
class MaterialDiscrepancy(ArtifactModel):
    field_path: NonEmptyString          # JSONPath-like locator into the spec
    summary: NonEmptyString             # human-readable one-line description
    blog_value: NonEmptyString          # the original blog_explicit value (stringified)
    authoritative_value: NonEmptyString # the overriding external_api value (stringified)
    source_of_record: ExternalDataSourceId  # which external source overrode
```

Rationale for each choice (all conservative, all derivable from existing docs):

- **`field_path`** mirrors `GapEntry.field_path` (the only other top-level
  pointer-into-the-spec list in §4.8), so the two "index" lists share a locator
  convention.
- **`blog_value` / `authoritative_value`** are the two sides of the override.
  Stringified (not generic `Provenance[T]`) because this is a *summary* surface,
  not the authoritative record — the typed values still live in the target
  field's `Provenance`. Keeping them plain strings avoids inventing a generic
  shape the docs never specified.
- **`source_of_record`** reuses the existing `ExternalDataSourceId` alias and
  matches `CveReference.source_of_record` in §4.5.
- **No `classification` field**: the list only ever holds *material*
  discrepancies (non-material ones are a silent provenance rewrite per the
  brief), so a classification enum on the entry would be redundant.

The field is `material_discrepancies: list[MaterialDiscrepancy] = []`, placed
adjacent to `gaps` on the envelope (both are framework/agent-produced top-level
index lists). It is **not** part of the OUT_OF_SCOPE negative-invariant set:
an out-of-scope spec has no enriched fields, so the list is simply empty, and
forbidding it would couple Task 1 to enrichment semantics it doesn't own.

## Consequences

- Task 4 (enrichment) appends `MaterialDiscrepancy` entries when it classifies
  an override as material; it remains the sole writer (framework-only authorship
  preserved).
- If a later doc update to `schema-details.md §4` pins a different shape, this is
  a localized change (one sub-block + one field) with no migration concern
  (`architecture.md §0.6`): the field is empty in every Phase 1 artifact until
  Task 4 ships, so no on-disk artifacts depend on the shape yet.
- **Doc-vs-code drift flagged for the maintainer:** `schema-details.md §4`
  should gain a `### 4.x MaterialDiscrepancy` block and a §7 cross-reference row,
  and the `AttackSpec` envelope listing should add the field. This ADR is the
  authoritative shape until the architect updates the doc.
