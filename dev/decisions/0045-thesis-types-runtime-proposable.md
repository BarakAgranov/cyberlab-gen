# 0045 — thesis_types becomes a runtime-proposable registry (amends ADR 0016)

**Date:** 2026-06-07
**Phase:** 1 (operational hardening — the Wiz-CodeBuild `--auto` halt)
**Architecture refs:** `schema.md §4.8` (thesis block), §4.16 (proposal lifecycle),
`registry-details.md §1`/§7.6 (the open-set-but-no-runtime-proposal classification this
ADR overturns), `validation.md §6.4`. **Amends ADR 0016** (closed bundled-only catalogs).
Builds on ADR 0044 (the propose → overlay → validate loop).

## Context

ADR 0016 modelled `thesis_types` as a closed bundled-only catalog ("open-set in spirit
but no runtime proposal flow — grows by maintainer PR"), deliberately *outside*
`MergedRegistries`. The Wiz-CodeBuild `--auto` run then halted on
`unknown_thesis_type` for `ci_cd_compromise` and `credential_theft` — both obviously
valid thesis types the bundled set simply did not enumerate, and which the model had no
way to propose (there was no `propose_thesis_type` tool).

The maintainer's decision (this session's sign-off): the test for "should this be
runtime-proposable" is *"can the bundled registry enumerate every value that will ever
exist?"* For `thesis_types` the answer is no — the same as `value_types` and `facets`.
So `thesis_types` joins the runtime-proposable registries.

## Decision

1. **`thesis_types` is a first-class runtime registry**, part of `MergedRegistries`
   (bundled + overlay), like `value_types` / `facets` / `execution_contexts`. This
   **reverses ADR 0016's** treatment *for `thesis_types` only* — the other four catalogs
   (`detection_components`, `severity_levels`, `detection_formats`,
   `provisioning_mechanisms`) stay closed enum-keyed catalogs (their membership is owned
   by a `StrEnum`; they genuinely are closed industry-stable sets).

   - `ThesisTypeEntry` moves from `schemas/catalogs.py` to `schemas/registries.py`, gains
     `ENTRY_KEY_FIELD = "name"` and `proposed_by` / `proposed_in_run` (mirroring
     `ValueTypeEntry`). New `ThesisTypesRegistry` container, `MergedRegistries.thesis_type`
     accessor + index, `thesis_types` added to `REGISTRY_FILE_NAMES` and the merge.
   - `catalog_loader.load_thesis_types` and the catalog `ThesisTypesCatalog` are removed;
     the validator resolves thesis types against the merged registry, not a catalog.
   - The bundled `registry/thesis_types.yaml` keeps its shape (`{entries: [{name,
     description}]}`); entries default `proposed_by='maintainer'`.

2. **`propose_thesis_type` tool** added to the Extractor (a fourth tool), emitting a
   `ProposedThesisType`. No category gate (unlike facets). It feeds the same ADR-0044
   loop: provisional resolution at Layer 1, overlay write at the acceptance point,
   `approval='auto'`/`'human'`. `thesis_type_proposals` flows through `ExtractionResult`
   → `RunResult` → the orchestrator's `PendingProposals` and the eval re-validation.

3. **`external_data_sources` stays maintainer-PR-only** (per the sign-off and
   `schema.md §4.16` line 744): a new source needs adapter code (auth, response parsing),
   not just a registry row; a proposed source with no adapter would resolve at validation
   but be permanently unavailable at `external_lookup`. Not made proposable.

## Seeding the obvious missing entries — flagged, not done

The bundled `thesis_types` set is missing obvious-but-valid entries the Wiz run needed
(`credential_theft`, `ci_cd_compromise`). **This ADR does not hand-add them.** Rationale:
with the loop in place the Extractor proposes them and they accumulate in the overlay,
then promote to bundled via the telemetry-driven curation path (`schema.md §4.16` step 4)
once they prove broadly useful — which is exactly the mechanism this work exists to
enable. Seeding them by hand now would pre-empt that signal. **Recommendation for the
maintainer:** if a fast-follow is wanted, seed `credential_theft` and `ci_cd_compromise`
(and any other curated-walk gaps) into `registry/thesis_types.yaml` in a separate
docs/registry PR; it is low-risk but orthogonal to the mechanism.

## Doc divergence to reconcile (maintainer doc pass — not edited here)

This reversal makes the architecture docs stale for `thesis_types`; per CLAUDE.md these
are not edited from an implementation task. To reconcile in a docs PR:
- `registry-details.md §1` — move `thesis_types` from "open-set in spirit but no runtime
  proposal flow" to the runtime-proposable set; update the §8 summary table.
- `registry-details.md §7.6` — note it is no longer a closed bundled-only catalog.
- `schema.md §4.16` "Proposal authority by registry" — add `thesis_types` (Extractor).

## Consequences

- A spec naming a not-yet-registered thesis type the Extractor proposes now ships
  (provisional pass → overlay write) instead of halting — the second half of the
  Wiz-CodeBuild failure (the first half, facets, was already fixed by ADR 0044).
- `MergedRegistries` now holds seven registries; smoke tests updated (6 → 7).
- ADR 0016 stands for the other four catalogs; only its `thesis_types` clause is reversed.
