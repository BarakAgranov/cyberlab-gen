# 0052 — Enrichment runs before the jury (shipped = reviewed); framework-written provenance is marked `framework_enriched`

**Date:** 2026-06-07
**Phase:** 1 (design-alignment / docs-revision pass)
**Architecture refs:** `pipeline.md §3.2.3`/`§3.2.4`/`§3.3`, `schema.md §4.9`,
`validation.md §6.10.2`, `agents.md §5.5`. Builds on ADR 0020 (enrichment skeleton —
framework-only authorship) and ADR 0017 (material discrepancies); composes with ADR 0048
(patch refinement) and ADR 0051 (the provenance-structure mechanical layer). This is item
**C1** of the A1–G1 design-alignment plan; the maintainer chose **both** sub-options (reorder
*and* mark), not one.

## Context

Two coupled problems:

1. **Shipped ≠ reviewed.** Enrichment ran *after* the jury (`§3.3`: Extractor → Jury → enrichment
   → interrupt → Planner). The jury reviewed the pre-enrichment spec, then enrichment rewrote
   provenance (CVE `cvss_score`/`severity` → `source: external_api`, discrepancy records appended)
   that no review saw. The jury's remit explicitly includes verifying `external_api` fields — yet
   the enrichment-added `external_api` fields arrived after it finished.
2. **The provenance model conflates two kinds of `external_api`.** A framework enrichment call
   (the framework's own authoritative NVD/MSRC lookup, trusted) and an agent-claimed external value
   (which must be tool-backed via search-before-claim) both stamp `source: external_api`. Nothing
   distinguishes them, so a consumer — or the mechanical provenance-structure layer (ADR 0051) —
   can't tell a trusted framework rewrite from a claim that needs trace evidence.

## Decision

**1. Enrich before the jury.** Execution order becomes Extractor → **enrichment** → Extractor-Jury
→ post-Extractor interrupt → Planner. The jury reviews the enriched spec; *what ships equals what
was reviewed*. On a jury `revise`, the Extractor patches the flagged fields (ADR 0048) and
enrichment re-runs on the patched spec before the jury re-reviews, so the invariant holds across
refinement iterations. Section numbers are **not** renumbered (that would break the many `§3.2.x`
cross-references); the §3.2.4 prose and the §3.3 cross-stage table carry the real execution order,
and the number/order mismatch is called out in-place.

**2. Mark framework-written provenance `framework_enriched: true`.** A boolean on the provenance
block, set by enrichment on every field it writes or rewrites. `external_api` + `framework_enriched:
true` = the framework's own authoritative call (the API-response citation is the evidence; no agent
tool-call required). `external_api` without it = agent-claimed (must have matching tool-call
evidence in the agent trace). This is **required** now that enrichment precedes the jury: framework
`external_api` fields reach the jury and the provenance-structure layer, which **exempt**
`framework_enriched` fields from the agent-trace requirement — without the mark they'd be
false-flagged as ungrounded (the framework's call isn't in the agent's trace). It builds on ADR
0020's framework-only authorship (only `enrich()` sets `source=external_api` + `discrepancy_with_blog`)
— the mark is additive.

## Consequences

- **Docs updated in this pass:** `pipeline.md §3.2.3` (jury input is the enriched spec; the jury
  exempts `framework_enriched`), `§3.2.4` (execution-order note; the `framework_enriched` stamp),
  `§3.3` table reordered; `schema.md §4.9` (the `framework_enriched` field + a "framework-enriched
  vs agent-claimed" subsection); `validation.md §6.10.2` (the provenance-structure layer exempts
  `framework_enriched`).
- **Code is a separate, later work-stream:** move enrichment before the jury in the graph; re-run
  it after a refinement patch; add the `framework_enriched` field to the provenance model; make the
  provenance-structure layer + the jury exempt `framework_enriched` from the agent-trace
  requirement. ADR 0020's enrichment behavior (which fields, materiality default, local MITRE) is
  unchanged.
- The post-Extractor interrupt's material-discrepancy review (`§3.2.5`) is unaffected — enrichment
  still produces the discrepancies; they now exist before the jury rather than after it.
