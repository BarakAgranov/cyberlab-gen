# 0069 — `spec_version` is framework-stamped and load-gated (no migration)

**Date:** 2026-06-10
**Phase:** 1 (pre-Phase-2 fix register — Batch A, item ①.3)
**Architecture refs:** `architecture.md §1.5` (the framework owns versioning, not the LLM),
`architecture.md §0.6` (refuse to *load* old-schema artifacts; never migrate), `schema.md` line 28
(the version is framework-recorded and gate-loaded). Source: investigation `0004 §1.1` (completeness
critic, hand-verified) / `§0.6`.

## Context

`AttackSpec.spec_version: int = Field(ge=1)` was **floor-checked only**. The Extractor emits the
whole `AttackSpec` (`output_type=AttackSpec`, set in `anthropic_provider.py`), so the model authored
the framework's own versioning fact — the same family as `lineage.model` / `completeness_score`. A
repo-wide search found **no `CURRENT_SPEC_VERSION` constant**, and `_load_spec_from_yaml`
re-validated an edited spec with `model_validate` and **no version check**. Yet `schema.md` promises
the version is framework-recorded and gate-loaded, and `architecture.md §0.6` makes "refuse to load
old-schema artifacts, never migrate" inviolable.

This matters before Phase 2: its `validate` / `fix` verbs load existing labs from disk — exactly
where an old-schema spec must be refused — and today there is neither a framework writer that stamps
the version nor a gate that refuses a mismatch.

## Decision

1. **`CURRENT_SPEC_VERSION = 1`** in `schemas/attack_spec.py` — the single source of truth for the
   version the framework writes.
2. **Framework-stamp on ship/persist.** `stamp_spec_version(spec)` overrides `spec_version` to
   `CURRENT_SPEC_VERSION`; it is folded into `stamp_framework_provenance(spec, ledger)` alongside the
   billed-model stamp (ADR 0065), so the one seam that applies framework-owned provenance stamps a
   spec applies *both*. The CLI runner's ship boundary and the shared `persist_pipeline_artifacts`
   both call it, so every spec written to disk carries the current version regardless of what the
   model emitted. The field keeps `ge=1` (a hand-built spec is never version 0); the model's emitted
   value simply never reaches disk.
3. **Load-time equality gate.** `_load_spec_from_yaml` now refuses a spec whose `spec_version` ≠
   `CURRENT_SPEC_VERSION` with a new `SpecVersionError` (the `§0.6` no-migration enforcement seam).

Scope is minimal (the chosen "minimal now, envelope later"): a shared `SpecEnvelope` base carrying
`spec_version` / `spec_kind` / `source` is **not** extracted here — that is deferred to Phase 2's
second use (`LabManifest`), when the real second instance can shape it without guessing.

## Alternatives considered

- **A `model_validator` forcing `spec_version == CURRENT_SPEC_VERSION`** — rejected. It would force
  the model to emit exactly the current version (the model should not know it), and it would fight
  the framework stamp (every `model_copy` re-validates). The gate belongs in the *load path*, not as
  an always-on construct-time invariant.
- **Extract a `SpecEnvelope` base now** — deferred. It touches the locked `AttackSpec` emit surface
  and is better shaped by the real second instance in Phase 2 (a Tier-4 item, verifier-lowered to
  should-fix).
- **Migrate old specs** — forbidden by `architecture.md §0.6`.

## Consequences

- Every persisted/shipped spec carries `CURRENT_SPEC_VERSION`; the model's emitted value never wins
  (pinned: `test_persist_stamps_current_spec_version`, `test_stamp_spec_version_sets_current`).
- Loading a spec with a different version is refused (`test_load_spec_refuses_unsupported_version`);
  a current-version spec loads unchanged (`test_load_spec_accepts_current_version`).
- `stamp_framework_provenance` is the single place framework-owned fields (model + version) are
  stamped onto a spec, so a future framework-owned field is added in exactly one location.
- Phase 2's `validate` / `fix` load paths reuse `CURRENT_SPEC_VERSION` + the gate when built.
- **No `docs/` edit** — `schema.md` line 28 and `architecture.md §0.6` already describe this; the
  code now matches. (Doc-improvement note: `schema.md:74`, cited in the fix register for the version
  promise, actually describes `LabManifest`; the AttackSpec promise is `schema.md:28`.)
