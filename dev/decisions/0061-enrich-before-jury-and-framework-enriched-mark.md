# 0061 — Implement enrich-before-jury + the `framework_enriched` provenance mark

**Date:** 2026-06-09
**Phase:** 1 (pre-Phase-2 consolidation batch — item **C1**)
**Architecture refs:** `pipeline.md §3.2.3`/`§3.2.4`/`§3.3`, `schema.md §4.9`, `validation.md §6.10.2`,
`agents.md §5.5`. **Implements ADR 0052** (the design). Builds on ADR 0020 (enrichment skeleton) and
ADR 0005 (confidence exclusive to llm_inference). **Interlocks with ADR 0051/0060** (the grounding
stack); **amends the ADR-0023-locked graph** additively (node order) and revisits ADR 0056's recursion
multiplier.

## Context

ADR 0052 settled C1: enrichment must run before the Extractor-Jury (so what ships equals what was
reviewed), and every framework-written provenance field gets a `framework_enriched: true` mark so the
grounding stack and the jury can exempt the framework's own authoritative `external_api` calls from the
agent-trace (search-before-claim) requirement. This ADR records the implementation decisions.

## Decision

**1. `framework_enriched` is a boolean on `Provenance`, constrained to `source == external_api`.** The
schema (`provenance.py`) gains `framework_enriched: bool = False`. A new `_source_rules` clause raises
when `framework_enriched=True` with any source other than `external_api` — enrichment is the only
writer and only ever stamps `external_api` (`schema.md §4.9` framework-only authorship). This is the
conservative reading the change-map flagged: the mark is meaningful *solely* on framework
`external_api` calls. It composes with `discrepancy_with_blog=True` (both coexist on a discrepant
enrichment) and does **not** relax ADR 0005 (confidence stays exclusive to `llm_inference`).

**2. Enrichment stamps `framework_enriched=True` on every field it writes/rewrites.** All four
construction sites in `enrichment.py` (`_rewrite_cvss_score` and `_rewrite_severity`, clean and
discrepant branches) set it.

**3. Re-enrichment is idempotent.** Because C1 re-runs enrichment on each refinement iteration's patched
spec, `_rewrite_cvss_score`/`_rewrite_severity` no-op on a field already `framework_enriched`. Without
the guard a second pass would (a) re-read the now-`external_api` field, find no `blog_explicit` value,
take the clean branch, and **silently drop** the original field-level discrepancy marking, and (b) the
guard makes the "no double-append to `material_discrepancies`" guarantee explicit and robust.

**4. Graph reorder (additive amendment to the ADR-0023-locked builder).** Execution order becomes
`extract → validate → enrich → grounding → jury` (was `… → grounding → jury → enrich(terminal)`).
Enrichment runs **before** the grounding stack on purpose: the framework-written `external_api` fields
must reach the grounding/provenance-structure layer so its `framework_enriched` exemption applies (the
C1↔A3/B1 interlock, ADR 0060), and the jury then reviews the *enriched* spec. `enrich_node` is now
mid-pipeline and no longer sets the terminal `SHIPPED` status — the **jury owns the ship decision**
(`approve` → `SHIPPED` → END; cap-exhausted `revise` → `SHIPPED_LOW_CONFIDENCE` → END). On a `revise`,
the refine→validate→enrich→grounding→jury cycle re-enriches the patched spec before re-review, so the
"shipped == reviewed" invariant holds across refinement iterations.

**5. The grounding exemption lands once, in the shared check.** `GroundingValidator._check_search_before_claim`
skips `prov.framework_enriched` fields; agent-claimed (`framework_enriched=False`) `external_api` fields
are still held to search-before-claim. This is the single de-duplicated home (ADR 0060), so the jury
inherits the exemption automatically.

**6. Recursion-limit multiplier re-derived 4x → 6x.** A refinement iteration now costs at most 5
super-steps (`extract → validate → enrich → grounding → jury`); `GRAPH_RECURSION_LIMIT = 6 ×
GLOBAL_ITERATION_CAP` keeps the semantic global cap (20) binding before the graph-level backstop. (ADR
0056's reasoning text, written for the 3-super-step era, is superseded by this note; the ADR is not
edited in place.)

## Consequences

- **`pipeline.md §3.2.3`/§3.2.4 invariant now holds in code**: the jury reviews the enriched spec.
- **No `docs/` edit** — `pipeline.md`/`schema.md`/`validation.md` already describe this end-state (the
  ADR-0052 design pass updated them).
- **Phase-1 reality:** the production `EnrichmentConfig` has no NVD client, so enrichment is largely a
  no-op for CVEs today (it records honest skips); the reorder + mark + exemption are nonetheless correct
  and in place for when the NVD/MITRE adapters are wired. The orchestrator unit tests exercise the
  enriched path by injecting a fake NVD client via `enrichment_config`.
- **Run-store / persistence unaffected:** `state.enrichment` is set before the jury, so the shipped
  outcome's enrichment account matches the reviewed (final-iteration) spec.
