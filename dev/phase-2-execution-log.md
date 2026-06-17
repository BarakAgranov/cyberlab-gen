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

## Architect-review follow-up #2: declared field ownership (2026-06-16)

**Context.** A second architect review of the ADR-0085 close-out hypothesized a patch-path
blind spot in the same class as #7. Item-1 VERIFY (read-only + 4 scratch probes on real HEAD +
a 3-skeptic adversarial workflow, all `overall_refuted: false`) **confirmed** it and found a
fourth shape the review didn't name: `apply_field_patch` imposes no content-field restriction,
and `neutralize_patch_provenance` scrubs by *shape*, so four forged framework-owned values reach
both the jury and disk on the refine path — (A) bare-leaf `source_of_record`, (B) top-level
`material_discrepancies`, (C) top-level `reproducibility`, (D) bare-leaf `framework_enriched` on
an already-`external_api` field (the `§1.6` hole: bypasses the enrichment no-op + the grounding
exemption). The existing `test_patch_cannot_forge_*` tests covered only whole-sub-tree shapes.

**Built (ADR 0087, 3 commits).**
- `cyberlab_gen/schemas/framework_owned.py`: `FrameworkOwned` inline marker (frozen dataclass,
  kept extensible for a future `mechanism` field) + cached `framework_owned_fields(model)`.
- Marked the reset-mechanism owned fields inline: `Provenance.{framework_enriched,
  discrepancy_with_blog, overridden_blog_value, discrepancy_classification}`,
  `CveReference.source_of_record`, `AttackSpec.{material_discrepancies, reproducibility}`.
- `provenance_guard.framework_owned_path_buckets()` generates the patch-path denylist from the
  markers (root-marked → top-level segment; nested-marked → leaf name) via a reachable-models
  walk; `apply_field_patch` now rejects a framework-owned target path (`RefinementPathError`).
  Closes A/B/C/D. Coverage test pins the generated denylist against the markers.
- `neutralize_framework_owned_provenance` rewritten as a marker-driven **instance** walk (exact
  `type()` per node, no shape heuristics, no union ambiguity); `_scrub_node` stays only for the
  pre-validation patch-value path (the deferred path→type residual).
- Fold-ins: `stamp_framework_provenance` docstring → ADR 0086/0087; ADR-0085 consequence note
  corrected (the no-forge claim was shape-scoped; now enforced by the path-check); ADR 0086
  marked partially-superseded.

**Decisions.** ADR 0087 (ownership declared inline; consumers derive; supersedes 0086's
hand-audit + inventory table, keeps the four-mechanism split).

**Surprises / drift.**
- `reproducibility` is the **one** ambiguous name in AttackSpec — framework-owned on `AttackSpec`
  (top-level, derived), authored content on `ChainStep` (per-step). Flat positional bucketing
  separates them today; it **provably collapses for LabManifest**, where `CoreBlock.reproducibility`
  (owned, `manifest.py:89`) is *nested* alongside `StepBlock.reproducibility` (content,
  `manifest.py:288`). That collision (already in the declared schema) is the concrete trigger
  for the deferred marker-aware path→type resolver (ADR 0087 "recorded, not built").
- `AlternativePath.reproducibility_summary` classified **authored, not owned** (schema.md §4.8
  "captured… not generated"); deliberately unmarked, with a code comment, and flagged for the
  architect to overrule if alt-path summaries are meant to be a framework rollup.
- The inline-`Annotated` marker survives the `Provenance[T]` generic + ADR-0066 pickle path
  (spiked on the real `Provenance` before building) — so inline over per-model ClassVar.

**Deferred.** Marker-aware runtime path→type resolution (triggered by LabManifest refinement);
stamp-mechanism fields (`spec_version`, `extraction_metadata.model`) join the marker set when
`FrameworkOwned` carries `mechanism`. Manifest-side stamping + `StepBlock.reproducibility`
carry-integrity still Planner-coupled (seams). `BaseModel→ArtifactModel` doc sweep (ADR 0004).

**Verify.** `just verify` green — 777 passed / 1 skipped.

---

## Task 2: Lab-level reproducibility derivation  (2026-06-16)

**Built.** `framework/reproducibility.py`: `classify_lab_level(tiers) -> ReproducibilityLabLevel`
(the pure `schema.md §4.8` any-heterogeneity-mixed rule — required = not-`not_reproducible`;
all-same → that tier; span ≥2 → `mixed`; none-remain → `not_reproducible`) and
`derive_lab_reproducibility(spec) -> ReproducibilityBlock` (assembles classification + mechanical
`caveats` [tier proportions over all chain steps] + `derivation_trace` [per-step tiers, dropped
ones marked excluded, + the result], `overall_assessment` left `None`). Sources the AttackSpec's
**canonical `chain.chain_steps` only** (ADR 0081), not manifest steps, not `alternative_paths`.
Both pure, no I/O; re-exported from `framework/__init__.py`. Tests:
`tests/unit/framework/test_reproducibility.py` (25) — every rule branch parametrized (homogeneous
×3 tiers, `mixed` incl. 4:1 + 99:1 lopsided, drop-exclusion, all-dropped/empty → `not_reproducible`,
single-step), block-assembly (proportions, dropped-but-visible, canonical-only-not-alt-paths,
round-trip, defensive `chain=None`) + 1 schema test pinning the optional `overall_assessment`.

**Decisions.** ADR 0088 — the rule + the all-dropped→`not_reproducible` reading (the **refusal**
stays Task 3's `cannot_plan`; Task 2 only classifies) + widening
`ReproducibilityBlock.overall_assessment` to `ProvenanceString | None = None` (architect-approved).

**Surprises / drift.**
- **`overall_assessment` had no honest framework `ProvenanceSource`.** The whole
  `ReproducibilityBlock` is `FrameworkOwned` (reset on first run, ADR 0087) so an LLM never authors
  it, yet `§4.9`'s source vocabulary is LLM/blog/API/user only — no framework value. Resolved
  (architect, Option 2): the framework derives only the three fields `§4.8` names
  (`classification_lab_level`, `caveats`, `derivation_trace`) and leaves the prose `None`; a later
  prose-producer authors it with a real source. **Rejected** adding `ProvenanceSource.FRAMEWORK_DERIVED`
  — re-expands the shared-`Provenance` forge surface D5/ADR 0085–0087 just closed, and *worse* than
  `framework_enriched` (no `external_api` field-position coupling to validate against).
- **`§4.8` was silent on the all-dropped edge** and on who refuses; both now stated in `schema.md §4.8`.
- **`AttackSpec.chain` is `ChainBlock | None`** (out-of-scope specs carry no chain) — the derive fn
  handles `None` defensively (→ empty → `not_reproducible`), never crashes.
- `FrameworkOwned` marker untouched (architect note); the ADR-0087 reset stays the active guard;
  `mechanism=` stays unpopulated. The post-Planner graph insertion is **Task 6's** (owns wiring) —
  not built here against a non-existent Planner/`plan` graph.

**Deferred.** Post-Planner wiring of `derive_lab_reproducibility` into the `plan` graph (Task 6);
the Planner's per-step carry-forward into `StepBlock` (Task 3); `overall_assessment` prose authoring
(later phase / architect). Doc edits surfaced: `schema.md §4.8` (all-dropped rule + optional note),
`schema-details.md §4.7` (optional field contract), inline on `attack_spec.py`.

**Verify.** `just verify` green — ruff + format clean, pyright 0 errors (40 pre-existing warnings),
803 passed / 1 skipped (+26 since the last entry).

---

## Task 3: Planner agent (non-proposing for the slice)  (2026-06-17)

**Built.** The `planner` subpackage (`agents/planner/`): `Planner(ToolUsingAgent)` with
`plan(attack_spec, *, preferences=None) -> PlanResult` — capability `HIGH_QUALITY_REASONING`,
`output_schema=LabManifest`, an AttackSpec-YAML + registry-digest user turn, and a framework
finalize (`plan()` overwrites `core.reproducibility` with `derive_lab_reproducibility(spec)`,
Task 2). `planner/tools.py`: `planner_tool_definitions` (the slice producer set = read-only
`external_lookup` only) + `PlannerToolExecutor` (a read-only `ExtractorToolExecutor` subtype
reusing the shared lookup engine). `planner/prompt.md` base prompt. `PlanResult(manifest, lookups)`
in `agents/results.py`. **Generalised the `ToolUsingAgent` contract**: factored the hardwired
Extractor inventory out of `_emit` into an overridable `_build_tools_and_executor()` hook
(default = today's Extractor wiring, so Extractor/Jury are byte-unchanged). `make_manifest()` added
to `pipeline_fakes`. Tests: `tests/unit/agents/test_planner.py` (9) — structural-valid + round-trip
manifest, lab-level reproducibility **derived overwriting a wrong mock value** (+ prose dropped to
`None`), per-step tiers carried through unchanged, capability-resolved model (no hardcoded name),
output cap reaches the provider, output schema rejects an untyped input, tool set = `external_lookup`
only / excludes the value-type proposal the Extractor keeps, executor serves lookup + refuses
proposals.

**Decisions.** ADR 0089 (the `ToolUsingAgent` tool-provider hook — extends ADR 0072; mandated by
the no-discretion "producer-not-jury" + "no value-type proposals" constraints, *not* a choice).
ADR 0090 (the Planner emits the full `LabManifest`; the framework derives `core.reproducibility`
in `plan()` — derive-at-seam, field stays required; considered + rejected the absent-from-LLM-schema
reduced-draft mechanism as a high-drift mirror of the actively-evolving manifest).

**Surprises / drift.**
- **The base `_emit` hardwired the Extractor tool inventory** (`ExtractorToolExecutor` +
  `extractor_tool_definitions`); its only knob is `verify_only`, and **neither** mode expresses the
  Planner's set — `verify_only=False` advertises the Extractor's `propose_*` (value-type authority),
  `verify_only=True` is the *jury* set. So the hook (ADR 0089) was **forced**, not optional. ADR
  0072's "subclass instead of re-copy" quietly assumed every agent shares the Extractor's inventory
  — the Planner is the first to break it (Generators/Critic break it again).
- **`query_value_types_registry` deferred to Task 7** (architect call). A non-proposing Planner
  references registered value-types by name from the prompt's registry digest — it has nothing to
  shape-search until proposing lands, so wiring it now would build an unexercised tool. The slice's
  producer set is `{external_lookup}` only (kept — the baseline read primitive, exercised by a test).
- **`CoreBlock.reproducibility` is required *and* framework-derived.** Resolved derive-at-seam in
  `plan()` (ADR 0090). `spec_version` + `GenerationBlock.model` are the manifest's other
  framework-owned fields — stamped at the **persist seam** (`run_persistence.py`), wired in Task 6,
  mirroring how the Extractor defers its own stamps (ADR 0086 marks the manifest `spec_version`
  stamp "prospective (Planner)"). `plan()` does **not** copy them (no third billed-model copy).
- **`StaticSchemaValidator` is AttackSpec-only** (no manifest Layer-1 / no value-type membership
  check yet — both Task 5/6). So "Layer-1-valid manifest" this phase = Pydantic structural validity
  + YAML round-trip; "untyped input fails the quality bar" is enforced by the output schema itself
  (`InputBlock.type` required — no untyped fallback).
- `PlannerToolExecutor` **subclasses** `ExtractorToolExecutor` (read-only) to reuse the
  `external_lookup` engine (NVD / unavailable / rate-limit, ADR 0042) without duplication — a
  pragmatic reuse, not "the Planner is an Extractor"; Task 9 should swap to the neutral ports module
  (ADR 0077).

**Deferred.** `query_value_types_registry` + the scoped `propose_facet` (Task 7); the `plan` verb +
graph wiring + persistence + `spec_version`/`GenerationBlock` stamping (Task 6); the Planner↔Jury
revise loop, AttackSpec-incoherence route-back, and the `cannot_plan` refusal path (Task 4);
`StepBlock.reproducibility` carry-integrity as a Layer-2 check (Task 5 / seams). No `docs/` edits —
the architecture already states lab-level reproducibility is framework-derived (`schema.md §4.8`);
ADR 0090 records *where* (`plan()`) and *which mechanism*.

**Verify.** `just verify` green — ruff + format clean, pyright 0 errors (40 pre-existing click/yaml
warnings), 812 passed / 1 skipped (+9 since Task 2).

---

## Task 4: Planner-Jury (verify-only) + refinement extension + route-back  (2026-06-17)

**Built.** Three commits.
- **Marker-aware path resolver (ADR 0091).** `provenance_guard.resolve_framework_owned(root, segments)`
  walks the artifact schema (unwrapping `Optional` / `list[T]` / `Provenance[T]`, descending `[i]`)
  to the exact `(model, field)` a `field_path` names and reads its inline `FrameworkOwned` marker —
  **replacing** the flat positional `framework_owned_path_buckets` (deleted, with `_reachable_models`)
  for *both* artifacts. `apply_field_patch` is now generic over `SpecEnvelope`
  (`type(prior).model_validate`), so the same convergent deep-set serves AttackSpec and LabManifest.
  `CoreBlock.reproducibility` marked `Annotated[..., FrameworkOwned()]`. Tests: resolver unit cases +
  manifest patch (convergence/byte-identity, `core.reproducibility` rejected, per-step content
  allowed); the two AttackSpec marker-drift pins rewired onto `framework_owned_fields`.
- **Planner route-back via `PlanAttempt` (ADR 0092).** `PlanOutcome {planned, attackspec_incoherent,
  cannot_plan}` + `PlannerRefusal` + the discriminated `PlanAttempt` wrapper (coupling validator),
  in the leaf `agents/results.py`; `PlanResult` extended (outcome + optional manifest/refusal +
  reprompts). `Planner.plan` forces `PlanAttempt`; `Planner.refine` adds the manifest targeted-patch
  path (re-deriving `core.reproducibility` after the patch — guard every path); `_finalize_manifest`
  factored. `errors.PlanningError` (the refine patch-budget halt). Tests: route-back/cannot_plan
  outcomes, the coupling validator, refine convergence/re-derive/owned-target-rejection.
- **Planner-Jury + plan coordinator (ADR 0093).** `agents/planner_jury/` — `PlannerJury(ToolUsingAgent)`,
  `verify_only_tools=True` (ADR 0078), reuses `JuryVerdict`, own 0.7 rubric-floor placeholder +
  asymmetric discipline. `framework/plan_orchestrator.py` — `build_plan_pipeline` / `run_plan_pipeline`
  / `finalize_plan_outcome` (Planner → Planner-Jury, linear): `revise` → `Planner.refine` (bounded by
  `refinement_cap`) → exhaust → `low_jury_confidence`; `reject`/`cannot_plan` → halt;
  `attackspec_incoherent` → `ROUTE_BACK_TO_EXTRACTOR` (a *returned* outcome); sub-floor-approve
  backstop (ADR-0067 mirror); global iteration cap (ADR 0056). `framework/graph_support.py`
  (`traced_async` / `traced_sync`) extracted and shared — the extract orchestrator refactored onto
  them (`_traced_*` deleted, `Callable` import dropped). Tests: the four exit-criterion paths + cannot_plan,
  sub-floor-approve, the cap, the driver outcome mapping; `FakePlanner`/`FakePlannerJury` + plan-result
  builders in `pipeline_fakes`. +32 tests (812 → 844 passed / 1 skipped).

**Decisions.** ADR 0091 (marker-aware resolver lands; generic `apply_field_patch`; flat buckets
deleted), 0092 (the `PlanAttempt` outcome wrapper + route-back; placement in the leaf to break a
cycle), 0093 (the plan-refinement coordinator + the Planner-Jury reusing `JuryVerdict`).

**Surprises / drift.**
- **Import cycle (the placement crux).** Putting the Planner outcome types in
  `agents/planner/outcome.py` created an `agents`↔`framework` load-time cycle: the leaf `results.py`
  (ADR 0075) importing `agents.planner.outcome` runs `agents.planner.__init__` → `planner.py` →
  `extractor.extractor` *mid-init* (which imports `results` to begin with). Moved
  `PlanOutcome`/`PlannerRefusal`/`PlanAttempt` into the leaf `results.py` itself (the one cycle-free
  home, alongside `PlanResult`), deleted `outcome.py`. The `agents.planner` surface re-exports them.
- **`PlanResult.manifest` is now optional** (a failed plan has no manifest) — the small ADR-0090
  contract evolution; Task-3 tests updated to narrow with `assert ... is not None`. Nothing consumes
  `PlanResult` yet.
- **The coordinator returns *all* terminal states (never raises)**, diverging from the extract
  `run_pipeline` (which raises on halts): route-back must be a returned value, and returning halts
  uniformly defers the CLI halt-vs-route-back-vs-ship policy to the Task-6 verb.
- **`CoreBlock.reproducibility` marked `FrameworkOwned` is NOT a manifest-lock violation** (Task-1
  lock): an `Annotated` marker is metadata — the on-disk shape is byte-identical — and the field was
  already specified framework-derived (`schema.md §4.8`). No manifest friction logged.
- **Planner-Jury reuses `JuryVerdict`** (not a bespoke verdict): `§5.8`/`§3.2.7` say "same shape as
  Extractor-Jury"; the four dimensions read naturally for the manifest.
- **Adversarial review** (a 4-dimension review→refute workflow over the diff: invariants, resolver
  correctness, contract fidelity, coordinator bugs) surfaced **no findings**.

**Deferred.**
- **Task 6:** the `plan` verb; wiring `ROUTE_BACK_TO_EXTRACTOR` to a real Extractor re-run
  (cross-pipeline); inserting the Layer-2 node into the plan graph; persistence; the manifest
  framework stamps (`spec_version`, `GenerationBlock.model`) at the persist seam (seams §2).
- **Task 5:** Validator Layer 2 (the coordinator has no Layer-2 node yet); the
  `StepBlock.reproducibility` carry-integrity check (seams §2, still open).
- **Task 7:** the Planner's `propose_facet`; the *jury-driven* missing-value-type route-back (the
  *Planner-driven* route-back is built — the §5.7 "Planner-Jury flags a missing value type" detection
  is a proposals-era concern).
- The `Stage`/`Node` refactor — `graph_support` shares only the trace wrappers; the larger
  consolidation lands at the first parallel node (Generators), per seams ③.1.

**Doc edits surfaced** (per ADR 0084): `dev/phase-2-seams.md §2` — the marker-aware resolver item
marked **LANDED** (ADR 0091). No `docs/` (architecture-tier) edits — `schema.md §4.8` /
`agents.md §5.7`/`§5.8` / `pipeline.md §3.2.6`–`§3.2.7` already specify what landed; the ADRs record
the mechanisms.

**Verify.** `just verify` green — ruff + format clean, pyright 0 errors (40 pre-existing click/yaml
warnings), 844 passed / 1 skipped (+32 since Task 3).

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
