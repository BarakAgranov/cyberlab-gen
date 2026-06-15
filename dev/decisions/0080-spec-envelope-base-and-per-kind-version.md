# 0080 — `SpecEnvelope` base, per-artifact `spec_version`, and the spec_kind load gate

**Date:** 2026-06-15
**Phase:** 2 (Task 1 — LabManifest schema, commit 2)
**Architecture refs:** `architecture.md §1.5` (the framework owns versioning), `§0.6` (refuse to load
old-schema artifacts; never migrate). **Amends ADR 0069** (which deferred the `SpecEnvelope` base to
"Phase 2's second use" and used a single `CURRENT_SPEC_VERSION`). Resolves the `dev/phase-2-seams.md §2`
`SpecEnvelope` deferral against the real second instance (`LabManifest`).

## Context

ADR 0069 stamped/load-gated `AttackSpec.spec_version` but deferred extracting a shared base "to be
shaped by the real second instance," tentatively listing it as carrying `spec_version` / `spec_kind`
/ **`source`**. `LabManifest` (Task 1) is that second instance, and it disproves the `+source` guess:
`AttackSpec.source` is top-level, but `LabManifest`'s lives in `CoreBlock.source`. The base must be
shaped from what the two artifacts actually share.

## Decision

1. **`SpecEnvelope(ArtifactModel)` base** (`schemas/envelope.py`) carries the *versioning + load-gate
   identity* only: `spec_version: int = Field(ge=1)` and a `CURRENT_VERSION: ClassVar[int]` contract.
   `AttackSpec` and `LabManifest` subclass it.

2. **`source` stays per-artifact** — **not** hoisted into the base. The envelope's single
   responsibility is *being a versioned, loadable artifact* (dispatch on `spec_kind`, version-equality
   refusal). `source` is blog provenance, not part of that identity, and hoisting it would
   over-constrain a future non-blog-derived spec. The flat (AttackSpec, top-level) vs grouped
   (LabManifest, in `core`) placement is two independently reasonable choices, not an accident.
   **Seam:** if a cross-artifact `source` consumer ever appears, add a typed `artifact_source(spec)`
   accessor *at that point* — do not reshape the artifacts, do not pre-build the accessor now.

3. **`spec_version` is per-artifact** (amends ADR 0069's single `CURRENT_SPEC_VERSION`). Two constants
   — `CURRENT_ATTACK_SPEC_VERSION` (in `attack_spec.py`) and `CURRENT_MANIFEST_VERSION` (in
   `manifest.py`) — each tracking its own schema; each subclass sets `CURRENT_VERSION` to its constant.
   Rationale: the two artifacts evolve independently — a manifest field addition must not invalidate
   every on-disk AttackSpec.

4. **The load gate dispatches on `spec_kind`.** `schemas/loading.py::load_spec(data)` reads `spec_kind`
   off the parsed mapping, dispatches to the concrete model, validates, and refuses any spec whose
   `spec_version` ≠ `type(spec).CURRENT_VERSION` (`SpecVersionError`; never migrated, `§0.6`). It is
   the single home for loading a *persisted* spec — the future `validate` / `fix` / `plan` verbs route
   through it. `stamp_spec_version` is generalised (PEP-695 generic over `SpecEnvelope`) to stamp the
   right per-kind version via `type(spec).CURRENT_VERSION`, still folded into
   `stamp_framework_provenance`.

5. **`spec_kind` is declared per-subclass, not on the base** (the one detail the per-kind change
   forced beyond the stamp + gate). Each subclass declares `spec_kind: Literal[SpecKind.X] =
   SpecKind.X`. Putting `spec_kind: SpecKind` on the base and narrowing it to `Literal` in each
   subclass is an unsound mutable-attribute override (pyright `reportIncompatibleVariableOverride`);
   declaring it fresh per-subclass keeps the precise `Literal` discriminator without a suppression and
   does not change behaviour (the gate reads `spec_kind` from the data, not the base type).

## Alternatives considered

- **Hoist `source` into the base** (ADR 0069's tentative shape) — rejected: the second instance nests
  `source` in `core`; hoisting would change the locked manifest shape and over-constrain future specs.
- **One shared `CURRENT_SPEC_VERSION`** (ADR 0069) — rejected: couples the two artifacts' version
  lifecycles; a manifest bump would refuse every old AttackSpec.
- **`spec_kind: SpecKind` on the base, narrowed in subclasses** — rejected: unsound override; the
  per-subclass `Literal` is cleaner and suppression-free.
- **Route `extract`'s post-interrupt edit through `load_spec`** — rejected for now: that edit path
  benefits from the `spec_kind` default for hand-edits (a YAML omitting `spec_kind` defaults to
  `AttackSpec`), which the dispatcher would regress. `extract` keeps its AttackSpec-specific validator
  referencing `AttackSpec.CURRENT_VERSION`; `load_spec` serves the persisted-spec load paths.

## Consequences

- `AttackSpec` on-disk shape is unchanged (existing round-trip/persistence tests stay green); only its
  base class and the source of `spec_version` moved.
- `load_spec` is the spec_kind-dispatching gate the Phase-2 `plan` verb (Task 6) and the `validate` /
  `fix` verbs will consume; pinned by `test_load_spec_*` and `test_stamp_spec_version_is_per_kind`.
- A future framework-owned envelope field is added once, on `SpecEnvelope`.
