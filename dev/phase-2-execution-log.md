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

## Task 0: Architect doc reconciliation  (2026-06-16)

**Built (docs only).** Reconciled the Phase-2 reading-path drift (brief Task 0) plus the new
drifts an engineering audit surfaced:
- **Reproducibility ownership (ADR 0081 follow-up).** `architecture.md §0.7` + `agents.md §5.7` +
  `schema.md §4.8`: the Extractor assigns the per-step tier (applies the §4.20 ladder); the Planner
  carries it forward *unchanged* and decides structural realization; the **framework** derives the
  lab-level rollup (`§1.5`). Removed the stale "Planner/Generator applies the ladder" (D3) and
  "`not_reproducible`→`demonstration_only`" upgrade (D4) lines; `schema.md §4.8` now attributes the
  derivation to the framework (D2). Added the carried-forward `reproducibility` to the §4.7
  manifest-step YAML example (D-04).
- **LabPlan → LabManifest (D-01).** `pipeline.md` + `agents.md` called the Planner's artifact
  "LabPlan" (no such model exists); renamed throughout, fixed the §3.3 contract-table rows, and
  dropped the duplicate in the artifacts list.
- **CLI verbs.** `architecture.md §2.1` listed four verbs; added `extract` (Phase 1) and `plan`
  (Phase 2) per the locked `extract → plan → generate` staging.
- **implementation-plan.md §5.** Phantom `FacetReference` block (D-02 → `facets: list[FacetName]`);
  "StepBlock from Phase 1" (D-03 → manifest-only, new in Phase 2); `lab-manifest.yaml`→`lab.yaml`
  (D-05); the LabPlan ref; and the `4 of 5` blog count → `2 of the 3` (the Phase-1 curated set is 3,
  one synthetic with no live URL).
- **Jury tools verify-only (D-07).** `agents.md §5.5`/`§5.8` "Same as Extractor/Planner" → the
  ADR-0078 verify-only contract (no `propose_*`); the §5.18 matrix was already correct.

**Already done / deferred.**
- `material_discrepancies` doc mirror — **already present** in `schema-details.md §4.9` (the
  `dev/phase-2-seams.md §3` entry was stale; corrected).
- `BaseModel`→`ArtifactModel` `schema-details.md` sweep — **deferred per ADR 0004**, which explicitly
  rejected a blanket sweep (~50 classes; §6 / `MergedRegistries` need per-class code checks — some are
  `InternalModel`, not `ArtifactModel`) in favor of incremental-per-transcription. Not done as a
  blanket Task-0 edit; remains tracked.

**Decisions.** ADR 0081 (per-step reproducibility placement), 0082 (framework-provenance
neutralization), 0083 (convention reconciliation) — opened across the audit follow-up.

**Verify.** `just verify` green — 763 passed / 1 skipped (doc-only edits in this entry).

---

## Architect-review follow-up: scoped reset + framework-owned-field guards  (2026-06-16)

**Built.** Acted on a tagged architect review (items #1–#7), reported-before-acting.
- **#1 + #7 (blocker fix, ADR 0085).** The extract-seam reset (ADR 0082) ran on the *merged*
  refine output, wiping a prior iteration's blog-vs-API material discrepancy that re-enrichment
  could no longer re-detect — silently dropping it. Scoped the reset to *what the run authored*:
  whole-spec on first run / structural retry / grounding retry; on a jury revise the patch
  `new_value`s are scrubbed at the merge seam (`neutralize_patch_provenance` in
  `apply_field_patch`) and the orchestrator no longer blanket-scrubs the merged spec. The
  first-run reset now also nulls `CveReference.source_of_record` (a forgeable framework id on
  every skipped enrichment lookup) and the framework-derived lab-level `reproducibility` block —
  two live holes the #7 sweep found. Tests: orchestrator discrepancy-survival regression (RED→
  GREEN), patch-cannot-forge-provenance / source_of_record, first-run nulls. Subsumes D5-02.
- **#3 (test).** Marker-invariant test pinning `{source, citations, framework_enriched}` as
  unique to `Provenance` (no offenders today — the guard can't silently scrub a future model).
- **#7 principle (ADR 0086, docs).** One guard per framework-owned field, on a path the field
  travels; four mechanisms (stamp / reset / derive / absent-from-LLM-schema); the audit rule;
  the field inventory. Recorded — not built — the two Planner-coupled prereqs (manifest-side
  framework stamping; `StepBlock.reproducibility` carry-integrity) in `dev/phase-2-seams.md`.
- **#2 (VERIFY→docs).** Strip-tested `from __future__ import annotations` on real `main`:
  removal raises `NameError` on TYPE_CHECKING-only names in eager annotation positions
  (`MitreTechniqueCatalog` dataclass field; `MergedRegistries` / `Path` signatures), **not**
  `get_type_hints(PipelineState)`. Corrected ADR 0083 CONV-2 + the `orchestrator.py:49-53`
  comment; import kept (no promote-to-droppable).
- **#5 (docs).** `pipeline.md §3.2.6` L163: the Planner *carries* reproducibility forward (the
  Extractor applied `§4.20`), does not re-apply the ladder — the residual D3 leak.
- **#4 (docs).** `provenance.py` comment: the only mechanical `framework_enriched` exemption is
  the CVE-scoped grounding check; the jury has none of its own.
- **Governance (ADR 0084).** `CLAUDE.md`: agent owns `docs/` edits (incl. architecture-tier),
  surfaced never silent — per maintainer instruction ("you edit, I verify").

**Decisions.** ADR 0084 (doc-edit authority), 0085 (scoped extract-seam reset), 0086
(framework-owned-field guard principle); corrected ADR 0083 CONV-2 rationale.

**Surprises / drift.**
- The blocker was an interaction of two correct-in-isolation mechanisms: the enrichment
  idempotency no-op (`enrichment.py:508`, whose comment anticipates *exactly* this loss) and
  ADR 0082's neutralize, which reset `framework_enriched=False` upstream and defeated the no-op.
- The verification workflow's `isolation: worktree` probes checked out a **stale base**
  (`3d81583`, pre-ADR-0082), so the empirical repro (probe-1c) ran against code lacking the
  fix target; the read-only agents + two adversarial skeptics on real `main` (plus a manual
  re-read of the two crux links) carried the #1 confirmation. The orphaned worktree + branch
  were pruned.
- `real_world_incidents.status` is **not** a framework-owned hole today — Extractor-authored
  from the blog until an incidents enrichment source lands (inventoried with that trigger).

**Deferred.** Manifest-side framework stamping + `StepBlock.reproducibility` carry-integrity
(Planner-coupled; ADR 0086 / seams). `stamp_framework_provenance`'s "single place" comment
should point at ADR 0086 (doc nit, not blocking). `BaseModel→ArtifactModel` schema-details
sweep still tracked (ADR 0004).

**Verify.** `just verify` green — 769 passed / 1 skipped (+6 tests since Task 0).

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
