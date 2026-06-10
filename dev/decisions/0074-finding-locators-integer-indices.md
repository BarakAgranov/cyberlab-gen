# 0074 — Finding locators canonicalised on integer indices, enforced on the Finding base

**Date:** 2026-06-10
**Phase:** 1 (pre-Phase-2 fix register — Batch B, ①.6 ride: locator canonicalisation)
**Architecture refs:** `architecture.md §1.7` (targeted-patch refinement), ADR 0048/0054
(`framework.refinement` patch paths). Source: investigation `0004 §1.1` (S15, SHOULD-FIX).

## Context

`framework.refinement._parse_path` requires patch paths to use **dotted names + integer list
indices** (`chain.chain_steps[0].description`) and raises `RefinementPathError` on a non-integer
index. Several mechanical-validator findings emitted **string-id** list indices instead —
`external_references.cves[CVE-2024-9999]`, `chain.chain_steps[step-1].provisioning_mechanism`. This
is latent today (refinement is fed solely by jury feedback, whose `field_path` is integer-indexed),
but the first time a validator finding feeds a targeted patch — a natural Phase-2 step — it would
raise.

## Decision

**Canonicalise finding locators on integer list indices at the producer, and enforce the
convention once on the shared `Finding` base** (the contract from ADR 0073).

- Producers fixed to positional integers (via `enumerate`), with the human-meaningful id kept in
  `detail`: `grounding_validator._check_search_before_claim` and `_check_cves` (`cves[i]`);
  `static_schema_validator`'s chain-step loop and `_check_mechanism` (`chain_steps[i]`).
- `Finding.location` gains a `field_validator` that rejects any non-integer `[...]` index at
  construction (mirroring `_parse_path`'s rule). Because it lives on the base, every present and
  future mechanical-validator layer inherits it for free.

## Citation reconciliation (the fix register's cites, checked against HEAD)

The register grouped three sites as "Finding locators the patch parser rejects." Two do **not**
survive contact and are intentionally left untouched:

- **`enrichment.py:432/440/510/570`** — these are `SkippedLookup.field_path` and enrichment-internal
  `path` strings keyed by `cve.cve_id`. They are **not** `Finding`s and are **not** fed to
  `refinement._parse_path` (enrichment writes via its own mechanism and works today with string-id
  paths). They are a separate locator family with no patch-parser exposure; canonicalising them here
  would be scope creep with no correctness benefit.
- **`grounding_validator.py:207`** (`_collect_technique_refs` / `_log_mitre`) — the
  `chain_steps[{step.id}]` path it builds is only ever used to log the *technique id*; it never
  becomes a `Finding.location`, so the patch parser never sees it.

The genuine string-id `Finding.location` sites were grounding `196/246` and static `312/337` — those
are fixed. The full suite passing with the new base validator confirms no other producer constructs
a string-id finding locator.

## Alternatives considered

- **Extend `_parse_path` to resolve id-keyed indices** (`chain_steps[step-1]` by matching `step.id`)
  — rejected (fix register decision is "integer indices at the producer"); it is a larger change to
  the patch machinery and the id is preserved in `detail` regardless.

## Consequences

- Every `Finding` carries a patch-addressable locator; the convention is enforced once on the base
  (pinned: a string-id index is refused at construction; integer + nested integer indices accepted).
- Ids stay discoverable in `detail`, so reports/logs lose no information.
- The separate enrichment / material-discrepancy locator family is left as-is; if Phase 2 ever feeds
  one to a targeted patch, it gets the same treatment then (tracked, not built now).
- **No `docs/` edit** — this aligns the producers with the patch-path convention already documented
  for `framework.refinement`.
