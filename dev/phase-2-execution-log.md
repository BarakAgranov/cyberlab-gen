# Phase 2 — Execution log

Append-only record of Phase 2 (Planner + Jury) task execution. One entry per task,
per the template at the bottom of `dev/phase-2-agent-brief.md`. Terse; surprises and
drift must be specific. This log feeds Phase 3's brief.

> **Task 0** (architect doc reconciliation) is the maintainer's and is tracked in the
> brief; it is not logged here until done. Wave-1 implementation starts at Task 1, which
> is independent of Task 0.

---

## Task 1: Full LabManifest schema + SpecEnvelope base  (2026-06-15)

**Built.** The complete `LabManifest` envelope and 16 inner blocks (`schemas/manifest.py`,
per `schema-details.md §5`) on `ArtifactModel`, with the four cross-field validators
(`ProducesWorldState` identifier XOR, `PrereqBlock` kind rules, `InputBlock` default rule,
`OutputBlock` reference XOR). Two new registry-validated primitives `ValueTypeName` /
`ExecutionContext` (= `SnakeName`, no embedded validator — membership is the Layer-1 pass).
Lab-level reproducibility reuses the AttackSpec `ReproducibilityBlock` (derivation is Task 2).
A shared `SpecEnvelope` base (`schemas/envelope.py`) carrying `spec_version` + a per-kind
`CURRENT_VERSION`; `AttackSpec`/`LabManifest` subclass it. A spec_kind-dispatching load gate
(`schemas/loading.py::load_spec`) + per-kind constants (`CURRENT_ATTACK_SPEC_VERSION`,
`CURRENT_MANIFEST_VERSION`); `stamp_spec_version` generalised (PEP-695 over `SpecEnvelope`).
Tests: `tests/unit/schemas/test_manifest.py` (20) — representative multi-phase round-trip,
every validator's failure modes, min-length constraints, `extra="forbid"`, path-optional
skeleton, spec_kind dispatch, per-kind version refusal, per-kind stamp. Two commits:
`c2a5eeb` (schema), `d405f57` (envelope + gate).

**Decisions.** ADR 0079 (`PhaseImplementation.path` is optional — Planner skeleton, Generator
materializes, Layer 2 enforces `path == derive(id)` + file-exists). ADR 0080 (`SpecEnvelope`
base; `source` per-artifact; per-artifact `spec_version` amending ADR 0069; spec_kind load
gate; spec_kind declared per-subclass).

**Surprises / drift.**
- **`StepBlock` is NOT from Phase 1** — the brief claimed it was reused; Phase 1 built `ChainStep`
  (the AttackSpec's narrative step). `StepBlock` is manifest-only and new here. Brief corrected.
- **`ValueTypeName` / `ExecutionContext` did not exist** as primitives though `§5` uses them
  pervasively. Added as open-set registry-validated `SnakeName` aliases (the `ThesisType` pattern).
- **`PhaseImplementation.path` required in `schema-details.md §5.5` vs `agents.md §5.7` ("without
  `implementation.path`")** — a required path makes the Planner's own deliverable fail Layer 1.
  Resolved optional (ADR 0079); architect-approved doc edits to `schema-details.md §5.5` +
  `schema.md §4.5`.
- **ADR 0069's deferred `SpecEnvelope` "+source"** does not survive the second instance: `AttackSpec.source`
  is top-level, `LabManifest`'s is nested in `CoreBlock`. `source` stays per-artifact (ADR 0080).
- **`spec_kind` Literal-narrowing on a shared base** trips pyright `reportIncompatibleVariableOverride`;
  declared per-subclass instead (keeps the precise discriminator, no suppression).
- `spec_version` is now per-artifact (two constants), amending ADR 0069's single `CURRENT_SPEC_VERSION`.

**Deferred.**
- Layer 2's `path == derive(id)` + file-exists check → Phase 3 (`TODO(phase-3)` on `PhaseImplementation`).
- `extract` is **not** routed through `load_spec` (its edit path relies on the `spec_kind` default for
  hand-edits); possible future unification. `load_spec` serves the persisted-spec load paths (Task 6+).
- `artifact_source(spec)` accessor — only if a cross-artifact `source` consumer appears (no second use).

**Verify.** `just verify` green — ruff + format clean, pyright 0 errors (40 pre-existing yaml/click
warnings), pytest 744 passed / 1 skipped.

---

## Task 1 (amendment): per-step reproducibility on `StepBlock`  (2026-06-16)

**Built.** Added the required `reproducibility: PerStepReproducibility` field to `StepBlock`
(`schemas/manifest.py`), reusing the AttackSpec block (mirrors `CoreBlock.reproducibility`). Two
tests in `tests/unit/schemas/test_manifest.py` (carries + round-trips the tier; the field is
required); the representative `_manifest()` now exercises **mixed** per-step tiers (phase 1 `full`,
phase 2 `demonstration_only`). Architect-approved doc edit to `schema-details.md §5.6` adds the field
(the architecture docs were already correct and are untouched).

**Decisions.** ADR 0081 (per-step reproducibility lives on the manifest `StepBlock`, carried forward
unchanged; lab-level derives from the AttackSpec's chain steps over all non-dropped steps; reject
the AttackSpec-only "Reading B" and the `implements_chain_steps` enrichment).

**Surprises / drift.** This **reopened the locked Task-1 schema** — the legitimate
first-consumer-infeasibility exception (`brief:156-163`), architect-ruled. Root cause: `StepBlock`
(`schema-details.md §5.6`) omitted the per-step tier that `architecture.md §0.7`/`§1.1`,
`pipeline.md`, and `agents.md §5.7`/`§5.9` all say the manifest carries; the doc, not the
architecture, was incomplete. The decisive consumer is the manifest-driven Per-phase Generator
(`agents.md §5.9`), whose AttackSpec access is prose excerpts only (`§5.18`), so it can read the
structured tier *only* from the manifest.

**Deferred.** (1) **Task 0 doc reconciliation** — D2 (`§4.8`/`§5.7` "Planner applies the rule" →
framework derives), D3 (the "who applies the §4.20 ladder" three-way muddle), D4 (`§5.7:214`
`not_reproducible`→`demonstration_only` contradicts `§0.7` "without modification"); all
architecture/agents edits, architect-owned. (2) `StepBlock`→`chain_step` back-ref for a Layer-2
carry-integrity check — Task 3 call. (3) Task 2 sources the lab-level rollup from the AttackSpec
`chain.chain_steps[*].reproducibility` (all non-dropped) — pinned by ADR 0081, built in Task 2.

**Verify.** `just verify` green — ruff + format clean, pyright 0 errors (40 pre-existing warnings),
pytest 746 passed / 1 skipped.

---

## Execution-log entry template

```
## Task N: <title>  (<date>)

**Built:** <what shipped — files, models, tests>
**Decisions:** <ADRs opened, with numbers>
**Surprises / drift:** <doc-vs-code drift, friction logged, anything the next task should know>
**Deferred:** <anything intentionally not done, with the owning task/phase>
**Verify:** <just verify result>
```
