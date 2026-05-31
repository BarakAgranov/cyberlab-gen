# 0015 -- `OverlayRegistryFile.proposals` key type is `RegistryKey`, not `SnakeName`

**Status:** Accepted
**Date:** 2026-05-29
**Deciders:** Barak Agranov (with implementing agent)
**Supersedes / superseded by:** none

## Context

`OverlayRegistryFile.proposals` (`cyberlab_gen/schemas/registries.py`) was
typed `dict[SnakeName, ProposalAuditBlock]`. `SnakeName` matches
`^[a-z][a-z0-9_]*$` -- no colon allowed.

Five of the six registries key their entries by `SnakeName` (`value_types`,
`execution_contexts`, `external_data_sources`, `static_catalogs`,
`lab_credentials`). The sixth, `facets`, keys by `FacetName` --
`category:value`, e.g. `target:aws` -- which contains a colon and therefore
**cannot be a `SnakeName`**.

`OverlayRegistryFile._proposal_keys_match_entries` resolves each entry's key
via `ENTRY_KEY_FIELD` and `FacetEntry.ENTRY_KEY_FIELD == "name"`, so for a
facet the resolved key is a `FacetName` such as `target:aws`. But that key can
never be *stored* in a `dict[SnakeName, ...]`: Pydantic rejects it at parse
time with `string_pattern_mismatch` before the validator runs. The result is
that **facet proposals are structurally impossible** -- an overlay file
carrying a facet proposal fails Layer 1 validation no matter how well-formed
it is.

This was dormant in Phase 0 because no proposals ship in the seeds, but it
blocks the Phase 1 overlay proposal accept-flow: the first time the Extractor
proposes a new `target:*` / `runtime:*` / `lab_class_signal:*` facet, the
framework cannot persist it.

The defect is present **identically in the spec**: `schema-details.md §6.6`
line 1416 shows the same `dict[SnakeName, ProposalAuditBlock]` annotation, and
the doc-comment on line 1413 ("Keyed by entry name (or id, whichever the entry
type uses)") together with the example resolver on line 1431
(`getattr(entry, "name", None) or getattr(entry, "id")`) is internally
inconsistent with that annotation -- `name` for a facet is a `FacetName`, not a
`SnakeName`. So this is a spec-and-code fix, not a code-correction against a
correct spec.

The authoritative key-shape rule is stated plainly in
`registry-details.md §1` (final paragraph): "every entry's keys conform to the
`SnakeName` convention ... Facet names use `category:value` shape ... where the
prefix is one of the closed category enums and the value follows `SnakeName`."

## Decision

Introduce a named alias in `cyberlab_gen/schemas/primitives.py`:

```python
RegistryKey = SnakeName | FacetName
```

and type `OverlayRegistryFile.proposals` as
`dict[RegistryKey, ProposalAuditBlock]`.

## Alternatives considered

1. **Bare union inline** (`dict[SnakeName | FacetName, ...]`). Same semantics,
   but unnamed -- the intent isn't documented at the type level and isn't
   reusable. Rejected in favour of the named alias, which carries a docstring
   citing this ADR and the §1 rule.
2. **Second generic parameter** (`OverlayRegistryFile[E, K]`) so each registry
   pins exactly its key type. Tightest typing, but adds a type parameter to
   every instantiation site and to the loader (`merge.py`, `loader.py`) for a
   Phase-0 bug. Disproportionate churn; revisit only if a future need for
   per-registry key precision appears.
3. **Leave `proposals` keyed by `SnakeName` and store facets by base name**
   (strip the `category:` prefix). Rejected: the proposal key must match the
   entry's actual registry key (`_proposal_keys_match_entries` depends on it),
   and the entry key *is* the full `FacetName`. Stripping would desynchronise
   the two.

## Consequences

- Facet proposals are now representable. A new regression test
  (`test_overlay_facet_keyed_proposal_matches_entry`) fails on the old
  `SnakeName` shape and passes on `RegistryKey`.
- **The union does not loosen the cross-registry guarantee.**
  `_proposal_keys_match_entries` still rejects any proposal key with no
  matching entry. A `value_types` overlay carrying a facet-shaped proposal key
  fails on the no-corresponding-entry rule
  (`test_overlay_facet_orphan_proposal_key_fails` pins this), and a key that is
  neither `SnakeName` nor `FacetName` (e.g. `bogus:value` with an invalid
  prefix) still fails the key-type pattern
  (`test_overlay_facet_proposal_rejects_non_facet_non_snake_key`).
- `RegistryKey` is available for any future proposal-shaped structure that
  must admit both key shapes.

## Doc-improvement note (for the architect, not part of this change)

`schema-details.md §6.6` line 1416 should change
`proposals: dict[SnakeName, ProposalAuditBlock]` to
`proposals: dict[RegistryKey, ProposalAuditBlock]`, and the resolver on line
1431 should reflect the `ENTRY_KEY_FIELD` approach already in the code
(ADR 0004 incremental-doc-update policy). The §1 final-paragraph rule is the
canonical justification and needs no change.
