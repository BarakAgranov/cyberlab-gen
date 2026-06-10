# 0077 — Work-stream: wire the external-source (NVD/MITRE/OSV) adapter; activate the inert CVE ship-gate and the post-enrichment checks

**Date:** 2026-06-10
**Phase:** 1 (pre-Phase-2 fix register — Tier ② tracking-promotion; ②.2 + ②.3)
**Status:** Tracked work-stream (not yet built — promoted to an owning ADR so the inert mechanical
checks and the deferred typing have one searchable home).
**Architecture refs:** ADR 0055/0058 (external sources are tools, not registries), ADR 0052/0061
(enrich-before-jury), investigation `0001 §5` (the NVD/MITRE/OSV adapter + `source_of_record`
verification), `agents.md §5.4`, `validation.md §6.10`. Source: investigation `0003 §1.3(a)` / `§4`.

## Context

Two related deferrals are tracked too thinly (at investigation-doc granularity, not as a numbered
work-stream):

1. **The CVE-hallucination mechanical ship-gate is inert in production.** No `NvdClient` is wired
   (`main.py`/`orchestrator.py` pass none), so `GroundingValidator._check_cves` returns `[]`.
   Provenance is never *falsified* (an unconfirmed CVE keeps its blog / `llm_inference` provenance,
   never a false `external_api` stamp), but the advertised mechanical CVE ship-gate is a no-op.
2. **`AdvisoryReference.source` is typed as a tool-id and `cve.source_of_record` is unverified.**
   `source` is an `ExternalDataSourceId` (a `SnakeName` alias) holding a *publisher label* like
   `aws`; the post-enrichment verification of `cve.source_of_record` is deferred. Sound today
   (ships a valid `SnakeName`; `source_of_record` is framework-authored and Phase-1-inert), but a
   latent mistyped-field / reserved-but-unenforced trap a naive Phase-2 consumer could misread.

## Decision (scoped, not built now)

Build a per-source external-data adapter behind the seam the registry already models
(`EnrichmentTrigger{field, action, endpoint}`, `ExternalSourceEndpoint{…, response_schema_ref}`,
resolved by an adapter module under `cyberlab_gen/external_data_sources/<id>/`). NVD is the first
adapter. Landing it activates, in one place:

- the **CVE ship-gate** — wire a real `NvdClient` so `_check_cves` actually verifies grounded CVE
  ids against NVD (the mechanical gate stops being a no-op);
- the **`source_of_record` post-enrichment check** — verify `cve.source_of_record` after enrichment;
- the **`advisory.source` retype** — give the publisher-label field a type distinct from the
  tool-adapter `ExternalDataSourceId` so it cannot be misread as a registry id.

This is the same adapter build the Phase-2 data-driven-enrichment seam points at (see the Phase-2
seams doc, ③.2). Do not build it now; this ADR is the owning home so it is scheduled, not forgotten.

## Consequences

- One searchable work-stream for the NVD/MITRE/OSV adapter and the three currently-inert/deferred
  pieces that ride it.
- No code change in this ADR — a tracking promotion. The Phase-2 enrichment seam (③.2) and this
  work-stream are the same build, sequenced together.
