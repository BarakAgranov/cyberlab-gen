# 0020 — Pre-Planner enrichment skeleton: operate on real schema fields, local MITRE catalog, conservative materiality default

**Date:** 2026-06-01
**Phase:** Phase 1 (Task 4)
**Architecture refs:** `pipeline.md §3.2.4`, `schema.md §4.9`, `implementation-plan.md §4.2`, `registry-details.md §5.1`, ADR 0005, ADR 0017

## Decision

The pre-Planner enrichment pass (`cyberlab_gen/framework/enrichment.py`) enriches the **actual** AttackSpec fields, not the (stale) JSONPath strings in the NVD registry entry's `enrichment_triggers`:

1. **CVE enrichment** rewrites the `Provenance` of `AttackSpec.external_references.cves[*].cvss_score` and `.severity` from NVD (`source=external_api`, both citations). The `cve.source_of_record` is set to `nvd`.
2. **MITRE technique enrichment** validates `chain.chain_steps[*].techniques.mitre[*]` and `external_references.mitre_techniques[*].technique_id` against a **bundled local catalog** (`registry/mitre_attack_techniques.yaml`, read via `load_mitre_techniques()`), never a live call — per `registry-details.md §5.1` ("read locally").
3. **Materiality default is material** when no `discrepancy_materiality_rules` rule names the field (the conservative reading from `pipeline.md §3.2.4`: "the framework never silently resolves a disagreement that would change the lab's character"). Severity discrepancies are classified by CVSS *tier* (same tier → non-material, cross-tier → material) regardless of whether a rule names `severity`.
4. **Budget** (default 100, configurable via `EnrichmentConfig.budget`) is consumed by external (non-local) calls only — CVEs in Phase 1. MITRE is local and free. Priority order `CVE > MITRE > GitHub > bulletins > other` is encoded in `LookupPriority` for when MITRE/GitHub become live.
5. **Graceful degradation:** budget exhaustion, rate-limit (`ExternalApiRateLimitError`), no-NVD-record, and not-integrated stubs each produce a `SkippedLookup` with an honest reason; the pass never raises on these. Rate-limit reason is the brief-mandated exact string `"external API rate-limited at enrichment time"`.

## Context

Three genuine under-specifications surfaced:

- **The NVD registry entry's `enrichment_triggers` field paths are stale vs. the Task-1 AttackSpec schema.** They name `chain.chain_steps[*].techniques.mitre[*].cve_ids[*]` (the `ChainStepTechniques` model has *no* `cve_ids` field) and `external_references.cve_references[*]` (the field is `external_references.cves`, a `list[CveReference]`). Following the trigger strings literally would enrich nothing. CLAUDE.md authority gradient: the architecture (the Task-1 schema, itself derived from `schema-details.md §4`) wins over a registry-seed string. So enrichment walks the real schema. The drift is flagged for the maintainer (below).
- **`schema-details.md §4` / §7 do not pin the per-CVE CVSS/severity home or a `material_discrepancies` element shape.** ADR 0017 already declared `MaterialDiscrepancy` (the top-level index) for Task 1; Task 4 reuses it unchanged and writes the authoritative record into the field's own `Provenance` (`discrepancy_with_blog` / `overridden_blog_value` / `discrepancy_classification`), exactly as `schema.md §4.9` and ADR 0017 prescribe.
- **No bundled MITRE catalog existed.** `registry-details.md §5.1` and `§7` describe a local MITRE reference but Phase 0 shipped none. A small seed catalog (`registry/mitre_attack_techniques.yaml`, 8 Enterprise techniques covering the curated-blog walks) is added, with a `MitreTechniqueCatalog` Pydantic model and a `load_mitre_techniques()` loader. It grows by maintainer PR as the curated set grows; it is not a live mirror.

## Alternatives considered

- **Follow the registry trigger strings literally / add a JSONPath walker.** Rejected: the strings don't match the real schema, a generic JSONPath mutator over `Provenance`-wrapped fields is far more than a Phase-1 *skeleton* needs, and it would silently enrich nothing today. Operating on the typed schema is correct and testable now.
- **Materiality default = non-material.** Rejected: violates `pipeline.md §3.2.4`'s conservative-resolution rule. An unclassified disagreement could change the lab's character and must surface.
- **Live MITRE lookup via the registry's `mitre_attack` entry.** Rejected: `registry-details.md §5.1` says the technique catalog is read locally; the live `mitre_attack` entry in `registry-details.md` is explicitly "for runtime lookup … when MITRE refresh ships" (a later phase). Local is the Phase-1 contract and needs no budget/rate-limit handling.
- **Put CVE CVSS/severity comparison values in `extras`.** Rejected: `CveReference` already carries typed `cvss_score: ProvenanceFloat | None` and `severity: Provenance[Severity] | None` fields whose `Provenance` is the right home for the blog-vs-API audit trail. `extras` is the untyped escape hatch, wrong for a typed field.

## Consequences

- Framework-only authorship holds: only `enrich()` sets `source=external_api` + `discrepancy_with_blog=True`; no agent path can.
- Phase 4 (full enrichment) wires the `material_discrepancies` list into the post-Extractor interrupt as the third review surface; Phase 1 surfaces it in the run report only (via `EnrichmentResult`).
- **Doc/registry drift flagged for the maintainer:**
  1. `registry/external_data_sources.yaml` NVD entry's `enrichment_triggers` field paths should be corrected to the real Task-1 schema paths (`external_references.cves[*]`; drop the non-existent `...techniques.mitre[*].cve_ids[*]` path or add a `cve_ids` field to `ChainStepTechniques` if per-step CVE attribution is wanted).
  2. `schema-details.md §5` / `registry-details.md §5.1` should note the bundled MITRE catalog now exists at `registry/mitre_attack_techniques.yaml` with the `MitreTechniqueCatalog` model and `load_mitre_techniques()` loader; pick the canonical filename/location for the wheel-packaging story (ADR 0010).
- The `mitre_attack_techniques` catalog is **not** part of `MergedRegistries` (like the closed catalogs, ADR 0016): it is read on demand by enrichment, not proposal-flow.
