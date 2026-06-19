# 0101 — Data-driven external-source enrichment seam + the ADR-0077 items (Task 9)

Status: accepted
Date: 2026-06-19
Supersedes/relates: ADR 0077 (the external-source adapter work-stream — this builds it),
0020 (enrichment-skeleton trigger-field corrections), 0042 (external-source unavailability never
fatal), 0052/0061 (enrich-before-jury, idempotent re-enrichment), 0055/0058 (MITRE seed is not an
authority), 0066 (checkpoint type registry), 0083 (no import cycles); `dev/phase-2-seams.md` ③.2 + ④.

## Context

Phase-2 Task 9 turns the single-source NVD enrichment stub into the **data-driven** pre-Planner
enrichment the architecture specifies (`pipeline.md §3.2.4`, `schema.md §4.9`/§4.14): dispatch from
each `external_data_sources` registry entry's `enrichment_triggers` to a per-source adapter, wire the
five new sources (MSRC, OSV.dev, KEV, EPSS, security bulletins), classify discrepancy materiality,
and land the three currently-inert/deferred pieces ADR 0077 tracks (the CVE ship-gate, the
`source_of_record` check, the `advisory.source` retype).

The user reviewed the Scope-A proposal (enrich into a typed audit channel; no AttackSpec-contract
expansion) and approved it with two refinements: (1) capture the **full documented publisher
response** per source as a typed record (lossless, not a scalar); (2) before finalizing placement,
confirm whether the existing NVD `cvss_score`/`severity` fields are **blog-corroborated** or
**enrichment-only**, and place the new records accordingly.

**Verified contract fact (the refinement-2 question).** `CveReference.cvss_score`/`severity` are
**corroboration-capable**: the Extractor populates them as `BLOG_EXPLICIT` when the blog states a
value (`tests/unit/framework/test_enrichment.py` `_cve()` builder), and enrichment checks that value
for a discrepancy and overrides it with `external_api` provenance + the discrepancy machinery. They
are *not* enrichment-only. So the "Corroborated" branch applies (see Decision §3).

## Decision

### 1. Data-driven dispatch behind a per-source adapter seam

`framework.enrichment.enrich()` becomes a thin **driver**: it iterates the `external_data_sources`
entries, resolves a `SourceAdapter` per entry via `external_data_sources.registry.resolve_adapter`,
and runs each in budget priority order (`LookupPriority`, stable on registry order). **No hardcoded
`_NVD_SOURCE_ID`/`_MITRE_SOURCE_ID` dispatch remains.** A registry entry with no registered adapter
(github_api) is an honest stub-skip. Each adapter reads its entry's `enrichment_triggers`, performs
budget-aware lookups via an injected client (`SourceClients`), and appends results / honest skips.

### 2. The neutral `external_data_sources` package (the ADR-0077 / seams-④ relocation)

A new top-level subpackage `cyberlab_gen/external_data_sources/` holds the client **ports**
(`NvdClient` + per-source `KevClient`/`EpssClient`/`MsrcClient`/`BulletinClient`, relocated out of
`framework.enrichment`), the typed **records**/result types, the **materiality** classifier, the
trigger-resolution **support** helpers, and the per-source adapter modules (`<id>/`). It depends only
on `schemas` — never on `framework` — so adapters, the driver, agents, and validators all import
*down* into one place with no cycle (ADR 0083). `framework.enrichment` re-exports the moved names for
back-compat.

### 3. The schema-home gap → Scope A (audit-channel records), placement deferred to Phase-3

The registry's `enrichment_triggers`/`discrepancy_materiality_rules` name fields the current
AttackSpec mostly does not have (`kev_inclusion`, `epss_score`, `affected_products`, `fix_version`,
`bulletin_status`, `targets.packages`). Only NVD's `cvss_score`/`severity` have typed homes.

Because NVD's fields are **corroboration-capable** and the new sources (KEV/EPSS/MSRC/bulletins) are
**purely additive** (no blog field claims a KEV listing or an EPSS score — there is nothing to
corroborate or disagree with), the corroborated-branch rule holds: NVD stays typed on `CveReference`
(the blog can speak to it; the discrepancy machinery lives there); the additive sources land as
**typed lossless records** (`KevRecord`/`EpssRecord`/`MsrcRecord`/`BulletinRecord`, modeling the
documented publisher shapes) in the `EnrichmentResult` **audit channel**, surfaced in the run report.
The AttackSpec is **not** expanded. Committing these to the versioned, boundary-crossing AttackSpec
contract is **deferred to the Phase-3 Generator** (the first real consumer, which defines the
consumed shape) — the one genuinely expensive-to-change surface, not worth guessing now.

### 4. Trigger-resolution mini-language: minimal, honest skips for the rest

The trigger-field resolver (`support.py`) resolves only the dialects that name real AttackSpec
collections this schema version: the CVE list (`external_references.cves[*]`), the chain-step MITRE
ids, and `facets[?value='target:<cloud>']` membership. A declared trigger whose field has no home
(OSV's `targets.packages[*]`, GitHub's `targets.repos[*]`) does **not** silently match — the owning
adapter records an honest skip naming the gap. Bundled-registry trigger fields were corrected to the
real schema (the ADR-0020 precedent already applied to NVD).

### 5. The three ADR-0077 items

- **CVE ship-gate, un-inerted honoring §1.6.** The grounding validator's `_check_cves` now consumes
  enrichment's per-CVE `CveResolution` (CONFIRMED/ABSENT/UNAVAILABLE) instead of the agent trace, so
  the gate actually verifies grounded CVE ids against NVD **without the validator doing network I/O**
  (enrichment is the network pass). ABSENT → CVE_HALLUCINATION (retry); UNAVAILABLE → never penalised
  (ADR 0042); no resolution → skipped (couldn't-check). This **refines** ADR 0077's "wire a client
  into the validator" wording, which predates the validator's crisp no-network contract.
- **`source_of_record` post-enrichment check.** A new mechanical, no-network grounding layer
  (`SOURCE_OF_RECORD_UNKNOWN`, informational — never retry: a framework-owned field) flags a
  `cve.source_of_record` that resolves in no registered source. Wired via `build_pipeline`'s
  `known_source_ids`, threaded from the CLI runner's registries; `None` skips it.
- **`advisory.source` retype.** Retyped off `ExternalDataSourceId` to a distinct `PublisherLabel`
  (a publisher provenance label, not a tool-adapter id) so it cannot be misread as a registry id.

### 6. Materiality

Classification is mechanical rule-lookup per each entry's `discrepancy_materiality_rules` (never an
LLM, `§1.6`). It is **demonstrated on a real disagreement only for NVD** — the sole
corroboration-capable source (cross-tier CVSS → material; same-tier → non-material). The additive
sources have no blog value to disagree with, so there is nothing to fabricate.

## Owned deferrals (named owner + interim exposure)

- **Typed KEV/EPSS/MSRC/bulletin homes on the AttackSpec** → owner: the **Phase-3 Generator** (its
  first consumer defines the shape). Interim exposure: `EnrichmentResult.{kev,epss,msrc,bulletin}_records`
  in the run report.
- **OSV / GitHub package- and repo-target enrichment** → owner: the **Phase-3 schema work / Scope B**
  (adding `ChainStep.targets.{packages,repos}` + the Extractor authorship that fills them). Interim
  exposure: the OSV adapter's honest skip + the github_api stub-skip, both in the run report.
- **Manifest Layer-1 registry-membership wiring** is unchanged from ADR 0099 §6 (still the Phase-3
  Validator's job) — note this means a `source_of_record`/facet typo on the *manifest* is not yet
  gated; the AttackSpec `source_of_record` check landed here is the spec-side analog.
- **Live `httpx` clients are not wired into the production CLI** (no API keys bundled, `schema.md
  §4.14`). Production enrichment runs hermetic (every source skips not-integrated) exactly as before;
  the clients are injectable via `SourceClients` for eval/tests. Owner: a later config/keys task.

## Consequences

- Enrichment is data-driven; the five new sources enrich on recorded fixtures (KEV/EPSS/MSRC/bulletins
  genuinely; OSV honestly skips); materiality works on a real disagreement; unavailable sources are
  never fatal. The CVE ship-gate is real (no-network); `source_of_record` is guarded; `advisory.source`
  cannot be misread.
- `just verify` green.

## Doc nit surfaced (ADR 0084)

The Task-9 brief's exit-criteria reference "`§5.5`" has no matching section in the reading-path docs
(`pipeline.md`/`schema.md`/`agents.md §5.5` is the Extractor-Jury, unrelated). Treated as the
`pipeline.md §3.2.4` materiality contract; flagged here as a brief typo, not acted on as a doc edit.
