# 0086 — One guard per framework-owned field, on a path the field travels

- **Status:** partially superseded by [ADR 0087](0087-declared-field-ownership.md) — the
  hand-audit rule and the inventory table below are replaced by ownership **declared inline on
  the field** (a `FrameworkOwned` marker every consumer derives from). The four-mechanism split
  (stamp / reset / derive / absent-from-LLM-schema) and the "recorded, not built" Planner
  prerequisites stand. The inventory table is now descriptive history, not the source of truth.
- **Date:** 2026-06-16
- **Deciders:** maintainer (architect), implementing agent
- **Frames:** ADR 0085 (the first guard re-homed under this principle), ADR 0082, ADR 0068,
  ADR 0065
- **Scope:** a principle + an inventory. It records — it does not build — the two
  Planner-coupled prerequisites in §"Recorded, not built".

## Context

`run_persistence.stamp_framework_provenance` documents itself as "the single place … so a new
framework-owned field is added in exactly one place and can never be forgotten." That is no longer
literally true: `provenance_guard` (ADR 0082/0085, a *reset*) and the lab-level reproducibility
*derivation* are two more homes, by genuinely different mechanisms. The drift this invites is real
— ADR 0068 already paid for one instance (a billed-model stamp that had to be added in a second
place) — and the architect-review #7 sweep found a live one (`source_of_record`, fixed in ADR
0085) plus prospective ones for the unbuilt Planner.

The instinct to collapse these into one registry or one strip-pass is **wrong**: the mechanisms
differ by field semantics and pipeline position. A stamp needs the cost ledger at ship time; a
reset must run before enrichment; a derive needs the source fields; absent-from-schema needs a
separate in-flight type. What they share is not a mechanism but a *requirement*.

## Decision (the principle)

Because the Extractor's structured output **is** the whole `AttackSpec` (and the future Planner's
will be the whole `LabManifest`), the LLM can physically emit a value for any field unless one of
four mechanisms intervenes. Every framework-owned field on an LLM-facing artifact must have
**exactly one guard, and the guard's home must sit on a path the field actually travels.**

The four mechanisms (do not collapse them):

1. **stamp** — overwrite with the framework value at the ship/persist seam (`spec_version`,
   `extraction_metadata.model`).
2. **reset** — blank to the framework default at the extract seam, so the later framework writer is
   the sole author (`framework_enriched` + the discrepancy record, `material_discrepancies`,
   `CveReference.source_of_record`, the lab-level `reproducibility` block).
3. **derive** — compute from other fields and overwrite (lab-level reproducibility, once its derive
   step exists; until then it is *reset*, ADR 0085).
4. **absent-from-LLM-schema** — the field is simply not in the model the LLM fills; the framework
   supplies it out of band (the proposal audit metadata via the in-flight `Proposed*` models +
   `to_entry`).

**The audit rule (this is the operative part):** for each framework-owned field, name its mechanism
**and the exact seam that applies it**. If the field can reach disk / the jury on a path where that
seam never runs — a skipped enrichment lookup, an unbuilt manifest persist path, an unbuilt derive
step — it is **unguarded regardless of intent**. "The Extractor leaves it None" is a convention,
not a guard.

## Inventory (Phase 1 + declared Phase-2 surface)

| Field | Mechanism | Seam / home | Status |
|---|---|---|---|
| `spec_version` (`SpecEnvelope`) | stamp | `stamp_spec_version` ← `stamp_framework_provenance`, extract persist seam | guarded |
| `extraction_metadata.model` | stamp | `stamp_billed_model` from the cost ledger (ADR 0065) | guarded |
| `Provenance.framework_enriched` | reset | `provenance_guard`, extract seam | guarded |
| `Provenance.discrepancy_with_blog` / `overridden_blog_value` / `discrepancy_classification` | reset | `provenance_guard`, extract seam | guarded |
| `AttackSpec.material_discrepancies` | reset | `provenance_guard`, extract seam | guarded |
| `CveReference.source_of_record` | reset | `provenance_guard`, extract seam | **guarded (ADR 0085)** — was a live hole; enrichment's conditional set (`enrichment.py:484`, success only) is not a guard on skipped lookups |
| `AttackSpec.reproducibility` (lab-level block) | reset (derive later) | `provenance_guard`, extract seam | **guarded (ADR 0085)** — declared derived but no Phase-1 derive step; reset until one exists |
| Proposal audit metadata (`proposed_by_model`, `proposed_at`, `source_lab`, `source_blog`, `approval`) | absent-from-LLM-schema | in-flight `Proposed*` omit them; framework stamps at `to_entry` | guarded |
| `*Entry.proposed_by` / `proposed_in_run` | stamp | `agents/proposals.py` `to_entry` accept boundary | guarded |
| `real_world_incidents.status` | (dual-authored) | Extractor authors from the blog in v1; `schema.md §4.10` allows a future enrichment override | **not a hole today** — revisit (add a `framework_enriched`-style marker) when an incidents enrichment source is integrated |
| `LabManifest.GenerationBlock.model` / `tool_version` / `timestamp` | stamp | **unbuilt** — `stamp_billed_model` is AttackSpec-typed | prospective (Planner) — see below |
| `LabManifest.spec_version` | stamp | `stamp_spec_version` is generic over `SpecEnvelope` but unwired to a manifest persist seam | prospective (Planner) — see below |

## Recorded, not built (Planner-coupled prerequisites)

These are real but not yet reachable (the Planner is a stub); they are tracked for when it lands,
**not built now**:

1. **Manifest-side framework stamping.** When the Planner's `LabManifest` ships,
   `GenerationBlock.model` must be stamped from the billed ledger (the ADR-0065 invariant — billed
   model, never the LLM's self-report) and `spec_version` stamped via the already-generic
   `stamp_spec_version`. **Generalize the one stamp home to dispatch on artifact type; do NOT add a
   third copy** (ADR 0068's whole point — the billed-model reader is already generalized,
   `run_persistence.py`). Tracked in `dev/phase-2-seams.md`.
2. **`StepBlock.reproducibility` carry-integrity.** When the Planner populates the manifest's
   per-step tiers (carried forward unchanged from the AttackSpec `ChainStep`, ADR 0081), a Layer-2
   check must assert `StepBlock.reproducibility == source ChainStep.reproducibility`. Whether this
   needs an explicit `StepBlock → chain_step` back-ref is decided when the Planner is built — it
   depends on whether the Per-phase Generator needs an explicit step→tier mapping. Tracked in
   `dev/phase-2-seams.md`.

## Consequences

- **+** A reusable audit rule that already caught `source_of_record` and the lab-level
  `reproducibility` block; the inventory is the checklist a new framework-owned field is added to.
- **+** The mechanism split is recorded as intentional, so a future reader does not "simplify" it
  into one registry/strip-pass and break the per-path requirement.
- **−** The "single place" claim in `stamp_framework_provenance` is now explicitly *three*
  mechanisms; the comment there should point at this ADR (a follow-up doc nit, not blocking).
- No code change in this ADR; the `source_of_record` / `reproducibility` fixes it frames landed in
  ADR 0085.
