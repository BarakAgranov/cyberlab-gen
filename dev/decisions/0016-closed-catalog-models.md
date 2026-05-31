# 0016 -- Closed bundled-only catalogs: enum-keyed entry models, outside `MergedRegistries`

**Status:** Accepted
**Date:** 2026-05-29
**Deciders:** Barak Agranov (with implementing agent)
**Supersedes / superseded by:** none

## Context

`registry-details.md §7` documents five "closed bundled-only catalogs":
`detection_components` (§7.1), `severity_levels` (§7.2), `detection_formats`
(§7.3), `provisioning_mechanisms` (§7.4), and `thesis_types` (§7.6). (§7.5,
`lab_credentials`, was already modelled and seeded in Task 4 and is not part of
this change.)

Task 3 shipped no Pydantic models for these five; Task 4 shipped no seed YAMLs.
The Phase-0 smoke check (`implementation-plan.md §3.4` check 4) explicitly
*deferred* them ("and each closed bundled-only catalog once those get Pydantic
models"). The Task 4 log flagged this as needing "a Phase-1-prep task to add
the models and seeds in lockstep." Phase 1 consumers need at least
`thesis_types` (the Extractor's `ThesisBlock.types`) and `severity_levels`
(Layer 3 severity-floor rules), so the gap blocks Phase 1.

Two facts shaped the design:

1. **Four of the five already have closed `StrEnum`s** in
   `cyberlab_gen/schemas/enums.py`: `DetectionComponent`, `Severity`,
   `DetectionFormat`, `ProvisioningMechanism`. Those enums are what validate
   the corresponding *fields* on the artifact models (`detection_format: kql`,
   `provisioning_mechanism: terraform`, `severity`, etc. -- see
   `schema.md §4.5` / §4.7). The catalogs are not a second list of which values
   exist; they carry display/ordinal/extension/validator-support metadata the
   enums cannot hold.
2. **`thesis_types` has no enum, by design.** `registry-details.md §1` classes
   it as "open-set in spirit but no runtime proposal flow" -- it grows by
   maintainer PR informed by telemetry, not by a closed enum. Its absence from
   `enums.py` is correct, not an omission.

## Decision

1. **New module `cyberlab_gen/schemas/catalogs.py`**, separate from
   `registries.py`, because these are not proposal-flow registries
   (`registry-details.md §1`) and should not sit alongside the six runtime
   registries.

2. **The four closed catalogs key their `name` on the existing enum.**
   `DetectionComponentEntry.name: DetectionComponent`,
   `SeverityLevelEntry.name: Severity`, etc. Membership stays owned by the
   enum -- a YAML value the enum doesn't know fails validation -- so there is no
   duplicate source of truth. Each entry adds only the metadata the enum can't
   carry (`display_name`, `description`, `ordinal`, `file_extension`,
   `validator_support`).

3. **`thesis_types` keys on `SnakeName`, not an enum**, matching its open-set
   classification. A new snake-case thesis type validates without a code
   change; the seed is the §7.6 curated-walk enumeration.

4. **These catalogs are NOT part of `MergedRegistries`.** The merged view
   (`schema-details.md §6.6`) holds the six runtime-consulted registries.
   These five are consulted on demand by specific consumers (Layer 3 reads
   `ordinal`; the Generator reads `validator_support`; the Docs Generator reads
   `display_name`) -- the same pattern as `lab_credentials` and
   `static_catalogs`, which are also consulted rather than iterated. A separate
   catalog-loader lands with the Phase-1 consumer that first needs it; Phase 0
   ships the models and seeds only, satisfying the deferred smoke check.

5. **The five entry models are deliberately not uniform.** Four are enum-keyed
   with differing metadata fields; one is `SnakeName`-keyed. A single shared
   base would have forced `thesis_types` into the closed-enum mould or stripped
   the enum-keying from the other four. The non-uniformity reflects a real
   distinction documented in §1, not an inconsistency.

## Alternatives considered

- **Fold the catalogs entirely into the enums** (no models, no YAML). Rejected:
  the enums cannot hold `ordinal` / `display_name` / `file_extension` /
  `validator_support`, which Layer 3, the Docs Generator, and the Generator
  need.
- **Five fresh models with `SnakeName` names, ignoring the enums.** Rejected:
  creates exactly the duplicate-source-of-truth the architecture forbids -- two
  independent lists of which detection formats exist, free to drift.
- **Put the catalogs in `MergedRegistries`.** Rejected: they are not
  proposal-flow registries and are consulted, not iterated; adding six
  accessors and merge logic for read-only closed sets is unwarranted.
- **One uniform catalog-entry base class.** Rejected per decision point 5.

## Consequences

- All five §7 seeds now exist as YAML and validate against their models
  (counts: 10 / 4 / 4 / 7 / 10). The deferred Phase-0 smoke check can be
  un-deferred.
- The four closed catalogs reject values absent from their enum; ordinals are
  bounded `[1, 4]`; `validator_support` is a closed `Literal`; `thesis_types`
  accepts arbitrary `SnakeName`. All inherit `extra="forbid"` from
  `ArtifactModel`, so unknown fields and stray `proposals:` blocks fail.
- A Phase-1 catalog loader (and its placement relative to `registries/`) is
  left open; the models and seeds are loader-agnostic.

## Doc-improvement note (for the architect, not part of this change)

`implementation-plan.md §3.4` check 4 can drop its "once those get Pydantic
models" deferral for these five catalogs. `registry-details.md §7.2`'s "or
inlined in the schema" aside for `severity_levels` is now resolved in favour of
a YAML seed plus a metadata-carrying model (the enum carries membership; the
model carries `ordinal`).
