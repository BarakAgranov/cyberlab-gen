# Phase 0 execution log

A running record of what each Phase 0 task actually built, what surprised the
implementer, and what was deferred. Entries are append-only; each task's
implementer adds an entry at the end.

The purpose is to inform Phase 1's brief and Phase 1's implementers: where
were the docs ambiguous? what design calls came up that the brief didn't
anticipate? what was harder or easier than expected?

Keep entries terse. Two paragraphs per task is usually right; a long entry
suggests something worth promoting into a `dev/decisions/` ADR instead.

---

## Task 0: Setup

**Date:** 2026-05-17
**Implementer:** Claude (Opus 4.7, 1M context)
**Time taken:** ~6 minutes execution (plan-mode work preceded; not counted)
**Commit:** `cdfa8261a9c4d8a77ace9dd963d8ece725da5f21`

### What was built

The eight `cyberlab_gen/` subpackages with docstring-only `__init__.py` files
(each docstring names the architectural section that governs the subpackage),
the `tests/{unit,integration,eval}/` layout with a one-test smoke file, the
`registry/` and `eval/{blog-sets,runner,reports}/` placeholder directories,
the tooling baseline (`pyproject.toml` with Phase 0 deps minus `openai` and
the three deferred `pytest-*` plugins, `uv.lock`, `justfile`, `.python-version`
pinned to 3.13, `.gitignore` extensions for venv/coverage/pyright), and the
GitHub Actions CI workflow on a Python 3.13/3.14 matrix. ADRs 0001 (typer),
0002 (hatchling), 0003 (Python upper bound `<3.15` with matrix) committed.
Local verification: ruff check, ruff format --check, pyright strict, pytest
all green.

### Surprises and friction

- **`just` not installed** on the dev machine (Windows). Ran the four gates
  directly via `uv run` instead. CI installs `just` via `extractions/setup-just@v4`
  and runs `just verify` end-to-end, so the gate is enforced in CI even when
  not locally.
- **Hatchling refused to build** because `pyproject.toml` declared `readme = "README.md"`
  but the file doesn't exist yet (Task 9 deliverable). Resolved by removing
  the `readme` line; Task 9 will re-add it together with `README.md` itself.
- **Ruff RUF002** caught an en-dash (`–`) in the `schemas/__init__.py` docstring
  ("Tasks 1–3"). Replaced with an ASCII hyphen. Worth knowing for Phase 1
  prompt-writing — avoid Unicode dashes in code.
- **Pyright doesn't honor `# noqa: F401`** — the original `import cyberlab_gen  # noqa: F401`
  in the smoke test still tripped `reportUnusedImport`. Rewrote the test to
  actually use the imported name (`assert cyberlab_gen.__name__ == "cyberlab_gen"`),
  which is also a stronger test.
- **`astral-sh/setup-uv` no longer publishes minor tags.** The plan's `@v3`
  reference was already known to need verification; web search confirmed the
  current best practice is pinning to an immutable patch tag. CI now uses
  `astral-sh/setup-uv@v8.1.0`.

### Deferred to later phases

- `README.md` and CONTRIBUTING.md (Task 9). The `readme` line in
  `pyproject.toml` will be re-added then.
- `cyberlab-gen` console_script entry-point in `pyproject.toml` (Task 7, when
  `cyberlab_gen.cli.main:main` exists).
- `pytest-cov`, `pytest-recording`, `pytest-asyncio` dev-deps (Phase 1+ when
  first exercised).
- `openai` SDK runtime dep (Phase 1+ when the OpenAI adapter is written).
- `tests/cassettes/` and any VCR plumbing (Phase 1+).
- Quote-style ADR (default `"double"` in `pyproject.toml` is the documentation).

### Doc-improvement notes for the next brief writer

Surface these to the architect as separate doc edits (not part of Task 0):

1. **`coding-conventions.md §1.1`'s `<3.14` Python cap is stale.** ADR 0003
   supersedes it for implementation; §1.1's literal cap value needs updating
   (the cap principle still holds — only the value is wrong).
2. **`coding-conventions.md §10.2` lists `openai` as a Phase 0 dep**, which
   conflicts with §10.1's just-in-time principle and with Tasks 5a/5b's
   "`<pinned-in-release>` placeholders" framing. Suggest moving `openai` to
   Phase 1 in §10.2.
3. **`coding-conventions.md §2.4` describes a testing stack** (`pytest-cov`,
   `pytest-recording`, `pytest-asyncio`) that doesn't apply to Phase 0. The
   doc could clarify which testing deps belong to which phase, matching the
   §10.2 pattern.
4. The phase-0 brief's Task 0 step 2 ("Move the architecture documents into
   `docs/`") is obsolete — docs were already in `docs/` at repo init.
5. The phase-0 brief uses `requires-python = ">=3.13"` whereas conventions §1.1
   uses `>=3.13,<3.14` and ADR 0003 now uses `>=3.13,<3.15`. The brief should
   pull the value from conventions rather than restate it.

---

## Task 1: Pydantic schema base layer

**Date:** 2026-05-17
**Implementer:** Claude (Opus 4.7, 1M context)
**Time taken:** ~15 minutes execution (plan-mode work preceded; not counted)
**Commit:** `29f68b5`

### What was built

`cyberlab_gen/schemas/base.py` (the `ArtifactModel` / `InternalModel` pair from
`schema-details.md §1`), `cyberlab_gen/schemas/primitives.py` (the seven
constrained-string aliases from §2.1 plus a `pydantic.HttpUrl` re-export), and
`cyberlab_gen/schemas/enums.py` (every closed `StrEnum` from §2.2, 22 enums
total, each retaining its `schema.md §X.Y` docstring citation per the schema-
details convention). `cyberlab_gen/schemas/__init__.py` now re-exports the
whole surface via an explicit `__all__`. Tests live under
`tests/unit/schemas/` split per source file: `test_base.py` (5 tests),
`test_primitives.py` (19 tests covering one-accept-one-reject per primitive),
`test_enums.py` (23 tests pinning every enum's full value set plus a Severity
round-trip through `ArtifactModel`). 48 tests pass; ruff, format-check, and
pyright strict all clean.

### Surprises and friction

- **Pyright's `type` is not iterable.** The first cut of `test_enums.py` had
  `def _values(enum_cls: type) -> set[str]` which pyright strict rejected
  (`"type" is not iterable`). Annotating as `type[StrEnum]` fixed it and
  is the stronger contract anyway.
- **The brief's enum example list includes `MessageRole`**, which lives in
  `provider-interface.md §4.1` (Task 5a), not in `schema-details.md §2.2`.
  Per CLAUDE.md's authority gradient (architecture > brief), `MessageRole`
  was omitted from `schemas/enums.py`; see the doc-improvement note below.
- No friction with PEP 695 syntax (none used here — generics land in Task 2's
  `Provenance[T]`).
- The architecture's choice of `use_enum_values=False` (`schema-details.md §1`)
  means `model_dump()` returns enum *members*, not their string values. The
  Severity round-trip test pins this so a future change to the config would
  fail loudly.

### Deferred to later phases

- `Provenance[T]`, `CitationBlock`, source-rules validator — Task 2.
- `to_yaml()` / `from_yaml()` artifact methods — Task 2 (first round-trip
  consumer is `AttackSpec`).
- Open-set string types (`ExecutionContext`, `ValueTypeName`, etc.) and their
  registry validators — Task 4.
- `MessageRole` enum — Task 5a (`providers/base.py`); see below.

### Doc-improvement notes for the next brief writer

1. **Task 1's `Required reading > Primary` mention of `MessageRole`** in the
   enum example list is cross-package. `MessageRole` is defined in
   `provider-interface.md §4.1` and owned by Task 5a (`providers/base.py`).
   Either replace `MessageRole` with another `schema-details.md §2.2`
   example (e.g., `CitationKind`) or note explicitly that the example was
   illustrative across subpackages.
2. **`schema-details.md §2.1` shows the constrained primitives living in
   `cyberlab_gen/schemas/common.py`** (line 58 of the doc) while the
   phase-0 brief specifies `primitives.py`. The brief wins for filename
   (decision-discretion); worth aligning the doc's filename comment so
   readers don't infer a second module name.

---

## Task 2: Pydantic schema envelope

**Date:** 2026-05-17
**Implementer:** Claude (Opus 4.7, 1M context)
**Time taken:** ~25 minutes execution (plan-mode work preceded; not counted)
**Commit:** `c879c36`

### What was built

`cyberlab_gen/schemas/provenance.py` (`Provenance[T]` PEP 695 generic +
`CitationBlock` + the eleven-rule `_source_rules` model_validator from
`schema-details.md §3`, plus the five convenience aliases
`ProvenanceString`/`ProvenanceStringList`/`ProvenanceFloat`/`ProvenanceInt`/
`ProvenanceBool`). `cyberlab_gen/schemas/attack_spec.py` (the
`AttackSpec` envelope, `ExtrasEntry`, and a single package-private
`_Phase0InnerStub(InternalModel)` placeholder that all nine inner content
blocks point at — each field carries a `# TODO(phase-1: schema-details.md §4.<sub>)`
comment naming the section that fills it in; the `_scope_consistency`
validator enforces the full IN_SCOPE / OUT_OF_SCOPE rule set from §4).
`cyberlab_gen/schemas/ingestion.py` (`IngestionResult` with eight required
fields from `implementation-plan.md §3.2`). `ArtifactModel` gained
`to_yaml()` / `from_yaml(cls)` via `ruamel.yaml` so every Phase-1 artifact
inherits round-trip for free. `__init__.py` re-exports the new public
surface (Provenance + aliases, CitationBlock, AttackSpec, ExtrasEntry,
IngestionResult); `_Phase0InnerStub` stays package-private. 63 new tests
under `tests/unit/schemas/` (26 provenance, 18 attack_spec including the
representative YAML round-trip, 19 ingestion, plus two new round-trip
tests on `_Artifact` in `test_base.py`); 113 tests pass total. Ruff,
format-check, and pyright strict all clean.

### Surprises and friction

- **`default_factory=list` is doubly awkward** under pyright-strict +
  ruff's RUF012. Pydantic v2's `default_factory=list` returns
  `list[Unknown]` to pyright (reportUnknownVariableType in strict
  mode), and the obvious workaround `= []` triggers RUF012
  ("mutable default value for class attribute") even though Pydantic
  copies the default per instance. The clean fix that satisfies both:
  `Field(default_factory=list[T])` — the parameterized form is a
  callable that returns `list[T]`, so pyright resolves the element
  type, and `Field(...)` keeps RUF012 quiet. Worth knowing for every
  artifact field with a list type in Phase 1.
- **`_Phase0InnerStub` is module-private**, but tests need to construct
  it as a placeholder for inner blocks. Pyright strict flags the
  cross-module import as `reportPrivateUsage`; suppressed with
  `# pyright: ignore[reportPrivateUsage]` on the test's import line.
  Phase 1 deletes `_Phase0InnerStub` entirely when each inner block
  gets its real Pydantic shape, so the suppression is temporary.
- **`ruamel.yaml`'s dump/load are untyped** (`reportUnknownMemberType`).
  Pyproject configures this as a warning, not an error, so the gate
  stays green. If the warning bothers a future reviewer, the fix is a
  narrow `# pyright: ignore[reportUnknownMemberType]` on the two call
  sites in `base.py` — but the warning is the most honest signal that
  the underlying API is loose, so leaving it is defensible.
- The doc's `_source_rules` validator includes a
  `# TODO(architecture)` for the EXTERNAL_API + confidence case
  (`schema-details.md §3`). Per the user's plan-review note, I did
  **not** add a rule the architecture has explicitly left open;
  the validator passes EXTERNAL_API with or without confidence.

### Deferred to later phases

- Every inner content block (`SourceBlock`, `ThesisBlock`, `ChainBlock`,
  `ExternalRefsBlock`, `RealWorldIncidentsBlock`, `DefenderTechniqueBlock`,
  `DefenseBlock`, `ReproducibilityBlock`, `GapEntry`,
  `ExtractionMetadataBlock`) — Phase 1 replaces `_Phase0InnerStub`
  field-by-field; the `# TODO(phase-1)` comments name the
  `schema-details.md §4.<sub>` section that fills each one in.
- Registry meta-schemas (Task 3) and the `LabManifest` envelope
  (Phase 1+) — both inherit `to_yaml`/`from_yaml` from `ArtifactModel`
  now.
- The EXTERNAL_API + confidence rule (the doc's own `# TODO(architecture)`).
- `Self`-return PEP 695 generics interaction with Pydantic's generic
  caching — Phase 1 may surface edge cases when the inner content
  blocks each parametrize Provenance with their value type.

### Doc-improvement notes for the next brief writer

1. **The Task 2 brief points at `schema-details.md §5.1`** for the
   AttackSpec envelope, but the doc itself numbers that section §4.
   Stale cite. Surface to the architect.
2. **`schema-details.md §4.1, §4.6, §4.8` declare inner blocks (e.g.,
   `SourceBlock`, `PublisherBlock`, `GapEntry`,
   `ExtractionMetadataBlock`, `ExtrasEntry`) as `BaseModel`** while
   re-specifying `model_config = ConfigDict(extra="forbid")`. The
   architectural intent (per `coding-conventions.md §11` and CLAUDE.md)
   is `ArtifactModel`. My implementations correctly inherit
   `ArtifactModel`. Flag for the architect: the `BaseModel` usages in
   `schema-details.md` should become `ArtifactModel` so the contract
   is uniform and the load-bearing config doesn't have to be repeated
   per class.
3. **The doc's `_source_rules` example in §3 uses `BaseModel` and
   redeclares `model_config = ConfigDict(extra="forbid", validate_
   assignment=True)`** for `Provenance[T]`. As the plan-reviewer
   noted, this is illustrative of the load-bearing settings, not a
   deliberate narrower config — Pydantic v2's config *replaces* rather
   than merges, so redeclaring even one setting drops the inherited
   `str_strip_whitespace`, `use_enum_values=False`, and
   `populate_by_name`. My `Provenance[T]` inherits `ArtifactModel`'s
   config without override. The doc could note this trap explicitly.
4. **`schema-details.md §3` line 403's `# TODO(architecture)` for
   EXTERNAL_API + confidence** is itself the right place to capture
   the deferred decision; flag for the architect to either decide it
   or move the TODO to a `dev/decisions/` ADR so it doesn't live in
   the contract doc indefinitely.

---

## Task 3: Registry meta-schemas

**Date:** 2026-05-18
**Implementer:** Claude (Opus 4.7, 1M context)
**Time taken:** ~30 minutes execution (plan-mode work preceded; not counted)
**Commit:** `e156e89`

### What was built

`cyberlab_gen/schemas/registries.py` — the full §6 meta-schema surface in
one file: six entry types (`ValueTypeEntry`, `FacetEntry`,
`ExternalDataSourceEntry`, `StaticCatalogEntry`, `ExecutionContextEntry`,
`LabCredentialEntry`); six supporting types (`ExternalSourceParam`,
`ExternalSourceEndpoint`, `RateLimit`, `CacheConfig`, `EnrichmentTrigger`,
`DiscrepancyMaterialityRule`); a private `_ExternalSourceEntryBase`
carrying the shared fields and the `_auth_rules` validator; six per-registry
container classes (`*Registry`); `ProposalAuditBlock`; and the PEP 695
generic per-file shapes `OverlayRegistryFile[E: BaseModel]` (with
`_proposal_keys_match_entries`) and `BundledRegistryFile[E: BaseModel]`.
Every class inherits `ArtifactModel` per ADR 0004. `__init__.py` re-exports
all public symbols (the private base stays in-module). 52 new tests in
`tests/unit/schemas/test_registries.py`; 165 tests pass total. `ArtifactModel.to_yaml()`
now passes `by_alias=True` so the `schema_: dict[str, Any] = Field(alias="schema")`
round-trip writes `schema:` rather than `schema_:`.

### Surprises and friction

- **`path_template` conflict between docs.** `schema-details.md §6.3` declares
  `path_template: NonEmptyString`, but 7 of 13 v1 seed entries in
  `registry-details.md §4.2` and `§5.2` (RSS feeds + all three static catalogs)
  use `path_template: ""`. Recorded as `dev/decisions/0007-empty-path-template.md`;
  implementation matches `schema-details.md §6.3` exactly (NonEmptyString) and
  the static-catalog test fixture uses a realistic non-empty path with an inline
  pointer to the ADR. Task 4 will hit this when loading the seeds — surface to
  the architect first.
- **`ENTRY_KEY_FIELD: ClassVar[str]` instead of `getattr(entry, 'name', None) or
  getattr(entry, 'id')`.** Per the user's hint, each entry type declares its
  registry-key field name as a class-level constant. `OverlayRegistryFile._entry_key`
  reads it with `getattr(type(entry), "ENTRY_KEY_FIELD")` (with `# noqa: B009`
  for the polymorphic lookup). Cleaner than the doc's fallback chain — a
  missing declaration becomes an immediate `AttributeError` instead of silently
  picking the wrong field.
- **Ruff `RUF100` on `# noqa: ANN401`.** CLAUDE.md / `coding-conventions.md §4.6`
  prescribe `# noqa: ANN401` next to every `Any`, but the project's ruff config
  doesn't enable `ANN401`, so ruff flags the noqa as unused. Dropped the noqa
  marker on the two `Any` sites in `ValueTypeEntry` (`schema_: dict[str, Any]`,
  `examples: list[Any]`) and kept the inline justification comment. The
  convention-vs-config gap is worth a sweep: either enable `ANN401` in ruff
  (and add the markers everywhere) or drop the markers from CLAUDE.md.
- **`ArtifactModel.to_yaml()` updated to pass `by_alias=True`.** Pre-existing
  behavior would have round-tripped `schema_` as the YAML key, not `schema`.
  Since `populate_by_name=True` already accepts both names on parse and no
  other artifact uses `Field(alias=...)` today, the change is monotonic — input
  with `schema:` round-trips to output `schema:`. Worth knowing for future
  artifact authors who reach for aliases.
- **`entry.model_fields` is deprecated** in Pydantic 2.13 in favor of
  `type(entry).model_fields`. Pyright flagged via `reportDeprecated`. Fixed
  the one site.

### Deferred to later phases

- Loader code (`cyberlab_gen/registries/loader.py`, `merge.py`),
  `MergedRegistries`, `RegistryLoadError`, the registry-load smoke test, and
  the bundled seed YAML files — all Task 4.
- Resolution of the `path_template` doc conflict (ADR 0007) — architect call
  or Task 4 escalation.
- Reconciling `coding-conventions.md §4.6`'s `# noqa: ANN401` convention with
  the ruff config that doesn't enable `ANN401`.

### Doc-improvement notes for the next brief writer

1. **`schema-details.md §6.3` declares `path_template: NonEmptyString` but
   `registry-details.md §4.2 / §5.2` ships 7 v1 seed entries with
   `path_template: ""`.** Two coherent resolutions: relax the schema to
   `str`, or change the seeds to a non-empty placeholder (`"/"` works). See
   ADR 0007. Task 4 cannot ship the documented seeds with the documented
   schema.
2. **The brief's Task 3 Step 4 names `StaticCatalogsRegistry._no_enrichment_triggers`
   and `_no_discrepancy_materiality_rules` validators** per `schema-details.md
   §6.3`. The doc does not define those validators — §6.3 enforces the type
   split structurally via `extra="forbid"` on `StaticCatalogEntry`. The
   implementation follows the doc (no separate validators); the test suite
   pins the structural guarantee explicitly (`test_static_catalog_rejects_*`).
   Worth updating the brief to match.
3. **Name discrepancy `schema-details.md §6` vs `registry-details.md §2/§3`.**
   §6.1 names `ValueTypeEntry`; §2.1 says "`ValueTypeRegistryEntry` from
   `schema-details.md`". Same for §6.2 `FacetEntry` vs §3.1
   `FacetRegistryEntry`. `registry-details.md` is also internally
   inconsistent: line 1320 in §3.5 uses `FacetEntry`, agreeing with §6.2.
   `schema-details.md §6` is the canonical naming; align `registry-details.md`.
4. **`schema-details.md §6.6` shows `MergedRegistries` at
   `cyberlab_gen/registries/loader.py` and per-registry shapes at
   `cyberlab_gen/schemas/registries/<name>.py`** (subpackage). Task 3's brief
   (Step 1) uses a single `cyberlab_gen/schemas/registries.py` file and that
   was implemented as written. The doc's subpackage layout might be desirable
   in Phase 1 when Phase-1 inner content blocks expand each file — could be
   noted as "subpackage split is a future option" rather than the canonical
   layout.
5. **`schema-details.md §6` should also note ADR 0004 explicitly** (per ADR
   0004's "doc updates incrementally as each section is exercised"). The §6
   sweep is now exercised; flag for the architect to update the §6 classes
   from `BaseModel + ConfigDict(extra="forbid")` to `ArtifactModel`.

---

## Task 3 follow-up: architect-locked decisions

**Date:** 2026-05-18
**Implementer:** Claude (Opus 4.7, 1M context)
**Commits:** `a98736e`, `241b42c`, `175ea8f`, `34d9118` (one per decision)

### What was built

The four "Doc-improvement notes" from Task 3's log entry were locked by the
architect and applied. ADR 0007 was rewritten to supersede its prior deferral
and `path_template` was relaxed to `str` across schema, doc, and ADR (Decision
1). Ruff `ANN401` was enabled in `pyproject.toml` and the lone real violation
was suppressed with an inline justification in `tests/unit/schemas/
test_ingestion.py` (Decision 2). The Task 3 brief's phantom validator names
(`_no_enrichment_triggers`, `_no_discrepancy_materiality_rules`) were replaced
with prose describing the structural enforcement that the implementation
actually ships (Decision 3). `schema-details.md §6` was swept from `BaseModel
+ ConfigDict(extra="forbid")` to `ArtifactModel` for eighteen classes, and
the stale `ValueTypeRegistryEntry` / `FacetRegistryEntry` names in
`registry-details.md §2.1` and §3.1 were corrected to match the canonical
names in §6 (Decision 4).

### Surprises and friction

- **`ANN401` does not fire on Pydantic field annotations.** The doc-improvement
  note in Task 3's entry assumed it did; the brief I received also assumed it
  would, instructing me to add `# noqa: ANN401` markers to `schema_:
  dict[str, Any]` and `examples: list[Any]`. Ruff reported them as unused
  (`RUF100`). The rule only checks function-signature `Any` — parameters and
  return types. The real violation surfaced in `_payload(**overrides: Any)`
  in `test_ingestion.py`, where the `noqa` was actually needed. The Task 3
  log's call to either "enable `ANN401` or drop the markers from
  `CLAUDE.md`" is now half-resolved: enabled in ruff, with a comment in
  `registries.py` explaining why no marker is needed at the field sites so a
  future reader doesn't add unnecessary ones.

- **Pre-existing ADR 0007 conflicted with the new locked decision.** Task 3
  had already filed a deferral ADR under the same number. Per ADR discipline
  (never silently rewrite history), the new content carries an explicit
  **Supersedes** section pointing at the prior version. The previous content
  is preserved in git history.

### Resolution status of Task 3's doc-improvement notes

1. `path_template` schema-vs-seeds conflict — **resolved** (Decision 1, ADR
   0007 supersede). Task 4 can ship the documented seeds.
2. Phantom validator names in the brief — **resolved** (Decision 3). Brief
   now matches `schema-details.md §6.3` and the implementation.
3. `ValueTypeRegistryEntry` / `FacetRegistryEntry` naming drift — **resolved**
   (Decision 4). `registry-details.md` now agrees with §6's canonical names.
4. Subpackage layout (`cyberlab_gen/schemas/registries/<name>.py` vs the
   single-file `cyberlab_gen/schemas/registries.py` that was implemented) —
   **left deferred** as the architect chose. Re-evaluate when Phase 1
   content blocks expand the file enough to make a split useful.
5. `§6` BaseModel → ArtifactModel sweep — **resolved** (Decision 4).

### Deferred to later phases

Nothing new beyond what Task 3 already deferred to Task 4 (the loader,
`MergedRegistries`, the bundled seed YAML files).

### Doc-improvement notes for the next brief writer

- The `coding-conventions.md §4.6` `# noqa: ANN401` convention should be
  re-read with the empirical finding above: the convention applies only to
  function signatures, not to Pydantic field annotations. A short clarifying
  sentence in §4.6 would prevent future implementers from adding markers
  that ruff will reject as unused.

---

## Task 4: Registry loaders and merge logic

**Date:** 2026-05-18
**Implementer:** Claude (Opus 4.7, 1M context)

### What was built

`cyberlab_gen/errors.py` with the Phase-0 error hierarchy stub: `CyberlabGenError`, `RegistryError`, `RegistryLoadError`. ADR 0009 records the partial structured-context fill (`run_id` declared but always `None` until Phase 1's pipeline runner lands) and the "only stub what's actually raised" scope.

`cyberlab_gen/registries/loader.py` with `load_bundled_file[E]`, `load_overlay_file[E]`, `load_bundled`, `load_overlay`, plus `bundled_registry_dir`, `default_overlay_dir`, `REGISTRY_FILE_NAMES`, and the `LoadedRegistryLayer` dataclass. Bundled files validate against `BundledRegistryFile[E]`; overlay files against `OverlayRegistryFile[E]` — the loader never shares a shape between the two so the Layer-1 structural guarantee (`schema-details.md §6.6` lines 1433-1434) is preserved. ADR 0010 records the bundled-path-resolution choice and the deferred wheel-packaging question.

`cyberlab_gen/registries/merge.py` with the `MergedRegistries` model, `_merge_entries[E]` helper, `merge_layers`, and `load_merged_registries`. `MergedRegistries` inherits from plain `BaseModel` (ADR 0008, ADR 0004 reserved case 3) with `frozen=True, extra="forbid"` and no `arbitrary_types_allowed`. Six accessors per `schema-details.md §6.6`; O(1) lookup via `PrivateAttr` indices built in `_build_indices`.

`cyberlab_gen/registries/__init__.py` rewritten to re-export the public surface (`MergedRegistries`, `RegistryLoadError`, the four load functions, helpers).

Six bundled seed YAMLs at `registry/<name>.yaml`, one entry each, transcribed verbatim from `docs/registry-details.md`: `aws_credentials` (§2.2.1), `target:aws` (§3.2), `nvd` (§4.2), `aws_iam_catalog` (§5.2), `attacker_local` (§6.2), `aws_test_access_key` (§7.5).

Two new integration test files. `tests/integration/test_registry_load.py` (16 tests) covers each registry's smoke load, the complete-layer load, the bundled-rejects-proposals structural guarantee, malformed-YAML / Pydantic-validation / missing-file / empty-file / duplicate-key error paths, and the `RegistryLoadError.path` attribute. `tests/integration/test_registry_merge.py` (12 tests) covers no-overlay baseline, overlay-wins for both `name`-keyed (facets) and `id`-keyed (external_data_sources) registries, overlay-only entries, real-and-orphan proposals, `lab_credential_patterns` filter, frozen immutability, accessor None-return, and merge ordering.

Three new ADRs: 0008 (`MergedRegistries` base class), 0009 (Phase-0 error hierarchy), 0010 (bundled registry path resolution).

### Surprises and friction

- **`PrivateAttr` + `frozen=True` empirical check came out positive.** Per the plan's risk note, ran a 5-line smoke before writing `merge.py`: a frozen `BaseModel` with `PrivateAttr` fields populated inside `@model_validator(mode="after")` works as intended. Public-field reassignment raises `ValidationError`; the private attrs are writable from the validator and queryable after. The plan's fallback (drop `frozen=True`) was therefore unnecessary. Pinned in `test_merged_registries_is_frozen`.

- **Duplicate-key check placed in the loader, not the schema.** The Task-3 file shapes (`BundledRegistryFile[E]`, `OverlayRegistryFile[E]`) don't enforce per-entry-key uniqueness. The Task-4 brief doesn't explicitly require it either, but the "clear error message on a malformed fixture" exit criterion implies it for any sensible read. Added the check inside `load_bundled_file` / `load_overlay_file` (post-Pydantic-validation) to avoid touching Task-3 schema code mid-Task-4. **Under unconstrained scope a `@model_validator(mode="after")` on the file shapes would be cleaner** — uniqueness is a structural property of the file, ValidationError flow is uniform, the loader surface shrinks. **Recorded as Task-5a refactor debt:** when scope opens up, move the check into the schema, drop the loader-level `_check_unique_keys`, and migrate `test_duplicate_entry_keys_rejected` from `tests/integration/test_registry_load.py` to `tests/unit/schemas/test_registries.py`.

- **`OverlayRegistryFile.proposals: dict[SnakeName, ProposalAuditBlock]` cannot key a `FacetName` entry.** Architectural debt blocking the proposal flow. A `FacetEntry` with `name = "target:aws"` (a `FacetName`, containing a colon) cannot have a matching proposals entry because the dict's key type is `SnakeName` (no colon allowed). Phase 0 ships no proposals so the bug is dormant in the seeds. **Phase 1 MUST widen this key type (or restructure to per-entry-type-keyed proposals) before any overlay-proposal accept-flow lands**, otherwise facet proposals will be silently impossible. Verified during merge-test writing: the orphan-proposals test uses `value_types` (`SnakeName`-keyed) to stay clear of the bug.

- **`schema-details.md §6.6` doc comments are slightly out of sync.** Line 1356's `# cyberlab_gen/registries/loader.py` comment locates `MergedRegistries` in `loader.py`; the implementation puts it in `merge.py` (single-direction import: loader → merge). Line 1358's `BaseModel(arbitrary_types_allowed=True)` keeps the now-unnecessary config. Both flagged as doc-improvement notes for incremental cleanup per ADR 0004's policy; not edited here per CLAUDE.md's no-implementation-doc-edits rule.

- **Closed bundled-only catalogs out of scope.** `registry-details.md §7` (and §1's category-2 list) names `detection_components`, `severity_levels`, `detection_formats`, `provisioning_mechanisms`, and `thesis_types` as closed-set bundled-only catalogs. Task 3 did NOT ship Pydantic models for any of them. Task 4 therefore did NOT ship seed YAMLs for them either — the Phase-0 smoke test (`implementation-plan.md §3.4` check 4) explicitly defers them ("and each closed bundled-only catalog once those get Pydantic models"). A Phase-1-prep task should add the models and seeds in lockstep.

### Resolution status of the three decisions flagged in the Task 4 plan

1. **`MergedRegistries` base class & `arbitrary_types_allowed`** — resolved. ADR 0008: `BaseModel` (ADR 0004 case 3), `frozen=True`, `extra="forbid"`, drop `arbitrary_types_allowed`.
2. **Bundled vs overlay separate validation** — resolved. Loader uses `BundledRegistryFile[E]` for bundled, `OverlayRegistryFile[E]` for overlay; pinned by `test_bundled_file_with_proposals_block_rejected`.
3. **`RegistryLoadError` location and base class** — resolved. ADR 0009: `cyberlab_gen/errors.py`; `Exception → CyberlabGenError → RegistryError → RegistryLoadError`. Registry-only scope for Phase 0; other stage classes per-task.

### Deferred to later phases

- Pydantic models + seed YAMLs for the five closed bundled-only catalogs (`detection_components`, `severity_levels`, `detection_formats`, `provisioning_mechanisms`, `thesis_types`). Phase-1-prep task.
- Schema-level duplicate-key enforcement (move `_check_unique_keys` from loader into `BundledRegistryFile` / `OverlayRegistryFile` validators). Task-5a refactor debt or earlier if convenient.
- `OverlayRegistryFile.proposals` key-type widening (`SnakeName` cannot key facet entries). **Must land before any Phase-1 proposal accept-flow.**
- Wheel-packaging of `registry/` and `importlib.resources`-based path resolution. ADR 0010 records the deferral; resolve when the distribution story lands.
- Wiring `LocalState.overlay_dir()` into `load_merged_registries`. Task 6 owns `LocalState`; the loader already accepts the override parameter.
- `run_id` plumbing through `RegistryLoadError`. Phase-1 pipeline-runner task.

### Doc-improvement notes for the next brief writer

1. `schema-details.md §6.6` line 1358 still shows `MergedRegistries(BaseModel)` with `arbitrary_types_allowed=True`. Per ADR 0008, the implementation drops `arbitrary_types_allowed` and adds `frozen=True` + `extra="forbid"`. Update §6.6 incrementally per ADR 0004's policy.
2. `schema-details.md §6.6` line 1356's `# cyberlab_gen/registries/loader.py` comment locates `MergedRegistries` in `loader.py`; the implementation puts it in `merge.py` for single-direction import. Update the doc comment to `merge.py` when §6 is next exercised, or drop the comment (the class's location is incidental to the spec).
3. `cyberlab_gen/schemas/registries.py:333` (`OverlayRegistryFile.proposals: dict[SnakeName, ProposalAuditBlock]`) is incompatible with `FacetName`-keyed entries. The fix likely needs a Phase-1 ADR plus a schema refactor (either widen the key type or introduce a per-entry-type proposals shape). Surfacing this prominently because any proposal-flow work that lands without addressing it will silently break facet proposals.

---
